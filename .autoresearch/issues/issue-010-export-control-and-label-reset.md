# Simplify photo export control and reset manual label layout

## Goal
- Remove the redundant JPG/PNG selector from the viewer's right toolbar.
- Treat any explicit label-layout selection as a reset of the current photo's saved manual label positions.

## Inputs Ready
- Existing photo export button on the photo.
- Existing face-label position persistence and reset API.

## Scope
- Keep the on-photo export icon and default JPEG export.
- Clear only the open photo's manual label positions when selecting auto, left, right, top, or bottom.
- Re-render with the selected automatic layout after the reset succeeds.
- Keep the explicit current-photo reset button as an additional recovery path.

## Acceptance Criteria
- [ ] No JPG/PNG selector appears in the right viewer toolbar.
- [ ] The on-photo export icon still exports JPEG.
- [ ] Selecting a layout after dragging clears the open photo's saved positions.
- [ ] Other photos' saved positions are unchanged.
- [ ] A failed reset does not pretend that manual positions were cleared.
- [ ] Focused browser and regression tests pass.

## Out of Scope
- Changing export rendering quality or filenames.
- Removing backend PNG support.
- Changing face-label appearance, thresholds, or photo data.

## Clarity Gate
- Score: 98/100
- Automation level: Level 3
- Remaining assumption: the visible on-photo export action should default to JPEG.

## Notes for Codex
- Use isolated data and a random port.
- Do not restart the formal workbench.
- Do not stage unrelated person-template work.
