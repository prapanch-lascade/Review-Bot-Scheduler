import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.state_manager import load_state, save_state


class StateManagerTests(unittest.TestCase):
    def setUp(self):
        # The legacy-name tests must not inherit APP_SLUG from the runner env.
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        os.environ.pop("APP_SLUG", None)
        self.addCleanup(patcher.stop)

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

    def test_playstore_uses_separate_review_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            with patch("common.state_manager.STATE_DIR", state_dir):
                save_state("playstore", {"reviews": {}})
            self.assertTrue((state_dir / "playstore_reviews.json").exists())

    def test_app_slug_scopes_state_into_app_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            with patch("common.state_manager.STATE_DIR", state_dir), patch.dict(
                os.environ, {"APP_SLUG": "airlines70"}
            ):
                save_state("appstore", {"reviews": {}})
                loaded = load_state("appstore")

            self.assertTrue((state_dir / "airlines70" / "appstore.json").exists())
            self.assertFalse((state_dir / "appstore_reviews.json").exists())
            self.assertFalse(list((state_dir / "airlines70").glob("*.tmp")))
            self.assertEqual(loaded["reviews"], {})


if __name__ == "__main__":
    unittest.main()
