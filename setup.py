from setuptools import setup, find_packages
import os

VERSION = '0.0.1b8'

INSTALL_REQUIRES = [
    'setuptools',
    'openprocurement.auction',
    'WTForms',
    'zope.component',
    'WTForms-JSON',
]
EXTRAS_REQUIRE = {
    'test': [
        'pytest',
        'mock',
        'pytest-mock',
        'pytest-cov'
    ]
}
ENTRY_POINTS = {
    'console_scripts': [
        'auction_texas = openprocurement.auction.texas.cli:main',
    ],
    'openprocurement.auction.components': [
        'texas = openprocurement.auction.texas.includeme:texas_components',
    ],
    'openprocurement.auction.routes': [
        'texas = openprocurement.auction.texas.includeme:texas_routes'
    ],
    'openprocurement.auction.robottests': [
        'texas = openprocurement.auction.texas.tests.functional.main:includeme'
    ],
}

setup(name='openprocurement.auction.texas',
      version=VERSION,
      description="",
      long_description=open(os.path.join("docs", "HISTORY.txt")).read(),
      # Get more strings from
      # http://pypi.python.org/pypi?:action=list_classifiers
      classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python",
      ],
      keywords='',
      author='Quintagroup, Ltd.',
      author_email='info@quintagroup.com',
      license='Apache License 2.0',
      url='https://github.com/openprocurement/openprocurement.auction.texas',
      packages=find_packages(exclude=['ez_setup']),
      namespace_packages=['openprocurement', 'openprocurement.auction'],
      include_package_data=True,
      zip_safe=False,
      install_requires=INSTALL_REQUIRES,
      extras_require=EXTRAS_REQUIRE,
      entry_points=ENTRY_POINTS,
      )
