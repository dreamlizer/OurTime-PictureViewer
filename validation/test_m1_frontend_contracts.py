"""Static and JavaScript-runtime gates for M1 async ownership contracts."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class FrontendContractTests(unittest.TestCase):
    def test_operation_runtime_is_loaded_before_callers(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        runtime = html.find('src="/operation-runtime.js"')
        app = html.find('src="/app.js"')
        self.assertGreaterEqual(runtime, 0)
        self.assertLess(runtime, app)

    def test_person_and_photo_writes_capture_entity_sessions(self) -> None:
        app = (WEB / "app.js").read_text(encoding="utf-8")
        viewer = (WEB / "viewer.js").read_text(encoding="utf-8")
        self.assertIn("captureEntityWrite", app)
        self.assertIn("entitySession", app)
        self.assertIn("draftRevision", app)
        self.assertIn("captureEntityWrite", viewer)

    def test_api_preserves_structured_error_metadata(self) -> None:
        app = (WEB / "app.js").read_text(encoding="utf-8")
        for marker in ("error.status", "error.errorCode", "error.operationId"):
            self.assertIn(marker, app)
        self.assertIn("combineAbortSignals", app)

    def test_query_runtime_discards_stale_failures(self) -> None:
        script = r"""
const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync(process.argv[1],'utf8');
const context={window:{},DOMException};
vm.createContext(context);vm.runInContext(code,context);
const runtime=context.window.OurTimeOperationRuntime;
const q=runtime.createQuerySession({query:{q:'old'},result:['old']});
const older=q.begin({q:'slow'});
const newer=q.begin({q:'new'});
q.commit(newer,[]);
const stale=q.fail(older,new Error('late'));
process.stdout.write(JSON.stringify({state:q.read(),stale}));
"""
        completed = subprocess.run(
            ["node", "-e", script, str(WEB / "operation-runtime.js")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertFalse(result["stale"])
        self.assertEqual({"q": "new"}, result["state"]["actualQuery"])
        self.assertEqual([], result["state"]["result"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
