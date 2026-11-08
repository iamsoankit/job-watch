import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import job_watch as app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cfg = json.loads((app.ROOT / 'config.json').read_text())
        (root / 'config.json').write_text(json.dumps(self.cfg))
        for name, value in [('CONFIG_PATH', root / 'config.json'), ('DB_PATH', root / 'db.sqlite3')]:
            p = patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={'Content-Type': 'application/json', **(headers or {})})
        response = conn.getresponse()
        data = response.read()
        result = response.status, data
        conn.close()
        return result

    def test_assets_and_state_are_served(self):
        for path in ['/', '/static/app.js', '/static/style.css', '/api/state']:
            self.assertEqual(self.request('GET', path)[0], 200)
        self.assertEqual(self.request('GET', '/config.json')[0], 404)
        self.assertEqual(self.request('GET', '/../job_watch.py')[0], 404)

    def test_settings_validation_and_roundtrip(self):
        self.assertEqual(self.request('PUT', '/api/settings', {**self.cfg, 'max_experience': 'wrong'})[0], 400)
        self.assertEqual(self.request('PUT', '/api/settings', {**self.cfg, 'max_experience': 1})[0], 200)
        self.assertEqual(json.loads(self.request('GET', '/api/state')[1])['settings']['max_experience'], 1)

    def test_cross_origin_writes_rejected(self):
        self.assertEqual(self.request('PUT', '/api/settings', self.cfg, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/scan', {}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_job_status_notes_validation_and_persistence(self):
        job = {'title': 'Junior Software Engineer', 'source': 'Test', 'location': 'Noida', 'url': 'https://example.com/1'}
        with app.db() as conn:
            conn.execute('INSERT INTO jobs(id,payload,first_seen,last_seen) VALUES (?,?,?,?)', ('test', json.dumps(job), app.now(), app.now()))
        for payload in [{'status': []}, {'status': 'bogus'}, {'notes': []}, {'id': 'other'}]:
            self.assertEqual(self.request('PATCH', '/api/jobs/test', payload)[0], 400)
        self.assertEqual(self.request('PATCH', '/api/jobs/test', {'status': 'applied', 'notes': 'Follow up'})[0], 200)
        result = json.loads(self.request('GET', '/api/state')[1])['jobs'][0]
        self.assertEqual((result['status'], result['notes']), ('applied', 'Follow up'))
        self.assertEqual(self.request('PATCH', '/api/jobs/missing', {'status': 'saved'})[0], 404)


if __name__ == '__main__':
    unittest.main()
