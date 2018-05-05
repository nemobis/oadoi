import os
from sickle import Sickle
from sickle.response import OAIResponse
import boto
import datetime
import requests
from time import sleep
import argparse

from app import logger
from repository import Endpoint


def repo_to_db(repo=None,
              first=None,
              last=None,
              today=None,
              chunk_size=None,
              scrape=None):
    if today:
        last = datetime.date.today().isoformat()
        first = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()

    my_repo = Endpoint.query.filter(Endpoint.name==repo).first()
    my_repo.call_pmh_endpoint(first=first, last=last, chunk_size=chunk_size, scrape=scrape)
    logger.info(u"my_repo {}".format(my_repo))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stuff.")

    function = repo_to_db
    parser.add_argument('--repo', nargs="?", type=str, default="arxiv", help="repo name to look up in db table")

    parser.add_argument('--first', type=str, help="first date to pull stuff from oai-pmh (example: --start_date 2016-11-10")
    parser.add_argument('--last', type=str, help="last date to pull stuff from oai-pmh (example: --end_date 2016-11-10")
    parser.add_argument('--today', action="store_true", default=False, help="use if you want to pull in base records from last 2 days")

    parser.add_argument('--chunk_size', nargs="?", type=int, default=10, help="how many rows before a db commit")

    parser.add_argument('--scrape', action="store_true", default=False, help="use if you want to scrape all the pages.  good for debugging.")

    parsed = parser.parse_args()

    logger.info(u"calling {} with these args: {}".format(function.__name__, vars(parsed)))
    function(**vars(parsed))

