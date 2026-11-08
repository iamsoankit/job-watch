"""Fetch public job postings from supported company career-board APIs.

Each fetcher yields a normalized posting with ``title``, ``location``,
``url``, ``description`` and ``posted_at`` fields. No career-site HTML is
scraped; URLs are limited to the corresponding providers' public domains.
"""
from datetime import datetime, timezone
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


TIMEOUT = 25
RETRIES = 2
RETRY_DELAY = 0.25
_USER_AGENT = "Mozilla/5.0 (compatible; personal-job-watch/1.0)"
_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class SourceError(URLError):
    """An actionable source/API error (also compatible with URLError)."""


class SourceTruncatedError(SourceError):
    """Raised when an explicitly configured maximum limits a result set."""


def _request(url, data=None, *, timeout=TIMEOUT, retries=RETRIES):
    """Request JSON, retrying transient HTTP failures a bounded number of times."""
    body = None
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    for attempt in range(retries + 1):
        request = Request(url, data=body, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                try:
                    return json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SourceError(f"Invalid JSON response from {url}: {exc}") from exc
        except HTTPError as exc:
            if exc.code not in (429,) and not 500 <= exc.code <= 599:
                raise SourceError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
            if attempt == retries:
                raise SourceError(
                    f"HTTP {exc.code} from {url} after {attempt + 1} attempts: {exc.reason}"
                ) from exc
            delay = RETRY_DELAY
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                try:
                    delay = min(2.0, max(0.0, float(retry_after)))
                except ValueError:
                    pass
            time.sleep(delay)
        except (TimeoutError, URLError) as exc:
            if attempt == retries:
                raise SourceError(
                    f"Request to {url} failed after {attempt + 1} attempts: {exc}"
                ) from exc
            time.sleep(RETRY_DELAY)


def _segment(value, label):
    if not isinstance(value, str) or not _SEGMENT.fullmatch(value):
        raise ValueError(f"{label} must be a non-empty URL path segment")
    return value


def _official_url(value, provider, label):
    parts = urlsplit(value or "")
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or not host or parts.username or parts.password
            or parts.port not in (None, 443) or parts.query or parts.fragment
            or not (host == provider or host.endswith("." + provider))):
        raise SourceError(f"{label} must be an HTTPS URL on {provider}; got {value!r}")
    return value.rstrip("/")


def _date(value):
    """Return a valid ISO date/time representation or an empty string."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = float(value)
        if number > 10_000_000_000:  # Common API representation: epoch milliseconds.
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    if not isinstance(value, str):
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat()
    except ValueError:
        return ""


def _posting_url(value, provider, source):
    parts = urlsplit(value or "")
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or not host or parts.username or parts.password
            or parts.port not in (None, 443) or not parts.path.startswith("/")
            or not (host == provider or host.endswith("." + provider))):
        raise SourceError(f"{source} API returned a non-{provider} posting URL: {value!r}")
    return value


def _greenhouse_posting_url(value, board, job_id):
    parts = urlsplit(value or "")
    host = (parts.hostname or "").lower()
    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        return _posting_url(value, "greenhouse.io", "Greenhouse")
    if parts.scheme == "https" and host and str(job_id).isdigit():
        return f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    return _posting_url(value, "greenhouse.io", "Greenhouse")


def _normalized(title, location, url, description="", posted_at="", source_id=None,
                updated_at=None):
    job = {
        "title": title or "",
        "location": location or "",
        "url": url,
        "description": description or "",
        "posted_at": _date(posted_at),
    }
    if source_id is not None:
        job["source_id"] = str(source_id)
    if updated_at is not None:
        job["updated_at"] = _date(updated_at)
    return job


def fetch_greenhouse(src):
    """Yield public Greenhouse board postings, including their content."""
    board = _segment(src.get("board"), "Greenhouse board")
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?{urlencode({'content': 'true'})}"
    data = _request(url)
    for item in data.get("jobs", []):
        posting_url = _greenhouse_posting_url(item.get("absolute_url"), board, item.get("id"))
        job = _normalized(
            item.get("title"), (item.get("location") or {}).get("name"), posting_url,
            item.get("content"), posted_at=item.get("first_published"), source_id=item.get("id"),
            updated_at=item.get("updated_at"),
        )
        job['deadline'] = _date(item.get('application_deadline'))
        for field in item.get('metadata') or []:
            if field.get('name', '').lower() == 'country' and isinstance(field.get('value'), str):
                job['country'] = field['value']
        yield job


def fetch_lever(src):
    """Yield public Lever postings with description and creation time."""
    company = _segment(src.get("company"), "Lever company")
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    data = _request(url)
    for item in data:
        categories = item.get("categories") or {}
        posting_url = _posting_url(item.get("hostedUrl"), "lever.co", "Lever")
        description_parts = [item.get("descriptionPlain") or item.get("description")]
        description_parts.extend(
            section.get("text") or section.get("content")
            for section in item.get("lists", [])
            if isinstance(section, dict)
        )
        description_parts.append(item.get("additionalPlain") or item.get("additional"))
        description = "\n\n".join(dict.fromkeys(
            part.strip() for part in description_parts if isinstance(part, str) and part.strip()
        ))
        yield _normalized(
            item.get("text"), categories.get("location"), posting_url,
            description,
            item.get("createdAt"), item.get("id"),
        )


def _workday_config(src):
    base = _official_url(src.get("base"), "myworkdayjobs.com", "Workday base")
    parts = urlsplit(base)
    if parts.path:
        raise SourceError("Workday base must be the HTTPS site origin without a path")
    tenant = _segment(src.get("tenant"), "Workday tenant")
    site = _segment(src.get("site"), "Workday site")
    return base, tenant, site


def _workday_url(base, site, external_path):
    if not isinstance(external_path, str) or not external_path.startswith("/"):
        raise SourceError(f"Workday API returned an invalid externalPath: {external_path!r}")
    parts = urlsplit(external_path)
    if parts.scheme or parts.netloc or ".." in parts.path.split("/"):
        raise SourceError(f"Workday API returned an invalid externalPath: {external_path!r}")
    return _posting_url(f"{base}/{site}{external_path}", "myworkdayjobs.com", "Workday")


def _workday_info(base, tenant, site, external_path):
    _workday_url(base, site, external_path)
    data = _request(f"{base}/wday/cxs/{tenant}/{site}{external_path}")
    info = data.get('jobPostingInfo')
    if not isinstance(info, dict):
        raise SourceError('Workday posting detail missing jobPostingInfo')
    return info


def _workday_description(base, tenant, site, external_path):
    return _workday_info(base, tenant, site, external_path).get('jobDescription', '')


def enrich_workday(src, job):
    base, tenant, site = _workday_config(src)
    prefix = f'{base}/{site}'
    if not job['url'].startswith(prefix + '/job/'):
        raise SourceError('Unexpected Workday job URL')
    info = _workday_info(base, tenant, site, job['url'][len(prefix):])
    location = info.get('location') or job['location']
    extra = info.get('additionalLocations') or []
    if extra:
        location = '; '.join([location] + [str(x.get('descriptor') or x.get('name') or '') if isinstance(x, dict) else str(x) for x in extra])
    country = info.get('country') or {}
    # startDate is Workday's posting start date, not the relative updated label.
    return {**job, 'description': info.get('jobDescription', ''), 'location': location,
            'posted_at': _date(info.get('startDate')), 'date_basis': 'published' if info.get('startDate') else 'unknown',
            'country': country.get('descriptor', '') if isinstance(country, dict) else str(country),
            'deadline': _date(info.get('endDate'))}


def _country_filter(facets, country):
    for facet in facets:
        parameter = facet.get('facetParameter', '')
        values = facet.get('values') or []
        if parameter.lower() in ('country', 'countries', 'locationcountry'):
            ids = [v['id'] for v in values if v.get('descriptor', '').casefold() == country.casefold() and v.get('id')]
            if ids:
                return {parameter: ids}
        found = _country_filter(values, country)
        if found:
            return found
    return {}


def fetch_workday(src):
    """Yield all Workday pages; an optional max_jobs cap is explicitly reported."""
    base, tenant, site = _workday_config(src)
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    page_size = 20
    max_jobs = src.get("max_jobs")
    if max_jobs is not None and (isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs < 1):
        raise ValueError("Workday max_jobs must be a positive integer")
    offset = 0
    yielded = 0
    total = None
    facets = {}
    if src.get('country'):
        discovery = _request(api, {'appliedFacets': {}, 'limit': 1, 'offset': 0, 'searchText': src.get('search', '')})
        facets = _country_filter(discovery.get('facets') or [], src['country'])

    while total is None or offset < total:
        request_size = page_size if max_jobs is None else min(page_size, max_jobs - yielded)
        data = _request(api, {
            "appliedFacets": facets,
            "limit": request_size,
            "offset": offset,
            "searchText": src.get("search", ""),
        })
        if "total" in data:
            try:
                total = int(data["total"])
            except (TypeError, ValueError) as exc:
                raise SourceError(f"Workday API returned invalid total: {data['total']!r}") from exc
        postings = data.get("jobPostings", [])
        if not postings:
            if total is not None and offset < total:
                raise SourceError(f"Workday pagination stopped at {offset} postings before total {total}")
            return

        for item in postings:
            external_path = item.get("externalPath")
            posting_url = _workday_url(base, site, external_path)
            job = _normalized(
                item.get("title"), item.get("locationsText"), posting_url,
                "", item.get("postedDate"), item.get("jobReqId") or item.get("id"),
            )
            yield enrich_workday(src, job) if src.get('fetch_details') else job
            yielded += 1
            if max_jobs is not None and yielded >= max_jobs and total is not None and total > yielded:
                raise SourceTruncatedError(
                    f"Workday results truncated at max_jobs={max_jobs}; API reports {total} postings"
                )

        offset += len(postings)
        if max_jobs is not None and yielded >= max_jobs:
            if total is None or total > yielded:
                raise SourceTruncatedError(
                    f"Workday results truncated at max_jobs={max_jobs}"
                    + (f"; API reports {total} postings" if total is not None else "")
                )
            return


def fetch_ashby(src):
    """Yield postings from Ashby's public posting API for a board."""
    board = _segment(src.get("board"), "Ashby board")
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    data = _request(url)
    for item in data.get("jobs", []):
        posting_url = _posting_url(item.get("jobUrl"), "ashbyhq.com", "Ashby")
        yield _normalized(
            item.get("title"), item.get("location"), posting_url,
            item.get("descriptionPlain") or item.get("descriptionHtml") or "",
            item.get("publishedAt"), item.get("id"),
        )


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "ashby": fetch_ashby,
}


def fetch_spglobal(src):
    from career_feeds import fetch_spglobal as fetch
    yield from fetch(src)


def fetch_zs(src):
    from career_feeds import fetch_zs as fetch
    yield from fetch(src)


def fetch_amex(src):
    from amex_feed import fetch_amex as fetch
    yield from fetch(src)


FETCHERS.update(spglobal=fetch_spglobal, zs=fetch_zs, amex=fetch_amex)
