from time import time
from time import sleep
import argparse
import logging
import datetime
import os

from sqlalchemy.dialects import postgresql
from sqlalchemy import orm
from sqlalchemy import text
from sqlalchemy import sql

from app import db
from app import logger
from util import elapsed
from util import chunks
from util import safe_commit
from util import run_sql


def update_fn(cls, method, obj_id_list, shortcut_data=None, index=1):

    # we are in a fork!  dispose of our engine.
    # will get a new one automatically
    # if is pooling, need to do .dispose() instead
    db.engine.dispose()

    start = time()

    # logger(u"obj_id_list: {}".format(obj_id_list))

    q = db.session.query(cls).options(orm.undefer('*')).filter(cls.id.in_(obj_id_list))
    obj_rows = q.all()
    num_obj_rows = len(obj_rows)

    # if the queue includes items that aren't in the table, build them
    # assume they can be built by calling cls(id=id)
    if num_obj_rows != len(obj_id_list):
        logger.info(u"not all objects are there, so creating")
        ids_of_got_objects = [obj.id for obj in obj_rows]
        for id in obj_id_list:
            if id not in ids_of_got_objects:
                new_obj = cls(id=id)
                db.session.add(new_obj)
        safe_commit(db)
        logger.info(u"done")

    q = db.session.query(cls).options(orm.undefer('*')).filter(cls.id.in_(obj_id_list))
    obj_rows = q.all()
    num_obj_rows = len(obj_rows)

    logger.info(u"{pid} {repr}.{method_name}() got {num_obj_rows} objects in {elapsed} seconds".format(
        pid=os.getpid(),
        repr=cls.__name__,
        method_name=method.__name__,
        num_obj_rows=num_obj_rows,
        elapsed=elapsed(start)
    ))

    for count, obj in enumerate(obj_rows):
        start_time = time()

        if obj is None:
            return None

        method_to_run = getattr(obj, method.__name__)

        logger.info(u"***")
        logger.info(u"#{count} starting {repr}.{method_name}() method".format(
            count=count + (num_obj_rows*index),
            repr=obj,
            method_name=method.__name__
        ))

        if shortcut_data:
            method_to_run(shortcut_data)
        else:
            method_to_run()

        logger.info(u"finished {repr}.{method_name}(). took {elapsed} seconds".format(
            repr=obj,
            method_name=method.__name__,
            elapsed=elapsed(start_time, 4)
        ))


    logger.info(u"committing\n\n")
    start_time = time()
    commit_success = safe_commit(db)
    if not commit_success:
        logger.info(u"COMMIT fail")
    logger.info(u"commit took {} seconds".format(elapsed(start_time, 2)))
    db.session.remove()  # close connection nicely
    return None  # important for if we use this on RQ



def enqueue_jobs(cls,
         method,
         ids_q_or_list,
         queue_number,
         append=False,
         chunk_size=25,
         shortcut_fn=None
    ):
    """
    Takes sqlalchemy query with IDs, runs fn on those repos.
    """

    shortcut_data = None
    if shortcut_fn:
        shortcut_data_start = time()
        logger.info(u"Getting shortcut data...")
        shortcut_data = shortcut_fn()
        logger.info(u"Got shortcut data in {} seconds".format(
            elapsed(shortcut_data_start)
        ))

    chunk_size = int(chunk_size)


    start_time = time()
    new_loop_start_time = time()
    index = 0

    try:
        logger.info(u"running this query: \n{}\n".format(
            ids_q_or_list.statement.compile(dialect=postgresql.dialect())))
        row_list = ids_q_or_list.all()

    except AttributeError:
        logger.info(u"running this query: \n{}\n".format(ids_q_or_list))
        row_list = db.engine.execute(sql.text(ids_q_or_list)).fetchall()

    if row_list is None:
        logger.info(u"no IDs, all done.")
        return None

    logger.info(u"finished enqueue_jobs query in {} seconds".format(elapsed(start_time)))
    object_ids = [row[0] for row in row_list]

    num_items = len(object_ids)
    logger.info(u"adding {} items to queue...".format(num_items))

    # iterate through chunks of IDs like [[id1, id2], [id3, id4], ...  ]
    object_ids_chunk = []

    for object_ids_chunk in chunks(object_ids, chunk_size):

        update_fn_args = [cls, method, object_ids_chunk]

        update_fn_args.append(shortcut_data)
        update_fn(*update_fn_args, index=index)

        if True: # index % 10 == 0 and index != 0:
            num_jobs_remaining = num_items - (index * chunk_size)
            try:
                jobs_per_hour_this_chunk = chunk_size / float(elapsed(new_loop_start_time) / 3600)
                predicted_mins_to_finish = round(
                    (num_jobs_remaining / float(jobs_per_hour_this_chunk)) * 60,
                    1
                )
                logger.info(u"\n\nWe're doing {} jobs per hour. At this rate, done in {}min".format(
                    int(jobs_per_hour_this_chunk),
                    predicted_mins_to_finish
                ))
                logger.info(u"(finished chunk {} of {} chunks in {} seconds total, {} seconds this loop)\n".format(
                    index,
                    num_items/chunk_size,
                    elapsed(start_time),
                    elapsed(new_loop_start_time)
                ))
            except ZeroDivisionError:
                # logger.info(u"not printing status because divide by zero")
                logger.info(u"."),


            new_loop_start_time = time()
        index += 1
    logger.info(u"last chunk of ids: {}".format(list(object_ids_chunk)))

    db.session.remove()  # close connection nicely
    return True






class UpdateRegistry():
    def __init__(self):
        self.updates = {}

    def register(self, update):
        self.updates[update.name] = update

    def get(self, update_name):
        return self.updates[update_name]

update_registry = UpdateRegistry()


class UpdateDbQueue():
    def __init__(self, **kwargs):
        self.job = kwargs["job"]
        self.method = self.job
        self.cls = self.job.im_class
        self.chunk = kwargs.get("chunk", 10)
        self.shortcut_fn = kwargs.get("shortcut_fn", None)
        self.shortcut_fn_per_chunk = kwargs.get("shortcut_fn_per_chunk", None)
        self.name = "{}.{}".format(self.cls.__name__, self.method.__name__)
        self.action_table = kwargs.get("action_table", None)
        self.where = kwargs.get("where", None)
        self.queue_name = kwargs.get("queue_name", None)


    def run(self, **kwargs):
        single_obj_id = kwargs.get("id", None)
        limit = kwargs.get("limit", 0)
        chunk = kwargs.get("chunk", self.chunk)
        after = kwargs.get("after", None)
        queue_table = "doi_queue"

        if single_obj_id:
            limit = 1
        else:
            if not limit:
                limit = 1000
            ## based on http://dba.stackexchange.com/a/69497
            if self.action_table == "base":
                text_query_pattern = """WITH selected AS (
                           SELECT *
                           FROM   {table}
                           WHERE  queue != '{queue_name}' and {where}
                       LIMIT  {chunk}
                       FOR UPDATE SKIP LOCKED
                       )
                    UPDATE {table} records_to_update
                    SET    queue='{queue_name}'
                    FROM   selected
                    WHERE selected.id = records_to_update.id
                    RETURNING records_to_update.id;"""
                text_query = text_query_pattern.format(
                    table=self.action_table,
                    where=self.where,
                    chunk=chunk,
                    queue_name=self.queue_name)
            else:
                my_dyno_name = os.getenv("DYNO", "unknown")
                if kwargs.get("hybrid", False) or "hybrid" in my_dyno_name:
                    queue_table += "_with_hybrid"
                elif kwargs.get("dates", False) or "dates" in my_dyno_name:
                    queue_table += "_dates"

                text_query_pattern = """WITH picked_from_queue AS (
                           SELECT *
                           FROM   {queue_table}
                           WHERE  started is null
                           ORDER BY rand
                       LIMIT  {chunk}
                       FOR UPDATE SKIP LOCKED
                       )
                    UPDATE {queue_table} doi_queue_rows_to_update
                    SET    started=now()
                    FROM   picked_from_queue
                    WHERE picked_from_queue.id = doi_queue_rows_to_update.id
                    RETURNING doi_queue_rows_to_update.id;"""
                text_query = text_query_pattern.format(
                    chunk=chunk,
                    queue_table=queue_table
                )
            logger.info(u"the queue query is:\n{}".format(text_query))

        index = 0

        start_time = time()
        while True:
            new_loop_start_time = time()
            if single_obj_id:
                object_ids = [single_obj_id]
            else:
                # logger.info(u"looking for new jobs")
                row_list = db.engine.execute(text(text_query).execution_options(autocommit=True)).fetchall()
                object_ids = [row[0] for row in row_list]
                # logger.info(u"finished get-new-ids query in {} seconds".format(elapsed(new_loop_start_time)))

            if not object_ids:
                # logger.info(u"sleeping for 5 seconds, then going again")
                sleep(5)
                continue

            update_fn_args = [self.cls, self.method, object_ids]

            shortcut_data = None
            if self.shortcut_fn_per_chunk:
                shortcut_data_start = time()
                logger.info(u"Getting shortcut data...")
                shortcut_data = self.shortcut_fn_per_chunk()
                logger.info(u"Got shortcut data in {} seconds".format(
                    elapsed(shortcut_data_start)))

            update_fn(*update_fn_args, index=index, shortcut_data=shortcut_data)

            try:
                ids_escaped = [id.replace(u"'", u"''") for id in object_ids]
            except TypeError:
                ids_escaped = object_ids
            object_ids_str = u",".join([u"'{}'".format(id) for id in ids_escaped])
            object_ids_str = object_ids_str.replace(u"%", u"%%")  #sql escaping
            run_sql(db, u"update {queue_table} set finished=now() where id in ({ids})".format(
                queue_table=queue_table, ids=object_ids_str))

            index += 1

            if single_obj_id:
                return
            else:
                num_items = limit  #let's say have to do the full limit
                num_jobs_remaining = num_items - (index * chunk)
                try:
                    jobs_per_hour_this_chunk = chunk / float(elapsed(new_loop_start_time) / 3600)
                    predicted_mins_to_finish = round(
                        (num_jobs_remaining / float(jobs_per_hour_this_chunk)) * 60,
                        1
                    )
                    logger.info(u"\n\nWe're doing {} jobs per hour. At this rate, if we had to do everything up to limit, done in {}min".format(
                        int(jobs_per_hour_this_chunk),
                        predicted_mins_to_finish
                    ))
                    logger.info(u"\t{} seconds this loop, {} chunks in {} seconds, {} seconds/chunk average\n".format(
                        elapsed(new_loop_start_time),
                        index,
                        elapsed(start_time),
                        round(elapsed(start_time)/float(index), 1)
                    ))
                except ZeroDivisionError:
                    # logger.info(u"not printing status because divide by zero")
                    logger.info(u"."),


class Update():
    def __init__(self, **kwargs):
        self.queue_id = kwargs.get("queue_id", None)
        self.query = kwargs["query"]
        self.job = kwargs["job"]
        self.method = self.job
        self.cls = self.job.im_class
        self.chunk = kwargs.get("chunk", 10)
        self.shortcut_fn = kwargs.get("shortcut_fn", None)
        self.name = "{}.{}".format(self.cls.__name__, self.method.__name__)


    def run(self, **kwargs):
        id = kwargs.get("id", None)
        limit = kwargs.get("limit", 0)
        chunk = kwargs.get("chunk", self.chunk)
        after = kwargs.get("after", None)
        append = kwargs.get("append", False)

        if not limit:
            limit = 1000

        query = self.query
        try:
            # do some query manipulation, unless it is a list of IDs
            # if num_jobs < 1000:
            #     query = query.order_by(self.cls.id)
            # else:
            #     logger.info(u"not using ORDER BY in query because too many jobs, would be too slow"

            if after:
                query = query.filter(self.cls.id > after)

            if id:
                # don't run the query, just get the id that was requested
                query = db.session.query(self.cls.id).filter(self.cls.id == id)
            else:
                query = query.limit(limit)
        except AttributeError:
            logger.info(u"appending limit to query string")
            query += u" limit {}".format(limit)

        enqueue_jobs(
            self.cls,
            self.method.__name__,
            query,
            self.queue_id,
            append,
            chunk,
            self.shortcut_fn
        )




class UpdateStatus():
    seconds_between_chunks = 15

    def __init__(self, num_jobs, queue_number):
        self.num_jobs_total = num_jobs
        self.queue_number = queue_number
        self.start_time = time()

        self.last_chunk_start_time = time()
        self.last_chunk_num_jobs_completed = 0
        self.number_of_prints = 0


def main(fn, optional_args=None):
    start = time()

    # call function by its name in this module, with all args :)
    # http://stackoverflow.com/a/4605/596939
    if optional_args:
        globals()[fn](*optional_args)
    else:
        globals()[fn]()

    logger.info(u"total time to run: {} seconds".format(elapsed(start)))


if __name__ == "__main__":

    # get args from the command line:
    parser = argparse.ArgumentParser(description="Run stuff.")
    parser.add_argument('function', type=str, help="what function you want to run")
    parser.add_argument('optional_args', nargs='*', help="positional args for the function")

    args = vars(parser.parse_args())

    function = args["function"]
    optional_args = args["optional_args"]

    logger.info(u"running main.py {function} with these args:{optional_args}\n".format(
        function=function, optional_args=optional_args))

    main(function, optional_args)

    db.session.remove()


