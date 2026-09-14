"""Build a standalone compact gallery from existing InsightFace embeddings.

This tool is deliberately isolated from the photo-library application. It opens
the source SQLite database read-only and creates a new SQLite file containing
only confirmed named people and one to five normalized templates per person.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

from sklearn import __version__ as sklearn_version
from sklearn.cluster import KMeans, MiniBatchKMeans
from sklearn.exceptions import ConvergenceWarning


FORMAT_VERSION = 1
ALGORITHM_NAME = "trimmed normalized k-means"
DEFAULT_SEED = 20260914
DEFAULT_MIN_TEMPLATES = 3
DEFAULT_MAX_TEMPLATES = 5
DEFAULT_TRIM_FRACTION = 0.05
DEFAULT_HOLDOUT_FRACTION = 0.20
DEFAULT_MAX_HOLDOUT_PER_PERSON = 200
DEFAULT_MATCH_THRESHOLD = 0.52
DEFAULT_MATCH_MARGIN = 0.08
REDUNDANT_CENTER_SIMILARITY = 0.9995


@dataclass
class SourceLayout:
    person_id: str
    face_id: str
    metadata_table: str | None


@dataclass
class Candidate:
    requested_k: int
    centers: np.ndarray
    mean_coverage: float
    p10_coverage: float


@dataclass
class PersonResult:
    person_id: int
    name: str
    alias: str
    source_face_count: int
    usable_face_count: int
    invalid_face_count: int
    template_rows: list[dict]
    validation_vectors: np.ndarray
    validation_person_ids: np.ndarray
    validation_face_ids: np.ndarray
    validation_centers: np.ndarray


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def detect_layout(connection: sqlite3.Connection) -> SourceLayout:
    people_columns = table_columns(connection, "people")
    face_columns = table_columns(connection, "faces")
    if {"source_person_id", "name", "confirmed", "ignored"} <= people_columns and {
        "source_face_id",
        "source_person_id",
        "embedding",
    } <= face_columns:
        metadata = "bundle_meta" if table_columns(connection, "bundle_meta") else None
        return SourceLayout("source_person_id", "source_face_id", metadata)
    if {"id", "name", "confirmed", "ignored"} <= people_columns and {
        "id",
        "person_id",
        "embedding",
    } <= face_columns:
        return SourceLayout("id", "id", None)
    raise RuntimeError("source database does not contain a supported people/faces schema")


def source_metadata(connection: sqlite3.Connection, layout: SourceLayout) -> dict[str, str]:
    if not layout.metadata_table:
        return {}
    return {
        str(row["key"]): str(row["value"])
        for row in connection.execute(
            f"SELECT key,value FROM {layout.metadata_table} ORDER BY key"
        )
    }


def named_people_query(layout: SourceLayout) -> str:
    return (
        f"SELECT p.{layout.person_id} AS person_id,p.name,"
        "coalesce(p.alias,'') AS alias,count(f.embedding) AS face_count "
        "FROM people p JOIN faces f ON f."
        + ("source_person_id" if layout.person_id == "source_person_id" else "person_id")
        + f"=p.{layout.person_id} "
        "WHERE p.confirmed=1 AND coalesce(p.ignored,0)=0 "
        "AND trim(coalesce(p.name,''))<>'' "
        "AND coalesce(f.ignored,0)=0 AND f.embedding IS NOT NULL "
        f"GROUP BY p.{layout.person_id},p.name,p.alias ORDER BY p.{layout.person_id}"
    )


def face_rows_query(layout: SourceLayout) -> str:
    person_column = (
        "source_person_id" if layout.person_id == "source_person_id" else "person_id"
    )
    return (
        f"SELECT {layout.face_id} AS face_id,embedding FROM faces "
        f"WHERE {person_column}=? AND coalesce(ignored,0)=0 "
        f"AND embedding IS NOT NULL ORDER BY {layout.face_id}"
    )


def normalize_embeddings(
    rows: list[sqlite3.Row], expected_dim: int
) -> tuple[np.ndarray, np.ndarray, int]:
    face_ids: list[int] = []
    vectors: list[np.ndarray] = []
    invalid = 0
    expected_bytes = expected_dim * np.dtype(np.float32).itemsize
    for row in rows:
        blob = row["embedding"]
        if blob is None or len(blob) != expected_bytes:
            invalid += 1
            continue
        vector = np.frombuffer(blob, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(vector).all() or not math.isfinite(norm) or norm <= 1e-12:
            invalid += 1
            continue
        vectors.append((vector / norm).astype(np.float32, copy=False))
        face_ids.append(int(row["face_id"]))
    if not vectors:
        return (
            np.empty((0, expected_dim), dtype=np.float32),
            np.empty((0,), dtype=np.int64),
            invalid,
        )
    return np.stack(vectors), np.asarray(face_ids, dtype=np.int64), invalid


def fit_model(vectors: np.ndarray, clusters: int, seed: int):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        if len(vectors) > 5000:
            model = MiniBatchKMeans(
                n_clusters=clusters,
                random_state=seed,
                n_init=5,
                batch_size=min(2048, len(vectors)),
                max_iter=300,
                max_no_improvement=30,
                reassignment_ratio=0.0,
            )
        else:
            model = KMeans(
                n_clusters=clusters,
                random_state=seed,
                n_init=10,
                max_iter=300,
                algorithm="lloyd",
            )
        model.fit(vectors)
    centers = np.asarray(model.cluster_centers_, dtype=np.float32)
    norms = np.linalg.norm(centers, axis=1, keepdims=True)
    return centers / np.maximum(norms, 1e-12)


def collapse_redundant_centers(centers: np.ndarray) -> np.ndarray:
    kept: list[np.ndarray] = []
    for center in centers:
        if kept and max(float(existing @ center) for existing in kept) >= REDUNDANT_CENTER_SIMILARITY:
            continue
        kept.append(center)
    return np.stack(kept) if kept else centers[:1]


def fit_trimmed_centers(
    vectors: np.ndarray, clusters: int, seed: int, trim_fraction: float
) -> np.ndarray:
    clusters = max(1, min(int(clusters), len(vectors)))
    centers = fit_model(vectors, clusters, seed)
    similarities = vectors @ centers.T
    labels = np.argmax(similarities, axis=1)
    keep = np.ones(len(vectors), dtype=bool)
    for cluster in range(len(centers)):
        members = np.flatnonzero(labels == cluster)
        if len(members) < 20:
            continue
        assigned_similarity = similarities[members, cluster]
        cutoff = float(np.quantile(assigned_similarity, trim_fraction))
        keep[members[assigned_similarity < cutoff]] = False
    trimmed = vectors[keep]
    if len(trimmed) >= clusters:
        centers = fit_model(trimmed, clusters, seed + 104729)
    return collapse_redundant_centers(centers).astype(np.float32, copy=False)


def deterministic_split(
    vectors: np.ndarray,
    face_ids: np.ndarray,
    person_id: int,
    seed: int,
    holdout_fraction: float,
    max_holdout: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if len(vectors) < 10:
        return vectors, face_ids, vectors[:0], face_ids[:0]
    count = min(max(2, int(round(len(vectors) * holdout_fraction))), max_holdout)
    if len(vectors) - count < 3:
        return vectors, face_ids, vectors[:0], face_ids[:0]
    generator = np.random.default_rng(seed ^ int(person_id))
    order = generator.permutation(len(vectors))
    holdout_indices = order[:count]
    train_indices = order[count:]
    return (
        vectors[train_indices],
        face_ids[train_indices],
        vectors[holdout_indices],
        face_ids[holdout_indices],
    )


def candidate_template_counts(sample_count: int, minimum: int, maximum: int) -> list[int]:
    if sample_count <= 0:
        return []
    if sample_count < minimum:
        return [sample_count]
    return list(range(minimum, min(maximum, sample_count) + 1))


def select_candidate(candidates: list[Candidate]) -> Candidate:
    best_p10 = max(item.p10_coverage for item in candidates)
    best_mean = max(item.mean_coverage for item in candidates)
    eligible = [
        item
        for item in candidates
        if item.p10_coverage >= best_p10 - 0.01
        and item.mean_coverage >= best_mean - 0.005
    ]
    if eligible:
        return min(eligible, key=lambda item: (len(item.centers), item.requested_k))
    return max(
        candidates,
        key=lambda item: (
            item.p10_coverage,
            item.mean_coverage,
            -len(item.centers),
        ),
    )


def build_person_result(
    connection: sqlite3.Connection,
    layout: SourceLayout,
    person_row: sqlite3.Row,
    expected_dim: int,
    seed: int,
    minimum: int,
    maximum: int,
    trim_fraction: float,
    holdout_fraction: float,
    max_holdout: int,
) -> PersonResult | None:
    person_id = int(person_row["person_id"])
    raw_rows = connection.execute(face_rows_query(layout), (person_id,)).fetchall()
    vectors, face_ids, invalid = normalize_embeddings(raw_rows, expected_dim)
    if not len(vectors):
        return None
    train, _train_ids, holdout, holdout_ids = deterministic_split(
        vectors,
        face_ids,
        person_id,
        seed,
        holdout_fraction,
        max_holdout,
    )
    candidates: list[Candidate] = []
    evaluation = holdout if len(holdout) else train
    for requested_k in candidate_template_counts(len(train), minimum, maximum):
        centers = fit_trimmed_centers(
            train,
            requested_k,
            seed ^ person_id ^ (requested_k * 7919),
            trim_fraction,
        )
        coverage = np.max(evaluation @ centers.T, axis=1)
        candidates.append(
            Candidate(
                requested_k=requested_k,
                centers=centers,
                mean_coverage=float(np.mean(coverage)),
                p10_coverage=float(np.quantile(coverage, 0.10)),
            )
        )
    selected = select_candidate(candidates)
    final_centers = fit_trimmed_centers(
        vectors,
        selected.requested_k,
        seed ^ person_id ^ 999983,
        trim_fraction,
    )
    similarities = vectors @ final_centers.T
    assignments = np.argmax(similarities, axis=1)
    template_rows: list[dict] = []
    for slot, center in enumerate(final_centers, start=1):
        members = np.flatnonzero(assignments == slot - 1)
        if not len(members):
            continue
        member_similarity = similarities[members, slot - 1]
        representative_index = int(members[int(np.argmax(member_similarity))])
        template_rows.append(
            {
                "slot": slot,
                "embedding": center.astype(np.float32, copy=False).tobytes(),
                "sample_count": int(len(members)),
                "representative_source_face_id": int(face_ids[representative_index]),
                "mean_similarity": float(np.mean(member_similarity)),
                "min_similarity": float(np.min(member_similarity)),
            }
        )
    validation_people = np.full(len(holdout), person_id, dtype=np.int64)
    return PersonResult(
        person_id=person_id,
        name=str(person_row["name"]),
        alias=str(person_row["alias"] or ""),
        source_face_count=len(raw_rows),
        usable_face_count=len(vectors),
        invalid_face_count=invalid,
        template_rows=template_rows,
        validation_vectors=holdout,
        validation_person_ids=validation_people,
        validation_face_ids=holdout_ids,
        validation_centers=selected.centers,
    )


def evaluate_holdout(
    results: list[PersonResult], threshold: float, margin_threshold: float
) -> dict:
    template_vectors: list[np.ndarray] = []
    template_people: list[int] = []
    validation_vectors: list[np.ndarray] = []
    validation_people: list[np.ndarray] = []
    validation_face_ids: list[np.ndarray] = []
    for result in results:
        template_vectors.extend(result.validation_centers)
        template_people.extend([result.person_id] * len(result.validation_centers))
        if len(result.validation_vectors):
            validation_vectors.append(result.validation_vectors)
            validation_people.append(result.validation_person_ids)
            validation_face_ids.append(result.validation_face_ids)
    if not validation_vectors:
        return {"faces": 0}
    templates = np.stack(template_vectors).astype(np.float32, copy=False)
    template_people_array = np.asarray(template_people, dtype=np.int64)
    order = np.argsort(template_people_array, kind="stable")
    templates = templates[order]
    sorted_people = template_people_array[order]
    unique_people, starts = np.unique(sorted_people, return_index=True)
    vectors = np.concatenate(validation_vectors)
    true_people = np.concatenate(validation_people)
    face_ids = np.concatenate(validation_face_ids)
    people_to_slot = {int(person_id): index for index, person_id in enumerate(unique_people)}

    top1_correct = 0
    routed_correct = 0
    routed_wrong = 0
    own_above_threshold = 0
    score_values: list[np.ndarray] = []
    margin_values: list[np.ndarray] = []
    per_person: dict[int, list[int]] = {}
    confusion_counts: dict[tuple[int, int], int] = {}
    confusion_examples: dict[tuple[int, int], list[dict]] = {}
    for offset in range(0, len(vectors), 512):
        chunk = vectors[offset : offset + 512]
        truth = true_people[offset : offset + len(chunk)]
        chunk_face_ids = face_ids[offset : offset + len(chunk)]
        template_scores = chunk @ templates.T
        person_scores = np.maximum.reduceat(template_scores, starts, axis=1)
        best_slots = np.argmax(person_scores, axis=1)
        best_scores = person_scores[np.arange(len(chunk)), best_slots]
        if person_scores.shape[1] > 1:
            second_scores = np.partition(person_scores, -2, axis=1)[:, -2]
        else:
            second_scores = np.full(len(chunk), -1.0, dtype=np.float32)
        best_people = unique_people[best_slots]
        margins = best_scores - second_scores
        correct = best_people == truth
        routed = (best_scores >= threshold) & (margins >= margin_threshold)
        top1_correct += int(np.sum(correct))
        routed_correct += int(np.sum(routed & correct))
        routed_wrong += int(np.sum(routed & ~correct))
        own_scores = np.asarray(
            [
                person_scores[index, people_to_slot[int(person_id)]]
                for index, person_id in enumerate(truth)
            ],
            dtype=np.float32,
        )
        own_above_threshold += int(np.sum(own_scores >= threshold))
        score_values.append(best_scores)
        margin_values.append(margins)
        for person_id, is_correct, is_routed, is_wrong in zip(
            truth, correct, routed, routed & ~correct, strict=True
        ):
            totals = per_person.setdefault(int(person_id), [0, 0, 0, 0])
            totals[0] += 1
            totals[1] += int(is_correct)
            totals[2] += int(is_routed and is_correct)
            totals[3] += int(is_wrong)
        for face_id, true_person, predicted_person, is_wrong, best_score, margin, own_score in zip(
            chunk_face_ids,
            truth,
            best_people,
            routed & ~correct,
            best_scores,
            margins,
            own_scores,
            strict=True,
        ):
            if not is_wrong:
                continue
            key = (int(true_person), int(predicted_person))
            confusion_counts[key] = confusion_counts.get(key, 0) + 1
            confusion_examples.setdefault(key, []).append(
                {
                    "source_face_id": int(face_id),
                    "best_score": float(best_score),
                    "own_score": float(own_score),
                    "margin": float(margin),
                }
            )

    total = len(vectors)
    macro_top1 = float(
        np.mean([correct / count for count, correct, _, _ in per_person.values()])
    )
    all_scores = np.concatenate(score_values)
    all_margins = np.concatenate(margin_values)
    return {
        "faces": total,
        "people": len(per_person),
        "threshold": threshold,
        "margin_threshold": margin_threshold,
        "top1_correct": top1_correct,
        "top1_accuracy": top1_correct / total,
        "macro_top1_accuracy": macro_top1,
        "routed_correct": routed_correct,
        "routed_wrong": routed_wrong,
        "routed_correct_rate": routed_correct / total,
        "routed_wrong_rate": routed_wrong / total,
        "abstained": total - routed_correct - routed_wrong,
        "own_template_above_threshold": own_above_threshold,
        "own_template_above_threshold_rate": own_above_threshold / total,
        "best_score_p05": float(np.quantile(all_scores, 0.05)),
        "best_score_median": float(np.median(all_scores)),
        "margin_p05": float(np.quantile(all_margins, 0.05)),
        "margin_median": float(np.median(all_margins)),
        "routed_wrong_pairs": [
            {
                "source_person_id": source,
                "predicted_person_id": target,
                "faces": count,
                "examples": confusion_examples[(source, target)][:20],
            }
            for (source, target), count in sorted(
                confusion_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }


def create_output_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE people (
            source_person_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            alias TEXT NOT NULL,
            source_face_count INTEGER NOT NULL,
            usable_face_count INTEGER NOT NULL,
            invalid_face_count INTEGER NOT NULL,
            template_count INTEGER NOT NULL,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE templates (
            id INTEGER PRIMARY KEY,
            source_person_id INTEGER NOT NULL REFERENCES people(source_person_id),
            slot INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            sample_count INTEGER NOT NULL,
            representative_source_face_id INTEGER NOT NULL,
            mean_similarity REAL NOT NULL,
            min_similarity REAL NOT NULL,
            UNIQUE(source_person_id,slot)
        );
        CREATE INDEX templates_person ON templates(source_person_id);
        """
    )


def verify_output(path: Path, expected_dim: int) -> dict:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        people = connection.execute("SELECT count(*) FROM people").fetchone()[0]
        templates = connection.execute("SELECT count(*) FROM templates").fetchone()[0]
        invalid_people = connection.execute(
            "SELECT count(*) FROM people WHERE template_count<1 OR template_count>5"
        ).fetchone()[0]
        vectors = [
            np.frombuffer(row["embedding"], dtype=np.float32)
            for row in connection.execute("SELECT embedding FROM templates")
        ]
    finally:
        connection.close()
    invalid_vectors = sum(
        vector.shape != (expected_dim,)
        or not np.isfinite(vector).all()
        or abs(float(np.linalg.norm(vector)) - 1.0) > 1e-5
        for vector in vectors
    )
    if integrity != "ok":
        raise RuntimeError(f"output integrity_check failed: {integrity}")
    if foreign_keys:
        raise RuntimeError("output foreign_key_check failed")
    if invalid_people or invalid_vectors:
        raise RuntimeError("output contains invalid template counts or vectors")
    return {
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "people": people,
        "templates": templates,
        "invalid_people": invalid_people,
        "invalid_vectors": invalid_vectors,
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"refusing to overwrite temporary report: {temporary}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def same_path(left: Path, right: Path) -> bool:
    a = left.resolve()
    b = right.resolve()
    if os.path.normcase(str(a)) == os.path.normcase(str(b)):
        return True
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def ensure_distinct_template_paths(
    source_path: Path, output_path: Path, report_path: Path | None
) -> Path | None:
    output_tmp = output_path.with_name(output_path.name + ".tmp")
    paths = [source_path, output_path, output_tmp]
    resolved_report = None
    if report_path is not None:
        resolved_report = report_path.resolve()
        paths.extend([resolved_report, resolved_report.with_name(resolved_report.name + ".tmp")])
    for index, first in enumerate(paths):
        for second in paths[index + 1 :]:
            if same_path(first, second):
                raise ValueError(f"source, output, report and temp files must be different: {first} vs {second}")
    if output_tmp.exists():
        raise FileExistsError(f"refusing to overwrite temporary output: {output_tmp}")
    if resolved_report is not None:
        report_tmp = resolved_report.with_name(resolved_report.name + ".tmp")
        if report_tmp.exists():
            raise FileExistsError(f"refusing to overwrite temporary report: {report_tmp}")
        if resolved_report.exists() and (
            same_path(resolved_report, source_path) or same_path(resolved_report, output_path)
        ):
            raise ValueError("report path must not overwrite source or output")
    return resolved_report


def generate_templates(
    source_path: Path,
    output_path: Path,
    report_path: Path | None = None,
    *,
    seed: int = DEFAULT_SEED,
    minimum: int = DEFAULT_MIN_TEMPLATES,
    maximum: int = DEFAULT_MAX_TEMPLATES,
    trim_fraction: float = DEFAULT_TRIM_FRACTION,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    max_holdout: int = DEFAULT_MAX_HOLDOUT_PER_PERSON,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    margin_threshold: float = DEFAULT_MATCH_MARGIN,
) -> dict:
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    report_path = ensure_distinct_template_paths(source_path, output_path, report_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"source database not found: {source_path}")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_path}")
    if minimum < 1 or maximum < minimum or maximum > 5:
        raise ValueError("template range must satisfy 1 <= minimum <= maximum <= 5")
    if not 0 <= trim_fraction < 0.25:
        raise ValueError("trim_fraction must be in [0,0.25)")

    source_hash_before = sha256_file(source_path)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary_path = output_path.with_name(output_path.name + ".tmp")
    if temporary_path.exists():
        raise FileExistsError(f"refusing to overwrite temporary output: {temporary_path}")
    temporary_created = False
    results: list[PersonResult] = []
    source_meta: dict[str, str] = {}
    expected_dim = 512
    try:
        source = readonly_connection(source_path)
        try:
            layout = detect_layout(source)
            source_meta = source_metadata(source, layout)
            expected_dim = int(source_meta.get("embedding_dimensions", "512"))
            people_rows = source.execute(named_people_query(layout)).fetchall()
            for index, person_row in enumerate(people_rows, start=1):
                result = build_person_result(
                    source,
                    layout,
                    person_row,
                    expected_dim,
                    seed,
                    minimum,
                    maximum,
                    trim_fraction,
                    holdout_fraction,
                    max_holdout,
                )
                if result is not None:
                    results.append(result)
                if index % 25 == 0 or index == len(people_rows):
                    print(f"processed_people={index}/{len(people_rows)}", flush=True)
        finally:
            source.close()

        holdout = evaluate_holdout(results, threshold, margin_threshold)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(temporary_path)
        temporary_created = True
        try:
            target.execute("PRAGMA journal_mode=DELETE")
            target.execute("PRAGMA synchronous=FULL")
            create_output_schema(target)
            target.executemany(
                """
                INSERT INTO people(
                    source_person_id,name,alias,source_face_count,usable_face_count,
                    invalid_face_count,template_count,generated_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        result.person_id,
                        result.name,
                        result.alias,
                        result.source_face_count,
                        result.usable_face_count,
                        result.invalid_face_count,
                        len(result.template_rows),
                        generated_at,
                    )
                    for result in results
                ],
            )
            for result in results:
                target.executemany(
                    """
                    INSERT INTO templates(
                        source_person_id,slot,embedding,sample_count,
                        representative_source_face_id,mean_similarity,min_similarity
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            result.person_id,
                            row["slot"],
                            row["embedding"],
                            row["sample_count"],
                            row["representative_source_face_id"],
                            row["mean_similarity"],
                            row["min_similarity"],
                        )
                        for row in result.template_rows
                    ],
                )
            metadata = {
                "format_version": str(FORMAT_VERSION),
                "generated_at": generated_at,
                "scope": "confirmed_named_people_only",
                "contains_passersby": "false",
                "contains_pending_people": "false",
                "contains_original_photos": "false",
                "contains_face_crops": "false",
                "contains_source_paths": "false",
                "source_file_name": source_path.name,
                "source_file_sha256": source_hash_before,
                "source_people_count": str(len(results)),
                "source_face_count": str(sum(item.source_face_count for item in results)),
                "usable_face_count": str(sum(item.usable_face_count for item in results)),
                "invalid_face_count": str(sum(item.invalid_face_count for item in results)),
                "template_count": str(sum(len(item.template_rows) for item in results)),
                "embedding_dimensions": str(expected_dim),
                "embedding_dtype": "float32",
                "algorithm": ALGORITHM_NAME,
                "algorithm_parameters": json.dumps(
                    {
                        "seed": seed,
                        "minimum_templates": minimum,
                        "maximum_templates": maximum,
                        "trim_fraction": trim_fraction,
                        "holdout_fraction": holdout_fraction,
                        "max_holdout_per_person": max_holdout,
                        "selection_p10_tolerance": 0.01,
                        "selection_mean_tolerance": 0.005,
                        "redundant_center_similarity": REDUNDANT_CENTER_SIMILARITY,
                    },
                    sort_keys=True,
                ),
                "numpy_version": np.__version__,
                "scikit_learn_version": sklearn_version,
                "holdout_metrics": json.dumps(holdout, sort_keys=True),
            }
            for key in ("embedding_model", "photo_identity"):
                if key in source_meta:
                    metadata[key] = source_meta[key]
            target.executemany(
                "INSERT INTO metadata(key,value) VALUES (?,?)", metadata.items()
            )
            target.commit()
        finally:
            target.close()

        source_hash_after = sha256_file(source_path)
        if source_hash_after != source_hash_before:
            raise RuntimeError("source database changed during template generation")
        verification = verify_output(temporary_path, expected_dim)
        os.replace(temporary_path, output_path)
        temporary_created = False
        output_hash = sha256_file(output_path)
        template_counts = [len(item.template_rows) for item in results]
        report = {
            "status": "PASS",
            "source": {
                "file_name": source_path.name,
                "sha256_before": source_hash_before,
                "sha256_after": source_hash_after,
                "unchanged": source_hash_before == source_hash_after,
                "named_people": len(results),
                "named_faces": sum(item.source_face_count for item in results),
                "usable_faces": sum(item.usable_face_count for item in results),
                "invalid_faces": sum(item.invalid_face_count for item in results),
            },
            "output": {
                "file_name": output_path.name,
                "sha256": output_hash,
                "bytes": output_path.stat().st_size,
                "people": len(results),
                "templates": sum(template_counts),
                "minimum_templates": min(template_counts) if template_counts else 0,
                "maximum_templates": max(template_counts) if template_counts else 0,
                "template_count_distribution": {
                    str(count): template_counts.count(count)
                    for count in sorted(set(template_counts))
                },
            },
            "algorithm": {
                "name": ALGORITHM_NAME,
                "numpy_version": np.__version__,
                "scikit_learn_version": sklearn_version,
                "seed": seed,
                "minimum_templates": minimum,
                "maximum_templates": maximum,
                "trim_fraction": trim_fraction,
            },
            "holdout_validation": holdout,
            "sqlite_verification": verification,
            "business_flow_changed": False,
        }
        if report_path is not None:
            write_json_atomic(report_path.resolve(), report)
        return report
    finally:
        if temporary_created and temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a standalone compact template database from existing face embeddings."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--min-templates", type=int, default=DEFAULT_MIN_TEMPLATES)
    parser.add_argument("--max-templates", type=int, default=DEFAULT_MAX_TEMPLATES)
    parser.add_argument("--trim-fraction", type=float, default=DEFAULT_TRIM_FRACTION)
    args = parser.parse_args()
    try:
        report = generate_templates(
            args.source,
            args.output,
            args.report,
            seed=args.seed,
            minimum=args.min_templates,
            maximum=args.max_templates,
            trim_fraction=args.trim_fraction,
        )
    except Exception as error:
        print(f"TEMPLATE_BUILD_FAILED {error}", file=sys.stderr)
        return 1
    print(f"TEMPLATE_BUILD_OK {args.output.resolve()}")
    print(
        json.dumps(
            {
                "people": report["output"]["people"],
                "templates": report["output"]["templates"],
                "bytes": report["output"]["bytes"],
                "source_unchanged": report["source"]["unchanged"],
                "holdout": report["holdout_validation"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
