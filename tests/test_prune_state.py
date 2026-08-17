import unittest
from datetime import datetime, timedelta, timezone

from prune_state import prune_inactive


NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


def days_ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def prune(reviews: dict, posted_ids=None) -> dict:
    state = {"reviews": reviews, "posted_ids": list(posted_ids or [])}
    return prune_inactive(state, NOW)


class PruneStateTests(unittest.TestCase):
    def test_open_review_kept_while_recent_dropped_when_old(self):
        state = prune(
            {
                "recent": {"posted_at": days_ago(5)},
                "old": {"posted_at": days_ago(40)},
            }
        )
        self.assertIn("recent", state["reviews"])
        self.assertNotIn("old", state["reviews"])
        # ids of pruned reviews are kept for dedup
        self.assertEqual(set(state["posted_ids"]), {"recent", "old"})

    def test_replied_review_kept_inside_edit_window_dropped_after(self):
        state = prune(
            {
                "fresh_reply": {"last_reply_ts": "1.0", "replied_at": days_ago(1)},
                "stale_reply": {"last_reply_ts": "1.0", "replied_at": days_ago(3)},
            }
        )
        self.assertIn("fresh_reply", state["reviews"])
        self.assertNotIn("stale_reply", state["reviews"])

    def test_disabled_thread_dropped_but_id_preserved(self):
        state = prune({"dead": {"slack_thread_disabled": True, "posted_at": days_ago(1)}})
        self.assertNotIn("dead", state["reviews"])
        self.assertIn("dead", state["posted_ids"])

    def test_missing_timestamp_is_kept(self):
        state = prune({"no_ts": {}})
        self.assertIn("no_ts", state["reviews"])

    def test_existing_posted_ids_preserved(self):
        state = prune({"r1": {"posted_at": days_ago(1)}}, posted_ids=["old1", "old2"])
        self.assertEqual(set(state["posted_ids"]), {"old1", "old2", "r1"})


if __name__ == "__main__":
    unittest.main()
