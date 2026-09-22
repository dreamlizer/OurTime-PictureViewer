# Stable map clusters

## Goal
Replace the double, full-refresh map aggregation with one stable hierarchical cluster layer that expands and contracts smoothly.

## Inputs Ready
- Existing `/api/places` viewport aggregation
- Existing `cell + bucket + viewport` click contract
- Existing Leaflet map and isolated map regressions

## Scope
- Stable parent/child cluster identifiers
- One aggregation layer
- Cancel stale viewport requests
- Incremental marker replacement with short parent/child motion
- Preserve marker count, clicked photo set, and viewer sequence

## Acceptance Criteria
- [x] One backend aggregation layer supplies all overview markers
- [x] Cluster children identify the same stable parent at the previous zoom
- [x] Real single-photo coordinates remain unchanged
- [x] Rapid zoom cannot let an older response overwrite the current layer
- [x] Existing count/click/viewer contracts still pass
- [x] Isolated backend and browser regressions pass

## Out of Scope
- GPS, EXIF, manual-place, scan, face, or original-photo changes
- Formal data migration
- Restarting the formal workbench

## Clarity Gate
- Score: 95/100
- Automation level: Level 3
- Remaining assumptions:
  - A 260ms parent/child transition is appropriate for the existing UI

## Notes for Codex
- Use only isolated data and a random port for dynamic verification
- Keep the existing map selection and clicked-result safety contracts
- Completed with 9 backend tests, structured-query regression, both isolated
  browser flows, JavaScript syntax validation, and the focused smoke suite.
