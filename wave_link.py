"""
wave_link.py - download the invoice PDF behind a Wave invoice link.

    pdf_bytes = wave_link.fetch_invoice_pdf("https://link.waveapps.com/pdcwcx-9ukyka")

Takes the short link from Wave's invoice emails (link.waveapps.com/...) or the public
invoice page it opens (next.waveapps.com/a/invoices/<business>/public/<invoice>/<token>),
and asks Wave for the same PDF the page's "Download PDF" button gives.

This uses the request Wave's own public invoice page makes, not a published API, so a
change on Wave's side can break it; dropping the PDF still works then. Only Wave
addresses are fetched. Standard library only.
"""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

GRAPHQL = "https://gql.waveapps.com/graphql/internal"
SHORT_HOSTS = ("link.waveapps.com",)
PAGE_HOSTS = ("next.waveapps.com", "waveapps.com", "www.waveapps.com")
PAGE_PATH = re.compile(r"/a/invoices/([0-9a-fA-F-]{36})/public/(\d+)/([0-9a-fA-F]+)/?$")
PDF_HOST = re.compile(r"(^|\.)amazonaws\.com$|(^|\.)waveapps\.com$")
MUTATION = ("mutation PublicInvoiceGeneratePdf($input: PublicInvoiceGeneratePdfInput!) {"
            " publicInvoiceGeneratePdf(input: $input) { pdfUrl didSucceed inputErrors { message } } }")
TIMEOUT = 30
MAX_PDF = 50 * 1024 * 1024


def is_wave_link(text):
    """True for a Wave invoice address, with or without https://."""
    url = text.strip()
    if "://" not in url:
        url = "https://" + url
    host = _host(url)
    return host in SHORT_HOSTS or host in PAGE_HOSTS


def _host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def page_url(link):
    """The public invoice page for a Wave link: short links are resolved one redirect at a time,
    each of which must stay on Wave."""
    url = link.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(5):
        if _host(url) in PAGE_HOSTS:
            return url
        if _host(url) not in SHORT_HOSTS:
            raise ValueError("not a Wave invoice link: %s" % link)
        try:
            opener.open(urllib.request.Request(url, method="HEAD"), timeout=TIMEOUT)
            raise ValueError("Wave link did not lead to an invoice: %s" % link)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
                raise ValueError("Wave link failed (HTTP %d): %s" % (e.code, link))
            url = urllib.parse.urljoin(url, e.headers["Location"])
    raise ValueError("too many redirects: %s" % link)


def ids(url):
    """(business id, invoice id, access token) from a public invoice page address."""
    m = PAGE_PATH.search(urllib.parse.urlsplit(url).path)
    if not m:
        raise ValueError("not a Wave public invoice page: %s" % url)
    return m.groups()


def fetch_invoice_pdf(link):
    """The invoice PDF (bytes) for a Wave invoice link; ValueError with a readable message otherwise."""
    business, invoice, token = ids(page_url(link))
    b64 = lambda s: base64.b64encode(s.encode()).decode()
    body = {"operationName": "PublicInvoiceGeneratePdf", "query": MUTATION, "variables": {"input": {
        "id": b64("Business:%s;PublicInvoice:%s" % (business, token)),
        "invoiceId": b64("Business:%s;Invoice:%s" % (business, invoice))}}}
    req = urllib.request.Request(GRAPHQL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        reply = json.load(urllib.request.urlopen(req, timeout=TIMEOUT))
    except (urllib.error.URLError, ValueError) as e:
        raise ValueError("could not reach Wave: %s" % getattr(e, "reason", e))
    result = (reply.get("data") or {}).get("publicInvoiceGeneratePdf") or {}
    pdf_url = result.get("pdfUrl")
    if not pdf_url:
        errs = [e.get("message", "") for e in (reply.get("errors") or []) + (result.get("inputErrors") or [])]
        raise ValueError("Wave did not return the invoice PDF%s" % (": " + "; ".join(errs) if errs else ""))
    if urllib.parse.urlsplit(pdf_url).scheme != "https" or not PDF_HOST.search(_host(pdf_url)):
        raise ValueError("Wave returned an unexpected PDF address")
    try:
        with urllib.request.urlopen(pdf_url, timeout=TIMEOUT) as r:
            data = r.read(MAX_PDF + 1)
    except urllib.error.URLError as e:
        raise ValueError("could not download the invoice PDF: %s" % getattr(e, "reason", e))
    if len(data) > MAX_PDF or not data.startswith(b"%PDF-"):
        raise ValueError("Wave's download was not a PDF")
    return data
