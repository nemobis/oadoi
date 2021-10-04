import datetime
import hashlib
import uuid

import shortuuid

from app import db
from recordthresher.pubmed import PubmedAffiliation, PubmedAuthor, PubmedReference, PubmedWork
from recordthresher.record import Record

from lxml import etree
import dateutil.parser

class PubmedRecord(Record):
    __tablename__ = None

    pmid = db.Column(db.Text)

    __mapper_args__ = {
        "polymorphic_identity": "pubmed_record"
    }

    @staticmethod
    def from_pmid(pmid):
        if not pmid:
            return None

        if not (pubmed_work := PubmedWork.query.get(pmid)):
            return None

        record_id = shortuuid.encode(
            uuid.UUID(bytes=hashlib.sha256(f'pubmed_record:{pmid}'.encode('utf-8')).digest()[0:16])
        )

        record = PubmedRecord.query.get(record_id)

        if not record:
            record = PubmedRecord(id=record_id)

        record.pmid = pmid
        record.title = pubmed_work.article_title
        record.abstract = pubmed_work.abstract or None

        work_tree = etree.fromstring(pubmed_work.pubmed_article_xml)

        pub_date, pub_year, pub_month, pub_day = None, None, '1', '1'

        if (pub_date := work_tree.find('.//PubDate')) is not None:
            if (year_element := pub_date.find('.//Year')) is not None:
                pub_year = year_element.text
            if (month_element := pub_date.find('.//Month')) is not None:
                pub_month = month_element.text
            if (day_element := pub_date.find('.//Day')) is not None:
                pub_day = day_element.text

        if pub_year:
            pub_date = dateutil.parser.parse(f'{pub_year} {pub_month} {pub_day}')

        record.published_date = pub_date

        record_authors = []
        pubmed_authors = PubmedAuthor.query.filter(PubmedAuthor.pmid == pmid).all()
        for pubmed_author in pubmed_authors:
            record_author = {
                'sequence': 'first' if pubmed_author.author_order == 1 else 'additional',
                'family': pubmed_author.family,
                'orcid': pubmed_author.orcid,
                'given': pubmed_author.given or pubmed_author.initials,
                'affiliation': []
            }

            pubmed_affiliations = PubmedAffiliation.query.filter(
                PubmedAffiliation.pmid == pmid, PubmedAffiliation.author_order == pubmed_author.author_order
            ).order_by(
                PubmedAffiliation.affiliation_number
            ).all()

            for pubmed_affiliation in pubmed_affiliations:
                record_author['affiliation'].append({'name': pubmed_affiliation.affiliation})

            record_authors.append(PubmedRecord.normalize_author(record_author))

        record.set_jsonb('authors', record_authors)

        record_citations = []
        pubmed_references = PubmedReference.query.filter(PubmedReference.pmid == pmid).all()
        for pubmed_reference in pubmed_references:
            record_citation = {'unstructured': pubmed_reference.citation}
            record_citations.append(PubmedRecord.normalize_citation(record_citation))

        record.set_jsonb('citations', record_citations)

        record.doi = pubmed_work.doi
        record.record_webpage_url = f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'
        record.record_structured_url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml'
        record.record_structured_archive_url = f'https://api.unpaywall.org/pubmed_xml/{pmid}'

        if db.session.is_modified(record):
            record.updated = datetime.datetime.utcnow().isoformat()

        return record
