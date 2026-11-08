import json
from datetime import datetime, timezone, timedelta
import threading
import unittest
from unittest.mock import patch

import job_watch as app
import test_job_watch
from job_sources import _country_filter


class MatchingTests(unittest.TestCase):
    setUp = test_job_watch.WatchTests.setUp
    def test_requires_real_recent_posting_date_when_configured(self):
        self.cfg.update(include_unknown_date=False, max_age_days=14)
        self.assertIsNone(app.evaluate(self.job, self.cfg))
        for days, matched in [(0, True), (5, True), (15, False), (-5, False)]:
            job = {**self.job, 'posted_at': (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()}
            self.assertEqual(app.evaluate(job, self.cfg) is not None, matched)
        self.cfg['include_unknown_date'] = True
        self.assertIsNotNone(app.evaluate(self.job, self.cfg))

    def test_expired_deadline_is_not_open(self):
        self.assertIsNone(app.evaluate({**self.job, 'deadline': '2020-01-01T00:00:00Z'}, self.cfg))

    def test_skills_are_optional_rank_signals_not_eligibility_claim(self):
        self.cfg['profile'] = {'skills': ['Python', 'SQL', 'React']}
        result = app.evaluate({**self.job, 'description': '0-2 years experience in Python and SQL'}, self.cfg)
        self.assertEqual(result['profile_matches'], ['Python', 'SQL'])
        self.assertEqual(result['profile_gaps'], [])
        self.assertEqual(result['eligibility'], 'review')
        self.assertLessEqual(result['score'], 100)

    def test_india_filter_excludes_other_countries_and_global_remote(self):
        for job, expected in [({'location': 'Gurgaon, India'}, True), ({'location': 'Noida'}, True),
                              ({'location': 'Remote'}, False), ({'location': 'London'}, False),
                              ({'location': 'Delhi', 'country': 'United States'}, False)]:
            self.assertEqual(app.in_india(job), expected)
        self.assertIsNone(app.evaluate({**self.job, 'country': 'United States'}, self.cfg))

    def test_freshness_applies_to_stored_jobs_during_feed_failure(self):
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([self.job])}):
            app.scan()
        cfg = {**self.cfg, 'include_unknown_date': False}
        app.CONFIG_PATH.write_text(json.dumps(cfg))
        self.assertFalse(app.state()['jobs'][0]['active'])

    def test_manual_company_cannot_look_like_automatic_success(self):
        self.cfg['sources'].append({'name': 'Manual employer', 'type': 'manual', 'career_url': 'https://example.com/careers', 'country': 'India', 'reason': 'Manual search needed'})
        app.CONFIG_PATH.write_text(json.dumps(self.cfg))
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([self.job])}):
            _, health = app.scan()
        manual = next(s for s in health if s['name'] == 'Manual employer')
        self.assertEqual(manual['status'], 'manual')
        self.assertIsNone(manual['checked_at'])
        self.assertEqual(manual['coverage'], 'manual')

    def test_scheduler_runs_without_http_request(self):
        called = threading.Event()
        with patch.object(app, 'scheduled_scan', side_effect=lambda: called.set()):
            thread = app.start_scheduler()
            try:
                self.assertTrue(called.wait(2), 'server scheduler did not run independently')
            finally:
                app.SCHEDULER_STOP.set()
                thread.join(2)
        self.assertFalse(app.SCHEDULER_RUNNING)
        app.SCHEDULER_STOP.clear()

    def test_country_facets_can_be_nested(self):
        facets = [{'values': [{'facetParameter': 'locationCountry', 'values': [{'descriptor': 'India', 'id': 'in-id'}, {'descriptor': 'UK', 'id': 'uk-id'}]}]}]
        self.assertEqual(_country_filter(facets, 'India'), {'locationCountry': ['in-id']})

    def test_profile_signals_are_not_stored_in_public_database(self):
        self.cfg['profile'] = {'skills': ['private-skill-marker']}
        app.CONFIG_PATH.write_text(json.dumps(self.cfg))
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([self.job])}):
            app.scan()
        with app.db() as conn:
            payload = json.loads(conn.execute('SELECT payload FROM jobs').fetchone()['payload'])
        self.assertNotIn('profile_matches', payload)
        self.assertNotIn('profile_gaps', payload)
        self.assertNotIn('private-skill-marker', json.dumps(payload))

    def test_private_profile_override_and_validation(self):
        private = app.CONFIG_PATH.parent / '.private/profile.json'
        private.parent.mkdir()
        private.write_text(json.dumps({'skills': ['SQL']}))
        self.assertEqual(app.load_config()['profile']['skills'], ['SQL'])
        self.assertNotIn('SQL', app.CONFIG_PATH.read_text())
        with patch.dict('os.environ', {'JOB_PROFILE_JSON': '{"skills": ["Python"]}'}):
            self.assertEqual(app.load_config()['profile']['skills'], ['Python'])

    def test_generic_fresher_titles_need_relevant_description(self):
        self.assertIsNotNone(app.evaluate({**self.job, 'title': 'Apprentice', 'description': 'Freshers can apply. Support our data analytics team using SQL.'}, self.cfg))
        self.assertIsNone(app.evaluate({**self.job, 'title': 'HR Intern', 'description': 'Recruitment and people operations.'}, self.cfg))

    def test_description_must_confirm_bounded_early_career_experience(self):
        self.cfg['exclude_title_keywords'] = []
        for description, accepted in [
            ('Database Engineer (4-6 years)', False),
            ('At least 2–6 years of relevant experience.', False),
            ('Minimum four to six years of experience.', False),
            ('0-2 years experience. Also needs 4 years of SQL experience.', False),
            ('2+ years experience', False),
            ('At least 2 years of experience', False),
            ('Minimum of 2 years of experience', False),
            ('More than 2 years of experience', False),
            ('2 years+ experience', False),
            ('2 years or more of experience', False),
            ('Experience with Python and SQL.', False),
            ('Not suitable for freshers.', False),
            ('1 year of experience required', False),
            ('0-1 years experience', True),
            ('0-2 years of professional experience', True),
            ('One to two years of relevant experience.', False),
            ('Up to 2 years of experience', True),
            ('No prior experience required.', True),
            ('Freshers can apply.', True),
        ]:
            with self.subTest(description=description):
                job = {**self.job, 'title': 'Software Engineer II', 'description': description}
                self.assertEqual(app.evaluate(job, self.cfg) is not None, accepted)
        self.assertIsNone(app.evaluate({**self.job, 'title': 'Fresher Software Engineer', 'description': ''}, self.cfg))

    def test_previously_stored_senior_matches_are_not_active_or_emailed(self):
        self.cfg.update(exclude_title_keywords=[], include_unknown_experience=True)
        app.CONFIG_PATH.write_text(json.dumps(self.cfg))
        unknown = {**self.job, 'description': 'SQL and database skills required.'}
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([unknown])}):
            app.scan()
        with patch('smtplib.SMTP_SSL') as smtp:
            self.assertEqual(app.send_notifications(), 0)
            smtp.assert_not_called()
        self.cfg['include_unknown_experience'] = False
        app.CONFIG_PATH.write_text(json.dumps(self.cfg))
        self.assertFalse(app.state()['jobs'][0]['active'])

    def test_profile_validation(self):
        for extra in [{'profile': {'skills': 'Python'}}, {'profile': {'skills': ['']}}, {'auto_scan_minutes': 0}, {'include_unknown_date': 'false'}, {'country': 'UK'}]:
            with self.assertRaises(ValueError):
                app.validate_config({**self.cfg, **extra})


if __name__ == '__main__':
    unittest.main()
