# Job Watch

Job Watch is a standard-library Python tool for finding and tracking early-career roles. It scans the public ATS sources configured in `config.json`, ranks likely matches for Delhi NCR and roles in software engineering, product, and data, and keeps a local SQLite history so repeated scans can focus on jobs you have not already been notified about.

It is a discovery and tracking aid, not an application bot. Review each employer's requirements and apply on the employer's official site. It cannot search every employer or guarantee complete, real-time coverage: it only checks configured supported public sources, and source endpoints, network access, and posting details can change. Google Careers and LinkedIn, Naukri, and Indeed are provided as manual search links; the tool does not scrape restricted sites or submit applications. Search engines and job boards may show stale listings, and a job's first-seen time is not its actual posting time.

## Requirements

- Python 3.11 or newer; no third-party Python packages are required.
- A copy of this repository. GitHub Actions stores its automation database on the dedicated `job-watch-state` branch. In a public repository that branch is public too, so it must contain generic job discovery data only—never resume details, profile skills, or application notes.

## Run locally

From the repository directory:

```sh
python3 job_watch.py doctor
python3 job_watch.py scan
python3 job_watch.py serve --host 127.0.0.1 --port 3000
```

Open <http://127.0.0.1:3000> while the server is running. The server checks sources automatically every 30 minutes even with the browser closed; keep the server running for local automation. Use `serve --no-auto-scan` to disable that worker, or set `auto_scan_minutes` (5–1440) in config. Cloud automation below also works when your computer is off once enabled. The dashboard shows discovered jobs and manual search links, lets you review the active configuration, and supports notes and application-status tracking. Keep the server bound to `127.0.0.1` unless you have deliberately secured any wider network access; the dashboard is intended for local use. The dashboard is branded **Northstar**; Job Watch is the repository and CLI name.

`scan` updates the SQLite database but does not send email unless you explicitly request it. The dashboard and scans on this machine use `data/jobs.sqlite3` by default. Override the database with the global `--db PATH` option, or with `JOB_WATCH_DB` when `--db` is omitted. The global options must come before the command:

```sh
python3 job_watch.py --config config.json --db data/jobs.sqlite3 scan
python3 job_watch.py --db /path/to/jobs.sqlite3 serve --host 127.0.0.1 --port 3000
```

The config defaults to `config.json`; use global `--config PATH` to select another file. `doctor` validates the config and reports whether `EMAIL_USER` and `EMAIL_APP_PASSWORD` are present. It does not display their values. `python3 job_watch.py --dry-run` is the legacy, read-only preview mode: it fetches and prints matches without sending email or saving state.

## Optional email alerts

Email is optional and is not needed for local scans or the dashboard. To have a scan send alerts for matching jobs not yet notified, set these environment variables and pass `--email`:

```sh
export EMAIL_USER='your-gmail-address@gmail.com'
export EMAIL_APP_PASSWORD='your-google-app-password'
# Optional; defaults to EMAIL_USER when omitted.
export EMAIL_TO='where-alerts-should-go@example.com'
python3 job_watch.py scan --email
```

Use a Google App Password (with 2-Step Verification enabled), not your normal account password. Keep credentials out of `config.json`, source control, terminal screenshots, and logs. A failed email send must leave those jobs eligible for a later retry; a successful send marks them notified. You can omit email entirely and use the dashboard to review jobs.

## Configure searches

Edit `config.json` to adjust sources and matching preferences. These public ATS source types are supported:

| `type` | Required fields | Find the ID on the official board URL |
|---|---|---|
| `greenhouse` | `board` | `boards.greenhouse.io/acme` → `acme` |
| `lever` | `company` | `jobs.lever.co/acme` → `acme` |
| `ashby` | `board` | `jobs.ashbyhq.com/acme` → `acme` |
| `workday` | `base`, `tenant`, `site` | Host, account prefix, and career-site path respectively |
| `spglobal` | no extra fields | Official S&P Global public India feed |
| `zs` | no extra fields | Official ZS public India feed |
| `amex` | optional `site: "CX_1"` | Official Amex Oracle public India feed |
| `manual` | `career_url`, `reason` | Official career page only; never reported as an automated scan |

Example source entries (replace IDs with those from the employer's official board):

```json
{"name":"Example Greenhouse","type":"greenhouse","board":"acme"},
{"name":"Example Lever","type":"lever","company":"acme"},
{"name":"Example Ashby","type":"ashby","board":"acme"},
{"name":"Example Workday","type":"workday","base":"https://acme.wd3.myworkdayjobs.com","tenant":"acme","site":"Careers"}
```

Add entries to `sources` in `config.json`, keeping valid JSON commas. Set `country` to `India` and add an official `career_url`. Workday discovers the public India country facet when provided, then checks locations locally. It fetches exact posting dates and descriptions for plausible NCR/role candidates, including multi-location postings. `"fetch_details": true` requests details for every result and can be slow. Any explicit `max_jobs` cap reports truncation rather than silently claiming complete coverage.

The directory contains **23 companies**. Automated feeds cover S&P Global, ZS Associates, American Express, Adobe, Gartner, Anaplan, Insurity India, Zscaler, MongoDB, Tower Research Capital, Paytm, ShyftLabs, Unlimit, Altera, and CSG. Official manual-only pages cover Mastercard, BlackRock, Expedia Group, Google, NatWest Group, Optum, Genpact, and Michelin. This is a curated watchlist, not an exhaustive list of NCR employers. Manual links cannot produce automatic alerts. Each company row also offers two LinkedIn links—a 24-hour entry-level job search for Delhi NCR and a recent-posts search for recruiter hiring announcements—plus a short guide to LinkedIn's own job alerts. The tool never logs in to LinkedIn, calls its API, or scrapes it; LinkedIn prohibits automation and may restrict accounts that use it, so those links are opened by you in a browser.

Defaults target India and Delhi NCR/India-remote locations, description-supported fresher or bounded 0–2-year experience requirements, and **known posting dates within 14 days**. `include_unknown_date: false` rejects undated listings. `max_age_days: 0` disables the local age cutoff; the published website intentionally keeps a recent, dated-only view. Greenhouse uses `first_published` (never `updated_at`); Workday uses posting-detail `startDate`; S&P/ZS use `posted_date`; Amex uses `ExternalPostedStartDate`; Lever uses `createdAt`.

Experience extraction is a conservative text heuristic. Unknown experience is excluded by default (`include_unknown_experience: false`), as are open-ended requirements such as “2+ years” ranges extending past two years such as “2–6 years”, and requirements with a nonzero minimum such as “1–2 years”. Full descriptions, not titles or stored scores, supply the evidence; a fresher/junior title alone is insufficient. Explicit fresher/no-prior-experience statements can qualify when no conflicting numeric requirement is found. Unknown location is excluded. Generic apprentice/intern titles require relevant technical/data/product work in the description. Engineer II is not automatically accepted or rejected by its level: its description must pass the same strict experience check. Optional local inclusion of unknown experience is for manual review only; publication and email always revalidate descriptions and exclude unknown, open-ended, and above-two-year requirements. This conservative text check may omit suitable jobs and does not verify degree/batch eligibility. Always check degree, graduation batch, experience, deadline, and employer requirements before applying.

### Private profile matching

Enter comma-separated skills in **Search preferences → Profile skills**. They are saved to `.private/profile.json` beside the config, not to the tracked config file. Keep `.private/` ignored. Matching ranks skills explicitly mentioned in the job; missing text is a review signal, not a definitive skill gap or an eligibility decision. With no skills supplied, only your role, location, experience, and freshness preferences are used. The tool does not invent skills from resume filenames or parse inaccessible attachments.

`JOB_PROFILE_JSON` can supply a private profile such as `{"skills":["Python","SQL"]}`. That environment value takes precedence over the local file; edit it at its source rather than in the dashboard. The cloud workflow accepts the same optional Actions secret. No PDF, contact detail, private profile, application status, or note is included in the static publication.

Use the dashboard for notes and application tracking. The local database is separate from the database used by scheduled GitHub Actions: local notes and status changes are not synchronized to the automation database.

## Run on GitHub Actions

The included workflow runs on a 30-minute schedule and can also be started from the repository's **Actions** tab with **Run workflow**. GitHub may delay scheduled runs, especially during busy periods; this is not a real-time alerting guarantee. The workflow scans the checked-out default-branch configuration and persists its discovery/notification database on the dedicated `job-watch-state` branch, not on the application branch. Treat that branch as reserved for the workflow; it stores the automation database and is not an application backup.

To enable scheduled email, add repository Actions secrets named `EMAIL_USER` and `EMAIL_APP_PASSWORD`; add `EMAIL_TO` only if alerts should go to a different address (otherwise the sender is used). Without either required secret, the workflow still scans and persists discoveries without email. If delivery fails, the workflow reports failure and retains unnotified jobs in its database for retry on a later run. The scan job uses repository contents write for the state branch and Pages read for configuration. The separate deployment job uses Pages write and OIDC in the `github-pages` environment. The workflow runs `python -m unittest discover -s tests` before scanning.

For private resume-based matching, store the profile JSON in the optional Actions secret `JOB_PROFILE_JSON` (for example, `{"skills":["Python","SQL"]}`). The workflow passes it to the scanner through the environment; do not commit resume data or skills in `config.json`. The scheduled state branch is public in a public repository, so only generic job fields may be persisted there. The static publisher independently removes profile matches/gaps and personal reasons, and recalculates its priority score from generic location, entry-level title, experience, and posting-date signals.

The workflow fetches and updates the `job-watch-state` branch with regular commits, preserving its history. The first successful scan can notify about all currently matching jobs found in the configured sources. Subsequent alerts are based on notification history, not a verified employer posting timestamp. Review the workflow's source errors and the original job page; no scanner can ensure every matching job is found or that an alert arrives before another applicant applies.

### Daily cloud dashboard

Static dashboard publication is enabled by default for authorized delivery; set the repository Actions variable `PUBLISH_DASHBOARD` to `false` to disable it. To publish only job listings, an owner must configure GitHub Pages to use **GitHub Actions** in repository settings, then run the workflow manually once or wait for its daily **02:20 UTC / 07:50 IST** run. The workflow must first be merged into the default branch; a PR or local preview alone does not activate cloud scheduling. Scans and database persistence continue every 30 minutes; the static snapshot refreshes daily and on manual runs. GitHub Pages must be enabled for the repository. A public Pages site is readable by anyone, so exported listings and their generic metadata will be public.

The export contains only active listings with a known posting date within the configured age window (14 days by default), plus safe company career-page coverage metadata. Publication independently requires description-backed fresher or bounded experience starting at zero and ending at at most two years, even if local review settings are broader. It omits profile skills/matches/gaps, configuration, notes, saved/application statuses, notification timestamps, and raw source error text; exported jobs are shown as new and the snapshot is read-only. The current local dashboard remains editable and is not changed by publishing.

## Tests

```sh
python3 -m unittest discover -s tests
```

Always verify the employer, job location, experience, graduation/batch, and application deadline on the official posting before applying. Never pay a recruiter or employer to apply.
