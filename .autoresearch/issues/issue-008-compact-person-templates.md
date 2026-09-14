# Build compact named-person templates

## Goal
Generate an independent compact face-template database from the existing portable people bundle, using only confirmed named people and excluding passersby.

## Inputs Ready
- `人物特征与姓名-20260914.sqlite3`
- 380 confirmed named person records
- Existing InsightFace buffalo_l 512-dimensional float32 embeddings
- NumPy 2.2.2 and scikit-learn 1.6.1 in the project Python environment

## Scope
- Read the existing SQLite bundle in read-only mode
- Generate one to five compact templates per confirmed named person
- Use established normalized-vector clustering and robust trimming
- Preserve model/version and generation statistics in a new SQLite file
- Produce independent validation evidence from held-out embeddings
- Make the generator rerunnable after a future source bundle is refreshed

## Acceptance Criteria
- [x] Source bundle remains byte-for-byte unchanged
- [x] Only `confirmed=1 AND ignored=0` people are exported
- [x] No passerby or pending person is exported
- [x] Every exported embedding is finite, L2-normalized, 512-dimensional float32
- [x] Every named person with a valid source embedding receives one to five templates
- [x] People with at least three usable embeddings receive three to five templates
- [x] Output passes SQLite integrity and foreign-key checks
- [x] A deterministic fixture test covers named, passerby, outlier, and small-sample cases
- [x] Real-data held-out validation and size statistics are recorded
- [x] No application, scan, naming, merge, split, or matching flow is changed

## Out of Scope
- Changing `app.py` or `library_db.py`
- Switching the current face index to compact templates
- Automatically rebuilding templates after naming
- Deleting or vacuuming existing face embeddings
- Changing face thresholds or user-interface behavior
- Importing templates into smart glasses or another photo library

## Clarity Gate
- Score: 96/100
- Automation level: Level 3
- Remaining assumptions:
  - Three templates are the default for people with at least three usable samples
  - A fourth or fifth template is retained only when clustering finds supported, non-redundant variation
  - The current source bundle is authoritative for this independent generation run

## Notes for Codex
- Stay local-only
- Use Codex as the only worker
- Keep the source database read-only
- Write generated biometric data only to an ignored standalone artifact
- Do not connect the result to any current business flow
