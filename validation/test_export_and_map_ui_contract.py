"""Static UI contracts for export fidelity and unambiguous map-area state."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExportAndMapUiContractTests(unittest.TestCase):
    def test_export_freezes_computed_dom_and_exposes_both_formats(self):
        source = (ROOT / "web" / "photo-export.js").read_text(encoding="utf-8")
        self.assertIn("snapshot_version:2", source)
        self.assertIn("getComputedStyle", source)
        self.assertIn("export-annotated-format", source)
        self.assertIn("photo-export-control", source)
        self.assertIn("X-OurTime-Export-Renderer", source)
        self.assertIn("mat.append(control)", source)
        self.assertIn("actions.append(picker)", source)
        self.assertNotIn("control.append(button,picker)", source)
        self.assertIn("<svg", source)
        self.assertIn("JPEG", source)
        self.assertIn("PNG", source)

    def test_map_area_button_has_no_decorative_box_and_reports_phase(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        button = html.split('id="map-area-start"', 1)[1].split("</button>", 1)[0]
        self.assertNotIn("<svg", button)
        script = (ROOT / "web" / "map-area-editor.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "map-area-editor.css").read_text(encoding="utf-8")
        self.assertIn("setButtonPhase", script)
        self.assertIn("dataset.phase", script)
        self.assertIn("@keyframes map-area-pulse", css)
        self.assertIn('[data-phase="drawing"]', css)
        self.assertIn('[data-phase="selected"]', css)


if __name__ == "__main__":
    unittest.main()
