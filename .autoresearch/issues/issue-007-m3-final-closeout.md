# M3 final focused closeout

## Goal
Finish the remaining map-boundary, scan-reconciliation, and export-snapshot correctness gaps.

## Inputs Ready
- `OurTime_M3_Final_Closeout_Task.md`
- Clean `master` at `d7d86019ef698e4ac13c9ea2d14cfcb5ca2098d2`
- M2 isolated backend and browser fixtures

## Scope
- Preserve clicked map viewport bounds through paging and viewer context
- Reconcile missing files by root-scoped SQL and short writes outside disk checks
- Collect JSON export tables from one explicit read snapshot
- Update only directly affected maintenance documentation and final report

## Acceptance Criteria
- [ ] Edge-of-viewport marker count equals clicked result count
- [ ] Moving the map later does not change the clicked result bounds
- [ ] Scan reconciliation stats only the selected root and handles missing/errors conservatively
- [ ] Disk checks do not hold a database transaction
- [ ] Export tables come from one read snapshot and retain format v2
- [ ] Integrity/FK and one affected regression combination pass
- [ ] Sanitized report is committed and pushed

## Out of Scope
- Production data or service restart
- Face reprocessing, thresholds, visual work
- Full restore/import platform, immutable global paging, scan attempt platform
- Repository-wide split or audit

## Clarity Gate
- Score: 97/100
- Automation level: Level 3
- Remaining assumptions:
  - New map bounds are optional for legacy callers but all-or-none when supplied
  - Scan reconciliation uses bounded ID pages and snapshot fields as update guards

## Notes for Codex
- Use only isolated DATA and random ports
- Stop after one remote verification
