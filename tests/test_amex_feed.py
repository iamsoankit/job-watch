import unittest
from unittest.mock import patch

import amex_feed
from job_sources import SourceError, SourceTruncatedError


class AmexFeedTests(unittest.TestCase):
    def test_fetches_india_pages_and_only_enriches_ncr(self):
        summaries = [
            {"Id": "260001", "Title": "Analyst", "PrimaryLocation": "Gurugram, HR, India",
             "PostedDate": "2026-11-05"},
            {"Id": "260002", "Title": "Associate", "PrimaryLocation": "Noida, UP, India",
             "PostedDate": "2026-11-06"},
            {"Id": "260003", "Title": "Engineer", "PrimaryLocation": "Bengaluru, India",
             "PostedDate": "2026-11-07"},
        ]

        def request(url, params):
            if url.endswith("recruitingCEJobRequisitions"):
                finder = params["finder"]
                offset = int(finder.split("offset=")[1].split(",")[0])
                self.assertIn('location="India"', finder)
                page = summaries[offset:offset + 2]
                return {"items": [{"TotalJobsCount": len(summaries), "requisitionList": page}]}
            job_id = params["finder"].split("Id=")[1].split(",")[0]
            return {"items": [{
                "Id": job_id,
                "ExternalPostedStartDate": "2026-11-05T15:21:02+00:00",
                "ExternalDescriptionStr": "<p>Build &amp; support</p><ul><li>SQL</li></ul>",
                "ExternalResponsibilitiesStr": "<p>Analyze data</p>",
                "ExternalQualificationsStr": "<p>0-2 years</p>",
            }]}

        with patch.object(amex_feed, "PAGE_SIZE", 2), \
                patch.object(amex_feed, "_api_get", side_effect=request) as api_get:
            jobs = list(amex_feed.fetch_amex({"type": "amex"}))

        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[0], {
            "title": "Analyst",
            "location": "Gurugram, HR, India",
            "url": "https://careers.americanexpress.com/en/sites/CX_1/job/260001/",
            "description": "Build & support\nSQL\n\nAnalyze data\n\n0-2 years",
            "posted_at": "2026-11-05T15:21:02+00:00",
            "source_id": "260001", "deadline": "",
        })
        self.assertEqual(jobs[2]["posted_at"], "2026-11-07T00:00:00")
        self.assertEqual(jobs[2]["description"], "")
        detail_calls = [call for call in api_get.call_args_list
                        if call.args[0].endswith("recruitingCEJobRequisitionDetails")]
        self.assertEqual(len(detail_calls), 2)
        list_calls = [call for call in api_get.call_args_list
                      if call.args[0].endswith("recruitingCEJobRequisitions")]
        self.assertEqual(len(list_calls), 2)
        self.assertTrue(all("egug.fa.us2.oraclecloud.com" in call.args[0] for call in api_get.call_args_list))

    def test_real_empty_search_is_not_an_api_error(self):
        with patch.object(amex_feed, "_api_get", return_value={"items": [{
            "TotalJobsCount": 0, "requisitionList": [],
        }]}):
            self.assertEqual(list(amex_feed.fetch_amex({})), [])

    def test_missing_search_result_or_incomplete_page_is_an_error(self):
        with patch.object(amex_feed, "_api_get", return_value={"items": []}):
            with self.assertRaisesRegex(SourceError, "no search result"):
                amex_feed._page(0)
        with patch.object(amex_feed, "_page", return_value=([], 5)):
            with self.assertRaisesRegex(SourceError, "stopped at 0"):
                list(amex_feed.fetch_amex({}))

    def test_missing_detail_is_an_error(self):
        with patch.object(amex_feed, "_api_get", return_value={"items": []}):
            with self.assertRaisesRegex(SourceError, "no job for 260010"):
                amex_feed._details("260010")

    def test_repeated_page_without_new_ids_is_an_error(self):
        page = ([{"Id": "260001", "Title": "Analyst", "PrimaryLocation": "Bengaluru, India"}], 2)
        with patch.object(amex_feed, "_page", side_effect=[page, page]):
            with self.assertRaisesRegex(SourceError, "repeated a page"):
                list(amex_feed.fetch_amex({}))

    def test_max_jobs_cap_reports_truncation(self):
        postings = [
            {"Id": "260001", "Title": "Analyst", "PrimaryLocation": "Bengaluru, India"},
            {"Id": "260002", "Title": "Associate", "PrimaryLocation": "Noida, India"},
        ]
        with patch.object(amex_feed, "_page", return_value=(postings, 2)):
            iterator = amex_feed.fetch_amex({"max_jobs": 1})
            self.assertEqual(next(iterator)["source_id"], "260001")
            with self.assertRaises(SourceTruncatedError):
                next(iterator)

    def test_missing_post_date_stays_blank(self):
        with patch.object(amex_feed, "_page", return_value=([{
            "Id": "260010", "Title": "Analyst", "PrimaryLocation": "Gurugram, India",
        }], 1)), patch.object(amex_feed, "_details", return_value={
            "ExternalDescriptionStr": "<p>Details</p>",
        }):
            job = list(amex_feed.fetch_amex({}))[0]
        self.assertEqual(job["posted_at"], "")
        self.assertEqual(job["description"], "Details")

    def test_rejects_invalid_site_and_max_jobs(self):
        with self.assertRaisesRegex(ValueError, "CX_1"):
            list(amex_feed.fetch_amex({"site": "other"}))
        for value in (0, -1, True, "3"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_jobs"):
                list(amex_feed.fetch_amex({"max_jobs": value}))


if __name__ == "__main__":
    unittest.main()
