#!/usr/bin/env python3
"""Build a sanitized, read-only static dashboard from the automation state."""

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import shutil
from urllib.parse import urlsplit

import job_watch


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / 'static'
JOB_FIELDS = (
    'id', 'title', 'source', 'location', 'url', 'description', 'posted_at',
    'updated_at', 'deadline', 'first_seen', 'last_seen', 'active', 'country', 'date_basis',
)
NCR_TERMS = ('delhi', 'gurugram', 'gurgaon', 'noida', 'faridabad')
ENTRY_LEVEL_TERMS = ('fresher', 'entry level', 'entry-level', 'graduate', 'junior', 'associate', 'trainee', 'intern')
SAFE_STATUSES = {'manual', 'ok', 'error', 'unknown', 'not_checked'}
SAFE_COVERAGE = {'manual', 'automated', 'unknown'}
SAFE_DATE_SUPPORT = {'published', 'mixed', 'unknown'}
SAFE_SOURCE_TYPES = {'manual', 'greenhouse', 'lever', 'ashby', 'workday', 'amex', 'spglobal', 'zs'}


def public_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def public_job_fields(job, as_of):
    item = {key: job[key] for key in JOB_FIELDS if key in job}
    title = str(item.get('title') or '').lower()
    location = str(item.get('location') or '').lower()
    local = any(term in location for term in NCR_TERMS)
    junior = any(term in title for term in ENTRY_LEVEL_TERMS)
    experience = job_watch.experience(f"{item.get('title', '')} {item.get('description', '')}")
    item['experience'] = experience

    posted = job_watch.parsed_date(item.get('posted_at'))
    age = (as_of - posted).total_seconds() / 86400 if posted else None
    recent = age is not None and 0 <= age <= 2
    reasons = ['Delhi NCR location' if local else 'Location matches search']
    if junior:
        reasons.append('Entry-level title signal')
    reasons.append(experience['label'])
    reasons.append('Posted within 48 hours' if recent else ('Posting date available' if posted else 'Posting date unavailable'))
    item['reasons'] = reasons
    item['score'] = 35 * local + 25 * junior + 20 * (experience['minimum'] is not None) + (20 if recent else 10 if posted else 0)
    item.update(status='new', notes='', notified_at=None)
    return item


def company_metadata(records):
    companies = []
    for record in records:
        if not isinstance(record, dict):
            continue
        company = {}
        for key in ('name', 'country'):
            value = record.get(key)
            if isinstance(value, str) and value:
                company[key] = value[:120]
        for key, allowed in (
            ('status', SAFE_STATUSES),
            ('coverage', SAFE_COVERAGE),
            ('date_support', SAFE_DATE_SUPPORT),
        ):
            if record.get(key) in allowed:
                company[key] = record[key]
        for key in ('count', 'matched'):
            if type(record.get(key)) is int and record[key] >= 0:
                company[key] = record[key]
        checked_at = record.get('checked_at')
        if job_watch.parsed_date(checked_at):
            company['checked_at'] = checked_at
        if public_url(record.get('career_url')):
            company['career_url'] = record['career_url']
        companies.append(company)
    return companies


def build_snapshot(state, published_at=None):
    as_of = job_watch.parsed_date(published_at) or datetime.now(timezone.utc)
    settings = state.get('settings') if isinstance(state.get('settings'), dict) else {}
    max_age_days = settings.get('max_age_days', 14)
    if type(max_age_days) is not int or max_age_days < 1:
        max_age_days = 14
    jobs = []
    for job in state.get('jobs', []):
        if not isinstance(job, dict) or job.get('active') is not True or not public_url(job.get('url')):
            continue
        posted = job_watch.parsed_date(job.get('posted_at'))
        age_seconds = (as_of - posted).total_seconds() if posted else None
        if age_seconds is None or age_seconds < 0 or age_seconds > max_age_days * 86400:
            continue
        jobs.append(public_job_fields(job, as_of))

    sources = []
    for source in state.get('sources', []):
        if not isinstance(source, dict):
            continue
        public_source = company_metadata([source])[0]
        if source.get('type') in SAFE_SOURCE_TYPES:
            public_source['type'] = source['type']
        sources.append(public_source)

    company_records = state.get('companies') or state.get('sources', [])
    companies = company_metadata(company_records)

    links = []
    for link in state.get('search_links', []):
        if isinstance(link, dict) and public_url(link.get('url')):
            links.append({key: link[key] for key in ('name', 'url', 'description') if key in link})

    return {
        'jobs': jobs,
        'sources': sources,
        'companies': companies,
        'last_scan': state.get('last_scan'),
        'scanning': False,
        'search_links': links,
        'read_only': True,
        'automation': {
            'mode': 'published',
            'published_at': published_at or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        },
    }


def publish(output_dir, state=None, static_dir=STATIC_DIR, published_at=None):
    output_dir = Path(output_dir)
    static_dir = Path(static_dir)
    if not (static_dir / 'index.html').is_file():
        raise FileNotFoundError(f'Missing dashboard assets in {static_dir}')
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(static_dir, output_dir / 'static', dirs_exist_ok=True)

    index_path = output_dir / 'index.html'
    shutil.copy2(static_dir / 'index.html', index_path)
    html = index_path.read_text(encoding='utf-8')
    html = html.replace('href="/static/', 'href="./static/')
    html = html.replace('src="/static/', 'src="./static/')
    html = re.sub(r'''<meta\b(?=[^>]*\bname=["']job-watch-mode["'])[^>]*>''', '', html, flags=re.IGNORECASE)
    marker = '<meta name="job-watch-mode" content="published">'
    if marker not in html:
        if '<title>' in html:
            html = html.replace('<title>', f'{marker}\n  <title>', 1)
        elif '<head>' in html:
            html = html.replace('<head>', f'<head>\n  {marker}', 1)
        else:
            html = marker + '\n' + html
    index_path.write_text(html, encoding='utf-8')

    snapshot = build_snapshot(job_watch.state() if state is None else state, published_at)
    (output_dir / 'state.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output_dir / '.nojekyll').touch()
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='Directory to write the Pages artifact')
    args = parser.parse_args()
    snapshot = publish(args.output)
    print(f"Published static snapshot with {len(snapshot['jobs'])} jobs to {args.output}")


if __name__ == '__main__':
    main()
