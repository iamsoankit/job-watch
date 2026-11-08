#!/usr/bin/env python3
"""Personal job-search dashboard and scheduled public-board watcher (stdlib only)."""
import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import sqlite3
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from job_sources import FETCHERS

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'config.json'
DB_PATH = Path(os.environ.get('JOB_WATCH_DB', ROOT / 'data/jobs.sqlite3'))
SCAN_LOCK = threading.Lock()
SCHEDULER_STOP = threading.Event()
SCHEDULER_NEXT = None
SCHEDULER_RUNNING = False
STATUSES = {'new', 'saved', 'applied', 'interview', 'rejected', 'archived'}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    return re.sub(r'\s+', ' ', re.sub('<[^>]+>', ' ', html.unescape(str(value or '')))).strip()


def contains(text, word):
    return bool(re.search(r'(?<!\w)' + re.escape(word.lower()) + r'(?!\w)', text.lower()))


def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError('Settings must be an object.')
    for key in ('include_title_keywords', 'exclude_title_keywords', 'locations'):
        if not isinstance(cfg.get(key), list) or any(not isinstance(x, str) or not x.strip() for x in cfg[key]):
            raise ValueError(f'{key} must be a list of nonempty strings.')
    for key, default, upper in [('max_experience', 2, 20), ('max_age_days', 14, 3650), ('auto_scan_minutes', 30, 1440)]:
        value = cfg.get(key, default)
        if type(value) is not int or not 0 <= value <= upper:
            raise ValueError(f'{key} must be an integer between 0 and {upper}.')
    for key in ('include_unknown_experience', 'include_unknown_location', 'include_unknown_date'):
        if key in cfg and type(cfg[key]) is not bool:
            raise ValueError(f'{key} must be true or false.')
    if cfg.get('auto_scan_minutes', 30) < 5:
        raise ValueError('auto_scan_minutes must be at least 5.')
    if cfg.get('country', 'India') != 'India':
        raise ValueError('This watchlist currently targets India.')
    profile = cfg.get('profile', {})
    if not isinstance(profile, dict) or set(profile) - {'skills'}:
        raise ValueError('profile must be an object with a skills list.')
    if not isinstance(profile.get('skills', []), list) or any(not isinstance(x, str) or not x.strip() or len(x) > 80 for x in profile.get('skills', [])):
        raise ValueError('Profile skills must be a list of nonempty strings up to 80 characters.')
    if not isinstance(cfg.get('sources'), list) or not 1 <= len(cfg['sources']) <= 100:
        raise ValueError('Add between 1 and 100 company sources.')
    names = set()
    for src in cfg['sources']:
        if not isinstance(src, dict):
            raise ValueError('Each source must be an object.')
        kind, name = src.get('type'), src.get('name')
        if kind not in {*FETCHERS, 'manual'} or not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('Each source needs a unique name and a supported type: ' + ', '.join(FETCHERS))
        names.add(name)
        fields = {'greenhouse': ['board'], 'lever': ['company'], 'ashby': ['board'], 'workday': ['tenant', 'site']}.get(kind, [])
        if kind == 'manual' and not safe_url(src.get('career_url', '')):
            raise ValueError(f'{name}: manual sources need an official career_url.')
        if 'career_url' in src and not safe_url(src['career_url']):
            raise ValueError(f'{name}: invalid career URL.')
        if src.get('country', 'India') != 'India':
            raise ValueError(f'{name}: only India sources are supported.')
        for field in fields:
            if not re.fullmatch(r'[\w.-]+', str(src.get(field, ''))):
                raise ValueError(f'{name}: invalid or missing {field}.')
        if kind == 'workday':
            u = urlsplit(src.get('base', ''))
            if u.scheme != 'https' or not re.fullmatch(r'[a-zA-Z0-9.-]+\.myworkdayjobs\.com', u.netloc) or u.path not in ('', '/'):
                raise ValueError(f'{name}: base must be an HTTPS myworkdayjobs.com host.')
        if 'max_jobs' in src and (type(src['max_jobs']) is not int or src['max_jobs'] < 1):
            raise ValueError(f'{name}: max_jobs must be a positive integer.')
    return cfg


def load_config():
    cfg = json.loads(CONFIG_PATH.read_text())
    profile_json = os.environ.get('JOB_PROFILE_JSON')
    private_path = CONFIG_PATH.parent / '.private/profile.json'
    if profile_json:
        cfg['profile'] = json.loads(profile_json)
    elif private_path.exists():
        cfg['profile'] = json.loads(private_path.read_text())
    return validate_config(cfg)


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
          notes TEXT NOT NULL DEFAULT '', notified_at TEXT, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def experience(text):
    text = clean(text).lower().replace('–', '-').replace('—', '-').replace('’', "'")
    words = 'zero one two three four five six seven eight nine ten eleven twelve'.split()
    text = re.sub(r'\b(' + '|'.join(words) + r')\b', lambda m: str(words.index(m[0])), text)
    number = r'\d+(?:\.\d+)?'
    pattern = re.compile(
        rf'(?<![\w.])(?P<low>{number})\s*(?:(?:-|to)\s*(?P<high>{number})\s*)?'
        r'(?P<plus>\+)?\s*(?:years?|yrs?)\b'
    )
    requirements = []
    for match in pattern.finditer(text):
        before, after = text[max(0, match.start() - 65):match.start()], text[match.end():match.end() + 90]
        numeric_range = match['high'] is not None
        context = bool(re.search(r"^['\s]*(?:of\s+)?(?:[a-z-]+\s+){0,5}experience\b", after)
                       or re.search(r'(?:experience|minimum|at least|more than|over|above|up to|maximum)\s*(?:of|:)?\s*$', before))
        if not numeric_range and not context:
            continue
        if re.match(r'\s*(?:degree|course|program|programme)\b', after):
            continue
        low = float(match['low'])
        high = float(match['high']) if numeric_range else low
        if re.search(r'(?:up to|maximum)\s*$', before):
            low = 0
        unbounded = bool(match['plus'] or re.match(r'\s*(?:\+|(?:or more|and above|and over|plus|minimum)\b)', after)
                         or (not numeric_range and re.search(r'(?:minimum|at least|more than|over|above)\s*(?:of\s*)?$', before)))
        requirements.append((low, high, unbounded, match[0]))
    if requirements:
        minimum = max(r[0] for r in requirements)
        maximum = max(r[1] for r in requirements)
        open_ended = any(r[2] for r in requirements)
        label = f'{minimum:g}–{maximum:g} years mentioned' if minimum != maximum else f'{minimum:g} years mentioned'
        if open_ended:
            label += ' (open-ended requirement)'
        return {'minimum': minimum, 'maximum': maximum, 'open_ended': open_ended,
                'evidence': [r[3] for r in requirements], 'label': label}
    fresher = re.search(
        r'\b(?:freshers?\s+(?:are\s+)?(?:welcome|can apply|may apply|eligible)|'
        r'(?:open to|suitable for)\s+freshers?|no\s+(?:prior\s+|previous\s+|work\s+|professional\s+)?experience\s+(?:is\s+)?(?:required|necessary))\b', text)
    negative = re.search(r"\b(?:not\s+(?:open to|suitable for)\s+freshers?|no\s+freshers?|freshers?\s+(?:are\s+)?not\s+(?:eligible|welcome|allowed))\b", text)
    if fresher and not negative:
        return {'minimum': 0, 'maximum': 0, 'open_ended': False,
                'evidence': [fresher[0]], 'label': 'Fresher / no prior experience required in description'}
    return {'minimum': None, 'maximum': None, 'open_ended': False, 'evidence': [],
            'label': 'Experience not confirmed — review posting'}


def experience_allowed(exp, maximum=2, allow_unknown=False):
    if exp['minimum'] is None:
        return allow_unknown
    if exp['minimum'] != 0 or exp['maximum'] > maximum:
        return False
    return allow_unknown or not exp['open_ended']


def parsed_date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def evaluate(job, cfg):
    title, loc = clean(job.get('title')), clean(job.get('location'))
    if not title or not safe_url(job.get('url', '')):
        return None
    title_match = any(contains(title, x) for x in cfg['include_title_keywords'])
    entry_title = any(contains(title, x) for x in ('apprentice', 'trainee', 'graduate', 'fresher', 'intern'))
    related_description = any(contains(clean(job.get('description')), x) for x in ('software', 'data analytics', 'data science', 'python', 'sql', 'machine learning', 'product management', 'technical support', 'business analyst'))
    if cfg['include_title_keywords'] and not title_match and not (entry_title and related_description):
        return None
    if any(contains(title, x) for x in cfg['exclude_title_keywords']):
        return None
    if not loc and not cfg.get('include_unknown_location', False):
        return None
    country = str(job.get('country', '')).lower()
    if country and country not in ('india', 'in', 'ind'):
        return None
    if loc and cfg['locations'] and not any(contains(loc, x) for x in cfg['locations']):
        return None
    exp = experience(job.get('description', ''))
    if not experience_allowed(exp, cfg.get('max_experience', 2), cfg.get('include_unknown_experience', False)):
        return None
    posted = parsed_date(job.get('posted_at'))
    if posted and posted > datetime.now(timezone.utc) + timedelta(days=1):
        return None
    if not posted and not cfg.get('include_unknown_date', True):
        return None
    deadline = parsed_date(job.get('deadline'))
    if deadline and len(str(job.get('deadline', ''))) == 10:
        deadline += timedelta(days=1)
    if deadline and deadline < datetime.now(timezone.utc):
        return None
    age = max(0, (datetime.now(timezone.utc) - posted).total_seconds() / 86400) if posted else None
    if age is not None and cfg.get('max_age_days', 14) and age > cfg.get('max_age_days', 14):
        return None
    local = any(contains(loc, x) for x in ['delhi', 'ncr', 'gurgaon', 'gurugram', 'noida', 'faridabad', 'ghaziabad'])
    junior = any(contains(title, x) for x in ['junior', 'graduate', 'trainee', 'intern', 'associate', 'engineer i', 'fresher', 'apprentice'])
    reasons = ['Delhi NCR location' if local else 'Location matches preferences']
    if junior:
        reasons.append('Entry-level title signal')
    if not title_match and entry_title and related_description:
        reasons.append('Entry-level title with relevant work mentioned in description')
    reasons.append(exp['label'])
    reasons.append('Posted within 48 hours' if age is not None and age <= 2 else ('Posting date available' if posted else 'Posting date unavailable; first seen is discovery time'))
    skills = cfg.get('profile', {}).get('skills', [])
    text = title + ' ' + clean(job.get('description'))
    matched_skills = [skill for skill in skills if contains(text, skill)]
    # A skill absent from a posting is not a gap in the candidate's profile.
    missing_skills = []
    if matched_skills:
        reasons.append('Your skills mentioned: ' + ', '.join(matched_skills))
    elif not skills:
        reasons.append('Profile skills not set; using role, location, experience preferences only')
    else:
        reasons.append('None of your skills found in available text; review requirements')
    score = 30 * local + 10 * junior + 20 * (exp['minimum'] is not None) + (20 if age is not None and age <= 2 else 10 if age is not None else 0)
    if skills:
        score += round(20 * min(1, len(matched_skills) / min(6, len(skills))))
    return {**job, 'title': title, 'location': loc, 'description': clean(job.get('description'))[:30000],
            'experience': exp, 'score': score, 'reasons': reasons,
            'profile_matches': matched_skills, 'profile_gaps': missing_skills,
            'eligibility': 'review', 'date_basis': job.get('date_basis') or ('published' if posted else 'unknown')}



def safe_url(url):
    try:
        u = urlsplit(url)
        return u.scheme in ('https', 'http') and bool(u.hostname) and not u.username and not u.password
    except (ValueError, TypeError):
        return False


def search_links(cfg):
    roles = ['software engineer', 'associate product manager', 'data scientist', 'data analyst']
    links = []
    for role in roles:
        links.append({'name': f'Google · {role}', 'url': 'https://www.google.com/about/careers/applications/jobs/results/?' + urlencode({'q': role, 'location': 'Gurugram, Haryana, India', 'target_level': 'EARLY'}), 'description': 'Official Google Careers search. Check experience and location on each posting.'})
    links.extend([
        {'name': 'LinkedIn · Delhi NCR', 'url': 'https://www.linkedin.com/jobs/search/?' + urlencode({'keywords': 'software engineer OR product analyst OR data analyst OR data scientist', 'location': 'Delhi, India', 'f_TPR': 'r86400', 'f_E': '1,2', 'sortBy': 'DD'}), 'description': 'Past 24 hours / entry-level search. Set a native job alert.'},
        {'name': 'Indeed · Gurugram', 'url': 'https://in.indeed.com/jobs?' + urlencode({'q': 'fresher software data product', 'l': 'Gurugram, Haryana', 'fromage': '1', 'sort': 'date'}), 'description': 'Recent search; use native alerts for coverage outside connected company boards.'},
        {'name': 'Naukri · Fresher NCR', 'url': 'https://www.naukri.com/fresher-jobs-in-delhi-ncr', 'description': 'Use platform filters and saved alerts. No automated login or scraping.'},
    ])
    return links


def source_career_url(src):
    if src.get('career_url'):
        return src['career_url']
    if src['type'] == 'greenhouse':
        return f"https://job-boards.greenhouse.io/{src['board']}"
    if src['type'] == 'lever':
        return f"https://jobs.lever.co/{src['company']}"
    if src['type'] == 'ashby':
        return f"https://jobs.ashbyhq.com/{src['board']}"
    if src['type'] == 'workday':
        return f"{src['base'].rstrip('/')}/{src['site']}"
    return ''


def scheduled_scan():
    try:
        scan()
    except ValueError as exc:
        if 'already running' not in str(exc):
            print(f'Automatic scan: {exc}', flush=True)
    except Exception as exc:
        print(f'Automatic scan failed: {exc}', flush=True)


def start_scheduler():
    global SCHEDULER_RUNNING
    SCHEDULER_RUNNING = True
    SCHEDULER_STOP.clear()

    def loop():
        global SCHEDULER_NEXT, SCHEDULER_RUNNING
        try:
            while not SCHEDULER_STOP.is_set():
                cfg = load_config()
                interval = timedelta(minutes=cfg.get('auto_scan_minutes', 30))
                with db() as conn:
                    last = parsed_date(get_meta(conn, 'last_scan'))
                due = (last + interval) if last else datetime.now(timezone.utc)
                SCHEDULER_NEXT = due.isoformat()
                delay = max(0, (due - datetime.now(timezone.utc)).total_seconds())
                if SCHEDULER_STOP.wait(min(delay, 30)):
                    break
                if delay > 30:
                    continue
                scheduled_scan()
                # Back off on scan/config/network failures instead of spinning.
                SCHEDULER_NEXT = (datetime.now(timezone.utc) + interval).isoformat()
                if SCHEDULER_STOP.wait(interval.total_seconds()):
                    break
        except Exception as exc:
            print(f'Automatic scanning stopped: {exc}', flush=True)
        finally:
            SCHEDULER_RUNNING = False
            SCHEDULER_NEXT = None

    thread = threading.Thread(target=loop, name='job-watch-scheduler', daemon=True)
    thread.start()
    return thread


def get_meta(conn, key, default=None):
    row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def set_meta(conn, key, value):
    conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))


def state():
    cfg = load_config()
    with db() as conn:
        jobs = []
        for row in conn.execute('SELECT * FROM jobs ORDER BY first_seen DESC'):
            payload = json.loads(row['payload'])
            current = evaluate(payload, cfg)
            jobs.append({**(current or payload), **{k: row[k] for k in ['id', 'first_seen', 'last_seen', 'status', 'notes', 'notified_at']}, 'active': bool(row['active']) and current is not None})
        checked = {s['name']: s for s in get_meta(conn, 'sources', [])}
        sources = []
        for src in cfg['sources']:
            manual = src['type'] == 'manual'
            sources.append({'name': src['name'], 'type': src['type'], 'country': 'India',
                            'career_url': source_career_url(src), 'coverage': 'manual' if manual else 'automated',
                            'status': 'manual' if manual else 'not_checked', 'date_support': 'unknown',
                            'checked_at': None, 'error': src.get('reason') if manual else None,
                            'count': 0, 'matched': 0, **checked.get(src['name'], {})})
        return {'jobs': jobs, 'sources': sources, 'last_scan': get_meta(conn, 'last_scan'), 'scanning': SCAN_LOCK.locked(), 'settings': cfg, 'search_links': search_links(cfg), 'read_only': False,
                'automation': {'mode': 'local', 'running': SCHEDULER_RUNNING, 'interval_minutes': cfg.get('auto_scan_minutes', 30),
                               'next_scan': SCHEDULER_NEXT, 'message': 'Scans automatically while this server is running; the browser can be closed.' if SCHEDULER_RUNNING else 'Automatic local scans start when you run serve. GitHub scheduling is separate.'}}


def in_india(job):
    country = str(job.get('country', '')).casefold()
    if country:
        return country in ('india', 'in', 'ind')
    location = clean(job.get('location', ''))
    return any(contains(location, place) for place in (
        'india', 'delhi', 'gurgaon', 'gurugram', 'noida', 'faridabad', 'ghaziabad',
        'bengaluru', 'bangalore', 'hyderabad', 'pune', 'mumbai', 'chennai', 'kolkata',
        'ahmedabad', 'chandigarh', 'kochi', 'coimbatore', 'jaipur', 'mohali', 'nagpur'))


def collect(src, cfg=None):
    jobs = []
    error = None
    try:
        for job in FETCHERS[src['type']](src):
            if src['type'] == 'workday' and cfg and not src.get('fetch_details'):
                # Fetch exact dates/descriptions only for plausible title/location candidates.
                preview_cfg = {**cfg, 'include_unknown_date': True, 'include_unknown_experience': True, 'max_age_days': 0}
                preview_job = {**job, 'location': ''} if re.fullmatch(r'\d+ locations?', job.get('location', ''), flags=re.I) else job
                if preview_job is not job:
                    preview_cfg['include_unknown_location'] = True
                if evaluate(preview_job, preview_cfg) is not None:
                    from job_sources import enrich_workday
                    job = enrich_workday(src, job)
            if src.get('country') == 'India' and not in_india(job):
                continue
            jobs.append({**job, 'source': src['name']})
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    return jobs, error


def public_job(job, cfg):
    stored = {k: v for k, v in job.items() if k not in {'profile_matches', 'profile_gaps', 'eligibility'}}
    stored['reasons'] = [r for r in job.get('reasons', []) if not any(x in r for x in ('Your skills mentioned:', 'Profile skills not set;', 'None of your skills found'))]
    generic = evaluate(job, {**cfg, 'profile': {'skills': []}})
    stored['score'] = generic['score'] if generic else 0
    return stored


def scan(dry_run=False, locked=False):
    if not locked and not SCAN_LOCK.acquire(blocking=False):
        raise ValueError('A scan is already running.')
    try:
        cfg = load_config()
        matches = []
        health = [{'name': src['name'], 'type': 'manual', 'status': 'manual', 'coverage': 'manual', 'career_url': src['career_url'], 'country': 'India', 'date_support': 'unknown', 'error': src.get('reason', 'Official career search requires a manual check; not scanned automatically.'), 'count': 0, 'matched': 0, 'checked_at': None} for src in cfg['sources'] if src['type'] == 'manual']
        with ThreadPoolExecutor(max_workers=min(6, len(cfg['sources']))) as pool:
            futures = {pool.submit(collect, src, cfg): src for src in cfg['sources'] if src['type'] != 'manual'}
            for future in as_completed(futures):
                src = futures[future]
                raw, error = future.result()
                accepted = [result for j in raw if (result := evaluate(j, cfg)) is not None]
                matches.extend(accepted)
                health.append({'name': src['name'], 'type': src['type'], 'status': 'error' if error else 'ok', 'error': error, 'count': len(raw), 'matched': len(accepted), 'checked_at': now(), 'coverage': 'automated', 'country': 'India', 'career_url': source_career_url(src), 'date_support': 'published' if raw and all(parsed_date(j.get('posted_at')) for j in raw) else 'mixed' if any(parsed_date(j.get('posted_at')) for j in raw) else 'unknown'})
        if not dry_run:
            timestamp = now()
            with db() as conn:
                # A failed source must not mark its old results unavailable.
                healthy = {s['name'] for s in health if s['status'] == 'ok'}
                configured = {s['name'] for s in cfg['sources'] if s['type'] != 'manual'}
                for row in conn.execute('SELECT id,payload FROM jobs').fetchall():
                    if json.loads(row['payload'])['source'] in healthy or json.loads(row['payload'])['source'] not in configured:
                        conn.execute('UPDATE jobs SET active=0 WHERE id=?', (row['id'],))
                for job in matches:
                    key = hashlib.sha256(job['url'].split('#')[0].rstrip('/').encode()).hexdigest()[:24]
                    job['id'] = key
                    stored = public_job(job, cfg)
                    conn.execute('''INSERT INTO jobs(id,payload,first_seen,last_seen) VALUES (?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen,active=1''', (key, json.dumps(stored), timestamp, timestamp))
                set_meta(conn, 'sources', health)
                set_meta(conn, 'last_scan', timestamp)
        return matches, health
    finally:
        SCAN_LOCK.release()


def send_notifications():
    snapshot = state()
    jobs = [j for j in snapshot['jobs'] if not j['notified_at'] and j['active'] and j['status'] not in {'archived', 'rejected', 'applied', 'interview'}
            and experience_allowed(experience(j.get('description', '')))]
    errors = [s for s in snapshot['sources'] if s['status'] == 'error']
    if not jobs and not errors:
        return 0
    user, password = os.environ.get('EMAIL_USER'), os.environ.get('EMAIL_APP_PASSWORD')
    to = os.environ.get('EMAIL_TO') or user
    if not user or not password or not to:
        raise ValueError('Email not configured. Set EMAIL_USER, EMAIL_APP_PASSWORD, optionally EMAIL_TO. Discoveries are saved and alerts remain retryable.')
    msg = EmailMessage()
    msg['From'], msg['To'] = user, to
    msg['Subject'] = f'Job watch: {len(jobs)} new matches, {len(errors)} source issues'
    lines = ['Review actual requirements before applying. First seen is not the publication date.', '']
    for j in sorted(jobs, key=lambda j: j['score'], reverse=True):
        lines.extend([f"{j['title']} — {j['source']} | {j['location']}", f"Score {j['score']} · {j['experience']['label']}", j['url'], ''])
    lines += ['Source issues: ' + s['name'] + ': ' + s['error'] for s in errors]
    msg.set_content('\n'.join(lines))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    with db() as conn:
        conn.executemany('UPDATE jobs SET notified_at=? WHERE id=?', [(now(), j['id']) for j in jobs])
    return len(jobs)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/api/state':
            try:
                return self.reply(200, state())
            except (ValueError, OSError) as exc:
                return self.reply(500, {'error': str(exc)})
        path = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/static/app.js': 'app.js', '/static/style.css': 'style.css'}.get(self.path.split('?')[0])
        if not path:
            return self.reply(404, {'error': 'Not found'})
        file = ROOT / 'static' / path
        if not file.exists():
            return self.reply(503, {'error': 'Dashboard assets not installed yet'})
        data = file.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'html': 'text/html; charset=utf-8', 'js': 'text/javascript', 'css': 'text/css'}[file.suffix[1:]])
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def mutate(self):
        # Local personal app: reject browser cross-origin writes (including DNS rebinding).
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return self.reply(403, {'error': 'Cross-origin writes are not allowed'})
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self.reply(403, {'error': 'Cross-origin writes are not allowed'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > 200000:
                raise ValueError('Request too large')
            payload = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('Request must be an object')
            if self.command == 'POST' and self.path == '/api/scan':
                if not SCAN_LOCK.acquire(blocking=False):
                    return self.reply(409, {'error': 'Scan already running'})
                threading.Thread(target=self.background_scan, daemon=True).start()
                return self.reply(202, {'message': 'Scan started'})
            if self.command == 'PUT' and self.path == '/api/settings':
                if SCAN_LOCK.locked():
                    return self.reply(409, {'error': 'Wait for scan to finish before changing settings'})
                validate_config(payload)
                if os.environ.get('JOB_PROFILE_JSON'):
                    raise ValueError('This profile is managed by JOB_PROFILE_JSON; change it in the environment, not in the dashboard.')
                private = CONFIG_PATH.parent / '.private/profile.json'
                private.parent.mkdir(parents=True, exist_ok=True)
                private_temp = private.with_suffix('.tmp')
                private_temp.write_text(json.dumps(payload.get('profile', {'skills': []}), indent=2) + '\n')
                private_temp.replace(private)
                payload = {**payload, 'profile': {'skills': []}}
                temp = CONFIG_PATH.with_suffix('.tmp')
                temp.write_text(json.dumps(payload, indent=2) + '\n')
                temp.replace(CONFIG_PATH)
                return self.reply(200, {'message': 'Settings saved. Scan to apply filters.'})
            if self.command == 'PATCH' and self.path.startswith('/api/jobs/'):
                key = self.path.removeprefix('/api/jobs/')
                if not payload or set(payload) - {'status', 'notes'}:
                    raise ValueError('Only status and notes can be updated')
                if 'status' in payload and (not isinstance(payload['status'], str) or payload['status'] not in STATUSES):
                    raise ValueError('Invalid application status')
                if 'notes' in payload and (not isinstance(payload['notes'], str) or len(payload['notes']) > 10000):
                    raise ValueError('Notes must be text up to 10000 characters')
                with db() as conn:
                    if not conn.execute('SELECT 1 FROM jobs WHERE id=?', (key,)).fetchone():
                        return self.reply(404, {'error': 'Job not found'})
                    for field, value in payload.items():
                        conn.execute(f'UPDATE jobs SET {field}=? WHERE id=?', (value, key))
                return self.reply(200, {'message': 'Updated'})
            return self.reply(404, {'error': 'Not found'})
        except (ValueError, OSError) as exc:
            return self.reply(400, {'error': str(exc)})

    def background_scan(self):
        try:
            scan(locked=True)
        except Exception as exc:
            with db() as conn:
                set_meta(conn, 'sources', [{'name': 'Scan', 'type': 'system', 'status': 'error', 'error': str(exc), 'count': 0, 'checked_at': now()}])

    do_POST = mutate
    do_PUT = mutate
    do_PATCH = mutate


def main():
    global CONFIG_PATH, DB_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--dry-run', action='store_true', help='Read-only scan; no email or state writes')
    commands = parser.add_subparsers(dest='command')
    serve = commands.add_parser('serve', help='Open local dashboard')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=3000)
    serve.add_argument('--no-auto-scan', action='store_true', help='Disable background scans for this server')
    scanner = commands.add_parser('scan', help='Fetch and save matching jobs')
    scanner.add_argument('--email', action='store_true')
    commands.add_parser('doctor', help='Check configuration without revealing secrets')
    args = parser.parse_args()
    CONFIG_PATH, DB_PATH = args.config, args.db
    try:
        cfg = load_config()
        if args.command == 'doctor':
            print(f"Configuration valid: {len(cfg['sources'])} company sources. Database: {DB_PATH}")
            print('Email: ' + ('configured' if os.environ.get('EMAIL_USER') and os.environ.get('EMAIL_APP_PASSWORD') else 'not configured (optional)'))
        elif args.command == 'serve':
            print(f'Dashboard: http://{args.host}:{args.port} (personal use; do not expose publicly without authentication)', flush=True)
            server = ThreadingHTTPServer((args.host, args.port), Handler)
            scheduler = start_scheduler() if not args.no_auto_scan else None
            try:
                server.serve_forever()
            finally:
                SCHEDULER_STOP.set()
                server.server_close()
                if scheduler:
                    scheduler.join(timeout=2)
        else:
            jobs, health = scan(dry_run=args.dry_run)
            print(json.dumps({'matches': [public_job(j, cfg) for j in jobs], 'sources': health}, indent=2))
            if args.command == 'scan' and args.email and not args.dry_run:
                print(f'Email delivered: {send_notifications()} new matches')
            automated = [s for s in health if s['coverage'] == 'automated']
            if automated and all(s['status'] == 'error' for s in automated):
                return 1
        return 0
    except (ValueError, OSError, smtplib.SMTPException) as exc:
        print(f'Error: {exc}', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
