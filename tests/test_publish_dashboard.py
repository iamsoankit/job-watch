import json
from pathlib import Path
import tempfile
import unittest

from publish_dashboard import build_snapshot, publish


class PublishDashboardTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            'jobs': [{
                'id': 'public-id', 'title': 'Graduate Analyst', 'source': 'Example',
                'location': 'Noida, India', 'url': 'https://jobs.example/123',
                'description': 'Public posting', 'posted_at': '2026-11-01',
                'updated_at': '2026-11-02', 'experience': {'minimum': 0},
                'score': 100, 'reasons': ['Your skills mentioned: PRIVATE PYTHON'],
                'profile_matches': ['PRIVATE PYTHON'], 'profile_gaps': ['PRIVATE KUBERNETES'],
                'first_seen': '2026-11-02T10:00:00Z',
                'last_seen': '2026-11-03T10:00:00Z', 'active': True,
                'source_id': 'internal-source-id', 'status': 'applied',
                'notes': 'PRIVATE NOTE', 'notified_at': 'private-notification-time',
            }],
            'sources': [{
                'name': 'Example', 'type': 'greenhouse', 'status': 'error', 'count': 2,
                'matched': 1, 'checked_at': '2026-11-03T10:00:00Z', 'error': 'PRIVATE ERROR URL',
                'career_url': 'https://careers.example/jobs', 'coverage': 'automated',
                'country': 'India', 'date_support': 'published',
            }],
            'companies': [{
                'name': 'Example', 'career_url': 'https://careers.example/jobs',
                'coverage': 'automated', 'country': 'India', 'date_support': 'published',
                'status': 'ok', 'checked_at': '2026-11-03T10:00:00Z', 'count': 2,
                'matched': 1, 'error': 'PRIVATE ERROR URL', 'api_token': 'PRIVATE TOKEN',
            }],
            'last_scan': '2026-11-03T10:00:00Z', 'scanning': True,
            'settings': {'sources': [{'secret': 'PRIVATE CONFIG'}], 'profile': {'skills': ['PRIVATE PYTHON']}},
            'search_links': [{'name': 'Careers', 'url': 'https://careers.example/', 'description': 'Public link'}],
        }

    def test_snapshot_exports_only_public_discovery_fields(self):
        snapshot = build_snapshot(self.state, '2026-11-04T00:00:00Z')
        job = snapshot['jobs'][0]
        self.assertEqual(job['status'], 'new')
        self.assertEqual(job['notes'], '')
        self.assertIsNone(job['notified_at'])
        self.assertNotIn('source_id', job)
        self.assertNotIn('profile_matches', job)
        self.assertNotIn('profile_gaps', job)
        self.assertEqual(job['score'], 70)
        self.assertEqual(job['reasons'], [
            'Delhi NCR location', 'Entry-level title signal',
            'Experience not confirmed — review posting', 'Posting date available',
        ])
        self.assertEqual(snapshot['sources'], [{
            'name': 'Example', 'type': 'greenhouse', 'status': 'error', 'count': 2,
            'matched': 1, 'checked_at': '2026-11-03T10:00:00Z',
            'coverage': 'automated', 'country': 'India', 'date_support': 'published',
            'career_url': 'https://careers.example/jobs',
        }])
        self.assertEqual(snapshot['companies'], [{
            'name': 'Example', 'coverage': 'automated', 'country': 'India',
            'date_support': 'published', 'status': 'ok', 'checked_at': '2026-11-03T10:00:00Z',
            'count': 2, 'matched': 1, 'career_url': 'https://careers.example/jobs',
        }])
        self.assertNotIn('settings', snapshot)
        self.assertTrue(snapshot['read_only'])
        self.assertEqual(snapshot['automation']['mode'], 'published')
        encoded = json.dumps(snapshot)
        for private in ('PRIVATE NOTE', 'PRIVATE ERROR URL', 'PRIVATE TOKEN', 'PRIVATE CONFIG', 'PRIVATE PROFILE', 'PRIVATE PYTHON', 'PRIVATE KUBERNETES', 'internal-source-id'):
            self.assertNotIn(private, encoded)

    def test_only_active_jobs_with_recent_posting_dates_are_exported(self):
        jobs = [
            self.state['jobs'][0],
            {**self.state['jobs'][0], 'id': 'inactive', 'active': False},
            {**self.state['jobs'][0], 'id': 'old', 'posted_at': '2026-10-01'},
            {**self.state['jobs'][0], 'id': 'unknown-date', 'posted_at': ''},
        ]
        snapshot = build_snapshot({**self.state, 'jobs': jobs, 'settings': {'max_age_days': 14}}, '2026-11-04T00:00:00Z')
        self.assertEqual([job['id'] for job in snapshot['jobs']], ['public-id'])

    def test_rejects_non_https_job_and_search_urls(self):
        state = {
            **self.state,
            'jobs': self.state['jobs'] + [{**self.state['jobs'][0], 'id': 'insecure', 'url': 'http://jobs.example/1'}],
            'search_links': self.state['search_links'] + [{'name': 'bad', 'url': 'javascript:alert(1)'}],
        }
        snapshot = build_snapshot(state, '2026-11-04T00:00:00Z')
        self.assertEqual([job['id'] for job in snapshot['jobs']], ['public-id'])
        self.assertEqual(len(snapshot['search_links']), 1)

    def test_build_copies_assets_rewrites_paths_and_writes_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / 'source'
            assets.mkdir()
            (assets / 'index.html').write_text('<head><meta name="job-watch-mode" content="local"></head><link href="/static/style.css"><script src="/static/app.js"></script>', encoding='utf-8')
            (assets / 'app.js').write_text('asset', encoding='utf-8')
            output = root / 'site'
            publish(output, self.state, assets, '2026-11-04T00:00:00Z')
            html = (output / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="./static/style.css"', html)
            self.assertIn('src="./static/app.js"', html)
            self.assertIn('name="job-watch-mode" content="published"', html)
            self.assertEqual(html.count('name="job-watch-mode"'), 1)
            self.assertNotIn('content="local"', html)
            self.assertTrue((output / '.nojekyll').is_file())
            self.assertTrue((output / 'static' / 'app.js').is_file())
            public_state = json.loads((output / 'state.json').read_text(encoding='utf-8'))
            self.assertEqual(public_state['automation']['published_at'], '2026-11-04T00:00:00Z')
            self.assertNotIn('PRIVATE NOTE', (output / 'state.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
