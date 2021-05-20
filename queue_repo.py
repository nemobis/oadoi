import argparse
import logging
import os
from random import shuffle
from time import sleep
from time import time

from sqlalchemy import text

import endpoint # magic
import pmh_record # magic
import pub # magic

from app import db
from app import logger
from endpoint import Endpoint
from queue_main import DbQueue
from util import safe_commit


class DbQueueRepo(DbQueue):
    def table_name(self, job_type):
        table_name = "endpoint"
        return table_name

    def process_name(self, job_type):
        process_name = "run_repo" # formation name is from Procfile
        return process_name

    def maint(self, **kwargs):
        if parsed_args.id:
            endpoints = Endpoint.query.filter(Endpoint.id == parsed_args.id).all()
        else:
            # endpoints = Endpoint.query.filter(Endpoint.harvest_identify_response==None, Endpoint.error==None).all()
            endpoints = Endpoint.query.filter(Endpoint.harvest_identify_response == None).all()
            shuffle(endpoints)

        for my_endpoint in endpoints:
            my_endpoint.run_diagnostics()
            db.session.merge(my_endpoint)
            safe_commit(db)
            logger.info("merged and committed my_endpoint: {}".format(my_endpoint))

    def add_pmh_record(self, **kwargs):
        endpoint_id = kwargs.get("id", None)
        record_id = kwargs.get("recordid")
        my_repo = Endpoint.query.get(endpoint_id)
        print("my_repo", my_repo)
        my_pmh_record = my_repo.get_pmh_record(record_id)
        my_pmh_record.mint_pages()

        # for my_page in my_pmh_record.pages:
        #     print "my_page", my_page
        #     my_page.scrape()
        my_pmh_record.delete_old_record()
        db.session.merge(my_pmh_record)
        # print my_pmh_record.pages

        safe_commit(db)



    def worker_run(self, **kwargs):
        single_obj_id = kwargs.get("id", None)
        chunk = kwargs.get("chunk")
        queue_table = "endpoint"
        run_method = kwargs.get("method")
        run_class = Endpoint

        limit = 1 # just do one repo at a time

        if not single_obj_id:
            text_query_pattern = """WITH picked_from_queue AS (
                        SELECT id
                        FROM   {queue_table}
                        WHERE (
                            most_recent_year_harvested is null
                            or (most_recent_year_harvested < (now() at time zone 'utc')::date - interval '1 day')
                        )
                        and (
                            last_harvest_started is null
                            or last_harvest_started < now() at time zone 'utc' - interval '8 hours'
                        )
                        and (
                            last_harvest_finished is null
                            or last_harvest_finished < now() at time zone 'utc' - interval '2 minutes'
                        )
                        and (
                            retry_at <= now()
                            or retry_at is null
                        )
                        and ready_to_run
                        ORDER BY random() -- not rand, because want it to be different every time
                    LIMIT  {chunk}
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE {queue_table} queue_rows_to_update
                SET    last_harvest_started = now() at time zone 'utc', last_harvest_finished=null
                FROM   picked_from_queue
                WHERE picked_from_queue.id = queue_rows_to_update.id
                RETURNING picked_from_queue.*;"""
            text_query = text_query_pattern.format(
                chunk=chunk,
                queue_table=queue_table
            )
            logger.info("the queue query is:\n{}".format(text_query))

        index = 0
        start_time = time()
        while True:
            new_loop_start_time = time()
            if single_obj_id:
                objects = [run_class.query.filter(run_class.id == single_obj_id).first()]
            else:
                endpoint_id_rows = db.engine.execute(text(text_query).execution_options(autocommit=True)).fetchall()
                endpoint_ids = [row[0] for row in endpoint_id_rows]

                objects = db.session.query(run_class).filter(run_class.id.in_(endpoint_ids)).all()
                db.session.commit()

            if not objects:
                logger.info("none ready, sleeping for 5 seconds, then going again")
                sleep(5)
                continue

            print("run_method", run_method)
            self.update_fn(run_class, run_method, objects, index=index)

            # finished is set in update_fn
            index += 1
            if single_obj_id:
                return
            else:
                self.print_update(new_loop_start_time, chunk, limit, start_time, index)


    def run_right_thing(self, parsed_args, job_type):
        if parsed_args.dynos != None:  # to tell the difference from setting to 0
            self.scale_dyno(parsed_args.dynos, job_type)

        if parsed_args.status:
            self.print_status(job_type)

        if parsed_args.monitor:
            self.monitor_till_done(job_type)
            self.scale_dyno(0, job_type)

        if parsed_args.logs:
            self.print_logs(job_type)

        if parsed_args.kick:
            self.kick(job_type)

        if parsed_args.add:
            self.add_pmh_record(**vars(parsed_args))
        elif parsed_args.maint:
            self.maint(**vars(parsed_args))
        else:
            if parsed_args.id or parsed_args.run:
                self.run(parsed_args, job_type)
                if parsed_args.tilltoday:
                    while True:
                        self.run(parsed_args, job_type)


# python queue_repo.py --hybrid --filename=data/dois_juan_accuracy.csv --dynos=40 --soup


if __name__ == "__main__":
    if os.getenv('OADOI_LOG_SQL'):
        logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
        db.session.configure()

    parser = argparse.ArgumentParser(description="Run stuff.")
    parser.add_argument('--id', nargs="?", type=str, help="id of the one thing you want to update (case sensitive)")
    parser.add_argument('--recordid', nargs="?", type=str, help="id of the record you want to update (case sensitive)")
    parser.add_argument('--method', nargs="?", type=str, default="harvest", help="method name to run")

    parser.add_argument('--run', default=False, action='store_true', help="to run the queue")
    parser.add_argument('--status', default=False, action='store_true', help="to logger.info(the status")
    parser.add_argument('--dynos', default=None, type=int, help="scale to this many dynos")
    parser.add_argument('--logs', default=False, action='store_true', help="logger.info(out logs")
    parser.add_argument('--monitor', default=False, action='store_true', help="monitor till done, then turn off dynos")
    parser.add_argument('--kick', default=False, action='store_true', help="put started but unfinished dois back to unstarted so they are retried")
    parser.add_argument('--limit', "-l", nargs="?", type=int, help="how many jobs to do")
    parser.add_argument('--chunk', "-ch", nargs="?", default=1, type=int, help="how many to take off db at once")
    parser.add_argument('--maint', default=False, action='store_true', help="to run the queue")
    parser.add_argument('--tilltoday', default=False, action='store_true', help="run all the years till today")

    parser.add_argument('--add', default=False, action='store_true', help="how many to take off db at once")

    parsed_args = parser.parse_args()

    job_type = "normal"  #should be an object attribute
    my_queue = DbQueueRepo()
    my_queue.run_right_thing(parsed_args, job_type)
    print("finished")
