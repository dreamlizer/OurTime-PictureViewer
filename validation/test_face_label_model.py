"""Pure face-label state, direction, preference and scale contracts."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "web" / "face-label-model.js"


def run_js(expression: str):
    script = (
        "const fs=require('fs'); const vm=require('vm');"
        f"const code=fs.readFileSync({json.dumps(str(MODEL))},'utf8');"
        "const sandbox={module:{exports:{}},exports:{},globalThis:{}};"
        "sandbox.globalThis.globalThis=sandbox.globalThis;"
        "vm.createContext(sandbox); vm.runInContext(code,sandbox,{filename:'face-label-model.js'});"
        "const api=sandbox.globalThis.OurTimeFaceLabels;"
        f"const result=({expression});"
        "process.stdout.write(JSON.stringify(result));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


class FaceLabelModelTests(unittest.TestCase):
    def test_truth_table(self):
        cases = [
            ("N01", {"name": "陈宁"}, ["陈宁", "陈宁", "陈宁"], "named"),
            ("N02", {"alias": "小宁"}, ["小宁", "小宁", "小宁"], "named"),
            ("N03", {"name": "待核对"}, ["+", "+", "+"], "pending"),
            ("N04", {"name": "命名", "alias": "小宁"}, ["小宁", "小宁", "小宁"], "named"),
            ("N05", {"name": "陈宁", "alias": "小宁"}, ["陈宁", "陈宁 / 小宁", "小宁"], "named"),
            ("N06", {"name": "陈宁", "alias": "陈宁"}, ["陈宁", "陈宁", "陈宁"], "named"),
            ("N07", {"name": "陈宁", "alias": "小宁", "ignored": 1}, ["+", "+", "+"], "passerby"),
            ("N08", {"name": "  ", "alias": " "}, ["+", "+", "+"], "pending"),
            ("N09", {}, ["+", "+", "+"], "pending"),
            ("N10", {"name": "路人甲"}, ["路人甲", "路人甲", "路人甲"], "named"),
            ("N11", {"name": "陈宁", "ignored": "0"}, ["陈宁", "陈宁", "陈宁"], "named"),
        ]
        for case_id, face, expected, kind in cases:
            with self.subTest(case_id):
                actual = [
                    run_js(f"api.resolveFaceLabelState({json.dumps(face, ensure_ascii=False)}, {json.dumps(mode)})")
                    for mode in ("off", "with", "only")
                ]
                self.assertEqual([item["displayText"] for item in actual], expected)
                self.assertTrue(all(item["kind"] == kind for item in actual))
                self.assertNotIn("undefined", json.dumps(actual, ensure_ascii=False))

    def test_explicit_alias_mode_does_not_read_a_global(self):
        face = {"name": "陈宁", "alias": "小宁"}
        result = run_js(
            "(()=>{globalThis.viewer={faceAliasMode:'with'};"
            f"return api.resolveFaceLabelState({json.dumps(face, ensure_ascii=False)},'off');}})()"
        )
        self.assertEqual(result["displayText"], "陈宁")

    def test_direction_boundaries(self):
        samples = {
            "陈宁": [True, False, True],
            "陈宁 Alex": [True, False, True],
            "A": [False, False, False],
            "A. B.": [False, False, False],
            "Anne-Marie": [False, False, False],
            "José": [False, False, False],
            "陈宁 / Alex": [True, False, True],
            "Alex / Alex Chen": [False, False, False],
        }
        for text, expected in samples.items():
            actual = [
                run_js(f"api.faceLabelVerticalFor({json.dumps(text, ensure_ascii=False)}, {json.dumps(mode)})")
                for mode in ("auto", "horizontal", "vertical")
            ]
            self.assertEqual(actual, expected, text)

    def test_combined_latin_name_and_alias_stays_horizontal(self):
        face = {"name": "Alex", "alias": "Alex Chen"}
        state = run_js(f"api.resolveFaceLabelState({json.dumps(face, ensure_ascii=False)}, 'with')")
        self.assertEqual(state["displayText"], "Alex / Alex Chen")
        actual = [
            run_js(
                f"api.faceLabelVerticalFor({json.dumps(state['displayText'], ensure_ascii=False)}, "
                f"{json.dumps(mode)}, {json.dumps(face, ensure_ascii=False)})"
            )
            for mode in ("auto", "horizontal", "vertical")
        ]
        self.assertEqual(actual, [False, False, False])

    def test_preference_migration_is_idempotent_and_prefers_new_fields(self):
        raw = {
            "faceAlias": True,
            "faceAliasMode": "off",
            "faceVertical": False,
            "faceDirMode": "vertical",
            "faceDirectionVersion": 1,
            "faceStyle": {"theme": "outline", "fontSize": 99},
            "slideDelay": 3,
        }
        once = run_js(f"api.normalizeViewerPrefs({json.dumps(raw)})")
        twice = run_js(f"api.normalizeViewerPrefs({json.dumps(once)})")
        self.assertEqual(once["faceAliasMode"], "off")
        self.assertEqual(once["faceDirMode"], "vertical")
        self.assertEqual(once["faceStyle"]["theme"], "tea")
        self.assertEqual(once["faceStyle"]["fontSize"], 16)
        self.assertEqual(once, twice)

    def test_invalid_preferences_do_not_emit_non_finite_values(self):
        raw = {"faceStyle": {"theme": "nope", "fontSize": "Infinity", "backgroundOpacity": "NaN", "textColor": "red"}}
        result = run_js(f"api.normalizeViewerPrefs({json.dumps(raw)})")
        encoded = json.dumps(result)
        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)
        self.assertEqual(result["faceStyle"]["theme"], "classic")
        self.assertEqual(result["faceStyle"]["fontSize"], 13)

    def test_legacy_exact_classic_migrates_but_custom_classic_survives(self):
        legacy = {
            "theme": "classic", "fontSize": 13, "fontFamily": "serif", "textColor": "#f6f1e6",
            "backgroundColor": "#141812", "backgroundOpacity": 0.38, "radius": 4,
            "paddingX": 7, "paddingY": 5, "shadow": True,
        }
        custom = dict(legacy, backgroundOpacity=0.5)
        migrated = run_js(f"api.sanitizeFaceStyle({json.dumps(legacy)})")
        kept = run_js(f"api.sanitizeFaceStyle({json.dumps(custom)})")
        self.assertEqual(migrated["fontFamily"], "kai")
        self.assertEqual(migrated["backgroundOpacity"], 0.5)
        self.assertEqual(kept["fontFamily"], "serif")

    def test_font_and_plate_contracts(self):
        kai = run_js("api.fontStack('kai')")
        self.assertIn("LXGW WenKai GB Screen", kai)
        self.assertNotIn('"LXGW WenKai"', kai)
        plates = run_js("api.plateUrls('ivory').concat(api.plateUrls('tea'))")
        self.assertEqual(plates, [
            "/api/face-label-bg/1.png", "/api/face-label-bg/7.png",
            "/api/face-label-bg/4.png", "/api/face-label-bg/8.png",
        ])
        state = run_js(
            "api.effectiveTheme('tea', api.resourceState({"
            "'/api/face-label-bg/4.png':true,'/api/face-label-bg/8.png':false}))"
        )
        self.assertTrue(state["fallback"])
        self.assertEqual(state["requested"], "tea")
        self.assertEqual(state["effective"], "classic")

    def test_scale_formula(self):
        values = [run_js(f"api.faceLabelScaleForHeight(140, {height})") for height in (900, 450, 300)]
        self.assertEqual(values, [1, 0.5, 1 / 3])
        plan = run_js(
            "api.labelScalePlan(["
            "{named:true,vertical:true,sourceHeight:100,kind:'named'},"
            "{named:true,vertical:true,sourceHeight:300,kind:'named'},"
            "{named:true,vertical:false,sourceHeight:300,kind:'named'}"
            "],900)"
        )
        self.assertEqual(plan[0]["reason"], "median")
        self.assertEqual(plan[1]["reason"], "median")
        self.assertEqual(plan[0]["scale"], plan[1]["scale"])
        self.assertEqual(plan[2]["reason"], "own-face")
        self.assertGreater(plan[2]["scale"], plan[0]["scale"])


if __name__ == "__main__":
    unittest.main()
