import logging
import sys

from copy import deepcopy

from urlparse import urljoin
from datetime import datetime
from couchdb import Database, Session

from gevent.event import Event
from gevent.lock import BoundedSemaphore

from requests import Session as RequestsSession
from dateutil.tz import tzlocal
from apscheduler.schedulers.gevent import GeventScheduler

from openprocurement.auction.gong.journal import (
    AUCTION_WORKER_SERVICE_AUCTION_RESCHEDULE,
    AUCTION_WORKER_SERVICE_AUCTION_NOT_FOUND,
    AUCTION_WORKER_SERVICE_AUCTION_STATUS_CANCELED,
    AUCTION_WORKER_SERVICE_AUCTION_CANCELED,
    AUCTION_WORKER_SERVICE_END_AUCTION,
    AUCTION_WORKER_SERVICE_START_AUCTION,
    AUCTION_WORKER_SERVICE_STOP_AUCTION_WORKER,
    AUCTION_WORKER_SERVICE_PREPARE_SERVER,
    AUCTION_WORKER_SERVICE_END_FIRST_PAUSE,
    AUCTION_WORKER_API_AUCTION_CANCEL,
    AUCTION_WORKER_API_AUCTION_NOT_EXIST
)
from openprocurement.auction.executor import AuctionsExecutor
from openprocurement.auction.utils import get_tender_data
from openprocurement.auction.worker_core.constants import TIMEZONE
from openprocurement.auction.gong.mixins import\
    DBServiceMixin,\
    BiddersServiceMixin, PostAuctionServiceMixin,\
    StagesServiceMixin
from openprocurement.auction.worker_core.mixins import (
    RequestIDServiceMixin,
    AuditServiceMixin,
    DateTimeServiceMixin
)
from openprocurement.auctions.gong.utils import (
    filter_bids,
    set_bids_information
)
from openprocurement.auction.gong.constants import (
    MULTILINGUAL_FIELDS,
    ADDITIONAL_LANGUAGES
)


LOGGER = logging.getLogger('Auction Worker')
SCHEDULER = GeventScheduler(job_defaults={"misfire_grace_time": 100},
                            executors={'default': AuctionsExecutor()},
                            logger=LOGGER)
SCHEDULER.timezone = TIMEZONE


class Auction(DBServiceMixin,
              RequestIDServiceMixin,
              AuditServiceMixin,
              BiddersServiceMixin,
              DateTimeServiceMixin,
              StagesServiceMixin,
              PostAuctionServiceMixin):
    """Auction Worker Class"""

    def __init__(self, tender_id,
                 worker_defaults={},
                 auction_data={},
                 lot_id=None):
        super(Auction, self).__init__()
        self.generate_request_id()
        self.tender_id = tender_id
        self.auction_doc_id = tender_id
        self.tender_url = urljoin(
            worker_defaults["resource_api_server"],
            '/api/{0}/{1}/{2}'.format(
                worker_defaults["resource_api_version"],
                worker_defaults["resource_name"],
                tender_id
            )
        )
        if auction_data:
            self.debug = True
            LOGGER.setLevel(logging.DEBUG)
            self._auction_data = auction_data
        else:
            self.debug = False
        self._end_auction_event = Event()
        self.bids_actions = BoundedSemaphore()
        self.session = RequestsSession()
        self.worker_defaults = worker_defaults
        if self.worker_defaults.get('with_document_service', False):
            self.session_ds = RequestsSession()
        self._bids_data = {}
        self.db = Database(str(self.worker_defaults["COUCH_DATABASE"]),
                           session=Session(retry_delays=range(10)))
        self.audit = {}
        self.retries = 10
        self.bidders_count = 0
        self.bidders_data = []
        self.bidders_features = {}
        self.bidders_coeficient = {}
        self.features = None
        self.mapping = {}
        self.rounds_stages = []
        self.use_api = False

    def schedule_auction(self):
        pass

    def wait_to_end(self):
        self._end_auction_event.wait()
        LOGGER.info("Stop auction worker",
                    extra={"JOURNAL_REQUEST_ID": self.request_id,
                           "MESSAGE_ID": AUCTION_WORKER_SERVICE_STOP_AUCTION_WORKER})

    def start_auction(self, switch_to_round=None):
        pass

    def end_auction(self):
        pass

    def cancel_auction(self):
        self.generate_request_id()
        if self.get_auction_document():
            LOGGER.info("Auction {} canceled".format(self.auction_doc_id),
                        extra={'MESSAGE_ID': AUCTION_WORKER_SERVICE_AUCTION_CANCELED})
            self.auction_document["current_stage"] = -100
            self.auction_document["endDate"] = datetime.now(tzlocal()).isoformat()
            LOGGER.info("Change auction {} status to 'canceled'".format(self.auction_doc_id),
                        extra={'MESSAGE_ID': AUCTION_WORKER_SERVICE_AUCTION_STATUS_CANCELED})
            self.save_auction_document()
        else:
            LOGGER.info("Auction {} not found".format(self.auction_doc_id),
                        extra={'MESSAGE_ID': AUCTION_WORKER_SERVICE_AUCTION_NOT_FOUND})

    def reschedule_auction(self):
        self.generate_request_id()
        if self.get_auction_document():
            LOGGER.info("Auction {} has not started and will be rescheduled".format(self.auction_doc_id),
                        extra={'MESSAGE_ID': AUCTION_WORKER_SERVICE_AUCTION_RESCHEDULE})
            self.auction_document["current_stage"] = -101
            self.save_auction_document()
        else:
            LOGGER.info("Auction {} not found".format(self.auction_doc_id),
                        extra={'MESSAGE_ID': AUCTION_WORKER_SERVICE_AUCTION_NOT_FOUND})

    def post_audit(self):
        pass

    def post_announce(self):
        if not self.use_api:
            return

        self.auction_document = self.get_auction_document()

        auction = self.get_auction_data()

        bids_information = filter_bids(auction)
        set_bids_information(self, self.auction_document, bids_information)

        self.generate_request_id()
        self.save_auction_document()

    def prepare_auction_document(self):
        self.generate_request_id()
        public_document = self.get_auction_document()

        self.auction_document = {}
        if public_document:
            self.auction_document = {"_rev": public_document["_rev"]}
        if self.debug:
            self.auction_document['mode'] = 'test'
            self.auction_document['test_auction_data'] = deepcopy(
                self._auction_data
            )

        self.synchronize_auction_info(prepare=True)

        self._prepare_auction_document_data()

        if self.worker_defaults.get('sandbox_mode', False):
            self._prepare_auction_document_stages(fast_forward=True)
        else:
            self._prepare_auction_document_stages()

        self.save_auction_document()

    def _prepare_auction_document_data(self):
        self.auction_document.update({
            "_id": self.auction_doc_id,
            "stages": [],
            "auctionID": self._auction_data["data"].get("auctionID", ""),
            "procurementMethodType": self._auction_data["data"].get(
                "procurementMethodType", "default"),
            "TENDERS_API_VERSION": self.worker_defaults["resource_api_version"],
            "current_stage": -1,
            "current_phase": "",
            "results": [],
            "procuringEntity": self._auction_data["data"].get(
                "procuringEntity", {}
            ),
            "items": self._auction_data["data"].get("items", []),
            "value": self._auction_data["data"].get("value", {}),
            "initial_value": self._auction_data["data"].get(
                "value", {}
            ).get('amount'),
            "auction_type": "kadastral",
        })

        for key in MULTILINGUAL_FIELDS:
            for lang in ADDITIONAL_LANGUAGES:
                lang_key = "{}_{}".format(key, lang)
                if lang_key in self._auction_data["data"]:
                    self.auction_document[lang_key] = self._auction_data["data"][lang_key]
            self.auction_document[key] = self._auction_data["data"].get(
                key, ""
            )

    def _prepare_auction_document_stages(self, fast_forward=False):
        if fast_forward:
            pass
        else:
            pass

    def synchronize_auction_info(self, prepare=False):
        if self.use_api:
            self._set_auction_data(prepare)

        self._set_start_date()
        self._set_bidders_data()
        self._set_mapping()

    def _set_auction_data(self, prepare=False):
        # Get auction from api and set it to _auction_data
        if not self.debug:
            if prepare:
                self._auction_data = get_tender_data(
                    self.tender_url,
                    request_id=self.request_id,
                    session=self.session
                )
            else:
                self._auction_data = {'data': {}}

            auction_data = get_tender_data(
                self.tender_url + '/auction',
                user=self.worker_defaults["resource_api_token"],
                request_id=self.request_id,
                session=self.session
            )

            if auction_data:
                self._auction_data['data'].update(auction_data['data'])
                self.startDate = self.convert_datetime(
                    self._auction_data['data']['auctionPeriod']['startDate']
                )
                del auction_data
            else:
                self.get_auction_document()
                if self.auction_document:
                    self.auction_document["current_stage"] = -100
                    self.save_auction_document()
                    LOGGER.warning("Cancel auction: {}".format(
                        self.auction_doc_id
                    ), extra={"JOURNAL_REQUEST_ID": self.request_id,
                              "MESSAGE_ID": AUCTION_WORKER_API_AUCTION_CANCEL})
                else:
                    LOGGER.error("Auction {} not exists".format(
                        self.auction_doc_id
                    ), extra={
                        "JOURNAL_REQUEST_ID": self.request_id,
                        "MESSAGE_ID": AUCTION_WORKER_API_AUCTION_NOT_EXIST
                    })
                    self._end_auction_event.set()
                    sys.exit(1)

    def _set_start_date(self):
        self.startDate = self.convert_datetime(
            self._auction_data['data'].get('auctionPeriod', {}).get('startDate', '')
        )

    def _set_bidders_data(self):
        self.bidders_data = [
            {
                'id': bid['id'],
                'date': bid['date'],
                'owner': bid.get('owner', '')
            }
            for bid in self._auction_data['data'].get('bids', [])
            if bid.get('status', 'active') == 'active'
        ]

    def _set_mapping(self):
        for index, bid in enumerate(self.bidders_data):
            if bid['id'] not in self.mapping:
                self.mapping[self.bidders_data[index]['id']] = len(self.mapping.keys()) + 1