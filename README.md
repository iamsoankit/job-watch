# Daily job watch

Checks the public career sites of the companies in `config.json` every morning
(8:00 IST) and emails you only the NEW roles that match your keywords.
It does not touch LinkedIn (LinkedIn bans bots; use its own saved alerts instead).

## Set up (about 10 minutes)

1. Create a new GitHub repo (private is fine) and upload everything in this folder,
   including the hidden `.github/workflows/job-watch.yml`.
2. In your Google account, turn on 2-Step Verification, then create an
   **App password** (Google Account > Security > App passwords). Copy the 16 characters.
3. In the repo: Settings > Secrets and variables > Actions > New repository secret.
   Add these three:
   - `EMAIL_USER`: your Gmail address
   - `EMAIL_APP_PASSWORD`: the 16-character app password
   - `EMAIL_TO`: where to send alerts (can be the same Gmail)
4. Go to the Actions tab, open "Daily job watch", click "Run workflow" to test it.
   The first run emails every current match; after that you only get new ones.

## Adding companies

Edit `config.json` > `sources`. Three types work:

| Type | What you need | Example career-site URL |
|---|---|---|
| greenhouse | `board` | job-boards.greenhouse.io/**alteradigitalhealthindia** |
| lever | `company` | jobs.lever.co/**companyname** |
| workday | `base`, `tenant`, `site` | **https://harriscomputer.wd3.myworkdayjobs.com**/**Altera** (tenant is the first part of the host: `harriscomputer`) |

Tune `include_title_keywords`, `exclude_title_keywords` and `locations` to taste.

## Test locally

    python job_watch.py --dry-run

Prints matches without emailing or saving anything.

## Notes

- Workday and Greenhouse endpoints can change; if a source shows under
  "Sources that failed today" in the email, check its URL parts in the config.
- Always read the posting's own experience, batch and CGPA criteria before applying.
- Never pay anyone to apply, and be careful with hiring posts that ask for money.
