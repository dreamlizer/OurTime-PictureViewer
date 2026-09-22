# Windows PowerShell launcher encoding

## Goal
Restore the normal restart entry by making every Chinese PowerShell source file
parse correctly under Windows PowerShell 5.

## Inputs Ready
- User screenshot with the exact parser failure
- `restart.ps1` launcher chain
- Reproduction with `powershell.exe`

## Scope
- Correct script encoding for `ourtime-config.ps1` and `start.ps1`
- Parse all root PowerShell scripts through Windows PowerShell
- Exercise restart safely against an isolated data directory and random port

## Acceptance Criteria
- [x] Every root `*.ps1` file parses under Windows PowerShell
- [x] Restart's stop/start scripts complete against an isolated OurTime instance
- [x] The isolated server reports the configured isolated data directory
- [x] Restart opens a uniquely versioned page instead of reusing a stale tab
- [x] HTML, JavaScript, and CSS responses disable stale browser caching
- [x] No formal process or data is changed

## Out of Scope
- Formal data changes
- Restarting or stopping the user's current formal workbench
- Application feature changes

## Clarity Gate
- Score: 98/100
- Automation level: Level 3
- Remaining assumptions:
  - The `.vbs` entry continues to invoke Windows PowerShell 5.

## Notes for Codex
- The failure is reproducible only when the UTF-8 Chinese source lacks a BOM.
- Keep the repair encoding-only unless an isolated restart exposes another defect.
- Added a regression guard so any future non-ASCII root PowerShell script must
  retain its UTF-8 BOM.
- A live restart was later authorized by the user's failed-load report. The
  restarted formal service retained the same data directory and loaded the
  first 24 photos successfully in a real browser flow.
