"""Deterministic isolated tests for the standalone person-template builder."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "validation" / "work"
MODULE_PATH = ROOT / "tools" / "build_person_templates.py"
SPEC = importlib.util.spec_from_file_location("build_person_templates", MODULE_PATH)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def unit(vector: np.ndarray) -> np.ndarray:
    return (vector / np.linalg.norm(vector)).astype(np.float32)


def nearby(center: np.ndarray, count: int, seed: int) -> list[np.ndarray]:
    generator = np.random.default_rng(seed)
    return [unit(center + generator.normal(0, 0.015, 512)) for _ in range(count)]


def create_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE bundle_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE people(
            source_person_id INTEGER PRIMARY KEY,
            name TEXT,
            alias TEXT NOT NULL DEFAULT '',
            confirmed INTEGER NOT NULL DEFAULT 0,
            ignored INTEGER NOT NULL DEFAULT 0,
            suggested_source_person_id INTEGER
        );
        CREATE TABLE faces(
            source_face_id INTEGER PRIMARY KEY,
            photo_sha256 TEXT NOT NULL,
            source_person_id INTEGER NOT NULL,
            bbox TEXT NOT NULL,
            embedding BLOB NOT NULL,
            score REAL,
            reviewed INTEGER NOT NULL DEFAULT 0,
            ignored INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    connection.executemany(
        "INSERT INTO bundle_meta(key,value) VALUES (?,?)",
        [
            ("embedding_dimensions", "512"),
            ("embedding_model", "InsightFace buffalo_l"),
        ],
    )
    connection.executemany(
        "INSERT INTO people(source_person_id,name,confirmed,ignored) VALUES (?,?,?,?)",
        [
            (1, "甲", 1, 0),
            (2, "乙", 1, 0),
            (3, "少样本", 1, 0),
            (4, "路人", 0, 1),
            (5, None, 0, 0),
        ],
    )
    axes = [np.eye(1, 512, index, dtype=np.float32).reshape(-1) for index in range(6)]
    rows = []
    face_id = 1
    for vector in nearby(axes[0], 12, 1) + nearby(axes[1], 12, 2) + nearby(axes[2], 12, 3):
        rows.append((face_id, f"{face_id:064x}", 1, "[]", vector.tobytes(), 0.99, 1, 0))
        face_id += 1
    for vector in nearby(axes[3], 18, 4):
        rows.append((face_id, f"{face_id:064x}", 2, "[]", vector.tobytes(), 0.98, 1, 0))
        face_id += 1
    for vector in nearby(axes[4], 2, 5):
        rows.append((face_id, f"{face_id:064x}", 3, "[]", vector.tobytes(), 0.97, 1, 0))
        face_id += 1
    for vector in nearby(axes[5], 8, 6):
        rows.append((face_id, f"{face_id:064x}", 4, "[]", vector.tobytes(), 0.96, 1, 1))
        face_id += 1
    connection.executemany(
        """
        INSERT INTO faces(
            source_face_id,photo_sha256,source_person_id,bbox,embedding,
            score,reviewed,ignored
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


class PersonTemplateBuilderTests(unittest.TestCase):
    def test_standalone_tool_is_not_imported_by_business_flow(self) -> None:
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        database_source = (ROOT / "library_db.py").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for source in (app_source, database_source):
            self.assertNotIn("build_person_templates", source)
            self.assertNotIn("人物紧凑模板", source)
        self.assertNotIn("scikit-learn", requirements)
        self.assertNotIn("sklearn", requirements)

    def test_named_only_compact_output_and_source_unchanged(self) -> None:
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="person-templates-", dir=WORK) as folder:
            root = Path(folder)
            source = root / "source.sqlite3"
            output = root / "templates.sqlite3"
            report = root / "report.json"
            create_fixture(source)
            before = file_hash(source)
            result = builder.generate_templates(source, output, report, seed=12345)
            self.assertEqual(before, file_hash(source))
            self.assertTrue(result["source"]["unchanged"])
            self.assertEqual(3, result["output"]["people"])
            self.assertTrue(report.is_file())

            connection = sqlite3.connect(output)
            connection.row_factory = sqlite3.Row
            try:
                people = connection.execute(
                    "SELECT source_person_id,name,template_count FROM people ORDER BY source_person_id"
                ).fetchall()
                self.assertEqual([1, 2, 3], [row["source_person_id"] for row in people])
                self.assertNotIn("路人", [row["name"] for row in people])
                self.assertGreaterEqual(people[0]["template_count"], 3)
                self.assertLessEqual(people[0]["template_count"], 5)
                self.assertGreaterEqual(people[1]["template_count"], 3)
                self.assertLessEqual(people[1]["template_count"], 5)
                self.assertEqual(2, people[2]["template_count"])
                templates = connection.execute(
                    "SELECT embedding FROM templates"
                ).fetchall()
                self.assertEqual(
                    sum(row["template_count"] for row in people), len(templates)
                )
                for row in templates:
                    vector = np.frombuffer(row["embedding"], dtype=np.float32)
                    self.assertEqual((512,), vector.shape)
                    self.assertAlmostEqual(1.0, float(np.linalg.norm(vector)), places=5)
                self.assertEqual(
                    "ok", connection.execute("PRAGMA integrity_check").fetchone()[0]
                )
                self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
            finally:
                connection.close()

    def test_refuses_to_overwrite_output(self) -> None:
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="person-templates-", dir=WORK) as folder:
            root = Path(folder)
            source = root / "source.sqlite3"
            output = root / "templates.sqlite3"
            create_fixture(source)
            builder.generate_templates(source, output, seed=222)
            with self.assertRaises(FileExistsError):
                builder.generate_templates(source, output, seed=222)


if __name__ == "__main__":
    unittest.main()
