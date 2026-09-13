# B00 + B01: photo/face persistence consistency

## Goal
Reproduce and repair F01, F02, and F03 against the current application code:
persist display-only cleanup policy, separate committed face results from
in-memory index publication failures, and keep the face index consistent with
database mutations.

## Inputs Ready
- `AGENTS.md`
- `README.md`
- Provided `OurTime_Code_Audit_and_Repair_Playbook_20260913.md`
- Current repository at audit baseline `5df2d3bf016f131b73ccb45885f32212919c5c9c`

## Scope
- B00 baseline, isolation, and current-state verification
- B01 / F01-F03 only
- Synthetic images, embeddings, and SQLite databases under `validation/work/`
- A versioned, idempotent schema migration exercised only on synthetic old schemas
- Local commit after final tests

## Acceptance Criteria
- [ ] Current implementation fails behavior tests for F01, F02, and F03 before repair
- [ ] Display-only exclusion survives ingest, app reinitialization, and restore without changing face identity data
- [ ] Unknown historical exclusions migrate conservatively and idempotently
- [ ] A post-commit index publication failure leaves one complete successful face result and does not rerun inference
- [ ] Pre-commit crop/database failures do not delete older successful resources or publish partial success
- [ ] Face/person mutations advance a persistent index revision and stale builds cannot publish
- [ ] Cleanup invalidates stale matching evidence in-process and after rebuild
- [ ] Same-asset concurrent/retried processing produces one committed face set
- [ ] API, SQLite integrity, foreign keys, and synthetic original hashes pass
- [ ] Migration and rollback boundaries are documented; formal migration is not run
- [ ] Final commit is local only; no push

## Out of Scope
- Formal `data/`, formal service lifecycle, scans, or historical face repair
- F04 and later findings
- Face thresholds or recognition-policy changes
- Frontend/visual changes
- Whole-repository refactors

## Clarity Gate
- Score: 97/100
- Automation level: Level 3
- Remaining assumptions:
  - A forward-only additive schema migration is acceptable when old code remains
    schema-readable but is documented as behaviorally unsafe after rollback.

## Notes for Codex
- Set `PHOTO_LIBRARY_DATA` before importing `app`
- Prove resolved isolation paths do not alias the formal `data/`
- Use current routes/domain functions and the real SQLite schema
- Fake only the face engine and explicit I/O/index failures
- Preserve red and green logs; stop after B01
