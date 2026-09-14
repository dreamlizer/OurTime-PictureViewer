"""Export a portable, read-only bundle of face embeddings and people states.

The bundle includes only named people and user-marked passersby. It deliberately
omits unconfirmed candidate groups, source paths, EXIF, thumbnails, face crops
and original photos. It links every face to the source photo's SHA256 instead
of the source database's numeric asset ID, so a future import can reconnect it
after scanning the same photo files on another computer.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


BUNDLE_FORMAT_VERSION = 1
MODEL_NAME = "InsightFace buffalo_l"
INCLUDED_PERSON = "(coalesce(p.ignored,0)=1 OR (p.confirmed=1 AND coalesce(p.ignored,0)=0))"


def source_connection(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE bundle_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE people (
            source_person_id INTEGER PRIMARY KEY,
            name TEXT,
            alias TEXT NOT NULL,
            confirmed INTEGER NOT NULL,
            ignored INTEGER NOT NULL,
            suggested_source_person_id INTEGER
        );
        CREATE TABLE faces (
            source_face_id INTEGER PRIMARY KEY,
            photo_sha256 TEXT NOT NULL,
            source_person_id INTEGER NOT NULL REFERENCES people(source_person_id),
            bbox TEXT NOT NULL,
            embedding BLOB NOT NULL,
            score REAL,
            reviewed INTEGER NOT NULL,
            ignored INTEGER NOT NULL
        );
        CREATE INDEX faces_photo_sha256 ON faces(photo_sha256);
        CREATE INDEX faces_person ON faces(source_person_id);
        """
    )


def verify_bundle(path: Path, expected_people: int, expected_faces: int, expected_bytes: int) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        row = connection.execute(
            "SELECT count(*) AS faces, coalesce(sum(length(embedding)),0) AS bytes, "
            "min(length(embedding)) AS min_bytes, max(length(embedding)) AS max_bytes FROM faces"
        ).fetchone()
        people = connection.execute("SELECT count(*) FROM people").fetchone()[0]
    finally:
        connection.close()
    if integrity != "ok":
        raise RuntimeError(f"bundle integrity_check failed: {integrity}")
    if foreign_keys:
        raise RuntimeError("bundle foreign_key_check failed")
    if people != expected_people or row[0] != expected_faces or row[1] != expected_bytes:
        raise RuntimeError("bundle counts do not match the source snapshot")
    return {"people": people, "faces": row[0], "embedding_bytes": row[1], "min_embedding_bytes": row[2], "max_embedding_bytes": row[3]}


def export_bundle(source_path: Path, output_path: Path) -> dict[str, int]:
    if not source_path.is_file():
        raise FileNotFoundError(f"source database not found: {source_path}")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing bundle: {output_path}")
    temporary_path = output_path.with_name(output_path.name + ".tmp")
    if temporary_path.exists():
        raise FileExistsError(f"refusing to overwrite temporary bundle: {temporary_path}")

    temporary_created = False
    try:
        with source_connection(source_path) as source:
            source_people = source.execute(
                "SELECT count(*) FROM people p WHERE " + INCLUDED_PERSON + " "
                "AND EXISTS(SELECT 1 FROM faces f WHERE f.person_id=p.id)"
            ).fetchone()[0]
            source_face_summary = source.execute(
                "SELECT count(*) AS faces, coalesce(sum(length(f.embedding)),0) AS bytes "
                "FROM faces f JOIN people p ON p.id=f.person_id WHERE " + INCLUDED_PERSON
            ).fetchone()

            target = sqlite3.connect(temporary_path)
            temporary_created = True
            try:
                # A 32 KiB page keeps each 2 KiB embedding in its table leaf page.
                # With SQLite's default 4 KiB page, nearly every vector needs a
                # separate overflow page and roughly doubles the bundle size.
                target.execute("PRAGMA page_size=32768")
                target.execute("PRAGMA journal_mode=DELETE")
                target.execute("PRAGMA synchronous=FULL")
                create_schema(target)
                target.executemany(
                    "INSERT INTO people(source_person_id,name,alias,confirmed,ignored,suggested_source_person_id) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        (row["id"], row["name"], row["alias"], row["confirmed"], row["ignored"], row["suggested_person_id"])
                        for row in source.execute(
                            "SELECT p.id,p.name,p.alias,p.confirmed,p.ignored,"
                            "CASE WHEN EXISTS(SELECT 1 FROM people target WHERE target.id=p.suggested_person_id "
                            "AND (coalesce(target.ignored,0)=1 OR (target.confirmed=1 AND coalesce(target.ignored,0)=0)) "
                            "AND EXISTS(SELECT 1 FROM faces tf WHERE tf.person_id=target.id)) "
                            "THEN p.suggested_person_id ELSE NULL END AS suggested_person_id "
                            "FROM people p WHERE " + INCLUDED_PERSON + " "
                            "AND EXISTS(SELECT 1 FROM faces f WHERE f.person_id=p.id) ORDER BY p.id"
                        )
                    ),
                )
                target.executemany(
                    "INSERT INTO faces(source_face_id,photo_sha256,source_person_id,bbox,embedding,score,reviewed,ignored) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        (
                            row["id"], row["sha256"], row["person_id"], row["bbox"], row["embedding"],
                            row["score"], row["reviewed"], row["ignored"],
                        )
                        for row in source.execute(
                            "SELECT f.id,a.sha256,f.person_id,f.bbox,f.embedding,f.score,f.reviewed,f.ignored "
                            "FROM faces f JOIN assets a ON a.id=f.asset_id JOIN people p ON p.id=f.person_id "
                            "WHERE " + INCLUDED_PERSON + " ORDER BY f.id"
                        )
                    ),
                )
                metadata = {
                    "format_version": str(BUNDLE_FORMAT_VERSION),
                    "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "embedding_model": MODEL_NAME,
                    "embedding_dtype": "float32",
                    "embedding_dimensions": "512",
                    "photo_identity": "sha256",
                    "contains_original_photos": "false",
                    "contains_source_paths": "false",
                    "contains_face_crops": "false",
                    "scope": "named_people_and_user_marked_passersby_only",
                    "source_people_count": str(source_people),
                    "source_face_count": str(source_face_summary[0]),
                    "source_embedding_bytes": str(source_face_summary[1]),
                }
                target.executemany("INSERT INTO bundle_meta(key,value) VALUES (?,?)", metadata.items())
                target.commit()
            finally:
                target.close()

        verified = verify_bundle(
            temporary_path,
            expected_people=source_people,
            expected_faces=source_face_summary[0],
            expected_bytes=source_face_summary[1],
        )
        os.replace(temporary_path, output_path)
        temporary_created = False
        verified["file_bytes"] = output_path.stat().st_size
        return verified
    finally:
        if temporary_created and temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    default_name = f"人物特征与姓名-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    parser = argparse.ArgumentParser(description="Export people and face embeddings to one portable SQLite file.")
    parser.add_argument("--source", type=Path, default=root / "data" / "library.sqlite3")
    parser.add_argument("--output", type=Path, default=root / default_name)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else (root / args.output)
    result = export_bundle(args.source, output)
    print(f"BUNDLE_OK {output}")
    for key in ("people", "faces", "embedding_bytes", "min_embedding_bytes", "max_embedding_bytes", "file_bytes"):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"BUNDLE_FAILED {error}", file=sys.stderr)
        raise SystemExit(1)
