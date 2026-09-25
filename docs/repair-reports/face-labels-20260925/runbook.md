# Face label verification runbook

Run commands from the independent task worktree. Do not run them from the production checkout.

## Preconditions

- Confirm http://127.0.0.1:8765/api/health still reports the production PID and production data directory. Do not restart it.
- Use the worktree interpreter that can import the project and Playwright. The recorded acceptance used the local project virtual environment and Chrome 153.0.8010.54.
- Leave PHOTO_LIBRARY_DATA unset in the production shell. The acceptance sets it only for its own process.

## Commands

~~~text
python -m unittest validation.test_face_label_model validation.test_face_label_config -v
python -m unittest validation.test_annotated_export_fix -v
python validation/test_annotated_export.py
python validation/smoke.py
python validation/test_face_label_browser.py
~~~

The browser command creates validation/work/face-labels-20260925/private/<timestamp>/. It prints EVIDENCE and the directory path on success. Screenshots, exports, checks.json, and server.log stay in that ignored directory.

## What to inspect

- checks.json must include four PNG and four JPEG acceptances, source-hash equality, delayed-write ordering, and the missing-plate fallback.
- exports/<theme>.png should show both the Chinese and Latin names. Ivory uses plate 1 vertically and plate 7 horizontally; tea uses plate 4 vertically and plate 8 horizontally.
- The source JPEG hashes in photos/ must match before and after.

## Rollback

Revert only the task-branch commits or the listed source files in the task worktree. Do not restore or replace the production SQLite database to undo a label-style change. Production deployment was not performed.
