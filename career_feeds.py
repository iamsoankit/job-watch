"""Public, company-hosted India career feeds for S&P Global and ZS."""
import re
from urllib.parse import urlencode

from job_sources import SourceError, SourceTruncatedError, _date, _request


_SOURCES = {
    "spglobal": "https://careers.spglobal.com",
    "zs": "https://jobs.zs.com",
}
_PAGE_SIZE = 10
_MAX_JOBS = 1000
_NCR = ("gurugram", "gurgaon", "noida", "greater noida", "delhi",
        "faridabad", "ghaziabad", "manesar")
_NCR_PATTERN = re.compile(r"(?<![a-z])(?:" + "|".join(_NCR) + r")(?![a-z])")
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _india(job):
    code = str(job.get("country_code") or "").strip().casefold()
    country = str(job.get("country") or "").strip().casefold()
    location = " ".join(str(job.get(key) or "") for key in
                        ("full_location", "short_location", "location_name"))
    if code:
        return code == "in"
    if country:
        return country == "india"
    return "india" in location.casefold()


def _location_text(job):
    fields = ("location_name", "city", "state", "country", "full_location", "short_location")
    pieces = [job.get(key) for key in fields]
    extra = job.get("additional_locations") or []
    if isinstance(extra, list):
        pieces.extend(" ".join(str(loc.get(key) or "") for key in
                                ("location_name", "city", "state", "country"))
                      for loc in extra if isinstance(loc, dict))
    return " ".join(str(piece) for piece in pieces if piece)


def _posting(source, item):
    job = item.get("data", item)
    if not isinstance(job, dict):
        raise SourceError(f"{source} API returned a malformed job record")
    if not _india(job) or not _NCR_PATTERN.search(_location_text(job).casefold()):
        return None

    source_id = job.get("req_id") or job.get("slug")
    if not isinstance(source_id, (str, int)) or not _SOURCE_ID.fullmatch(str(source_id)):
        raise SourceError(f"{source} API returned a job without a valid source ID")
    posted_at = _date(job.get("posted_date"))
    result = {
        "title": job.get("title") or "",
        "location": job.get("full_location") or job.get("location_name") or ", ".join(
            str(job.get(key)) for key in ("city", "state", "country") if job.get(key)
        ),
        "url": f"{_SOURCES[source]}/jobs/{source_id}?lang=en-us",
        "description": job.get("description") or "",
        "posted_at": posted_at,
        "source_id": str(source_id),
    }
    if posted_at:
        result["date_basis"] = "posted_date"
    return result


def _fetch(source, src):
    max_jobs = src.get("max_jobs", _MAX_JOBS)
    if isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs < 1:
        raise ValueError("max_jobs must be a positive integer")

    root = _SOURCES[source]
    page = 1
    total = None
    scanned = 0
    while total is None or (page - 1) * _PAGE_SIZE < total:
        query = urlencode({"country": "India", "page": page})
        data = _request(f"{root}/api/jobs?{query}")
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise SourceError(f"{source} API returned an invalid jobs page")
        try:
            page_total = int(data["totalCount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"{source} API omitted a valid totalCount") from exc
        if page_total < 0:
            raise SourceError(f"{source} API returned an invalid totalCount: {page_total}")
        if total is None:
            total = page_total
        elif total != page_total:
            raise SourceError(f"{source} API totalCount changed during pagination")

        rows = data["jobs"]
        if not rows and (page - 1) * _PAGE_SIZE < total:
            raise SourceError(f"{source} pagination stopped before totalCount={total}")
        remaining = max(0, total - (page - 1) * _PAGE_SIZE)
        if len(rows) > remaining:
            raise SourceError(f"{source} API returned more jobs than totalCount")

        for item in rows:
            if scanned >= max_jobs:
                raise SourceTruncatedError(
                    f"{source} results truncated at max_jobs={max_jobs}; API reports {total} India postings"
                )
            if not isinstance(item, dict):
                raise SourceError(f"{source} API returned a malformed job record")
            scanned += 1
            posting = _posting(source, item)
            if posting:
                yield posting

        page += 1
    if total > max_jobs and scanned >= max_jobs:
        raise SourceTruncatedError(
            f"{source} results truncated at max_jobs={max_jobs}; API reports {total} India postings"
        )


def fetch_spglobal(src):
    """Yield public S&P Global India postings in Gurugram/Noida NCR."""
    yield from _fetch("spglobal", src)


def fetch_zs(src):
    """Yield public ZS Associates India postings in Gurugram/Noida NCR."""
    yield from _fetch("zs", src)
