# Face label baseline, 2026-09-25

## Versions

- Reference commit: ad20f676fc069bafb095a3cfec30e002a82f0a2d.
- Worktree: independent checkout on fix/face-label-system-20260925.
- Production service observed before and after the isolated run: PID 28992, port 8765, data directory is the production data directory. It was not restarted, written, scanned, or used for face recognition.
- Production checkout remained on master at the same reference commit. Its pre-existing unrelated changes were not staged, edited, or cleaned.

## Isolation

The browser acceptance creates a timestamped directory under validation/work, seeds a temporary SQLite database, copies only the four required label plates, and starts app.py on a random localhost port. It refuses the production data directory, production web root, port 8765, and production PID. Playwright uses a fresh Chrome context at 1440x900 and device scale 1. The private evidence directory is covered by the existing validation/work/ ignore rule.

## Existing changes retained

The branch already contained the shared face-label state model, four-theme appearance, per-label direction, preference migration, plate fallback, and shared preview/live styling. Those changes were retained. This pass corrected the export allowlist, shared CSS contract, smoke coverage, and acceptance flow rather than reverting them.

## Baseline findings actually reproduced

- D10: the frozen snapshot emitted --face-label-image-vertical and --face-label-image-horizontal, while the export allowlist still recognized only the old --face-label-[sml]-image names. Ivory PNG export returned HTTP 422 before the fix. Evidence: isolated run 20260925-124601 and photo_export.py before this pass.
- Smoke coverage: the shared stylesheet contained the live plate contract, but smoke.py read only viewer-overrides.css, so a correct shared implementation failed the static gate.
- The explicit position-reset control lives inside the style panel. Closing the panel before clicking it made the acceptance wait on a hidden button; this was a test-flow defect, not a reset-contract defect.

D01-D09 were reviewed against the current code and covered by the focused model, config, browser, and export tests. No previously repaired behavior was reverted.
