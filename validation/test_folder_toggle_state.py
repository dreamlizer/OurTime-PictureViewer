from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("async function toggleFolderInclusion")
    end = source.index("function renderScanRoots", start)
    toggle = source[start:end]
    assert "state.folderNav.cache={};" in toggle
    assert toggle.index("state.folderNav.cache={};") < toggle.index(
        "await loadFolderBrowser(state.folderPath||'');"
    )
    print("FOLDER_TOGGLE_STATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
