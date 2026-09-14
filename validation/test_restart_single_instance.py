from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    command = (ROOT / "重启拾光.cmd").read_text(encoding="utf-8")
    restart = (ROOT / "restart.ps1").read_text(encoding="utf-8")
    assert "restart.ps1" in command
    assert "\npause" not in command.lower()
    assert "PHOTO_RESTART_CMD_PATH=%~f0" in command
    assert "[Threading.Mutex]" in restart
    assert "WaitOne(0)" in restart
    assert "Get-CimInstance Win32_Process" in restart
    assert "Stop-Process -Id $_.ProcessId -Force" in restart
    assert "stop.ps1" in restart and "start.ps1" in restart
    assert "$env:PHOTO_NO_BROWSER = '1'" in restart
    print("RESTART_SINGLE_INSTANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
