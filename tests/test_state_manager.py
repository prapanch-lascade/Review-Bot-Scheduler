import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.state_manager import load_state, save_state


class StateManagerTests(unittest.TestCase):
    def test_state_round_trip_and_atomic_file_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            with patch("common.state_manager.STATE_DIR", state_dir):
                state = {"last_review_id": "r1", "reviews": {"r1": {"slack_ts": "1.0"}}}
                save_state("appstore", state)
                loaded = load_state("appstore")

            self.assertEqual(loaded["last_review_id"], "r1")
            self.assertEqual(loaded["reviews"]["r1"]["slack_ts"], "1.0")
            self.assertFalse(list(state_dir.glob("*.tmp")))
            self.assertIsInstance(json.loads((state_dir / "appstore_reviews.json").read_text()), dict)

    def test_invalid_json_fails_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "appstore_reviews.json").write_text("{")
            with patch("common.state_manager.STATE_DIR", state_dir):
                with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                    load_state("appstore")


if __name__ == "__main__":
    unittest.main()
