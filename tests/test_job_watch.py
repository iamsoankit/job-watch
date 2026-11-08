import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import job_watch as app


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = {'include_title_keywords': ['engineer', 'product manager', 'data analyst'],
                    'exclude_title_keywords': ['senior', 'sr', 'engineer ii'],
                    'locations': ['gurugram', 'noida', 'delhi', 'remote india'],
                    'max_experience': 2, 'max_age_days': 30,
                    'sources': [{'type': 'greenhouse', 'name': 'Test', 'board': 'test'}]}
        self.path = Path(self.temp.name)
        (self.path / 'config.json').write_text(json.dumps(self.cfg))
        for name, value in [('CONFIG_PATH', self.path / 'config.json'), ('DB_PATH', self.path / 'jobs.db')]:
            p = patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.job = {'title': 'Software Engineer I', 'location': 'Gurugram, India',
                    'url': 'https://example.com/jobs/1', 'description': '0-2 years of experience', 'posted_at': ''}

    def test_product_manager_not_excluded(self):
        job = {**self.job, 'title': 'Associate Product Manager'}
        self.assertIsNotNone(app.evaluate(job, self.cfg))

    def test_filters_experience_location_seniority_and_unsafe_url(self):
        self.assertIsNotNone(app.evaluate(self.job, self.cfg))
        for overrides in [{'title': 'Senior Software Engineer'}, {'title': 'Software Engineer II'},
                          {'description': 'Minimum 5 years of professional experience'},
                          {'location': 'Bengaluru, India'}, {'location': ''}, {'url': 'javascript:alert(1)'},
                          {'posted_at': '2020-01-01T00:00:00Z'}]:
            with self.subTest(overrides=overrides):
                self.assertIsNone(app.evaluate({**self.job, **overrides}, self.cfg))

    def test_unknown_experience_is_explicit(self):
        result = app.evaluate({**self.job, 'description': ''}, self.cfg)
        self.assertIsNone(result['experience']['minimum'])
        self.cfg['include_unknown_experience'] = False
        self.assertIsNone(app.evaluate({**self.job, 'description': ''}, self.cfg))

    def test_experience_ranges(self):
        for text, value in [('0–2 years of experience', 0), ('2+ years experience', 2), ('experience: 3 years', 3), ('minimum 4 years', 4)]:
            self.assertEqual(app.experience(text)['minimum'], value)

    def test_dry_run_never_writes(self):
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([self.job])}):
            jobs, health = app.scan(dry_run=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(health[0]['status'], 'ok')
        self.assertFalse(app.DB_PATH.exists())

    def test_dedupe_preserves_status_notes_and_notification(self):
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([self.job, self.job])}):
            app.scan()
            before = app.state()['jobs'][0]
            with app.db() as conn:
                conn.execute("UPDATE jobs SET status='applied',notes='Follow up Friday',notified_at='sent'")
            app.scan()
        jobs = app.state()['jobs']
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['status'], 'applied')
        self.assertEqual(jobs[0]['notes'], 'Follow up Friday')
        self.assertEqual(jobs[0]['notified_at'], 'sent')
        self.assertEqual(jobs[0]['first_seen'], before['first_seen'])

    def test_failure_keeps_existing_jobs_and_reports_health(self):
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([self.job])}):
            app.scan()
        with patch.dict(app.FETCHERS, {'greenhouse': MagicMock(side_effect=TimeoutError('offline'))}):
            app.scan()
        snapshot = app.state()
        self.assertTrue(snapshot['jobs'][0]['active'])
        self.assertIn('offline', snapshot['sources'][0]['error'])
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([])}):
            app.scan()
        self.assertFalse(app.state()['jobs'][0]['active'])

    def test_email_failure_is_retryable_then_no_duplicate(self):
        with patch.dict(app.FETCHERS, {'greenhouse': lambda src: iter([self.job])}):
            app.scan()
        with patch.dict(os.environ, {'EMAIL_USER': 'test@example.com', 'EMAIL_APP_PASSWORD': 'not-a-real-secret'}):
            with patch('smtplib.SMTP_SSL', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    app.send_notifications()
            self.assertIsNone(app.state()['jobs'][0]['notified_at'])
            with patch('smtplib.SMTP_SSL') as smtp:
                self.assertEqual(app.send_notifications(), 1)
                self.assertEqual(app.send_notifications(), 0)
                self.assertEqual(smtp.return_value.__enter__.return_value.send_message.call_count, 1)

    def test_config_validation(self):
        app.validate_config(self.cfg)
        for override in [{'max_experience': '2'}, {'sources': []}, {'locations': 'India'}, {'include_unknown_location': 'false'}]:
            with self.assertRaises(ValueError):
                app.validate_config({**self.cfg, **override})

    def test_search_links_include_google_and_native_platforms(self):
        links = app.search_links(self.cfg)
        self.assertEqual(sum('Google' in link['name'] for link in links), 4)
        self.assertTrue(all(app.safe_url(link['url']) for link in links))


if __name__ == '__main__':
    unittest.main()
