import os, sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["PHOTO_LIBRARY_DATA"] = tempfile.mkdtemp(prefix="face-label-config-")
import ourtime_config


class FaceLabelDirectoryTests(unittest.TestCase):
    def test_complete_directory_wins_over_partial_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            partial = root / "partial"
            complete = root / "complete"
            partial.mkdir()
            complete.mkdir()
            (partial / "1.png").write_bytes(b"partial")
            for name in ("1.png", "4.png", "7.png", "8.png"):
                (complete / name).write_bytes(b"complete")
            chosen = ourtime_config.resolve_face_label_dir.__wrapped__ if hasattr(ourtime_config.resolve_face_label_dir, "__wrapped__") else None
            original = ourtime_config._face_label_candidates
            ourtime_config._face_label_candidates = lambda: [partial, complete]
            try:
                self.assertEqual(ourtime_config.resolve_face_label_dir(), complete)
            finally:
                ourtime_config._face_label_candidates = original


if __name__ == "__main__":
    unittest.main()
