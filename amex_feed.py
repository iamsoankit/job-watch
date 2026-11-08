"""Public American Express Oracle Candidate Experience job feed."""
from html.parser import HTMLParser
import re
from urllib.parse import urlencode

from job_sources import SourceError, SourceTruncatedError, _date, _request, _segment


API_URL = (
    "https://egug.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/"
    "recruitingCEJobRequisitions"
)
DETAILS_URL = (
    "https://egug.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/"
    "recruitingCEJobRequisitionDetails"
)
CAREER_URL = "https://careers.americanexpress.com/en/sites/CX_1/job/{}"
SITE_NUMBER = "CX_1"
PAGE_SIZE = 25
DEFAULT_MAX_JOBS = 1000
_NCR_PATTERN = re.compile(r"(?<![a-z])(?:delhi|ncr|gurgaon|gurugram|noida|faridabad|ghaziabad)(?![a-z])")


class _TextExtractor(HTMLParser):
    _BLOCK_TAGS = {"br", "div", "h1", "h2", "h3", "li", "p", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def _plain_text(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _api_get(url, params):
    return _request(f"{url}?{urlencode(params)}")


def _page(offset):
    finder = (
        f'findReqs;siteNumber={SITE_NUMBER},location="India",'
        f"limit={PAGE_SIZE},offset={offset},sortBy=POSTING_DATES_DESC"
    )
    data = _api_get(API_URL, {
        "onlyData": "true",
        "expand": "requisitionList",
        "finder": finder,
    })
    if not isinstance(data, dict):
        raise SourceError("American Express API returned a malformed response")
    searches = data.get("items")
    if not isinstance(searches, list) or not searches:
        raise SourceError("American Express API response has no search result")
    search = searches[0]
    if not isinstance(search, dict):
        raise SourceError("American Express API returned a malformed search result")
    postings = search.get("requisitionList")
    if not isinstance(postings, list):
        raise SourceError("American Express API response has no requisitionList")
    try:
        total = int(search["TotalJobsCount"])
    except (TypeError, ValueError) as exc:
        raise SourceError("American Express API returned an invalid TotalJobsCount") from exc
    if total < 0:
        raise SourceError("American Express API returned a negative TotalJobsCount")
    if not postings and offset < total:
        raise SourceError(f"American Express pagination stopped at {offset} postings before total {total}")
    return postings, total


def _details(job_id):
    finder = f"ById;Id={job_id},siteNumber={SITE_NUMBER}"
    data = _api_get(DETAILS_URL, {
        "onlyData": "true",
        "expand": "all",
        "finder": finder,
    })
    if not isinstance(data, dict):
        raise SourceError(f"American Express detail API returned a malformed response for {job_id}")
    items = data.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise SourceError(f"American Express detail API returned no job for {job_id}")
    return items[0]


def _description(detail, summary):
    parts = []
    for field in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"):
        text = _plain_text(detail.get(field))
        if text and text not in parts:
            parts.append(text)
    if not parts:
        fallback = _plain_text(summary.get("ShortDescriptionStr"))
        if fallback:
            parts.append(fallback)
    return "\n\n".join(parts)


def fetch_amex(src):
    """Yield normalized public Amex India postings, including genuine dates.

    The public Oracle API's ``PostedDate``/``ExternalPostedStartDate`` values
    are retained as dates; absent source dates remain empty rather than inferred.
    """
    if src.get("site", SITE_NUMBER) != SITE_NUMBER:
        raise ValueError(f"American Express site must be {SITE_NUMBER}")
    max_jobs = src.get("max_jobs", DEFAULT_MAX_JOBS)
    if isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs < 1:
        raise ValueError("American Express max_jobs must be a positive integer")

    offset = 0
    seen = set()
    while True:
        postings, total = _page(offset)
        if not postings:
            if offset < total:
                raise SourceError(f"American Express pagination stopped at {offset} postings before total {total}")
            return
        if offset + len(postings) > total:
            raise SourceError("American Express API returned more postings than TotalJobsCount")
        new_ids = set()
        for summary in postings:
            if not isinstance(summary, dict):
                raise SourceError("American Express API returned a malformed posting")
            raw_id = summary.get("Id")
            job_id = _segment(str(raw_id) if raw_id is not None else "", "American Express job ID")
            if job_id in seen:
                continue
            seen.add(job_id)
            new_ids.add(job_id)
            location = summary.get("PrimaryLocation") or ""
            detail = _details(job_id) if _NCR_PATTERN.search(location.casefold()) else {}
            yield {
                "title": summary.get("Title") or detail.get("Title") or "",
                "location": location,
                "url": CAREER_URL.format(job_id) + "/",
                "description": _description(detail, summary),
                "posted_at": _date(
                    detail.get("ExternalPostedStartDate") or summary.get("PostedDate")
                ),
                "source_id": job_id,
                "deadline": _date(detail.get("ExternalPostedEndDate")),
            }
            if len(seen) >= max_jobs and len(seen) < total:
                raise SourceTruncatedError(
                    f"American Express results truncated at max_jobs={max_jobs}; API reports {total} India postings"
                )
        if not new_ids:
            raise SourceError("American Express pagination repeated a page without new job IDs")
        offset += len(postings)
        if offset >= total:
            return
