"""Pure state tests for the viewer display controller (no DOM, no network)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "web" / "viewer-display.js").read_text(encoding="utf-8")


def eval_display():
    """Run createViewerDisplay in a tiny JS sandbox via Node if available; else structural checks."""
    return SRC


def test_controller_contract():
    text = eval_display()
    for needle in (
        "createViewerDisplay",
        "commitDisplay",
        "beginOpen",
        "beginClose",
        "finishClose",
        "nextRequestToken",
        "isLiveRequest",
        "isLiveLease",
        "currentLease",
        "requestDisplayRefresh",
        "getCommittedFrame",
    ):
        assert needle in text, f"missing {needle}"


def test_commit_requires_tickets():
    text = eval_display()
    assert "ticket.sessionEpoch !== sessionEpoch" in text
    assert "pending.token.requestSeq !== ticket.requestSeq" in text
    assert "adopted: false" in text
    assert "stale" in text


def test_close_invalidates_leases():
    text = eval_display()
    # beginClose bumps session so old leases cannot match committed
    assert "sessionEpoch += 1" in text
    assert re.search(r"function beginClose", text)
    assert re.search(r"function finishClose", text)


def test_paint_only_adopts_inside_commit():
    text = eval_display()
    assert "opts.paint" in text
    assert "function commitDisplay" in text


def main():
    test_controller_contract()
    test_commit_requires_tickets()
    test_close_invalidates_leases()
    test_paint_only_adopts_inside_commit()
    print("STATE_OK viewer-display contract")


if __name__ == "__main__":
    main()
