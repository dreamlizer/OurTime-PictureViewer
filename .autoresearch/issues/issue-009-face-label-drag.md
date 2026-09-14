# Persist draggable face labels

## Goal
Allow a face label in the photo viewer to be dragged directly, persist its
position on pointer release, open the existing face editor on double-click,
and restore the current photo to automatic layout from the style popover.

## Inputs Ready
- Existing face-label layout and viewer interactions in `web/viewer.js`
- Existing operation receipts and SQLite edit history
- User-provided group-photo example

## Scope
- Per-face, per-photo normalized label positions
- Direct pointer drag with a movement threshold
- One auto-save after pointer release
- Double-click to open the existing face editor
- Restore automatic layout for the current photo
- Isolated API and browser regression coverage

## Acceptance Criteria
- [ ] A dragged label remains where released
- [ ] Refresh, zoom and viewer reopen preserve the saved position
- [ ] Dragging does not open the face editor
- [ ] Double-click opens the face editor without moving the label
- [ ] Rapid writes cannot make the label jump back to an older position
- [ ] Save failure visibly restores the previous position
- [ ] Automatic reset clears only the current photo's manual positions
- [ ] Annotated export freezes the saved manual position
- [ ] Original photos and the formal data directory remain untouched

## Out of Scope
- Re-recognizing faces
- Changing face thresholds or label visual themes
- Moving labels across multiple photos at once
- Full undo platform

## Clarity Gate
- Score: 97/100
- Automation level: Level 3
- Remaining assumption:
  - A plain single click only shows the existing guide; movement requires a
    pointer drag, while double-click retains the existing edit action.

## Notes for Codex
- Use isolated `PHOTO_LIBRARY_DATA` and a random local port.
- Add only an additive SQLite table; do not migrate formal data during tests.
- Keep unrelated worktree changes out of the commit.
