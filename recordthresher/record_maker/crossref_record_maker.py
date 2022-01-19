import datetime
import hashlib
import math
import re
import uuid
from urllib.parse import quote

import shortuuid
from sqlalchemy import text

from app import db
from recordthresher.crossref_doi_record import CrossrefDoiRecord
from recordthresher.util import normalize_author
from recordthresher.util import normalize_citation
from recordthresher.util import parseland_parse
from util import normalize
from .record_maker import RecordMaker


class CrossrefRecordMaker(RecordMaker):
    @staticmethod
    def _is_specialized_record_maker(pub):
        return False

    @classmethod
    def make_record(cls, pub):
        return cls._dispatch(pub=pub)

    @staticmethod
    def _parseland_api_url(pub):
        return f'https://parseland.herokuapp.com/parse-publisher?doi={pub.id}'

    @classmethod
    def record_id_for_pub(cls, pub):
        return shortuuid.encode(
            uuid.UUID(bytes=hashlib.sha256(f'crossref_doi:{pub.id}'.encode('utf-8')).digest()[0:16])
        )

    @classmethod
    def find_record(cls, pub):
        if record_id := cls.record_id_for_pub(pub):
            return CrossrefDoiRecord.query.get(record_id)

    @classmethod
    def _make_record_impl(cls, pub):
        record = cls.find_record(pub) or CrossrefDoiRecord(id=cls.record_id_for_pub(pub))

        record.title = pub.title
        authors = [normalize_author(author) for author in pub.authors] if pub.authors else []
        record.set_jsonb('authors', authors)

        record.doi = pub.id
        record.abstract = pub.abstract_from_crossref or None
        record.published_date = pub.issued or pub.created
        record.genre = pub.genre

        pub_crossref_api_raw = pub.crossref_api_raw_new or {}

        citations = [
            normalize_citation(ref)
            for ref in pub_crossref_api_raw.get('reference', [])
        ]
        record.set_jsonb('citations', citations)

        record.record_webpage_url = pub.url
        record.journal_issn_l = pub.issn_l
        record.journal_id = pub.journalsdb_journal_id
        record.publisher = pub.publisher
        record.is_retracted = pub.is_retracted

        record.issue = pub.issue
        record.volume = pub.volume
        record.first_page = pub.first_page
        record.last_page = pub.last_page

        crossref_institution = pub_crossref_api_raw.get('institution', [])

        if not isinstance(crossref_institution, list):
            crossref_institution = [crossref_institution]

        for ci in crossref_institution:
            if 'place' in ci and not isinstance(ci['place'], list):
                ci['place'] = [ci['place']]
            if 'department' in ci and not isinstance(ci['department'], list):
                ci['department'] = [ci['department']]
            if 'acronym' in ci and not isinstance(ci['acronym'], list):
                ci['acronym'] = [ci['acronym']]

        record.set_jsonb('institution_host', crossref_institution)

        record.record_webpage_archive_url = pub.landing_page_archive_url() if pub.doi_landing_page_is_archived else None

        record.record_structured_url = f'https://api.crossref.org/v1/works/{quote(pub.id)}'
        record.record_structured_archive_url = f'https://api.unpaywall.org/crossref_api_cache/{quote(pub.id)}'

        doi_oa_location = None
        for oa_location in pub.all_oa_locations:
            if oa_location.metadata_url == pub.url and not oa_location.endpoint_id:
                doi_oa_location = oa_location

        if doi_oa_location:
            record.work_pdf_url = doi_oa_location.pdf_url
            record.is_work_pdf_url_free_to_read = True if doi_oa_location.pdf_url else None
        else:
            record.work_pdf_url = None
            record.is_work_pdf_url_free_to_read = None

        cls._make_source_specific_record_changes(record, pub)

        if db.session.is_modified(record):
            record.updated = datetime.datetime.utcnow().isoformat()

        record.flag_modified_jsonb()

        return record

    @classmethod
    def _merge_parseland_parse(cls, record, pub):
        if (pl_parse := parseland_parse(cls._parseland_api_url(pub))) is not None:
            if cls._should_merge_affiliations(record, pub):
                pl_authors = pl_parse.get('authors', [])
                normalized_pl_authors = [normalize(author.get('raw', '')) for author in pl_authors]

                for crossref_author_idx, crossref_author in enumerate(record.authors):
                    family = normalize(crossref_author.get('family') or '')
                    given = normalize(crossref_author.get('given') or '')

                    best_match_score = (0, -math.inf)
                    best_match_idx = -1
                    for pl_author_idx, pl_author_name in enumerate(normalized_pl_authors):
                        name_match_score = 0

                        if family and family in pl_author_name:
                            name_match_score += 2

                        if given and given in pl_author_name:
                            name_match_score += 1

                        index_difference = abs(crossref_author_idx - pl_author_idx)

                        if name_match_score:
                            match_score = (name_match_score, -index_difference)

                            if match_score > best_match_score:
                                best_match_score = match_score
                                best_match_idx = pl_author_idx

                    if best_match_idx > -1:
                        crossref_author['affiliation'] = pl_authors[best_match_idx].get('affiliation', [])

                record.set_authors(record.authors)

            record.abstract = pl_parse.get('abstract')

    @classmethod
    def _make_source_specific_record_changes(cls, record, pub):
        cls._merge_parseland_parse(record, pub)

    @classmethod
    def _should_merge_affiliations(cls, record, pub):
        for f in parseland_affiliation_doi_filters():
            if (
                (
                    f['filter_type'] == 'publisher'
                    and pub.publisher
                    and re.search(r'\b' + f['filter_value'] + r'\b', pub.publisher)
                )
                or
                (
                    f['filter_type'] == 'doi'
                    and pub.doi
                    and re.search(f['filter_value'], pub.doi)
                )
            ):
                if f['replace_crossref'] or not any(author.get('affiliation') for author in record.authors):
                    return True

        return False


_parseland_affiliation_doi_filters = None


def parseland_affiliation_doi_filters():
    global _parseland_affiliation_doi_filters
    if _parseland_affiliation_doi_filters is None:
        _parseland_affiliation_doi_filters = [dict(f) for f in db.engine.execute(text(
            'select filter_type, filter_value, replace_crossref from recordthresher.parseland_affiliation_doi_filters'
        ))]

    return _parseland_affiliation_doi_filters
