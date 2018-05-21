from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_compress import Compress
from flask_debugtoolbar import DebugToolbarExtension
from sqlalchemy import exc
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.pool import Pool

import logging
import sys
import os
import requests
import json
import boto
import random
import airbrake

from util import safe_commit
from util import elapsed
from util import HTTPMethodOverrideMiddleware

HEROKU_APP_NAME = "articlepage"

# set up logging
# see http://wiki.pylonshq.com/display/pylonscookbook/Alternative+logging+configuration
logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format='%(thread)d: %(message)s'  #tried process but it was always "6" on heroku
)
logger = logging.getLogger("oadoi")
logger.addHandler(airbrake.AirbrakeHandler())

libraries_to_mum = [
    "requests",
    "urllib3",
    "requests.packages.urllib3",
    "requests_oauthlib",
    "stripe",
    "oauthlib",
    "boto",
    "newrelic",
    "RateLimiter",
    "paramiko",
    "chardet"
]

for a_library in libraries_to_mum:
    the_logger = logging.getLogger(a_library)
    the_logger.setLevel(logging.WARNING)
    the_logger.propagate = True

requests.packages.urllib3.disable_warnings()

app = Flask(__name__)

# database stuff
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = True  # as instructed, to suppress warning
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_ECHO'] = (os.getenv("SQLALCHEMY_ECHO", False) == "True")

# from http://stackoverflow.com/a/12417346/596939
class NullPoolSQLAlchemy(SQLAlchemy):
    def apply_driver_hacks(self, app, info, options):
        options['poolclass'] = NullPool
        return super(NullPoolSQLAlchemy, self).apply_driver_hacks(app, info, options)

db = NullPoolSQLAlchemy(app, session_options={"autoflush": False})

# do compression.  has to be above flask debug toolbar so it can override this.
compress_json = os.getenv("COMPRESS_DEBUG", "False")=="True"


# set up Flask-DebugToolbar
if (os.getenv("FLASK_DEBUG", False) == "True"):
    logger.info(u"Setting app.debug=True; Flask-DebugToolbar will display")
    compress_json = False
    app.debug = True
    app.config['DEBUG'] = True
    app.config["DEBUG_TB_INTERCEPT_REDIRECTS"] = False
    app.config["SQLALCHEMY_RECORD_QUERIES"] = True
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
    toolbar = DebugToolbarExtension(app)

# gzip responses
Compress(app)
app.config["COMPRESS_DEBUG"] = compress_json


# aws s3 connection
s3_conn = boto.connect_s3(
    os.getenv("AWS_ACCESS_KEY_ID"),
    os.getenv("AWS_SECRET_ACCESS_KEY")
)

# imports got here for tables that need auto-created.
# import publication
# import version
# import gs
#
# import run_through_dois
# import oa_base
# import date_range
# db.create_all()
# commit_success = safe_commit(db)
# if not commit_success:
#     logger.info(u"COMMIT fail making objects")


# from http://docs.sqlalchemy.org/en/latest/core/pooling.html
# This recipe will ensure that a new Connection will succeed even if connections in the pool
# have gone stale, provided that the database server is actually running.
# The expense is that of an additional execution performed per checkout
# @event.listens_for(Pool, "checkout")
# def ping_connection(dbapi_connection, connection_record, connection_proxy):
#     cursor = dbapi_connection.cursor()
#     try:
#         cursor.execute("SELECT 1")
#     except:
#         # optional - dispose the whole pool
#         # instead of invalidating one at a time
#         # connection_proxy._pool.dispose()
#
#         # raise DisconnectionError - pool will try
#         # connecting again up to three times before raising.
#         raise exc.DisconnectionError()
#     cursor.close()


with open("data/doaj_issns.json", "r") as fh:
    doaj_issns_with_hyphens = json.load(fh)
    # remove hyphens here so don't have to do it every time
    doaj_issns = []
    for row in doaj_issns_with_hyphens:
        (row_issn_with_hyphen, row_license, doaj_start_year) = row
        row_issn_no_hypen = row_issn_with_hyphen.replace("-", "")
        doaj_issns.append([row_issn_no_hypen, row_license, doaj_start_year])

with open("data/doaj_titles.json", "r") as fh:
    doaj_titles = [(title.encode("utf-8"), license, start_year) for (title, license, start_year) in json.load(fh)]

