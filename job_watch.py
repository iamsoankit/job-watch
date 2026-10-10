#!/usr/bin/env python3
"""Daily fresher-job watcher.

Checks the *public* job boards of the companies listed in config.json
(Greenhouse, Lever and Workday career sites), keeps only roles that match
your keywords, and emails you the ones you have not seen before.

It does NOT touch LinkedIn (LinkedIn's terms ban bots and scrapers).
Standard library only, so there is nothing to install.

Usage:
    python job_watch.py            # fetch, filter, email new matches
    python job_watch.py --dry-run  # fetch and print, send nothing, save nothing
"""
import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
SEEN_PATH = ROOT / "seen_jobs.json"
USER_AGENT = "Mozilla/5.0 (personal job-watch script)"
TIMEOUT = 25


# ---------------------------------------------------------------- fetching
def _request(url, data=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def fetch_greenhouse(src):
    data = _request(f"https://boards-api.greenhouse.io/v1/boards/{src['board']}/jobs")
    for j in data.get("jobs", []):
        yield {
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
        }


def fetch_lever(src):
    data = _request(f"https://api.lever.co/v0/postings/{src['company']}?mode=json")
    for j in data:
        yield {
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", "") or "",
            "url": j.get("hostedUrl", ""),
        }


def fetch_workday(src):
    """src needs: base (https://x.wd3.myworkdayjobs.com), tenant, site."""
    base, tenant, site = src["base"].rstrip("/"), src["tenant"], src["site"]
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    offset, total = 0, None
    while total is None or offset < min(total, src.get("max_jobs", 200)):
        data = _request(api, {
            "appliedFacets": {},
            "limit": 20,
            "offset": offset,
            "searchText": src.get("search", ""),
        })
        total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            yield {
                "title": j.get("title", ""),
                "location": j.get("locationsText", ""),
                "url": f"{base}/{site}{j.get('externalPath', '')}",
            }
        offset += 20


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workday": fetch_workday,
}


# --------------------------------------------------------------- filtering
def matches(job, cfg):
    title = job["title"].lower()
    loc = job["location"].lower()
    if cfg["include_title_keywords"] and not any(k in title for k in cfg["include_title_keywords"]):
        return False
    if any(k in title for k in cfg["exclude_title_keywords"]):
        return False
    # If the board gives a location, it must match one of yours; blank = keep.
    if loc and cfg["locations"] and not any(k in loc for k in cfg["locations"]):
        return False
    return True


# ------------------------------------------------------------------ state
def load_seen():
    if SEEN_PATH.exists():
        return set(json.loads(SEEN_PATH.read_text()))
    return set()


def save_seen(seen):
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=1))


# ------------------------------------------------------------------ email
def build_body(new_jobs, errors):
    lines = [f"{len(new_jobs)} new matching role(s):", ""]
    for j in new_jobs:
        lines.append(f"- {j['title']}  [{j['source']}]")
        if j["location"]:
            lines.append(f"  {j['location']}")
        lines.append(f"  {j['url']}")
        lines.append("")
    lines.append("Always check the posting's own experience, batch and CGPA criteria "
                 "before applying. Never pay anyone to apply.")
    if errors:
        lines += ["", "Sources that failed today:"] + [f"- {e}" for e in errors]
    return "\n".join(lines)


def send_email(subject, body):
    user = os.environ.get("EMAIL_USER")
    password = os.environ.get("EMAIL_APP_PASSWORD")
    to = os.environ.get("EMAIL_TO", user)
    if not (user and password and to):
        raise RuntimeError("Set EMAIL_USER, EMAIL_APP_PASSWORD (and optionally EMAIL_TO).")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, password)
        s.send_message(msg)


# ------------------------------------------------------------------- main
def run(cfg, dry_run=False):
    seen = load_seen()
    new_jobs, errors = [], []
    for src in cfg["sources"]:
        name = src.get("name", src["type"])
        fetcher = FETCHERS.get(src["type"])
        if not fetcher:
            errors.append(f"{name}: unknown type {src['type']}")
            continue
        try:
            for job in fetcher(src):
                job["source"] = name
                key = f"{name}|{job['url']}"
                if key in seen or not matches(job, cfg):
                    continue
                new_jobs.append(job)
                seen.add(key)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            errors.append(f"{name}: {exc}")
    return new_jobs, errors, seen


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = json.loads(CONFIG_PATH.read_text())
    new_jobs, errors, seen = run(cfg, dry_run)
    if new_jobs:
        body = build_body(new_jobs, errors)
        if dry_run:
            print(body)
        else:
            send_email(f"Job watch: {len(new_jobs)} new role(s)", body)
            save_seen(seen)
            print(f"Emailed {len(new_jobs)} new role(s).")
    else:
        print("No new matching roles today.")
        if not dry_run:
            save_seen(seen)
    for e in errors:
        print("WARNING:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
