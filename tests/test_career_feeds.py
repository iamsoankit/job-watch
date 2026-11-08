import unittest
from unittest.mock import patch

import career_feeds
from job_sources import SourceError, SourceTruncatedError


def _item(title, *, city="Gurgaon", country="India", country_code="IN",
          posted="2026-11-06T12:00:00+0000", slug="123", description="Role details"):
    return {"data": {
        "title": title,
        "city": city,
        "state": "Haryana",
        "country": country,
        "country_code": country_code,
        "full_location": f"{city}, {country}",
        "slug": slug,
        "description": description,
        "posted_date": posted,
        "update_date": "2026-11-08T11:59:00Z",
    }}


class CareerFeedTests(unittest.TestCase):
    def test_spglobal_uses_official_india_filter_and_actual_posted_date(self):
        payload = {"totalCount": 2, "jobs": [
            _item("Apprentice", slug="329593", posted="2026-11-08T00:00:00+0000"),
            _item("Older Analyst", posted="2024-01-01T00:00:00+0000"),
        ]}
        with patch.object(career_feeds, "_request", return_value=payload) as request:
            jobs = list(career_feeds.fetch_spglobal({}))
        self.assertEqual(request.call_args.args[0],
                         "https://careers.spglobal.com/api/jobs?country=India&page=1")
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["url"], "https://careers.spglobal.com/jobs/329593?lang=en-us")
        self.assertEqual(jobs[0]["posted_at"], "2026-11-08T00:00:00+00:00")
        self.assertEqual(jobs[0]["date_basis"], "posted_date")
        self.assertEqual(jobs[1]["posted_at"], "2024-01-01T00:00:00+00:00")

    def test_zs_returns_old_and_unknown_dates_for_core_freshness_policy(self):
        payload = {"totalCount": 3, "jobs": [
            _item("Associate", city="Noida"),
            _item("Old role", posted="2020-01-01T00:00:00+0000"),
            _item("Unknown date", posted="", slug="unknown"),
        ]}
        with patch.object(career_feeds, "_request", return_value=payload) as request:
            jobs = list(career_feeds.fetch_zs({}))
        self.assertEqual(request.call_args.args[0],
                         "https://jobs.zs.com/api/jobs?country=India&page=1")
        self.assertEqual([job["title"] for job in jobs], ["Associate", "Old role", "Unknown date"])
        self.assertEqual(jobs[0]["date_basis"], "posted_date")
        self.assertEqual(jobs[1]["posted_at"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(jobs[2]["posted_at"], "")
        self.assertNotIn("date_basis", jobs[2])

    def test_both_sources_filter_to_india_ncr_and_keep_description(self):
        for source, fetch in (("spglobal", career_feeds.fetch_spglobal),
                              ("zs", career_feeds.fetch_zs)):
            payload = {"totalCount": 3, "jobs": [
                _item("NCR role", description="Full official description"),
                _item("Pune role", city="Pune"),
                _item("US Delhi role", city="Delhi", country="United States", country_code="US"),
            ]}
            with self.subTest(source=source), \
                    patch.object(career_feeds, "_request", return_value=payload):
                jobs = list(fetch({}))
            self.assertEqual([job["title"] for job in jobs], ["NCR role"])
            self.assertEqual(jobs[0]["description"], "Full official description")

    def test_paginates_india_jobs(self):
        pages = [
            {"totalCount": 11, "jobs": [_item(f"Pune {i}", city="Pune") for i in range(10)]},
            {"totalCount": 11, "jobs": [_item("Noida associate", city="Noida", slug="req456")]},
        ]
        with patch.object(career_feeds, "_request", side_effect=pages) as request:
            jobs = list(career_feeds.fetch_zs({}))
        self.assertEqual([call.args[0] for call in request.call_args_list], [
            "https://jobs.zs.com/api/jobs?country=India&page=1",
            "https://jobs.zs.com/api/jobs?country=India&page=2",
        ])
        self.assertEqual([job["title"] for job in jobs], ["Noida associate"])

    def test_additional_ncr_location_survives_normal_matching(self):
        from job_watch import evaluate

        for fetch in (career_feeds.fetch_spglobal, career_feeds.fetch_zs):
            item = _item("Software Engineer", city="Pune")
            item["data"]["additional_locations"] = [
                {"city": "Noida", "state": "Uttar Pradesh", "country": "India"},
                {"city": "Noida", "state": "Uttar Pradesh", "country": "India"},
            ]
            payload = {"totalCount": 1, "jobs": [item]}
            with self.subTest(source=fetch.__name__), \
                    patch.object(career_feeds, "_request", return_value=payload):
                jobs = list(fetch({}))
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0]["location"], "Pune, India; Noida, Uttar Pradesh, India")
                self.assertIsNotNone(evaluate(jobs[0], {
                    "include_title_keywords": ["software engineer"],
                    "exclude_title_keywords": [], "locations": ["noida"], "max_age_days": 0,
                }))

    def test_bad_total_and_incomplete_page_are_errors(self):
        with patch.object(career_feeds, "_request", return_value={"jobs": []}):
            with self.assertRaisesRegex(SourceError, "totalCount"):
                list(career_feeds.fetch_zs({}))
        with patch.object(career_feeds, "_request", return_value={"totalCount": 11, "jobs": []}):
            with self.assertRaisesRegex(SourceError, "stopped"):
                list(career_feeds.fetch_spglobal({}))

    def test_cap_reports_truncation_and_source_errors_propagate(self):
        payload = {"totalCount": 3, "jobs": [_item(f"Role {i}") for i in range(3)]}
        with patch.object(career_feeds, "_request", return_value=payload):
            iterator = career_feeds.fetch_zs({"max_jobs": 2})
            next(iterator)
            next(iterator)
            with self.assertRaisesRegex(SourceTruncatedError, "max_jobs=2"):
                next(iterator)
        with patch.object(career_feeds, "_request", side_effect=SourceError("unavailable")):
            with self.assertRaisesRegex(SourceError, "unavailable"):
                list(career_feeds.fetch_spglobal({}))


if __name__ == "__main__":
    unittest.main()
