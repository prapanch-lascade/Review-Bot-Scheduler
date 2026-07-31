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

    def test_merge_preserves_google_reply_status(self):
        remote = {"reviews": {"r1": {"google_reply_sent": True, "last_reply_ts": "2.0"}}}
        local = {"reviews": {"r1": {"google_reply_sent": False, "last_reply_ts": "1.0"}}}

        merged = merge_states(remote, local)

        self.assertTrue(merged["reviews"]["r1"]["google_reply_sent"])
        self.assertEqual(merged["reviews"]["r1"]["last_reply_ts"], "2.0")

    def test_merge_keeps_reply_timestamp_and_hash_from_same_snapshot(self):
        remote = {
            "reviews": {
                "r1": {
                    "last_reply_ts": "3.0",
                    "last_sent_reply_hash": "hash-for-3",
                }
            }
        }
        local = {
            "reviews": {
                "r1": {
                    "last_reply_ts": "4.0",
                    "last_sent_reply_hash": "hash-for-4",
                }
            }
        }

        merged = merge_states(remote, local)

        self.assertEqual(merged["reviews"]["r1"]["last_reply_ts"], "4.0")
        self.assertEqual(merged["reviews"]["r1"]["last_sent_reply_hash"], "hash-for-4")


if __name__ == "__main__":
    unittest.main()
