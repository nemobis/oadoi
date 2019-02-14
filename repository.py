import os
import re
from sickle import Sickle
from sickle.response import OAIResponse
from sickle.iterator import OAIItemIterator
from sickle.models import ResumptionToken
from sickle.oaiexceptions import NoRecordsMatch
import requests
from time import sleep
from time import time
import datetime
import shortuuid
from random import random
import argparse
import lxml
from sqlalchemy import or_
from sqlalchemy import and_
import hashlib
import json

from app import db
from app import logger
import pmh_record
import pub
from util import elapsed
from util import safe_commit

def get_repos_by_ids(ids):
    repos = db.session.query(Repository).filter(Repository.id.in_(ids)).all()
    return repos

def lookup_repo_by_pmh_url(pmh_url_query=None):
    repos = Endpoint.query.filter(Endpoint.pmh_url.ilike(u"%{}%".format(pmh_url_query))).all()
    return repos

def get_sources_data(query_string=None):
    response = get_repository_data(query_string) + get_journal_data(query_string)
    return response

def get_sources_data_fast():
    all_journals = JournalMetadata.query.all()
    all_repos = Repository.query.all()
    all_sources = all_journals + all_repos

    return all_sources

    # all_sources_dict = {}
    # for source in all_sources:
    #     all_sources_dict[source.dedup_name] = source
    #
    # return all_sources_dict.values()


def get_journal_data(query_string=None):
    journal_meta_query = JournalMetadata.query
    if query_string:
        journal_meta_query = journal_meta_query.filter(or_(
            JournalMetadata.journal.ilike(u"%{}%".format(query_string)),
            JournalMetadata.publisher.ilike(u"%{}%".format(query_string)))
        )
    journal_meta = journal_meta_query.all()
    return journal_meta

def get_raw_repo_meta(query_string=None):
    raw_repo_meta_query = Repository.query.distinct(Repository.repository_name, Repository.institution_name)
    if query_string:
        raw_repo_meta_query = raw_repo_meta_query.filter(or_(
            Repository.repository_name.ilike(u"%{}%".format(query_string)),
            Repository.institution_name.ilike(u"%{}%".format(query_string)),
            Repository.home_page.ilike(u"%{}%".format(query_string)),
            Repository.id.ilike(u"%{}%".format(query_string))
        ))
    raw_repo_meta = raw_repo_meta_query.all()
    return raw_repo_meta

def get_repository_data(query_string=None):
    raw_repo_meta = get_raw_repo_meta(query_string)
    block_word_list = [
        "journal",
        "jurnal",
        "review",
        "revista",
        "annals",
        "annales",
        "magazine",
        "conference",
        "proceedings",
        "anales",
        "publisher",
        "press",
        "ojs",
        "bulletin",
        "acta"
    ]
    good_repo_meta = []
    for repo_meta in raw_repo_meta:
        if repo_meta.repository_name and repo_meta.institution_name:
            good_repo = True
            if repo_meta.bad_data:
                good_repo = False
            if repo_meta.is_journal:
                good_repo = False
            for block_word in block_word_list:
                if block_word in repo_meta.repository_name.lower() \
                        or block_word in repo_meta.institution_name.lower() \
                        or block_word in repo_meta.home_page.lower():
                    good_repo = False
                for endpoint in repo_meta.endpoints:
                    if endpoint.pmh_url and block_word in endpoint.pmh_url.lower():
                        good_repo = False
            if good_repo:
                good_repo_meta.append(repo_meta)
    return good_repo_meta


# created using this:
#     create table journal_metadata as (
#         select distinct on (normalize_title_v2(journal_name), normalize_title_v2(publisher))
#         journal_name as journal, publisher, journal_issns as issns from export_main_no_versions_20180116 where genre = 'journal-article')
# delete from journal_metadata where publisher='CrossRef Test Account'
class JournalMetadata(db.Model):
    publisher = db.Column(db.Text, primary_key=True)
    journal = db.Column(db.Text, primary_key=True)
    issns = db.Column(db.Text)

    @property
    def text_for_comparision(self):
        response = ""
        for attr in ["publisher", "journal"]:
            value = getattr(self, attr)
            if not value:
                value = ""
            response += value.lower()
        return response

    @property
    def dedup_name(self):
        return self.publisher.lower() + " " + self.journal.lower()

    @property
    def home_page(self):
        if self.issns:
            issn = self.issns.split(",")[0]
        else:
            issn = ""
        url = u"https://www.google.com/search?q={}+{}".format(self.journal, issn)
        url = url.replace(u" ", u"+")
        return url

    def to_csv_row(self):
        row = []
        for attr in ["home_page", "publisher", "journal"]:
            value = getattr(self, attr)
            if not value:
                value = ""
            value = value.replace(",", "; ")
            row.append(value)
        csv_row = u",".join(row)
        return csv_row

    def __repr__(self):
        return u"<JournalMetadata ({} {})>".format(self.journal, self.publisher)

    def to_dict(self):
        response = {
            "home_page": self.home_page,
            "institution_name": self.publisher,
            "repository_name": self.journal
        }
        return response



class Repository(db.Model):
    id = db.Column(db.Text, db.ForeignKey('endpoint.repo_unique_id'), primary_key=True)
    home_page = db.Column(db.Text)
    institution_name = db.Column(db.Text)
    repository_name = db.Column(db.Text)
    error_raw = db.Column(db.Text)
    bad_data = db.Column(db.Text)
    is_journal = db.Column(db.Boolean)

    endpoints = db.relationship(
        'Endpoint',
        lazy='subquery',
        cascade="all, delete-orphan",
        backref=db.backref("meta", lazy="subquery"),
        foreign_keys="Endpoint.repo_unique_id"
    )

    def __init__(self, **kwargs):
        self.id = shortuuid.uuid()[0:10]
        super(self.__class__, self).__init__(**kwargs)

    @property
    def text_for_comparision(self):
        return self.home_page.lower() + self.repository_name.lower() + self.institution_name.lower() + self.id.lower()

    @property
    def dedup_name(self):
        return self.institution_name.lower() + " " + self.repository_name.lower()

    def __repr__(self):
        return u"<Repository ({}) {}>".format(self.id, self.institution_name)

    def to_csv_row(self):
        row = []
        for attr in ["home_page", "institution_name", "repository_name"]:
            value = getattr(self, attr)
            if not value:
                value = ""
            value = value.replace(",", "; ")
            row.append(value)
        csv_row = u",".join(row)
        return csv_row

    def to_dict(self):
        response = {
            # "id": self.id,
            "home_page": self.home_page,
            "institution_name": self.institution_name,
            "repository_name": self.repository_name
            # "pmh_url": self.endpoint.pmh_url,
        }
        return response


def test_harvest_url(pmh_url):
    response = {}
    temp_endpoint = Endpoint()
    temp_endpoint.pmh_url = pmh_url
    temp_endpoint.set_identify_info()
    response["harvest_identify_response"] = temp_endpoint.harvest_identify_response

    # first = datetime.datetime(2000, 01, 01, 0, 0)
    # last = first + datetime.timedelta(days=30)
    # (pmh_input_record, pmh_records, error) = temp_endpoint.get_pmh_input_record(first, last)
    # if error:
    #     response["harvest_test_initial_dates"] = error
    # elif pmh_input_record:
    #     response["harvest_test_initial_dates"] = "SUCCESS!"
    # else:
    #     response["harvest_test_initial_dates"] = None

    last = datetime.datetime.utcnow()
    first = last - datetime.timedelta(days=30)
    response["sample_pmh_record"] = None
    (pmh_input_record, pmh_records, error) = temp_endpoint.get_pmh_input_record(first, last)
    if error:
        response["harvest_test_recent_dates"] = error
    elif pmh_input_record:
        response["harvest_test_recent_dates"] = "SUCCESS!"
        response["sample_pmh_record"] = json.dumps(pmh_input_record.metadata)
    else:
        response["harvest_test_recent_dates"] = "error, no pmh_input_records returned"

    # num_records = 0
    # while num_records < 100:
    #     num_records += 1
    #
    # response["pmh_records"] = len(pmh_records)

    return response


class Endpoint(db.Model):
    id = db.Column(db.Text, primary_key=True)
    id_old = db.Column(db.Text)
    repo_unique_id = db.Column(db.Text, db.ForeignKey('repository.id'))
    pmh_url = db.Column(db.Text)
    pmh_set = db.Column(db.Text)
    last_harvest_started = db.Column(db.DateTime)
    last_harvest_finished = db.Column(db.DateTime)
    most_recent_year_harvested = db.Column(db.DateTime)
    earliest_timestamp = db.Column(db.DateTime)
    email = db.Column(db.Text)  # to help us figure out what kind of repo it is
    error = db.Column(db.Text)
    repo_request_id = db.Column(db.Text)
    harvest_identify_response = db.Column(db.Text)
    harvest_test_recent_dates = db.Column(db.Text)
    sample_pmh_record = db.Column(db.Text)
    contacted = db.Column(db.DateTime)
    contacted_text = db.Column(db.Text)


    def __init__(self, **kwargs):
        super(self.__class__, self).__init__(**kwargs)
        if not self.id:
            self.id = shortuuid.uuid()[0:20].lower()

    def run_diagnostics(self):
        response = test_harvest_url(self.pmh_url)
        self.harvest_identify_response = response["harvest_identify_response"]
        # self.harvest_test_initial_dates = response["harvest_test_initial_dates"]
        self.harvest_test_recent_dates = response["harvest_test_recent_dates"]
        self.sample_pmh_record = response["sample_pmh_record"]

    def harvest(self):
        first = self.most_recent_year_harvested

        if not first:
            first = datetime.datetime(2000, 01, 01, 0, 0)

        if first > (datetime.datetime.utcnow() - datetime.timedelta(days=2)):
            first = datetime.datetime.utcnow() - datetime.timedelta(days=2)

        if self.id_old in ['citeseerx.ist.psu.edu/oai2',
                       'europepmc.org/oai.cgi',
                       'export.arxiv.org/oai2',
                       'www.ncbi.nlm.nih.gov/pmc/oai/oai.cgi',
                       'www.ncbi.nlm.nih.gov/pmc/oai/oai.cgi2']:
            first_plus_delta = first + datetime.timedelta(days=7)
        else:
            first_plus_delta = first.replace(year=first.year + 1)

        tomorrow = datetime.datetime.utcnow() + datetime.timedelta(days=1)
        last = min(first_plus_delta, tomorrow)
        first = first - datetime.timedelta(days=1)

        # now do the harvesting
        self.call_pmh_endpoint(first=first, last=last)

        # if success, update so we start at next point next time
        if self.error:
            logger.info(u"error so not saving finished info: {}".format(self.error))
        else:
            logger.info(u"success!  saving info")
            self.last_harvest_finished = datetime.datetime.utcnow().isoformat()
            self.most_recent_year_harvested = last
            self.last_harvest_started = None



    def get_my_sickle(self, repo_pmh_url, timeout=120):
        if not repo_pmh_url:
            return None

        proxies = {}
        if "citeseerx" in repo_pmh_url:
            proxy_url = os.getenv("STATIC_IP_PROXY")
            proxies = {"https": proxy_url, "http": proxy_url}
        my_sickle = MySickle(repo_pmh_url, proxies=proxies, timeout=timeout, iterator=MyOAIItemIterator)
        return my_sickle

    def get_pmh_record(self, record_id):
        my_sickle = self.get_my_sickle(self.pmh_url)
        pmh_input_record = my_sickle.GetRecord(identifier=record_id, metadataPrefix="oai_dc")
        my_pmh_record = pmh_record.PmhRecord()
        my_pmh_record.populate(pmh_input_record)
        my_pmh_record.repo_id = self.id_old  # delete once endpoint_id is populated
        my_pmh_record.endpoint_id = self.id
        return my_pmh_record

    def set_identify_info(self):
        if not self.pmh_url:
            self.harvest_identify_response = u"error, no pmh_url given"
            return

        try:
            # set timeout quick... if it can't do this quickly, won't be good for harvesting
            logger.debug(u"getting my_sickle for {}".format(self))
            my_sickle = self.get_my_sickle(self.pmh_url, timeout=10)
            data = my_sickle.Identify()
            self.harvest_identify_response = "SUCCESS!"

        except Exception as e:
            logger.exception(u"in set_identify_info")
            self.error = u"error in calling identify: {} {}".format(
                e.__class__.__name__, unicode(e.message).encode("utf-8"))
            if my_sickle:
                self.error += u" calling {}".format(my_sickle.get_http_response_url())

            self.harvest_identify_response = self.error



    def get_pmh_input_record(self, first, last):
        args = {}
        args['metadataPrefix'] = 'oai_dc'
        pmh_records = []
        error = None

        my_sickle = self.get_my_sickle(self.pmh_url)
        logger.info(u"connected to sickle with {}".format(self.pmh_url))

        args['from'] = first.isoformat()[0:10]
        if last:
            args["until"] = last.isoformat()[0:10]

        if self.pmh_set:
            args["set"] = self.pmh_set

        logger.info(u"calling ListRecords with {} {}".format(self.pmh_url, args))
        try:
            pmh_records = my_sickle.ListRecords(ignore_deleted=True, **args)
            # logger.info(u"got pmh_records with {} {}".format(self.pmh_url, args))
            pmh_input_record = self.safe_get_next_record(pmh_records)
        except NoRecordsMatch as e:
            logger.info(u"no records with {} {}".format(self.pmh_url, args))
            pmh_input_record = None
        except Exception as e:
            logger.exception(u"error with {} {}".format(self.pmh_url, args))
            pmh_input_record = None
            self.error = u"error in get_pmh_input_record: {} {}".format(
                e.__class__.__name__, unicode(e.message).encode("utf-8"))
            if my_sickle:
                self.error += u" calling {}".format(my_sickle.get_http_response_url())
            print error

        return (pmh_input_record, pmh_records, error)


    def call_pmh_endpoint(self,
                          first=None,
                          last=None,
                          chunk_size=50,
                          scrape=False):

        start_time = time()
        records_to_save = []
        num_records_updated = 0
        loop_counter = 0

        (pmh_input_record, pmh_records, error) = self.get_pmh_input_record(first, last)

        if error:
            self.error = u"error in get_pmh_input_record: {}".format(error)
            return

        while pmh_input_record:
            loop_counter += 1
            # create the record
            my_pmh_record = pmh_record.PmhRecord()

            # set its vars
            my_pmh_record.repo_id = self.id_old  # delete once endpoint_ids are all populated
            my_pmh_record.endpoint_id = self.id
            my_pmh_record.rand = random()
            my_pmh_record.populate(pmh_input_record)

            if is_complete(my_pmh_record):
                my_pages = my_pmh_record.mint_pages()
                my_pmh_record.pages = my_pages
                # logger.info(u"made {} pages for id {}: {}".format(len(my_pages), my_pmh_record.id, [p.url for p in my_pages]))
                if scrape:
                    for my_page in my_pages:
                        my_page.scrape_if_matches_pub()
                records_to_save.append(my_pmh_record)
                db.session.merge(my_pmh_record)
                # logger.info(u"my_pmh_record {}".format(my_pmh_record))
            else:
                logger.info(u"pmh record is not complete")
                # print my_pmh_record
                pass

            if len(records_to_save) >= chunk_size:
                num_records_updated += len(records_to_save)
                last_record = records_to_save[-1]
                # logger.info(u"last record saved: {} for {}".format(last_record.id, self.id))
                safe_commit(db)
                records_to_save = []

            if loop_counter % 100 == 0:
                logger.info(u"iterated through 100 more items, loop_counter={} for {}".format(loop_counter, self.id))

            pmh_input_record = self.safe_get_next_record(pmh_records)

        # make sure to get the last ones
        if records_to_save:
            num_records_updated += len(records_to_save)
            last_record = records_to_save[-1]
            logger.info(u"saving {} last ones, last record saved: {} for {}, loop_counter={}".format(
                len(records_to_save), last_record.id, self.id, loop_counter))
            safe_commit(db)
        else:
            logger.info(u"finished loop, but no records to save, loop_counter={}".format(loop_counter))

        # if num_records_updated > 0:
        if True:
            logger.info(u"updated {} PMH records for endpoint_id={}, took {} seconds".format(
                num_records_updated, self.id, elapsed(start_time, 2)))


    def safe_get_next_record(self, current_record):
        self.error = None
        try:
            next_record = current_record.next()
        except (requests.exceptions.HTTPError, requests.exceptions.SSLError):
            logger.info(u"requests exception!  skipping")
            self.error = u"requests error in safe_get_next_record; try again"
            return None
        except (KeyboardInterrupt, SystemExit):
            # done
            return None
        except StopIteration:
            logger.info(u"stop iteration! stopping")
            return None
        except Exception:
            logger.exception(u"misc exception!  skipping")
            self.error = u"error in safe_get_next_record; try again"
            return None
        return next_record

    def get_num_pmh_records(self):
        from pmh_record import PmhRecord
        num = db.session.query(PmhRecord.id).filter(PmhRecord.endpoint_id==self.id).count()
        return num

    def get_num_pages(self):
        from page import PageNew
        num = db.session.query(PageNew.id).filter(PageNew.endpoint_id==self.id).count()
        return num

    def get_num_open_with_dois(self):
        from page import PageNew
        num = db.session.query(PageNew.id).\
            distinct(PageNew.normalized_title).\
            filter(PageNew.endpoint_id==self.id).\
            filter(PageNew.num_pub_matches != None, PageNew.num_pub_matches >= 1).\
            filter(or_(PageNew.scrape_pdf_url != None, PageNew.scrape_metadata_url != None)).\
            count()
        return num

    def get_num_title_matching_dois(self):
        from page import PageNew
        num = db.session.query(PageNew.id).\
            distinct(PageNew.normalized_title).\
            filter(PageNew.endpoint_id==self.id).\
            filter(PageNew.num_pub_matches != None, PageNew.num_pub_matches >= 1).\
            count()
        return num

    def get_open_pages(self, limit=10):
        from page import PageNew
        pages = db.session.query(PageNew).\
            distinct(PageNew.normalized_title).\
            filter(PageNew.endpoint_id==self.id).\
            filter(PageNew.num_pub_matches != None, PageNew.num_pub_matches >= 1).\
            filter(or_(PageNew.scrape_pdf_url != None, PageNew.scrape_metadata_url != None)).\
            limit(limit).all()
        return [(p.id, p.url, p.normalized_title, p.pub.url, p.pub.unpaywall_api_url, p.scrape_version) for p in pages]

    def get_closed_pages(self, limit=10):
        from page import PageNew
        pages = db.session.query(PageNew).\
            distinct(PageNew.normalized_title).\
            filter(PageNew.endpoint_id==self.id).\
            filter(PageNew.num_pub_matches != None, PageNew.num_pub_matches >= 1).\
            filter(PageNew.scrape_updated != None, PageNew.scrape_pdf_url == None, PageNew.scrape_metadata_url == None).\
            limit(limit).all()
        return [(p.id, p.url, p.normalized_title, p.pub.url, p.pub.unpaywall_api_url, p.scrape_updated) for p in pages]

    def get_num_pages_still_processing(self):
        from page import PageNew
        num = db.session.query(PageNew.id).filter(PageNew.endpoint_id==self.id, PageNew.num_pub_matches == None).count()
        return num

    def __repr__(self):
        return u"<Endpoint ( {} ) {}>".format(self.id, self.pmh_url)


    def to_dict(self):
        response = {
            "_endpoint_id": self.id,
            "_pmh_url": self.pmh_url,
            "num_pmh_records": self.get_num_pmh_records(),
            "num_pages": self.get_num_pages(),
            "num_open_with_dois": self.get_num_open_with_dois(),
            "num_title_matching_dois": self.get_num_title_matching_dois(),
            "num_pages_still_processing": self.get_num_pages_still_processing(),
            "pages_open": u"{}/debug/repo/{}/examples/open".format("http://localhost:5000", self.repo_unique_id), # self.get_open_pages(),
            "pages_closed": u"{}/debug/repo/{}/examples/closed".format("http://localhost:5000", self.repo_unique_id), # self.get_closed_pages(),
            "metadata": {}
        }

        if self.meta:
            response.update({
                "metadata": {
                    "home_page": self.meta.home_page,
                    "institution_name": self.meta.institution_name,
                    "repository_name": self.meta.repository_name
                }
            })
        return response

    def to_dict_status(self):
        response = {
            "results": {},
            "metadata": {}
        }

        for field in ["id", "repo_unique_id", "pmh_url", "email"]:
            response[field] = getattr(self, field)

        for field in ["harvest_identify_response", "harvest_test_recent_dates", "sample_pmh_record"]:
            response["results"][field] = getattr(self, field)

        if self.meta:
            for field in ["home_page", "institution_name", "repository_name"]:
                response["metadata"][field] = getattr(self.meta, field)


        return response


def is_complete(record):
    if not record.id:
        return False
    if not record.title:
        return False
    if not record.urls:
        return False

    if record.oa == "0":
        logger.info(u"record {} is closed access. skipping.".format(record["id"]))
        return False

    return True









class MyOAIItemIterator(OAIItemIterator):
    def _get_resumption_token(self):
        """Extract and store the resumptionToken from the last response."""
        resumption_token_element = self.oai_response.xml.find(
            './/' + self.sickle.oai_namespace + 'resumptionToken')
        if resumption_token_element is None:
            return None
        token = resumption_token_element.text
        cursor = resumption_token_element.attrib.get('cursor', None)
        complete_list_size = resumption_token_element.attrib.get(
            'completeListSize', None)
        expiration_date = resumption_token_element.attrib.get(
            'expirationDate', None)
        resumption_token = ResumptionToken(
            token=token, cursor=cursor,
            complete_list_size=complete_list_size,
            expiration_date=expiration_date
        )
        return resumption_token

    def get_complete_list_size(self):
        """Extract and store the resumptionToken from the last response."""
        resumption_token_element = self.oai_response.xml.find(
            './/' + self.sickle.oai_namespace + 'resumptionToken')
        if resumption_token_element is None:
            return None
        complete_list_size = resumption_token_element.attrib.get(
            'completeListSize', None)
        if complete_list_size:
            return int(complete_list_size)
        return complete_list_size

# subclass so we can customize the number of retry seconds
class MySickle(Sickle):
    RETRY_SECONDS = 120

    def get_http_response_url(self):
        if hasattr(self, "http_response_url"):
            return self.http_response_url
        return None

    def harvest(self, **kwargs):  # pragma: no cover
        """Make HTTP requests to the OAI server.
        :param kwargs: OAI HTTP parameters.
        :rtype: :class:`sickle.OAIResponse`
        """
        start_time = time()
        for _ in range(self.max_retries):
            if self.http_method == 'GET':
                payload_str = "&".join("%s=%s" % (k,v) for k,v in kwargs.items())
                url_without_encoding = u"{}?{}".format(self.endpoint, payload_str)
                http_response = requests.get(url_without_encoding,
                                             **self.request_args)
                self.http_response_url = http_response.url
            else:
                http_response = requests.post(self.endpoint, data=kwargs,
                                              **self.request_args)
                self.http_response_url = http_response.url
            if http_response.status_code == 503:
                retry_after = self.RETRY_SECONDS
                logger.info("HTTP 503! Retrying after %d seconds..." % retry_after)
                sleep(retry_after)
            else:
                logger.info("took {} seconds to call pmh url: {}".format(elapsed(start_time), http_response.url))

                http_response.raise_for_status()
                if self.encoding:
                    http_response.encoding = self.encoding
                return OAIResponse(http_response, params=kwargs)


class RepoRequest(db.Model):
    id = db.Column(db.Text, primary_key=True)
    updated = db.Column(db.DateTime)
    email = db.Column(db.Text)
    pmh_url = db.Column(db.Text)
    repo_name = db.Column(db.Text)
    institution_name = db.Column(db.Text)
    examples = db.Column(db.Text)
    repo_home_page = db.Column(db.Text)
    comments = db.Column(db.Text)
    duplicate_request = db.Column(db.Text)

    def __init__(self, **kwargs):
        super(self.__class__, self).__init__(**kwargs)

    # trying to make sure the rows are unique
    def set_id_seed(self, id_seed):
        self.id = hashlib.md5(id_seed).hexdigest()[0:6]

    @classmethod
    def list_fieldnames(self):
        # these are the same order as the columns in the input google spreadsheet
        fieldnames = "id updated email pmh_url repo_name institution_name examples repo_home_page comments duplicate_request".split()
        return fieldnames

    @property
    def is_duplicate(self):
        return self.duplicate_request == "dup"

    @property
    def endpoints(self):
        return []

    @property
    def repositories(self):
        return []

    def matching_endpoints(self):

        response = self.endpoints

        if not self.pmh_url:
            return response

        url_fragments = re.findall(u'//([^/]+/[^/]+)', self.pmh_url)
        if not url_fragments:
            return response
        matching_endpoints_query = Endpoint.query.filter(Endpoint.pmh_url.ilike(u"%{}%".format(url_fragments[0])))
        hits = matching_endpoints_query.all()
        if hits:
            response += hits
        return response


    def matching_repositories(self):

        response = self.repositories

        if not self.institution_name or not self.repo_name:
            return response

        matching_query = Repository.query.filter(and_(
            Repository.institution_name.ilike(u"%{}%".format(self.institution_name)),
            Repository.repository_name.ilike(u"%{}%".format(self.repo_name))))
        hits = matching_query.all()
        if hits:
            response += hits
        return response


    def to_dict(self):
        response = {}
        for fieldname in RepoRequest.list_fieldnames():
            response[fieldname] = getattr(self, fieldname)
        return response

    def __repr__(self):
        return u"<RepoRequest ( {} ) {}>".format(self.id, self.pmh_url)



class BqRepoStatus(db.Model):
    id = db.Column(db.Text, primary_key=True)
    collected = db.Column(db.DateTime)
    repository_name = db.Column(db.Text)
    institution_name = db.Column(db.Text)
    pmh_url = db.Column(db.Text)
    check0_identify_status = db.Column(db.Text)
    check1_query_status = db.Column(db.Text)
    last_harvest = db.Column(db.DateTime)
    num_pmh_records = db.Column(db.Numeric)
    num_pmh_records_matching_dois = db.Column(db.Numeric)
    num_pmh_records_matching_dois_with_fulltext = db.Column(db.Numeric)
    submittedVersion = db.Column(db.Numeric)
    acceptedVersion = db.Column(db.Numeric)
    publishedVersion = db.Column(db.Numeric)


    def to_dict(self):
        results = {}
        results["metadata"] = {
            "repository_name": self.repository_name,
            "institution_name": self.institution_name,
            "pmh_url": self.pmh_url
        }
        results["status"] = {
            "check0_identify_status": self.harvest_identify_response,
            "check1_query_status": self.harvest_test_recent_dates,
            "num_pmh_records": self.num_distinct_pmh_records,
            "last_harvest": self.last_harvested,
            "num_pmh_records_matching_dois": self.num_distinct_pmh_has_matches,
            "num_pmh_records_matching_dois_with_fulltext": self.num_distinct_pmh_scrape_version_not_null
        }
        results["by_version_distinct_pmh_records_matching_dois"] = {
            "submittedVersion": self.num_distinct_pmh_submitted_version,
            "acceptedVersion": self.num_distinct_pmh_accepted_version,
            "publishedVersion": self.num_distinct_pmh_published_version
        }
        return results

    def __repr__(self):
        return u"<BqRepoStatus ( {} ) {}>".format(self.id, self.pmh_url)

def send_announcement_email():
    from emailer import send
    from emailer import create_email

    endpoints = Endpoint.query.filter(Endpoint.repo_request_id != None,
                                      Endpoint.contacted_text == None).all()
    for my_endpoint in endpoints:
        my_endpoint_id = my_endpoint.id
        email_address = my_endpoint.email
        repo_name = my_endpoint.meta.repository_name
        institution_name = my_endpoint.meta.institution_name
        print my_endpoint_id, email_address, repo_name, institution_name
        # prep email
        email = create_email(email_address,
                     "Update on your Unpaywall indexing request",
                     "repo_pulse",
                     {"data": {"endpoint_id": my_endpoint_id, "repo_name": repo_name, "institution_name": institution_name}},
                     [])
        send(email, for_real=True)

