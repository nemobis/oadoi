from sqlalchemy.dialects.postgresql import JSONB
from executor import execute
import requests
from time import time
import datetime
import shortuuid
from urllib import quote
import os
import re
import geoip2.webservice

from app import logger
from app import db
from util import elapsed
from util import safe_commit
from util import clean_doi
from util import normalize_title
from pub import build_new_pub
from pub import add_new_pubs
import put_crossref_in_db


# truncate doi_queue_dates;
# insert into doi_queue_dates (select s as id, random() as rand, false as enqueued, null::timestamp as finished, null::timestamp as started, null::text as dyno FROM generate_series
#         ( '1900-01-01'::timestamp
#         , '2018-02-21'::timestamp
#         , '1 day'::interval) s);
# truncate date_range;
# insert into date_range (select id from doi_queue_dates);

class DateRange(db.Model):
    id = db.Column(db.DateTime, primary_key=True)
    # end_date = db.Column(db.DateTime)

    @property
    def first(self):
        return self.id

    @property
    def first_day(self):
        return self.id.isoformat()[0:10]

    @property
    def last_day(self):
        return self.last.isoformat()[0:10]

    @property
    def last(self):
        return self.first + datetime.timedelta(days=1)

    def save_new_dois(self, rows=1000):
        number_added = put_crossref_in_db.save_new_dois(first=self.first_day, last=self.last_day, chunk_size=rows)
        return number_added


    def get_unpaywall_events(self, rows=100):
        insights_client = geoip2.webservice.Client(os.getenv("MAXMIND_CLIENT_ID"), os.getenv("MAXMIND_API_KEY"))

        tar_gz_filename = "today-{}.tsv.gz".format(self.first_day)

        execute("rm {}".format(tar_gz_filename), check=False)  # clear it if there is already one there
        command_template = """curl --no-include -o {} -L -H "X-Papertrail-Token: {}" https://papertrailapp.com/api/v1/archives/{}/download"""

        command = command_template.format(tar_gz_filename, os.getenv("PAPERTRAIL_API_KEY"), self.first_day)
        execute(command)
        if execute("ls -lh {}".format(tar_gz_filename), check=False):
            execute("zgrep email=unpaywall@impactstory.org {} > unpaywall_events.txt".format(tar_gz_filename), capture=True, check=False)

        else:
            # no file.  get the files for all the hours instead
            execute("rm unpaywall_events.txt", check=False)  # clear it if there is already one there, because appending
            for hour in range(24):
                day_with_hour = "{}-{:02d}".format(self.first_day, hour)
                command = command_template.format(tar_gz_filename, os.getenv("PAPERTRAIL_API_KEY"), day_with_hour)
                execute(command)
                execute("zgrep email=unpaywall@impactstory.org {} >> unpaywall_events.txt".format(tar_gz_filename), capture=True, check=False)


        # writing into database

        fh = open("unpaywall_events.txt", "r")
        if execute("ls -lh unpaywall_events.txt", check=False):
            num_this_loop = 0
            for line in fh:
                #only keep lines that are the right kind of log lines
                if line and not (u"?email=unpaywall@impactstory.org" in line and
                                         u'\toadoi\t' in line and
                                         u'\theroku/router\t' in line and
                                         u'at=info method=GET path="/10' in line):
                    continue

                columns = line.split("\t")
                collected = columns[1]
                if not collected.startswith("20"):
                    # not a valid timestamp, skip this line
                    continue

                # at=info method=GET path="/10.1177_1073858413514136?email=unpaywall@impactstory.org" host=api.oadoi.org request_id=7ae3022c-0dcd-44b7-ae7e-a888d8843d4f fwd="70.666.777.999" dyno=web.6 connect=1ms service=40ms status=200 bytes=774 protocol=https \n
                try:
                    doi = re.findall('path="/(.*)\?email=unpaywall@impactstory.org', line)[0]
                    doi = doi.lower()
                    id = re.findall('request_id=(.*?) ', line)[0]
                    ip = re.findall('fwd="(.*)"', line)[0]
                except IndexError:
                    # skip this line, it doesn't have a doi or ip or whatever, continue to next line
                    continue

                # print collected, doi, ip, id
                unpaywall_obj = UnpaywallEvent(doi=doi, ip=ip, collected=collected)
                db.session.merge(unpaywall_obj)
                insights = IpInsights.query.filter(IpInsights.ip==ip).first()
                if not insights:
                    try:
                        response_insights = insights_client.insights(ip)
                    except ValueError:
                        # this is what it throws if bad ip address
                        response_insights = None

                    if response_insights:
                        insight_dict = response_insights.raw
                        for key in ["city", "country", "continent", "registered_country"]:
                            if key in insight_dict and  "names" in insight_dict[key]:
                                insight_dict[key]["name"] = insight_dict[key]["names"]["en"]
                                del insight_dict[key]["names"]
                        for key in ["subdivisions"]:
                            if key in insight_dict:
                                my_list = []
                                for item in insight_dict[key]:
                                    if "names" in item:
                                        item["name"] = item["names"]["en"]
                                        del item["names"]
                                my_list.append(item)
                                insight_dict[key] = my_list
                        insights = IpInsights(ip=ip, insights=insight_dict)
                        db.session.merge(insights)

                    num_this_loop += 1

                    if num_this_loop > rows:
                        logger.info(u"committing")
                        safe_commit(db)
                        num_this_loop = 0

        logger.info(u"done everything, saving last ones")
        safe_commit(db)


    def __repr__(self):
        return u"<DateRange (starts: {})>".format(self.id)





class UnpaywallEvent(db.Model):
    id = db.Column(db.Text, primary_key=True)
    doi = db.Column(db.Text)
    collected = db.Column(db.DateTime)
    updated = db.Column(db.DateTime)
    ip = db.Column(db.Text)

    def __init__(self, **kwargs):
        self.id = shortuuid.uuid()[0:20]
        self.updated = datetime.datetime.utcnow()
        super(UnpaywallEvent, self).__init__(**kwargs)


class IpInsights(db.Model):
    id = db.Column(db.Text, primary_key=True)
    ip = db.Column(db.Text)
    insights = db.Column(JSONB)
    updated = db.Column(db.DateTime)

    def __init__(self, **kwargs):
        self.id = shortuuid.uuid()[0:20]
        self.updated = datetime.datetime.utcnow()
        super(IpInsights, self).__init__(**kwargs)
