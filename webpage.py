#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import os
import requests
from requests.auth import HTTPProxyAuth
import re
from time import time
from lxml import html
from lxml import etree
from contextlib import closing

from app import logger
from oa_local import find_normalized_license
from open_location import OpenLocation
from http_cache import http_get
from util import is_doi_url
from util import elapsed
from util import get_tree
from util import get_link_target
from util import NoDoiException
from util import normalize
from util import is_same_publisher
from http_cache import is_response_too_large

DEBUG_SCRAPING = False



class Webpage(object):
    def __init__(self, **kwargs):
        self.url = None
        self.scraped_pdf_url = None
        self.scraped_open_metadata_url = None
        self.scraped_license = None
        self.error = ""
        self.related_pub_doi = None
        self.related_pub_publisher = None
        self.match_type = None
        self.base_id = None
        self.base_doc = None
        self.r = None
        for (k, v) in kwargs.iteritems():
            self.__setattr__(k, v)
        if not self.url:
            self.url = u"http://doi.org/{}".format(self.doi)

    # from https://stackoverflow.com/a/865272/596939
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    @property
    def doi(self):
        return self.related_pub_doi

    # sometimes overriden, for publisherwebpage
    @property
    def ask_slowly(self):
        return False

    @property
    def publisher(self):
        return self.related_pub_publisher

    def is_same_publisher(self, publisher):
        return is_same_publisher(self.related_pub_publisher, publisher)

    @property
    def fulltext_url(self):
        if self.scraped_pdf_url:
            return self.scraped_pdf_url
        if self.scraped_open_metadata_url:
            return self.scraped_open_metadata_url
        if self.is_open:
            return self.url
        return None

    @property
    def has_fulltext_url(self):
        if self.scraped_pdf_url or self.scraped_open_metadata_url:
            return True
        return False


    #overridden in some subclasses
    @property
    def is_open(self):
        if self.scraped_pdf_url or self.scraped_open_metadata_url:
            return True
        if self.scraped_license and self.scraped_license != "unknown":
            return True
        return False

    def mint_open_location(self):
        my_location = OpenLocation()
        my_location.pdf_url = self.scraped_pdf_url
        my_location.metadata_url = self.scraped_open_metadata_url
        my_location.license = self.scraped_license
        my_location.doi = self.related_pub_doi
        my_location.evidence = self.open_version_source_string
        my_location.match_type = self.match_type
        my_location.pmh_id = self.base_id
        my_location.base_doc = self.base_doc
        my_location.error = ""
        if self.is_open and not my_location.best_url:
            my_location.metadata_url = self.url
        return my_location

    def set_r_for_pdf(self):
        self.r = None
        try:
            self.r = http_get(url=self.scraped_pdf_url, stream=False, publisher=self.publisher, ask_slowly=self.ask_slowly)

        except requests.exceptions.ConnectionError as e:
            self.error += u"ERROR: connection error on {} in set_r_for_pdf: {}".format(self.scraped_pdf_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except requests.Timeout as e:
            self.error += u"ERROR: timeout error on {} in set_r_for_pdf: {}".format(self.scraped_pdf_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except requests.exceptions.InvalidSchema as e:
            self.error += u"ERROR: InvalidSchema error on {} in set_r_for_pdf: {}".format(self.scraped_pdf_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except requests.exceptions.RequestException as e:
            self.error += u"ERROR: RequestException in set_r_for_pdf"
            logger.info(self.error)
        except requests.exceptions.ChunkedEncodingError as e:
            self.error += u"ERROR: ChunkedEncodingError error on {} in set_r_for_pdf: {}".format(self.scraped_pdf_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except NoDoiException as e:
            self.error += u"ERROR: NoDoiException error on {} in set_r_for_pdf: {}".format(self.scraped_pdf_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except Exception as e:
            self.error += u"ERROR: Exception error in set_r_for_pdf"
            logger.exception(self.error)



    def is_a_pdf_page(self):
        if self.resp_is_pdf_from_header():
            if DEBUG_SCRAPING:
                logger.info(u"http header says this is a PDF {}".format(
                    self.r.request.url))
            return True

        # everything below here needs to look at the content
        # so bail here if the page is too big
        if is_response_too_large(self.r):
            if DEBUG_SCRAPING:
                logger.info(u"response is too big for more checks in gets_a_pdf")
            return False

        content = self.r.content_big()

        # PDFs start with this character
        if re.match(u"%PDF", content):
            return True

        if self.publisher:
            says_free_publisher_patterns = [
                    ("Wiley-Blackwell", u'<span class="freeAccess" title="You have free access to this content">'),
                    ("Wiley-Blackwell", u'<iframe id="pdfDocument"'),
                    ("JSTOR", ur'<li class="download-pdf-button">.*Download PDF.*</li>'),
                    ("Institute of Electrical and Electronics Engineers (IEEE)", ur'<frame src="http://ieeexplore.ieee.org/.*?pdf.*?</frameset>'),
                    ("IOP Publishing", ur'Full Refereed Journal Article')
                        ]
            for (publisher, pattern) in says_free_publisher_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
                if self.is_same_publisher(publisher) and matches:
                    return True
        return False


    # it matters this is just using the header, because we call it even if the content
    # is too large.  if we start looking in content, need to break the pieces apart.
    def resp_is_pdf_from_header(self):
        looks_good = False
        for k, v in self.r.headers.iteritems():
            if v:
                key = k.lower()
                val = v.lower()

                if key == "content-type" and "application/pdf" in val:
                    looks_good = True

                if key == 'content-disposition' and "pdf" in val:
                    looks_good = True
        return looks_good


    def gets_a_pdf(self, link, base_url):
    
        if is_purchase_link(link):
            return False
    
        absolute_url = get_link_target(link.href, base_url)
        if DEBUG_SCRAPING:
            logger.info(u"checking to see if {} is a pdf".format(absolute_url))
    
        start = time()
        try:
            self.r = http_get(absolute_url, stream=True, publisher=self.publisher, ask_slowly=self.ask_slowly)

            if self.r.status_code != 200:
                self.error += u"ERROR: status_code={} on {} in gets_a_pdf".format(self.r.status_code, absolute_url)
                return False

            if self.is_a_pdf_page():
                return True

        except requests.exceptions.ConnectionError as e:
            self.error += u"ERROR: connection error in gets_a_pdf for {}: {}".format(absolute_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except requests.Timeout as e:
            self.error += u"ERROR: timeout error in gets_a_pdf for {}: {}".format(absolute_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except requests.exceptions.InvalidSchema as e:
            self.error += u"ERROR: InvalidSchema error in gets_a_pdf for {}: {}".format(absolute_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except requests.exceptions.RequestException as e:
            self.error += u"ERROR: RequestException error in gets_a_pdf"
            logger.info(self.error)
        except requests.exceptions.ChunkedEncodingError as e:
            self.error += u"ERROR: ChunkedEncodingError error in gets_a_pdf for {}: {}".format(absolute_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except NoDoiException as e:
            self.error += u"ERROR: NoDoiException error in gets_a_pdf for {}: {}".format(absolute_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
        except Exception as e:
            self.error += u"ERROR: Exception error in gets_a_pdf"
            logger.exception(self.error)

        if DEBUG_SCRAPING:
            logger.info(u"we've decided this ain't a PDF. took {} seconds [{}]".format(
                elapsed(start), absolute_url))
        return False


    def find_pdf_link(self, page):

        if DEBUG_SCRAPING:
            logger.info(u"in find_pdf_link in {}".format(self.url))

        # before looking in links, look in meta for the pdf link
        # = open journal http://onlinelibrary.wiley.com/doi/10.1111/j.1461-0248.2011.01645.x/abstract
        # = open journal http://doi.org/10.1002/meet.2011.14504801327
        # = open repo http://hdl.handle.net/10088/17542
        # = open http://handle.unsw.edu.au/1959.4/unsworks_38708 cc-by

        # logger.info(page)

        link = get_pdf_in_meta(page)
        if link:
            return link

        link = get_pdf_from_javascript(page)
        if link:
            return link


        for link in get_useful_links(page):

            if DEBUG_SCRAPING:
                logger.info(u"trying {}, {} in find_pdf_link".format(link.href, link.anchor))

            # there are some links that are SURELY NOT the pdf for this article
            if has_bad_anchor_word(link.anchor):
                continue

            # there are some links that are SURELY NOT the pdf for this article
            if has_bad_href_word(link.href):
                continue


            # download link ANCHOR text is something like "manuscript.pdf" or like "PDF (1 MB)"
            # = open repo http://hdl.handle.net/1893/372
            # = open repo https://research-repository.st-andrews.ac.uk/handle/10023/7421
            # = open repo http://dro.dur.ac.uk/1241/
            if link.anchor and "pdf" in link.anchor.lower():
                return link

            # button says download
            # = open repo https://works.bepress.com/ethan_white/45/
            # = open repo http://ro.uow.edu.au/aiimpapers/269/
            # = open repo http://eprints.whiterose.ac.uk/77866/
            if "download" in link.anchor:
                if "citation" in link.anchor:
                    pass
                else:
                    return link

            if "download" in link.anchor:
                if "citation" in link.anchor:
                    pass
                else:
                    return link

            # want it to match for this one https://doi.org/10.2298/SGS0603181L
            # but not this one: 10.1097/00003643-201406001-00238
            if self.publisher and not self.is_same_publisher("Ovid Technologies (Wolters Kluwer Health)"):
                if link.anchor and "full text" in link.anchor.lower():
                    return link

            # download link is identified with an image
            for img in link.findall("img"):
                try:
                    if "pdf" in img.attrib["src"].lower():
                        return link
                except KeyError:
                    pass  # no src attr

            try:
                if "pdf" in link.attrib["title"].lower():
                    return link
            except KeyError:
                pass

        return None



    def __repr__(self):
        return u"<{} ({}) {}>".format(self.__class__.__name__, self.url, self.is_open)




class PublisherWebpage(Webpage):
    open_version_source_string = u"publisher landing page"


    @property
    def ask_slowly(self):
        return True

    @property
    def is_open(self):
        if self.scraped_pdf_url or self.scraped_open_metadata_url:
            return True
        # just having the license isn't good enough
        return False

    def scrape_for_fulltext_link(self):
        landing_url = self.url

        if DEBUG_SCRAPING:
            logger.info(u"checking to see if {} says it is open".format(landing_url))

        start = time()
        try:
            self.r = http_get(landing_url, stream=True, publisher=self.publisher, ask_slowly=self.ask_slowly)

            if self.r.status_code != 200:
                self.error += u"ERROR: status_code={} on {} in scrape_for_fulltext_link, skipping.".format(self.r.status_code, self.r.url)
                logger.info(u"DIDN'T GET THE PAGE: {}".format(self.error))
                logger.debug(self.r.request.headers)
                return

            # example 10.1007/978-3-642-01445-1
            if u"crossref.org/_deleted-doi/" in self.r.url:
                logger.info(u"this is a deleted doi")
                return

            # if our landing_url redirects to a pdf, we're done.
            # = open repo http://hdl.handle.net/2060/20140010374
            if self.is_a_pdf_page():
                if DEBUG_SCRAPING:
                    logger.info(u"this is a PDF. success! [{}]".format(landing_url))
                self.scraped_pdf_url = landing_url
                self.open_version_source_string = "open (via free pdf)"
                # don't bother looking for open access lingo because it is a PDF (or PDF wannabe)
                return

            else:
                if DEBUG_SCRAPING:
                    logger.info(u"landing page is not a PDF for {}.  continuing more checks".format(landing_url))

            # get the HTML tree
            page = self.r.content_small()

            # set the license if we can find one
            scraped_license = find_normalized_license(page)
            if scraped_license:
                self.scraped_license = scraped_license

            pdf_download_link = self.find_pdf_link(page)

            if pdf_download_link is not None:
                pdf_url = get_link_target(pdf_download_link.href, self.r.url)
                if self.gets_a_pdf(pdf_download_link, self.r.url):
                    self.scraped_pdf_url = pdf_url
                    self.scraped_open_metadata_url = self.url
                    self.open_version_source_string = "open (via free pdf)"

            # now look and see if it is not just free, but open!
            license_patterns = [u"(creativecommons.org\/licenses\/[a-z\-]+)",
                        u"distributed under the terms (.*) which permits",
                        u"This is an open access article under the terms (.*) which permits",
                        u"This is an open access article published under (.*) which permits",
                        u'<div class="openAccess-articleHeaderContainer(.*?)</div>'
                        ]
            for pattern in license_patterns:
                matches = re.findall(pattern, page, re.IGNORECASE)
                if matches:
                    self.scraped_license = find_normalized_license(matches[0])
                    self.scraped_open_metadata_url = self.url
                    self.open_version_source_string = "open (via page says license)"


            says_open_url_snippet_patterns = [("projecteuclid.org/", u'<strong>Full-text: Open access</strong>'),
                        ]
            for (url_snippet, pattern) in says_open_url_snippet_patterns:
                matches = re.findall(pattern, self.r.content_small(), re.IGNORECASE)
                if url_snippet in self.r.request.url.lower() and matches:
                    self.scraped_open_metadata_url = self.r.request.url
                    self.open_version_source_string = "open (via page says Open Access)"
                    self.scraped_license = "implied-oa"

            says_open_access_patterns = [("Informa UK Limited", u"/accessOA.png"),
                        ("Oxford University Press (OUP)", u"<i class='icon-availability_open'"),
                        ("Institute of Electrical and Electronics Engineers (IEEE)", ur'"isOpenAccess":true'),
                        ("Institute of Electrical and Electronics Engineers (IEEE)", ur'"openAccessFlag":"yes"'),
                        ("Informa UK Limited", u"/accessOA.png"),
                        ("Royal Society of Chemistry (RSC)", u"/open_access_blue.png"),
                        ("Cambridge University Press (CUP)", u'<span class="icon access open-access cursorDefault">'),
                        ]
            for (publisher, pattern) in says_open_access_patterns:
                matches = re.findall(pattern, page, re.IGNORECASE | re.DOTALL)
                if self.is_same_publisher(publisher) and matches:
                    self.scraped_license = "implied-oa"
                    self.scraped_open_metadata_url = landing_url
                    self.open_version_source_string = "open (via page says Open Access)"

            if self.is_open:
                if DEBUG_SCRAPING:
                    logger.info(u"we've decided this is open! took {} seconds [{}]".format(
                        elapsed(start), landing_url))
                return True
            else:
                if DEBUG_SCRAPING:
                    logger.info(u"we've decided this doesn't say open. took {} seconds [{}]".format(
                        elapsed(start), landing_url))
                return False
        except requests.exceptions.ConnectionError as e:
            self.error += u"ERROR: connection error in scrape_for_fulltext_link on {}: {}".format(landing_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return False
        except requests.Timeout as e:
            self.error += u"ERROR: timeout error in scrape_for_fulltext_link on {}: {}".format(landing_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return False
        except requests.exceptions.InvalidSchema as e:
            self.error += u"ERROR: InvalidSchema error in scrape_for_fulltext_link on {}: {}".format(landing_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return False
        except requests.exceptions.RequestException as e:
            self.error += u"ERROR: RequestException error in scrape_for_fulltext_link"
            logger.info(self.error)
            return False
        except requests.exceptions.ChunkedEncodingError as e:
            self.error += u"ERROR: ChunkedEncodingError error in scrape_for_fulltext_link on {}: {}".format(landing_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return False
        except NoDoiException as e:
            self.error += u"ERROR: NoDoiException error in scrape_for_fulltext_link on {}: {}".format(landing_url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return False
        except Exception as e:
            self.error += u"ERROR: Exception error in scrape_for_fulltext_link"
            logger.exception(self.error)
            return False


# abstract.  inherited by PmhRepoWebpage
class RepoWebpage(Webpage):
    @property
    def open_version_source_string(self):
        return self.base_open_version_source_string


    def scrape_for_fulltext_link(self):
        url = self.url

        dont_scrape_list = [
                u"ncbi.nlm.nih.gov",
                u"europepmc.org",
                u"/europepmc/",
                u"pubmed",
                u"elar.rsvpu.ru",  #these ones based on complaint in email
                u"elib.uraic.ru",
                u"elar.usfeu.ru",
                u"elar.urfu.ru",
                u"elar.uspu.ru"]
        for url_fragment in dont_scrape_list:
            if url_fragment in url:
                logger.info(u"not scraping {} because is on our do not scrape list.".format(url))
                return

        try:
            self.r = http_get(url, stream=True, publisher=self.publisher, ask_slowly=self.ask_slowly)

            if self.r.status_code != 200:
                self.error += u"ERROR: status_code={} on {} in scrape_for_fulltext_link".format(self.r.status_code, url)
                return

            # if our url redirects to a pdf, we're done.
            # = open repo http://hdl.handle.net/2060/20140010374
            if self.is_a_pdf_page():
                if DEBUG_SCRAPING:
                    logger.info(u"this is a PDF. success! [{}]".format(url))
                self.scraped_pdf_url = url
                return

            else:
                if DEBUG_SCRAPING:
                    logger.info(u"is not a PDF for {}.  continuing more checks".format(url))

            # now before reading the content, bail it too large
            if is_response_too_large(self.r):
                logger.info(u"landing page is too large, skipping")
                return

            # get the HTML tree
            page = self.r.content_small()

            # set the license if we can find one
            scraped_license = find_normalized_license(page)
            if scraped_license:
                self.scraped_license = scraped_license

            pdf_download_link = None
            # special exception for citeseer because we want the pdf link where
            # the copy is on the third party repo, not the cached link, if we can get it
            if url and u"citeseerx.ist.psu.edu/" in url:
                matches = re.findall(u'<h3>Download Links</h3>.*?href="(.*?)"', page, re.DOTALL)
                if matches:
                    pdf_download_link = DuckLink(unicode(matches[0], "utf-8"), "download")

            # osf doesn't have their download link in their pages
            # so look at the page contents to see if it is osf-hosted
            # if so, compute the url.  example:  http://osf.io/tyhqm
            elif page and u"osf-cookie" in unicode(page, "utf-8"):
                pdf_download_link = DuckLink(u"{}/download".format(url), "download")

            # otherwise look for it the normal way
            else:
                pdf_download_link = self.find_pdf_link(page)

            if pdf_download_link is not None:
                if DEBUG_SCRAPING:
                    logger.info(u"found a PDF download link: {} {} [{}]".format(
                        pdf_download_link.href, pdf_download_link.anchor, url))

                pdf_url = get_link_target(pdf_download_link.href, self.r.url)
                # if they are linking to a PDF, we need to follow the link to make sure it's legit
                if DEBUG_SCRAPING:
                    logger.info(u"checking to see the PDF link actually gets a PDF [{}]".format(url))
                if self.gets_a_pdf(pdf_download_link, self.r.url):
                    self.scraped_pdf_url = pdf_url
                    self.scraped_open_metadata_url = url
                    return

            # try this later because would rather get a pdfs
            # if they are linking to a .docx or similar, this is open.
            doc_link = find_doc_download_link(page)
            if doc_link is not None:
                if DEBUG_SCRAPING:
                    logger.info(u"found a .doc download link {} [{}]".format(
                        get_link_target(doc_link.href, self.r.url), url))
                self.scraped_open_metadata_url = url
                return

        except requests.exceptions.ConnectionError as e:
            self.error += u"ERROR: connection error on {} in scrape_for_fulltext_link: {}".format(url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return
        except requests.Timeout as e:
            self.error += u"ERROR: timeout error on {} in scrape_for_fulltext_link: {}".format(url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return
        except requests.exceptions.InvalidSchema as e:
            self.error += u"ERROR: InvalidSchema error on {} in scrape_for_fulltext_link: {}".format(url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return
        except requests.exceptions.RequestException as e:
            self.error += u"ERROR: RequestException in scrape_for_fulltext_link"
            logger.info(self.error)
            return
        except requests.exceptions.ChunkedEncodingError as e:
            self.error += u"ERROR: ChunkedEncodingError error on {} in scrape_for_fulltext_link: {}".format(url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return
        except NoDoiException as e:
            self.error += u"ERROR: NoDoiException error on {} in scrape_for_fulltext_link: {}".format(url, unicode(e.message).encode("utf-8"))
            logger.info(self.error)
            return
        except Exception as e:
            self.error += u"ERROR: Exception error on in scrape_for_fulltext_link"
            logger.exception(self.error)
            return

        if DEBUG_SCRAPING:
            logger.info(u"found no PDF download link.  end of the line. [{}]".format(url))

        return self



class PmhRepoWebpage(RepoWebpage):
    @property
    def base_open_version_source_string(self):
        if self.match_type:
            return u"oa repository (via OAI-PMH {} match)".format(self.match_type)
        return u"oa repository (via OAI-PMH)"





def find_doc_download_link(page):
    for link in get_useful_links(page):
        # there are some links that are FOR SURE not the download for this article
        if has_bad_href_word(link.href):
            continue

        if has_bad_anchor_word(link.anchor):
            continue

        # = open repo https://lirias.kuleuven.be/handle/123456789/372010
        if ".doc" in link.href or ".doc" in link.anchor:
            return link

    return None





class DuckLink(object):
    def __init__(self, href, anchor):
        self.href = href
        self.anchor = anchor


def get_useful_links(page):
    links = []

    tree = get_tree(page)
    if tree is None:
        return []

    # remove related content sections

    # references and related content sections
    bad_section_finders = [
        "//div[@class=\'relatedItem\']",  #http://www.tandfonline.com/doi/abs/10.4161/auto.19496
        "//div[@class=\'citedBySection\']",  #10.3171/jns.1966.25.4.0458
        "//div[@class=\'references\']"  #https://www.emeraldinsight.com/doi/full/10.1108/IJCCSM-04-2017-0089
    ]
    for section_finder in bad_section_finders:
        for bad_section in tree.xpath(section_finder):
            bad_section.clear()

    # now get the links
    link_elements = tree.xpath("//a")

    for link in link_elements:
        link_text = link.text_content().strip().lower()
        if link_text:
            link.anchor = link_text
            if "href" in link.attrib:
                link.href = link.attrib["href"]

        else:
            # also a useful link if it has a solo image in it, and that image includes "pdf" in its filename
            link_content_elements = [l for l in link]
            if len(link_content_elements)==1:
                link_insides = link_content_elements[0]
                if link_insides.tag=="img":
                    if "src" in link_insides.attrib and "pdf" in link_insides.attrib["src"]:
                        link.anchor = u"image: {}".format(link_insides.attrib["src"])
                        if "href" in link.attrib:
                            link.href = link.attrib["href"]

        if hasattr(link, "anchor") and hasattr(link, "href"):
            links.append(link)

    return links


def is_purchase_link(link):
    # = closed journal http://www.sciencedirect.com/science/article/pii/S0147651300920050
    if "purchase" in link.anchor:
        logger.info(u"found a purchase link! {} {}".format(link.anchor, link.href))
        return True
    return False


def has_bad_href_word(href):
    href_blacklist = [
        # = closed 10.1021/acs.jafc.6b02480
        # editorial and advisory board
        "/eab/",

        # = closed 10.1021/acs.jafc.6b02480
        "/suppl_file/",

        # https://lirias.kuleuven.be/handle/123456789/372010
        "supplementary+file",

        # http://www.jstor.org/action/showSubscriptions
        "showsubscriptions",

        # 10.7763/ijiet.2014.v4.396
        "/faq",

        # 10.1515/fabl.1988.29.1.21
        "{{",

        # 10.2174/1389450116666150126111055
        "cdt-flyer",

        # 10.1111/fpa.12048
        "figures",

        # https://www.crossref.org/iPage?doi=10.3138%2Fecf.22.1.1
        "price-lists",

        # prescribing information, see http://www.nejm.org/doi/ref/10.1056/NEJMoa1509388#t=references
        "janssenmd.com",

        # prescribing information, see http://www.nejm.org/doi/ref/10.1056/NEJMoa1509388#t=references
        "community-register",

        # prescribing information, see http://www.nejm.org/doi/ref/10.1056/NEJMoa1509388#t=references
        "quickreference",

        # 10.4158/ep.14.4.458
        "libraryrequestform",

        # http://www.nature.com/nutd/journal/v6/n7/full/nutd201620a.html
        "iporeport",

        #https://ora.ox.ac.uk/objects/uuid:06829078-f55c-4b8e-8a34-f60489041e2a
        "no_local_copy",

        ".zip",

        # https://zenodo.org/record/1238858
        ".gz",

        # https://zenodo.org/record/1238858
        ".tar.",

        # http://www.bioone.org/doi/full/10.1642/AUK-18-8.1
        "/doi/full/10.1642",
    ]
    for bad_word in href_blacklist:
        if bad_word in href.lower():
            return True
    return False


def has_bad_anchor_word(anchor_text):
    anchor_blacklist = [
        # = closed repo https://works.bepress.com/ethan_white/27/
        "user",
        "guide",

        # = closed 10.1038/ncb3399
        "checklist",

        # wrong link
        "abstracts",

        # https://hal.archives-ouvertes.fr/hal-00085700
        "metadata from the pdf file",
        u"récupérer les métadonnées à partir d'un fichier pdf",

        # = closed http://europepmc.org/abstract/med/18998885
        "bulk downloads",

        # http://www.utpjournals.press/doi/pdf/10.3138/utq.35.1.47
        "license agreement",

        # = closed 10.1021/acs.jafc.6b02480
        "masthead",

        # closed http://eprints.soton.ac.uk/342694/
        "download statistics",

        # no examples for these yet
        "supplement",
        "figure",
        "faq"
    ]
    for bad_word in anchor_blacklist:
        if bad_word in anchor_text.lower():
            return True

    return False


def get_pdf_in_meta(page):
    if "citation_pdf_url" in page:
        if DEBUG_SCRAPING:
            logger.info(u"citation_pdf_url in page")

        tree = get_tree(page)
        if tree is not None:
            metas = tree.xpath("//meta")
            for meta in metas:
                if "name" in meta.attrib:
                    if meta.attrib["name"]=="citation_pdf_url":
                        if "content" in meta.attrib:
                            link = DuckLink(href=meta.attrib["content"], anchor="<meta citation_pdf_url>")
                            return link
        else:
            # backup if tree fails
            regex = r'<meta name="citation_pdf_url" content="(.*?)">'
            matches = re.findall(regex, page)
            if matches:
                link = DuckLink(href=matches[0], anchor="<meta citation_pdf_url>")
                return link
    return None

def get_pdf_from_javascript(page):
    matches = re.findall('"pdfUrl":"(.*?)"', page)
    if matches:
        link = DuckLink(href=matches[0], anchor="pdfUrl")
        return link
    return None





