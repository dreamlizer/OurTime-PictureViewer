import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "seed_public_faces.py"
SPEC = importlib.util.spec_from_file_location("seed_public_faces", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicFaceSeedTests(unittest.TestCase):
    def test_private_inbox_names_stay_out(self) -> None:
        self.assertTrue(MODULE.is_excluded_filename("incoming-duo-full.jpg"))
        self.assertTrue(MODULE.is_excluded_filename("najia-garden-in.jpg"))
        self.assertTrue(MODULE.is_excluded_filename("marco-polo-beijing-group.jpg"))
        self.assertTrue(MODULE.is_excluded_filename("040b622f9540684d769dc76e01abbc9-opq236663848.jpg"))
        self.assertFalse(MODULE.is_excluded_filename("jack-ma.jpg"))
        self.assertFalse(MODULE.is_excluded_filename("steve-jobs.jpg"))

    def test_folder_name_uses_alias_then_filename(self) -> None:
        self.assertEqual("jack-ma", MODULE.folder_name("马云", "Jack Ma", ["jack-ma-wef.jpg", "jack-ma.jpg"]))
        self.assertEqual("xi-jinping-brics", MODULE.folder_name("习近平", "", ["xi-jinping-brics.jpg"]))
        self.assertEqual("习近平", MODULE.folder_name("习近平", "", ["brics-2019.jpg", "r4-brics.jpg"]))


if __name__ == "__main__":
    unittest.main()
