"""Copy named public-figure reference photos and their face embeddings.

The formal library and the source folder are opened read-only. Photos whose
names mark them as private inbox material stay in the source folder. Embeddings
go into a separate SQLite file, without private ids or absolute paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMAT_VERSION = 1
MODEL_NAME = "InsightFace buffalo_l"
EMBEDDING_BYTES = 2048
EXCLUDED_PREFIXES = ("incoming-", "najia-", "marco-polo", "040b")


def is_excluded_filename(filename: str) -> bool:
    return Path(filename).name.lower().startswith(EXCLUDED_PREFIXES)


def slug_from_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip())
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def folder_name(name: str, alias: str, filenames: list[str]) -> str:
    slug = slug_from_text(alias)
    if slug:
        return slug
    stems = [slug_from_text(Path(filename).stem) for filename in filenames]
    stems = [stem for stem in stems if stem]
    if stems:
        stems.sort(key=len)
        shortest = stems[0]
        if all(stem == shortest or stem.startswith(shortest + "-") for stem in stems):
            return shortest
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", name.strip()).strip(" .")
    return cleaned or "person"


def source_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def inbox_like(inbox: Path) -> str:
    return str(inbox).rstrip("\\/") + "\\%"


def load_inbox_faces(connection: sqlite3.Connection, inbox: Path) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT p.id AS person_id, p.name, p.alias, p.confirmed, p.ignored AS person_ignored,
               fa.id AS face_id, fa.embedding, fa.bbox, fa.score, fa.ignored AS face_ignored,
               f.path, a.sha256
        FROM files f
        JOIN assets a ON a.id = f.asset_id
        JOIN faces fa ON fa.asset_id = a.id
        JOIN people p ON p.id = fa.person_id
        WHERE f.exists_now = 1 AND f.path LIKE ?
        """,
        (inbox_like(inbox),),
    ).fetchall()


def select_people(rows: list[sqlite3.Row]) -> tuple[list[dict], int]:
    grouped: dict[int, dict] = {}
    for row in rows:
        person = grouped.setdefault(
            row["person_id"],
            {
                "person_id": row["person_id"],
                "name": (row["name"] or "").strip(),
                "alias": (row["alias"] or "").strip(),
                "confirmed": row["confirmed"],
                "ignored": row["person_ignored"],
                "faces": {},
                "filenames": set(),
            },
        )
        filename = Path(row["path"]).name
        person["filenames"].add(filename)
        face = person["faces"].setdefault(
            row["face_id"],
            {
                "embedding": bytes(row["embedding"]),
                "bbox": row["bbox"] or "",
                "score": row["score"],
                "ignored": row["face_ignored"],
                "files": [],
            },
        )
        face["files"].append({"filename": filename, "path": row["path"], "sha256": row["sha256"] or ""})

    selected = []
    rejected = 0
    for person in grouped.values():
        if person["confirmed"] != 1 or not person["name"] or person["ignored"]:
            rejected += 0
            continue
        if any(is_excluded_filename(filename) for filename in person["filenames"]):
            rejected += 1
            continue
        samples = []
        for face in person["faces"].values():
            if face["ignored"]:
                continue
            if len(face["embedding"]) != EMBEDDING_BYTES:
                raise RuntimeError("unexpected embedding length for " + person["name"])
            usable = [item for item in face["files"] if not is_excluded_filename(item["filename"])]
            if not usable:
                continue
            samples.append({**face, "file": usable[0]})
        if not samples:
            continue
        selected.append({**person, "samples": samples})
    selected.sort(key=lambda item: (item["name"], item["alias"], item["person_id"]))
    return selected, rejected


def assign_folders(people: list[dict]) -> None:
    used: set[str] = set()
    for person in people:
        filenames = sorted({sample["file"]["filename"] for sample in person["samples"]})
        base = folder_name(person["name"], person["alias"], filenames)
        slug = base
        suffix = 2
        while slug.casefold() in used:
            slug = f"{base}-{suffix}"
            suffix += 1
        used.add(slug.casefold())
        person["slug"] = slug


def file_sha256(path: Path, claimed: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", claimed or ""):
        return claimed.lower()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def outside_names(connection: sqlite3.Connection, inbox: Path, people: list[dict]) -> list[str]:
    if not people:
        return []
    ids = [person["person_id"] for person in people]
    marks = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT fa.person_id
        FROM faces fa
        JOIN files f ON f.asset_id = fa.asset_id AND f.exists_now = 1
        WHERE fa.person_id IN ({marks}) AND f.path NOT LIKE ?
        """,
        (*ids, inbox_like(inbox)),
    ).fetchall()
    outside_ids = {row["person_id"] for row in rows}
    return sorted(person["alias"] or person["name"] for person in people if person["person_id"] in outside_ids)


def unnamed_reference_files(connection: sqlite3.Connection, inbox: Path, copied: set[str]) -> list[str]:
    names = {
        Path(row["path"]).name
        for row in connection.execute(
            "SELECT path FROM files WHERE exists_now=1 AND path LIKE ?",
            (inbox_like(inbox),),
        )
    }
    return sorted(name for name in names - copied if not is_excluded_filename(name))


def source_file_count(inbox: Path) -> int:
    return sum(1 for path in inbox.iterdir() if path.is_file())


def create_database(path: Path, people: list[dict], records: list[dict]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        connection.execute("PRAGMA page_size=32768")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE pack_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE people (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                alias TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','retired'))
            );
            CREATE TABLE samples (
                id INTEGER PRIMARY KEY,
                person_id INTEGER NOT NULL REFERENCES people(id),
                filename TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                image_sha256 TEXT NOT NULL,
                embedding BLOB NOT NULL,
                score REAL,
                bbox TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'accepted' CHECK(status IN ('accepted','rejected'))
            );
            CREATE INDEX samples_person ON samples(person_id);
            CREATE UNIQUE INDEX samples_identity ON samples(person_id, image_sha256, bbox);
            """
        )
        meta = {
            "format_version": str(FORMAT_VERSION),
            "model": MODEL_NAME,
            "embedding_bytes": str(EMBEDDING_BYTES),
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "people": str(len(people)),
            "samples": str(len(records)),
            "policy": "named reference photos only; excluded inbox filenames and non-inbox photos are omitted",
        }
        connection.executemany("INSERT INTO pack_meta(key, value) VALUES (?, ?)", meta.items())
        connection.executemany(
            "INSERT INTO people(id, slug, name, alias) VALUES (?, ?, ?, ?)",
            [(index, person["slug"], person["name"], person["alias"]) for index, person in enumerate(people, start=1)],
        )
        connection.executemany(
            """
            INSERT INTO samples(person_id, filename, relative_path, image_sha256, embedding, score, bbox)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["person_id"],
                    record["filename"],
                    record["relative_path"],
                    record["sha256"],
                    record["embedding"],
                    record["score"],
                    record["bbox"],
                )
                for record in records
            ],
        )
        connection.commit()
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise
    connection.close()
    temporary.replace(path)


def verify_database(path: Path, people: int, samples: int) -> None:
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        stored_people = connection.execute("SELECT count(*) FROM people").fetchone()[0]
        row = connection.execute(
            "SELECT count(*), min(length(embedding)), max(length(embedding)) FROM samples"
        ).fetchone()
        leaked = connection.execute(
            """
            SELECT count(*) FROM samples
            WHERE relative_path LIKE '%:%'
               OR relative_path LIKE '%\\%'
               OR filename LIKE '%\\%'
               OR filename LIKE 'incoming-%'
               OR filename LIKE 'najia-%'
               OR filename LIKE 'marco-polo%'
               OR filename LIKE '040b%'
            """
        ).fetchone()[0]
    finally:
        connection.close()
    if integrity != "ok" or foreign_keys:
        raise RuntimeError("public face database failed integrity checks")
    if stored_people != people or row[0] != samples or row[1] != EMBEDDING_BYTES or row[2] != EMBEDDING_BYTES:
        raise RuntimeError("public face database counts do not match the copy")
    if leaked:
        raise RuntimeError("public face database contains a private path or excluded photo")


def build(library: Path, inbox: Path, dest: Path, rebuild: bool, dry_run: bool) -> dict:
    if not library.is_file():
        raise FileNotFoundError(library)
    inbox = inbox.resolve()
    dest = dest.resolve()
    if not inbox.is_dir():
        raise FileNotFoundError(inbox)
    if inbox == dest or dest.is_relative_to(inbox) or inbox.is_relative_to(dest):
        raise RuntimeError("destination overlaps the source folder")
    if not dest.is_relative_to((ROOT / "resources").resolve()):
        raise RuntimeError("destination must stay inside resources")
    before = source_file_count(inbox)
    connection = source_connection(library)
    try:
        selected, rejected = select_people(load_inbox_faces(connection, inbox))
        assign_folders(selected)
        also_private = outside_names(connection, inbox, selected)
        copied = {sample["file"]["filename"] for person in selected for sample in person["samples"]}
        unnamed = unnamed_reference_files(connection, inbox, copied)
    finally:
        connection.close()

    unique_paths = {sample["file"]["path"] for person in selected for sample in person["samples"]}
    total_bytes = sum(Path(path).stat().st_size for path in unique_paths)
    summary = {
        "people": len(selected),
        "samples": sum(len(person["samples"]) for person in selected),
        "files": len(copied),
        "bytes": total_bytes,
        "rejected_people": rejected,
        "also_in_private_library": also_private,
        "unnamed_files": len(unnamed),
        "unnamed": unnamed,
        "source_files_before": before,
    }
    if dry_run:
        summary["source_files_after"] = source_file_count(inbox)
        return summary
    if dest.exists():
        if not rebuild:
            raise FileExistsError(f"public face pack already exists: {dest}")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    try:
        references = dest / "references"
        records = []
        seen_targets: set[Path] = set()
        for index, person in enumerate(selected, start=1):
            folder = references / person["slug"]
            folder.mkdir(parents=True)
            for sample in person["samples"]:
                source = Path(sample["file"]["path"])
                filename = sample["file"]["filename"]
                target = folder / filename
                if target not in seen_targets:
                    shutil.copy2(source, target)
                    if target.stat().st_size != source.stat().st_size or not source.is_file():
                        raise RuntimeError("copy failed for " + filename)
                    seen_targets.add(target)
                records.append(
                    {
                        "person_id": index,
                        "filename": filename,
                        "relative_path": f"references/{person['slug']}/{filename}",
                        "sha256": file_sha256(target, sample["file"]["sha256"]),
                        "embedding": sample["embedding"],
                        "score": sample["score"],
                        "bbox": sample["bbox"],
                    }
                )
        database = dest / "public-faces.sqlite3"
        create_database(database, selected, records)
        verify_database(database, len(selected), len(records))
        after = source_file_count(inbox)
        if after != before:
            raise RuntimeError("source folder file count changed")
        report = {
            "format_version": FORMAT_VERSION,
            "people": len(selected),
            "samples": len(records),
            "files": len(copied),
            "bytes": total_bytes,
            "rejected_people_with_private_inbox_names": rejected,
            "reference_people_who_also_have_private_library_photos": also_private,
            "private_library_photos_copied": 0,
            "source_folder_deleted": False,
            "source_files_before": before,
            "source_files_after": after,
            "unnamed_reference_files_left_in_source": unnamed,
            "excluded_filename_prefixes": list(EXCLUDED_PREFIXES),
        }
        (dest / "SEED-REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        if dest.exists():
            shutil.rmtree(dest)
        raise
    summary["source_files_after"] = source_file_count(inbox)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy public-figure references and embeddings.")
    parser.add_argument("--library", type=Path, default=ROOT / "data" / "library.sqlite3")
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--dest", type=Path, default=ROOT / "resources" / "public-faces")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build(args.library, args.inbox, args.dest, args.rebuild, args.dry_run)
    public = {
        key: summary[key]
        for key in (
            "people",
            "samples",
            "files",
            "bytes",
            "rejected_people",
            "also_in_private_library",
            "unnamed_files",
            "source_files_before",
            "source_files_after",
        )
    }
    print(json.dumps(public, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
