# M2 focused correctness repairs

## Goal
Close the five focused M2 correctness gaps in the current implementation.

## Inputs Ready
- `OurTime_M2_Focused_Task.md`
- Current `master` at `8fd23da`
- Existing M1 isolated test harnesses

## Scope
- Stable asset-lock shard ordering
- Write-queue rejection and cleanup ownership
- Operation-receipt state interpretation
- Person-detail pagination after removing a loaded face
- Map grid aggregation, click-query parity, and truncation visibility

## Acceptance Criteria
- [ ] Reversed shard batches and same-shard collisions complete
- [ ] Failed queued writes do not leak unhandled rejections or block later writes
- [ ] Pending/rejected/cleanup-incomplete receipts are not reported as full success
- [ ] Removing a loaded face cannot skip the next page
- [ ] Map count and clicked grid query use the same floor-based grid
- [ ] One focused related regression combination passes
- [ ] Sanitized report is committed and pushed

## Out of Scope
- O1/O2 unless core work finishes early
- Production data, service restart, face reprocessing, thresholds, visual changes
- New pagination/protocol platforms or repository-wide refactors

## Clarity Gate
- Score: 96/100
- Automation level: Level 3
- Remaining assumptions:
  - Preserve offset API compatibility and repair the active browser window locally
  - Use an explicit truncation flag rather than adaptive grid expansion

## Notes for Codex
- All writes and browser tests use isolated DATA and ports
- Stop after push and remote verification
