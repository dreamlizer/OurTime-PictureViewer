import tempfile
import unittest
from pathlib import Path

from PIL import Image

from public_figures import PublicFigureShelf, match_asset_ids, scan_folder


def write_image(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), color).save(path, format="JPEG")


class PublicFigureFolderScanTests(unittest.TestCase):
    def test_scan_replaces_added_and_removed_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            references = root / "references"
            keep = references / "ada" / "keep.jpg"
            gone = references / "ada" / "gone.jpg"
            write_image(keep, (20, 40, 60))
            write_image(gone, (200, 20, 20))
            cache: dict = {}
            first = scan_folder(references, cache)
            self.assertEqual(2, len(first))
            gone.unlink()
            added = references / "lin" / "added.jpg"
            write_image(added, (10, 180, 30))
            second = scan_folder(references, {})
            names = {item["filename"] for item in second}
            self.assertEqual({"keep.jpg", "added.jpg"}, names)

    def test_missing_library_file_is_not_linked(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "references" / "ada" / "ada.jpg"
            write_image(image, (1, 2, 3))
            records = scan_folder(root / "references", {})
            sha = records[0]["sha256"]
            db_path = root / "library.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY, sha256 TEXT, excluded INTEGER)")
            connection.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, asset_id INTEGER, path TEXT, excluded INTEGER, exists_now INTEGER)")
            connection.execute("INSERT INTO assets(id, sha256, excluded) VALUES (1, ?, 0)", (sha,))
            connection.execute("INSERT INTO files(asset_id, path, excluded, exists_now) VALUES (1, 'missing.jpg', 0, 0)")
            connection.commit()
            connection.close()
            self.assertEqual({}, match_asset_ids(db_path, {sha}))
            shelf = PublicFigureShelf()
            shelf.configure(db_path, root / "public-faces.sqlite3", True)
            shelf.refresh()
            visible = shelf.visible_records()
            self.assertEqual(1, len(visible))
            self.assertIsNone(visible[0]["asset_id"])
            self.assertEqual((), shelf.asset_ids())


if __name__ == "__main__":
    unittest.main()
