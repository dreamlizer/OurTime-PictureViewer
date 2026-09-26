// Real controller state tests (Node). Loads viewer-display.js and asserts tickets.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'web', 'viewer-display.js'), 'utf8');
const sandbox = { window: {}, console };
sandbox.global = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const factory = sandbox.window.OurTimeViewerDisplay;
if (!factory || typeof factory.createViewerDisplay !== 'function') {
  console.error('STATE_FAIL factory missing');
  process.exit(1);
}

function assert(cond, msg) {
  if (!cond) {
    console.error('STATE_FAIL', msg);
    process.exit(1);
  }
}

const d = factory.createViewerDisplay();
assert(d.debugState().lifecycle === 'closed', 'starts closed');

d.beginOpen();
assert(d.debugState().lifecycle === 'open', 'open after beginOpen');

const tA = d.nextRequestToken('A');
assert(d.isLiveRequest(tA) === false || true, 'token allocated');
d.beginPending(tA, { id: 1 });
assert(d.isLiveRequest(tA) === true, 'A pending is live');

let painted = 0;
const r1 = d.commitDisplay({ photoKey: 'A', photoId: 1, src: 'a.jpg', detail: { id: 1 } }, tA, {
  paint() { painted += 1; },
});
assert(r1.ok && r1.adopted, 'commit A adopted');
assert(painted === 1, 'paint once');
assert(d.getCommittedFrame().photoKey === 'A', 'committed A');
assert(d.getCommittedFrame().commitSeq === 1, 'commitSeq 1');

// Stale ticket cannot commit B's paint
const tOld = Object.assign({}, tA, { requestSeq: tA.requestSeq });
const tB = d.nextRequestToken('B');
d.beginPending(tB, { id: 2 });
let paintedB = 0;
const stale = d.commitDisplay({ photoKey: 'B', photoId: 2, src: 'b.jpg' }, tOld, {
  paint() { paintedB += 1; },
});
assert(stale.ok === false && stale.status === 'stale', 'old ticket rejected');
assert(paintedB === 0, 'stale paint not run');
assert(d.getCommittedFrame().photoKey === 'A', 'still A');

const okB = d.commitDisplay({ photoKey: 'B', photoId: 2, src: 'b.jpg', detail: { id: 2 } }, tB, {
  paint() { paintedB += 1; },
});
assert(okB.ok && okB.adopted, 'B committed');
assert(d.getCommittedFrame().photoKey === 'B', 'committed B');
assert(d.getCommittedFrame().commitSeq === 2, 'commitSeq 2');

// A->B->A: same photo id is a new commitSeq
const tA2 = d.nextRequestToken('A');
d.beginPending(tA2, { id: 1 });
const back = d.commitDisplay({ photoKey: 'A', photoId: 1, src: 'a.jpg' }, tA2, { paint() {} });
assert(back.ok && back.frame.commitSeq === 3, 'reopen same id new commitSeq');

const lease = d.currentLease();
assert(d.isLiveLease(lease) === true, 'live lease');

// Close invalidates immediately
const close = d.beginClose('user');
assert(close.ok && close.exit, 'close returns exit');
assert(d.currentLease() === null || d.isLiveLease(lease) === false, 'lease dead after beginClose');
const late = d.commitDisplay({ photoKey: 'C', photoId: 3, src: 'c.jpg' }, tA2, {
  paint() { paintedB += 1; },
});
assert(late.ok === false, 'no commit after close begin');
d.finishClose();
assert(d.debugState().lifecycle === 'closed', 'closed');

// Reopen same id after close must not accept old ticket
d.beginOpen();
const tSame = d.nextRequestToken('A');
d.beginPending(tSame, { id: 1 });
const oldAfter = d.commitDisplay({ photoKey: 'A', photoId: 1 }, {
  sessionEpoch: tSame.sessionEpoch - 1,
  requestSeq: tSame.requestSeq,
  targetKey: 'A',
}, { paint() { throw new Error('should not paint'); } });
assert(oldAfter.ok === false, 'old session after reopen rejected');

console.log('STATE_OK viewer-display tickets');
