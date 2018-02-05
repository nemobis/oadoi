from collections import defaultdict

from time import time
from util import elapsed

# things to set here:
#       license, free_metadata_url, free_pdf_url
# free_fulltext_url is set automatically from free_metadata_url and free_pdf_url

def get_overrides_dict():
    override_dict = defaultdict(dict)

    # cindy wu example
    override_dict["10.1038/nature21360"] = {
        "pdf_url": "https://arxiv.org/pdf/1703.01424.pdf",
        "version": "submittedVersion"
    }

    # example from twitter
    override_dict["10.1021/acs.jproteome.5b00852"] = {
        "pdf_url": "http://pubs.acs.org/doi/pdfplus/10.1021/acs.jproteome.5b00852",
        "host_type_set": "publisher",
        "version": "publishedVersion"
    }

    # have the unpaywall example go straight to the PDF, not the metadata page
    override_dict["10.1098/rspa.1998.0160"] = {
        "pdf_url": "https://arxiv.org/pdf/quant-ph/9706064.pdf",
        "version": "submittedVersion"
    }

    # missed, not in BASE, from Maha Bali in email
    override_dict["10.1080/13562517.2014.867620"] = {
        "pdf_url": "http://dar.aucegypt.edu/bitstream/handle/10526/4363/Final%20Maha%20Bali%20TiHE-PoD-Empowering_Sept30-13.pdf",
        "version": "submittedVersion"
    }

    # otherwise links to figshare match that only has data, not the article
    override_dict["110.1126/science.aaf3777"] = {}

    #otherwise links to a metadata page that doesn't have the PDF because have to request a copy: https://openresearch-repository.anu.edu.au/handle/1885/103608
    override_dict["10.1126/science.aad2622"] = {
        "pdf_url": "https://lra.le.ac.uk/bitstream/2381/38048/6/Waters%20et%20al%20draft_post%20review_v2_clean%20copy.pdf",
        "version": "submittedVersion"
    }

    # otherwise led to http://www.researchonline.mq.edu.au/vital/access/services/Download/mq:39727/DS01 and authorization error
    override_dict["10.1126/science.aad2622"] = {}

    # otherwise led to https://dea.lib.unideb.hu/dea/bitstream/handle/2437/200488/file_up_KMBT36220140226131332.pdf;jsessionid=FDA9F1A60ACA567330A8B945208E3CA4?sequence=1
    override_dict["10.1007/978-3-211-77280-5"] = {}

    # otherwise led to publisher page but isn't open
    override_dict["10.1016/j.renene.2015.04.017"] = {}

    # override old-style webpage
    override_dict["10.1210/jc.2016-2141"] = {
        "pdf_url": "https://academic.oup.com/jcem/article-lookup/doi/10.1210/jc.2016-2141",
        "host_type_set": "publisher",
        "version": "publishedVersion",
    }

    # not indexing this location yet, from @rickypo
    override_dict["10.1207/s15327957pspr0203_4"] = {
        "pdf_url": "http://www2.psych.ubc.ca/~schaller/528Readings/Kerr1998.pdf",
        "version": "submittedVersion"
    }

    # mentioned in world bank as good unpaywall example
    override_dict["10.3386/w23298"] = {
        "pdf_url": "https://economics.mit.edu/files/12774",
        "version": "submittedVersion"
    }

    # from email
    override_dict["10.1038/nature21377"] = {
        "pdf_url": "http://eprints.whiterose.ac.uk/112179/1/ppnature21377_Dodd_for%20Symplectic.pdf",
        "version": "submittedVersion"
    }

    # from twitter
    override_dict["10.1103/physreva.97.013421"] = {
        "pdf_url": "https://arxiv.org/pdf/1711.10074.pdf",
        "version": "submittedVersion"
    }

    # from email
    override_dict["10.1561/1500000012"] = {
        "pdf_url": "http://citeseerx.ist.psu.edu/viewdoc/download?doi=10.1.1.174.8814&rep=rep1&type=pdf",
        "version": "publishedVersion"
    }

    return override_dict
