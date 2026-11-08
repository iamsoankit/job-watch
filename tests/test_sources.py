import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import job_sources


class _Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class SourceTests(unittest.TestCase):
    def test_greenhouse_requests_content_and_normalizes_fields(self):
        payload = {"jobs": [{
            "id": 17,
            "title": "Junior Engineer",
            "location": {"name": "Delhi"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/17",
            "content": "Role details",
            "updated_at": "2026-03-01T10:00:00Z",
        }]}
        with patch.object(job_sources, "_request", return_value=payload) as request:
            jobs = list(job_sources.fetch_greenhouse({"board": "acme"}))
        self.assertEqual(request.call_args.args[0],
                         "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true")
        self.assertEqual(jobs, [{
            "title": "Junior Engineer", "location": "Delhi",
            "url": "https://boards.greenhouse.io/acme/jobs/17",
            "description": "Role details", "posted_at": "", "updated_at": "2026-03-01T10:00:00+00:00",
            "source_id": "17", "deadline": "",
        }])

    def test_greenhouse_uses_first_publication_not_update_date(self):
        payload = {'jobs': [{'id': 17, 'title': 'Engineer', 'absolute_url': 'https://job-boards.greenhouse.io/acme/jobs/17',
                             'first_published': '2026-10-10T10:00:00Z', 'updated_at': '2026-11-08T10:00:00Z'}]}
        with patch.object(job_sources, '_request', return_value=payload):
            job = list(job_sources.fetch_greenhouse({'board': 'acme'}))[0]
        self.assertEqual(job['posted_at'], '2026-10-10T10:00:00+00:00')
        self.assertNotEqual(job['posted_at'], job['updated_at'])

    def test_greenhouse_external_apply_url_uses_canonical_numeric_job_url(self):
        payload = {"jobs": [{
            "id": 165,
            "title": "Engineer",
            "absolute_url": "https://tower-research.com/?gh_jid=165",
        }, {
            "id": "52",
            "title": "Analyst",
            "absolute_url": "https://mongodb.com/careers/role/52",
        }]}
        with patch.object(job_sources, "_request", return_value=payload):
            jobs = list(job_sources.fetch_greenhouse({"board": "acme"}))
        self.assertEqual([job["url"] for job in jobs], [
            "https://job-boards.greenhouse.io/acme/jobs/165",
            "https://job-boards.greenhouse.io/acme/jobs/52",
        ])

    def test_greenhouse_external_url_requires_numeric_id(self):
        payload = {"jobs": [{
            "id": "req-52",
            "title": "Analyst",
            "absolute_url": "https://mongodb.com/careers/role/52",
        }]}
        with patch.object(job_sources, "_request", return_value=payload):
            with self.assertRaisesRegex(job_sources.SourceError, "non-greenhouse.io"):
                list(job_sources.fetch_greenhouse({"board": "acme"}))

    def test_lever_includes_description_and_created_at(self):
        payload = [{
            "id": "abc", "text": "Analyst", "categories": {"location": "Gurugram"},
            "hostedUrl": "https://jobs.lever.co/acme/abc", "description": "Details",
            "lists": [{"text": "Requirements: 0-2 years"}, {"content": "SQL experience"}],
            "additionalPlain": "Application notes", "createdAt": 1772366400000,
        }]
        with patch.object(job_sources, "_request", return_value=payload) as request:
            job = list(job_sources.fetch_lever({"company": "acme"}))[0]
        self.assertEqual(request.call_args.args[0], "https://api.lever.co/v0/postings/acme?mode=json")
        self.assertEqual(job["description"],
                         "Details\n\nRequirements: 0-2 years\n\nSQL experience\n\nApplication notes")
        self.assertEqual(job["posted_at"], "2026-03-01T12:00:00+00:00")

    def test_ashby_uses_public_board_api_and_fields(self):
        payload = {"jobs": [{
            "id": "j1", "title": "Data Scientist", "location": "Delhi NCR",
            "jobUrl": "https://jobs.ashbyhq.com/acme/j1", "descriptionPlain": "Details",
            "publishedAt": "2026-04-05T08:30:00Z",
        }]}
        with patch.object(job_sources, "_request", return_value=payload) as request:
            job = list(job_sources.fetch_ashby({"board": "acme"}))[0]
        self.assertEqual(request.call_args.args[0], "https://api.ashbyhq.com/posting-api/job-board/acme")
        self.assertEqual(job["description"], "Details")
        self.assertEqual(job["posted_at"], "2026-04-05T08:30:00+00:00")

    def test_workday_paginates_without_implicit_200_limit(self):
        all_postings = [{"title": f"Role {i}", "locationsText": "Delhi",
                         "externalPath": f"/job/India/REQ-{i}"} for i in range(205)]
        calls = []

        def request(url, data=None):
            calls.append((url, data))
            start = data["offset"]
            return {"total": len(all_postings), "jobPostings": all_postings[start:start + data["limit"]]}

        with patch.object(job_sources, "_request", side_effect=request):
            jobs = list(job_sources.fetch_workday({
                "base": "https://acme.wd3.myworkdayjobs.com", "tenant": "acme", "site": "Careers",
            }))
        self.assertEqual(len(jobs), 205)
        self.assertEqual([call[1]["offset"] for call in calls], list(range(0, 205, 20)))
        self.assertEqual(jobs[-1]["url"],
                         "https://acme.wd3.myworkdayjobs.com/Careers/job/India/REQ-204")
        self.assertEqual(jobs[0]["description"], "")

    def test_workday_max_jobs_reports_truncation(self):
        payload = {"total": 5, "jobPostings": [
            {"title": f"Role {i}", "externalPath": f"/job/REQ-{i}"} for i in range(2)
        ]}
        with patch.object(job_sources, "_request", return_value=payload):
            iterator = job_sources.fetch_workday({
                "base": "https://acme.wd3.myworkdayjobs.com", "tenant": "acme", "site": "Careers",
                "max_jobs": 2,
            })
            self.assertEqual(next(iterator)["title"], "Role 0")
            self.assertEqual(next(iterator)["title"], "Role 1")
            with self.assertRaisesRegex(job_sources.SourceTruncatedError, "max_jobs=2"):
                next(iterator)

    def test_workday_optional_detail_fetch(self):
        listing = {"total": 1, "jobPostings": [{
            "title": "Engineer", "externalPath": "/job/India/REQ-1", "jobReqId": "REQ-1",
        }]}
        with patch.object(job_sources, "_request", side_effect=[listing, {
            "jobPostingInfo": {"jobDescription": "Full description", "startDate": "2026-11-06", "country": {"descriptor": "India"}},
        }]) as request:
            job = list(job_sources.fetch_workday({
                "base": "https://acme.wd3.myworkdayjobs.com", "tenant": "acme", "site": "Careers",
                "fetch_details": True,
            }))[0]
        self.assertEqual(job["description"], "Full description")
        self.assertEqual(job["posted_at"], "2026-11-06T00:00:00")
        self.assertEqual(job["country"], "India")
        self.assertEqual(request.call_args_list[1].args[0],
                         "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers/job/India/REQ-1")

    def test_request_retries_429_and_5xx_with_bounded_attempts(self):
        headers = {}
        failures = [
            HTTPError("https://api.example.test", 429, "rate limited", headers, None),
            HTTPError("https://api.example.test", 503, "unavailable", headers, None),
        ]
        with patch.object(job_sources, "urlopen", side_effect=failures + [_Response({"ok": True})]) as opened, \
                patch.object(job_sources.time, "sleep") as sleep:
            self.assertEqual(job_sources._request("https://api.example.test"), {"ok": True})
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == job_sources.TIMEOUT for call in opened.call_args_list))

    def test_request_gives_up_after_bounded_retries(self):
        failure = HTTPError("https://api.example.test", 503, "unavailable", {}, None)
        with patch.object(job_sources, "urlopen", side_effect=failure) as opened, \
                patch.object(job_sources.time, "sleep"):
            with self.assertRaisesRegex(job_sources.SourceError, "after 3 attempts"):
                job_sources._request("https://api.example.test")
        self.assertEqual(opened.call_count, 3)

    def test_rejects_non_provider_posting_urls_and_invalid_segments(self):
        with patch.object(job_sources, "_request", return_value={"jobs": [{
            "title": "Bad", "absolute_url": "https://malicious.example/jobs/1",
        }]}):
            with self.assertRaisesRegex(job_sources.SourceError, "non-greenhouse.io"):
                list(job_sources.fetch_greenhouse({"board": "acme"}))
        with self.assertRaisesRegex(ValueError, "board"):
            list(job_sources.fetch_greenhouse({"board": "../not-a-board"}))

    def test_failed_source_error_is_compatible_with_urlerror(self):
        with patch.object(job_sources, "_request", side_effect=HTTPError(
                "https://api.example.test", 404, "not found", {}, None)):
            with self.assertRaises(job_sources.URLError):
                job_sources._request("https://api.example.test")


if __name__ == "__main__":
    unittest.main()
