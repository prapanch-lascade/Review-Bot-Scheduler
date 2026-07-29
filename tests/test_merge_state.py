import unittest

from merge_state import merge_states


class StateMergeTests(unittest.TestCase):
    def test_merge_preserves_reviews_and_latest_reply_progress(self):
        remote = {
            "last_review_id": "old",
            "last_checked": "2026-07-29T08:00:00+00:00",
            "reviews": {"r1": {"slack_ts": "1.0", "last_reply_ts": "2.0", "apple_reply_sent": False}},
        }
        local = {
            "last_review_id": "new",
            "last_checked": "2026-07-29T08:01:00+00:00",
            "reviews": {
                "r1": {"slack_ts": "1.0", "last_reply_ts": "3.0", "apple_reply_sent": True},
                "r2": {"slack_ts": "4.0", "last_reply_ts": None, "apple_reply_sent": False},
            },
        }

        merged = merge_states(remote, local)

        self.assertEqual(merged["last_review_id"], "new")
        self.assertEqual(merged["reviews"]["r1"]["last_reply_ts"], "3.0")
        self.assertTrue(merged["reviews"]["r1"]["apple_reply_sent"])
        self.assertIn("r2", merged["reviews"])


if __name__ == "__main__":
    unittest.main()
