# Face label completion report, 2026-09-25

## A. Result

P0 through P4 were carried through in the isolated worktree. The shared label model, four themes, direction, preference migration, preview/live component, and export path are present. The final isolated browser run passed, including four-theme PNG/JPEG export and unchanged source hashes.

Code status: changes are in the task worktree and are not yet the final commit recorded by this report. They are not pushed, not merged, and not deployed. Production PID 28992 and its production data directory were unchanged after verification.

Visual status: the four exported PNGs and the 1440px screenshots were opened and inspected locally. This is synthetic-sample visual acceptance, not a real private-photo gallery review.

## B. Fixes

| ID | Evidence | Fix | Result |
|---|---|---|---|
| D10 | Ivory export returned 422 because directional plate variables were outside the old allowlist. | Accept only --face-label-image, --face-label-image-vertical, and --face-label-image-horizontal in addition to the legacy names. URL, script, and arbitrary-style checks are unchanged. | PASS |
| Smoke gate | Shared plate rules were invisible to the old CSS string check. | Read face-labels.css together with viewer-overrides.css and restore the exact scale fallback contract. | PASS |
| Acceptance flow | Reset button exists inside the closed style panel. | Open the panel before reset and close it before photo navigation. | PASS |

D01-D09 remain covered by the existing shared model and browser checks: named state, appearance reset without deleting coordinates, per-label direction, preference migration, complete plate sets, async measurement, and shared preview/live rendering.

## C. Ownership and cleanup ledger

| Rule | Decision | Reason | Verification |
|---|---|---|---|
| web/face-label-model.js | Keep | Single preference, direction, scale, font, and plate contract. | Model tests |
| web/face-labels.css | Keep | Shared live and preview appearance. | Browser comparison and smoke |
| faceAliasMode / faceDirMode | Keep as derived compatibility | Older validation scripts and saved preferences still read them; they are synchronized from the new state. | Model migration test and browser run |
| Legacy --face-label-[sml]-image | Keep in allowlist only | Old frozen snapshots may still contain it. No current theme emits it. | Export unit test |
| validation/work private runs | Do not commit | Ignored local evidence, including synthetic images. | git check-ignore |
| Accidental $null file | Do not commit | 25-byte console artifact, unrelated to the feature. | Left untracked |

No second active theme implementation remains in the live path. viewer.js applies state and position; face-labels.css supplies appearance; photo-export.js freezes the current DOM; photo_export.py validates and renders it.

## D. Visual and export

All four themes were compared in the same 1440x900 Chrome context at device scale 1, then exported from that frozen view.

| Theme | Observed appearance | Export |
|---|---|---|
| classic | Neutral sans label, Chinese vertical, Latin horizontal. | classic.png / classic.jpg |
| ivory | WenKai vertical plate 1.png and horizontal plate 7.png. | ivory.png / ivory.jpg |
| tea | Brush-style plate 4.png and horizontal plate 8.png. | tea.png / tea.jpg |
| accent | Dark red serif treatment without a decorative plate. | accent.png / accent.jpg |

The inspected ivory, tea, accent, and classic PNGs all retain both names, the bottom signature, original 1600px image width, and no tooltip, focus box, or export control. Default parameters were not intentionally changed. No fifth theme was added.

## E. Tests

- python -m unittest validation.test_face_label_model validation.test_face_label_config: 9 tests, OK.
- python -m unittest validation.test_annotated_export_fix: 6 tests, OK after adding the directional-variable boundary.
- python validation/test_annotated_export.py: PASS, 5000px synthetic source and unchanged hash.
- python validation/smoke.py: SMOKE_OK, 24 checks, 0 skipped.
- python validation/test_face_label_browser.py: PASS. Final evidence directory is validation/work/face-labels-20260925/private/20260925-125238.

The full validation/validate.py suite was not run. This change does not alter scan recovery, database schema, person matching, or merge policy, and the task card reserves that suite for those triggers.

## F. Limits

- Visual evidence uses three synthetic photos, not a private multi-face gallery. Real-photo visual review remains available but was not part of the isolated evidence.
- The full FL matrix was not executed as a Cartesian product. Cases not exercised are marked NOT_RUN in test-results.json rather than treated as failures or passes.
- Extreme long-name overflow beyond the tested five-character name was not exhaustively measured.
- Performance was not benchmarked numerically, so no speed claim is made.

## G. Release and rollback

This task is not deployed. Before any future deployment, confirm the production process is idle and backup policy is satisfied; do not restart or scan as part of this change. To undo the code, revert the task-branch commits in the task worktree only. Do not restore the production database for a CSS or export-allowlist rollback.
