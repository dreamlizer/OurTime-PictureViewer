"""Focused JavaScript-runtime regressions for the M2 queue and receipt fixes."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "web" / "operation-runtime.js"


def run_node(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script, str(RUNTIME)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


class M2FrontendContractTests(unittest.TestCase):
    def test_write_queue_rejection_is_owned_and_tail_is_released(self) -> None:
        result = run_node(
            r"""
const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync(process.argv[1],'utf8');
const context={window:{},DOMException,setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(code,context);
const queue=context.window.OurTimeOperationRuntime.createWriteQueue();
const events=[],unhandled=[];
process.on('unhandledRejection',reason=>unhandled.push(String(reason)));
(async()=>{
  const first=queue('person',1,async()=>{events.push('first-start');throw new Error('expected');});
  await first.catch(error=>events.push(error.message));
  const second=queue('person',1,async()=>{events.push('second');return 2;});
  const independent=queue('person',2,async()=>{events.push('independent');return 3;});
  const values=await Promise.all([second,independent]);
  await new Promise(resolve=>setImmediate(resolve));
  process.stdout.write(JSON.stringify({
    events,values,unhandled,pending:queue.pendingCount()
  }));
})().catch(error=>{process.stderr.write(error.stack);process.exit(1);});
"""
        )
        self.assertEqual([], result["unhandled"])
        self.assertEqual(0, result["pending"])
        self.assertEqual([2, 3], result["values"])
        self.assertLess(result["events"].index("expected"), result["events"].index("second"))
        self.assertIn("independent", result["events"])

    def test_receipt_interpreter_preserves_all_operation_states(self) -> None:
        result = run_node(
            r"""
const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync(process.argv[1],'utf8');
const context={window:{},DOMException};
vm.createContext(context);vm.runInContext(code,context);
const interpret=context.window.OurTimeOperationRuntime.interpretOperationReceipt;
const cases=[
  {operation_id:'done',status:'committed',state_committed:true,cleanup_status:'completed',updated:1},
  {operation_id:'cleanup',status:'committed',state_committed:true,cleanup_status:'failed',error_code:'cleanup_incomplete'},
  {operation_id:'pending',status:'pending',state_committed:false,cleanup_status:'not_applicable'},
  {operation_id:'rejected',status:'rejected',state_committed:false,error_code:'scan_busy',error_detail:'busy'}
];
const values=cases.map(item=>{
  try{return {ok:true,value:interpret(item)};}
  catch(error){return {ok:false,code:error.errorCode,id:error.operationId,message:error.message};}
});
process.stdout.write(JSON.stringify(values));
"""
        )
        self.assertTrue(result[0]["ok"])
        self.assertFalse(result[0]["value"]["cleanupIncomplete"])
        self.assertTrue(result[1]["ok"])
        self.assertTrue(result[1]["value"]["cleanupIncomplete"])
        self.assertEqual(
            {"ok": False, "code": "operation_pending", "id": "pending"},
            {k: result[2][k] for k in ("ok", "code", "id")},
        )
        self.assertEqual(
            {"ok": False, "code": "scan_busy", "id": "rejected"},
            {k: result[3][k] for k in ("ok", "code", "id")},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
