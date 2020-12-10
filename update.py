from time import time
import argparse

from jobs import update_registry
from util import elapsed
from app import db
from app import logger

# needs to be imported so the definitions get loaded into the registry
import jobs_defs


"""
examples of calling this:

# update everything
python update.py Person.refresh --limit 10 --chunk 5 --rq

# update one thing not using rq
python update.py Package.test --id 0000-1111-2222-3333

"""

def parse_update_optional_args(parser):
    # just for updating lots
    parser.add_argument('--limit', "-l", nargs="?", type=int, help="how many jobs to do")
    parser.add_argument('--chunk', "-ch", nargs="?", default=10, type=int, help="how many to take off db at once")
    parser.add_argument('--after', nargs="?", type=str, help="minimum id or id start, ie 0000-0001")
    parser.add_argument('--rq', action="store_true", default=False, help="do jobs in this thread")
    parser.add_argument('--order', action="store_true", default=True, help="order them")
    parser.add_argument('--append', action="store_true", default=False, help="append, dont' clear queue")
    parser.add_argument('--name', nargs="?", type=str, help="name for the thread")

    # just for updating one
    parser.add_argument('--id', nargs="?", type=str, help="id of the one thing you want to update")
    parser.add_argument('--doi', nargs="?", type=str, help="doi of the one thing you want to update")

    # parse and run
    parsed_args = parser.parse_args()
    return parsed_args


def run_update(parsed_args):
    update = update_registry.get(parsed_args.fn)

    start = time()

    #convenience method for handling an doi
    if parsed_args.doi:
        from pub import Pub
        from util import normalize_doi

        my_pub = db.session.query(Pub).filter(Pub.id==normalize_doi(parsed_args.doi)).first()
        parsed_args.id = my_pub.id
        logger.info(u"Got database hit for this doi: {}".format(my_pub.id))

    update.run(**vars(parsed_args))

    db.session.remove()
    logger.info(u"finished update in {} secconds".format(elapsed(start)))



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stuff.")
    # for everything
    parser.add_argument('fn', type=str, help="what function you want to run")
    parsed_args = parse_update_optional_args(parser)
    run_update(parsed_args)


