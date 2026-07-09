// Headless drive surface for the interactive canary builds.
//
// The interactive fixtures (`tests/canaries/jet-fighters/build`,
// `known-good-interactive/build`) expose a tiny headless drive surface
// (`window.__canary.getState()` + DOM `keydown` events) so a cold verifier can
// exercise their tier-1 criteria WITHOUT a human (PRD E3, criterion 1/2b). This
// script is the machine driver run_canaries.py uses when a JS runtime is
// available: it loads a build, runs a per-criterion drive PROGRAM, and returns
// the raw getState() snapshots. It records observations only - it does NOT
// classify pass/fail. Classification lives in run_canaries.py so the driver
// stays a dumb observer and the judgement stays deterministic Python (E3:
// "classification follows from the observations, not from a quality opinion").
//
// It runs each criterion in a FRESH `vm` context so input from one criterion
// (e.g. Space -> PLAYING) never leaks into the next (which may expect READY).
// The DOM shim is deliberately minimal - exactly the surface the two builds
// touch (a no-op 2d canvas context, keydown listeners, requestAnimationFrame,
// document.getElementById). A build that needs more than this surface fails
// loudly rather than being silently mis-driven.
//
// Protocol:
//   argv[2] = path to the build's index.html
//   stdin   = JSON {criteria: [{id, program: [step, ...]}]}
//             step = {op:'getState'} | {op:'key', key} | {op:'frames', n}
//   stdout  = JSON {results: [{id, snapshots: [state|null, ...], hasCanary}]}
'use strict';

import fs from 'node:fs';
import vm from 'node:vm';

const CANVAS_METHODS = [
  'clearRect', 'strokeRect', 'fillRect', 'fillText', 'beginPath',
  'moveTo', 'lineTo', 'stroke', 'save', 'restore', 'arc', 'closePath',
  'rect', 'fill', 'translate', 'rotate', 'scale', 'setTransform',
];

function extractScript(html) {
  const matches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  if (matches.length === 0) {
    throw new Error('build has no <script> block to drive');
  }
  return matches[matches.length - 1][1];
}

// A fresh shimmed environment per criterion. `win` is kept by the caller so it
// can read window.__canary, invoke registered listeners, and pump rAF.
function makeEnv() {
  const ctx = {};
  for (const name of CANVAS_METHODS) {
    ctx[name] = function () {};
  }
  const canvas = { width: 480, height: 320, getContext: () => ctx };
  const listeners = {};
  const raf = [];
  const addListener = (type, cb) => {
    (listeners[type] = listeners[type] || []).push(cb);
  };
  const win = {
    __listeners: listeners,
    __raf: raf,
    addEventListener: addListener,
    removeEventListener: () => {},
  };
  const documentShim = {
    getElementById: () => canvas,
    addEventListener: addListener,
  };
  const requestAnimationFrame = (cb) => {
    raf.push(cb);
    return raf.length;
  };
  const sandbox = {
    window: win,
    document: documentShim,
    requestAnimationFrame,
    console,
    JSON,
    Math,
    String,
    Array,
    Object,
  };
  sandbox.globalThis = sandbox;
  return { sandbox, win };
}

// Pump the requestAnimationFrame queue n times. Each frame drains the queue and
// invokes the callbacks; a well-behaved loop re-schedules itself, so the queue
// refills and the next frame advances the simulation another tick.
function pump(win, n) {
  for (let i = 0; i < n; i++) {
    const callbacks = win.__raf.splice(0);
    for (const cb of callbacks) {
      try {
        cb(i);
      } catch (_e) {
        // A throwing frame callback stalls the loop; observed as no advance.
      }
    }
  }
}

function dispatchKey(win, key) {
  const bound = win.__listeners.keydown || [];
  for (const cb of bound) {
    try {
      cb({ key, preventDefault() {} });
    } catch (_e) {
      // A throwing listener is observed as no state change downstream.
    }
  }
}

function snapshot(win) {
  try {
    if (win.__canary && typeof win.__canary.getState === 'function') {
      return win.__canary.getState();
    }
  } catch (_e) {
    // fall through
  }
  return null;
}

function runCriterion(html, program) {
  const { sandbox, win } = makeEnv();
  try {
    vm.runInNewContext(extractScript(html), sandbox, { timeout: 2000 });
  } catch (e) {
    return { snapshots: [], hasCanary: false, error: String(e) };
  }
  const snapshots = [];
  for (const step of program) {
    if (step.op === 'getState') {
      snapshots.push(snapshot(win));
    } else if (step.op === 'key') {
      dispatchKey(win, step.key);
    } else if (step.op === 'frames') {
      pump(win, step.n);
    }
  }
  return { snapshots, hasCanary: !!win.__canary };
}

function main() {
  const buildPath = process.argv[2];
  if (!buildPath) {
    process.stderr.write('usage: drive_interactive.mjs <build-html> < request.json\n');
    process.exit(2);
  }
  const html = fs.readFileSync(buildPath, 'utf8');
  let input = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    input += chunk;
  });
  process.stdin.on('end', () => {
    let request;
    try {
      request = JSON.parse(input);
    } catch (e) {
      process.stderr.write('invalid request JSON: ' + String(e) + '\n');
      process.exit(2);
      return;
    }
    const results = (request.criteria || []).map((c) => ({
      id: c.id,
      ...runCriterion(html, c.program || []),
    }));
    process.stdout.write(JSON.stringify({ results }));
  });
}

main();
