#!/usr/bin/env python3
"""Acceptance-contract canary harness with blind discrimination checks.

`run_canaries.py` is the semantic layer of the acceptance-contract floor
(PRD prd-acceptance-contract.md E3/E4, success criteria 1, 2, 2b, 3, 12, 14). It
exercises the REAL gate implementations - the real freeze gate (`freeze.py`), the
real cold-exit verifier via the real chokepoint (`spawn_verifier.py` +
`verifier.py`), the real completion validator (`validate_completion.py`) and the
real complete gate (`complete_gate.py`) - against the committed fixtures under
`tests/canaries/` AND against per-run generated blind pairs. It exits non-zero
unless every coupled assertion holds together in one invocation. There are no
mocks and no fixture-identity shortcuts: the gates never see a fixture name, only
a product to drive and a contract to freeze.

## The two-layer execution model (E3), stated honestly

The harness is split into a deterministic layer and an agent-driven layer, and it
never fakes an observation across that seam:

- **Deterministic / machine-executable layer** (always runs, no live agent):
  - The CLI-class fixtures are driven directly - the reference implementation is a
    real `python3` program, so "driving the build" is running it on real input and
    comparing exit code + bytes (criteria 2 and 12, the load-bearing
    machine-verifiable path).
  - Blind-pair GENERATION, the behaviour-preserving transform, the
    behaviour-breaking mutation, the vacuous/sound contract synthesis, the
    drive-against-null kill test, and all pass/fail classification are pure code.
  - The freeze structural checks, record validation, and token authenticity are
    the real gates, imported and called.
- **Agent-driven layer** (interactive fixtures, criteria 1 and 2b): driving a
  browser build's tier-1 criteria genuinely needs a JS runtime. The observations
  enter through an explicit, validated input path - either a bundled headless
  driver (`drive_interactive.mjs`, used when `node` is on PATH) or an
  operator/agent-supplied `--observations` JSON file. If NEITHER is available the
  harness reports those criteria as NOT-RUN and FAILS CLOSED (non-zero); it never
  invents an observation. Classification stays in Python: the driver reports raw
  `getState()` snapshots and this module decides pass/fail from them, so judge
  discretion is minimized (the observation is binary; the verdict follows).

## Blind discrimination (E4) - the anti-lookup-table defence

Fixed fixtures are spoofable by a gate that recognizes fixture identity, so every
run also generates pairs with no fixture identity available:

- **Exit blind pair (criterion 12):** the known-good CLI build is copied to two
  anonymized temp paths with fresh random names, a behaviour-PRESERVING transform
  is applied to BOTH (so neither is byte-identical to the committed fixture and
  static comparison is defeated), and a fresh randomized behaviour-BREAKING but
  parse-valid, non-crashing defect is planted in one. Both are driven through the
  real gates; the intact copy must certify PASS and the mutated copy must FAIL.
- **Freeze blind pair (criterion 14):** a vacuous and a sound contract are
  generated from randomized templates (fresh wording, ids, tokens, paths) each
  run, their criteria are driven against a real null artifact to produce honest
  kill-test results, and the real `freeze.py` must reject the vacuous one at B1
  and permit the sound one.

An implementation of the machine-verifiable canaries that hardcodes fixture
identity passes the named canaries but fails these blind checks by construction.

## Invocation (wired into the floor by the E-wave task 14 / floor.yml)

    python scripts/canaries/run_canaries.py             # node drives interactive
    python scripts/canaries/run_canaries.py --observations obs.json

`obs.json` is `{"<fixture-dir>": {"<criterion-id>": "pass|fail|undriven|escalated"}}`
for the interactive fixtures - the cold agent's recorded observations. The harness
exits 0 iff all coupled assertions hold, non-zero otherwise (including when the
interactive layer could not be run).

The deterministic internals (blind-pair generation properties, the porcelain
driver, the freeze-blind generator) are unit-tested by
`tests/contract/test_canary_harness.py`, so the regular pytest suites cover them
without needing a JS runtime.
"""
from __future__ import annotations

import argparse
import json
import random
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# The real gates live in scripts/contract as flat sibling modules (no package).
# Put that directory on the path and import them so the harness drives the REAL
# implementations, exactly as they run in production (PRD E3).
REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIR_SRC = REPO / "scripts" / "contract"
CANARIES = REPO / "tests" / "canaries"
DRIVER_JS = Path(__file__).resolve().parent / "drive_interactive.mjs"
sys.path.insert(0, str(CONTRACT_DIR_SRC))

import complete_gate as cg  # noqa: E402
import freeze as fz  # noqa: E402
import record_readiness as rr  # noqa: E402
import spawn_verifier as sv  # noqa: E402
import validate_completion as vc  # noqa: E402
import verifier as vr  # noqa: E402


class HarnessError(RuntimeError):
    """A harness-internal failure (driver missing, generation could not satisfy
    its own invariant) distinct from a canary assertion failing."""


# --------------------------------------------------------------------------- #
# Result plumbing.
# --------------------------------------------------------------------------- #


@dataclass
class CheckResult:
    """One coupled canary assertion's outcome."""

    criterion: str
    name: str
    ok: bool
    detail: str
    not_run: bool = False


@dataclass
class PipelineOutcome:
    """The outcome of running one fixture through the real gate pipeline."""

    frozen: bool
    freeze_reasons: List[str] = field(default_factory=list)
    verdict: Optional[str] = None
    certified: bool = False
    complete_gate_exit: Optional[int] = None
    criteria_results: List[Dict[str, Any]] = field(default_factory=list)

    def result_for(self, criterion_id: str) -> Optional[str]:
        for entry in self.criteria_results:
            if entry.get("id") == criterion_id:
                return entry.get("result")
        return None


# --------------------------------------------------------------------------- #
# Subprocess helper.
# --------------------------------------------------------------------------- #


def _run(cmd: List[str], stdin_bytes: bytes = b"", cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run a command capturing bytes; never raises on non-zero exit."""
    return subprocess.run(
        cmd,
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        timeout=60,
        check=False,
    )


# --------------------------------------------------------------------------- #
# CLI-class driver (machine-executable - PRD E3, criteria 2 & 12).
#
# Drives a porcelain-shaped CLI product: it runs the REAL program on real input
# and compares exit code + stdout bytes against the committed expectation. This
# is the harness's knowledge of its OWN fixture (a legitimate test-driver fact),
# never the gate's - the gate only sees the driven pass/fail. The blind exit pair
# uses the same driver on anonymized copies with no identity available, so a
# fixture-lookup gate is caught by construction (criterion 12).
# --------------------------------------------------------------------------- #

KNOWN_GOOD = CANARIES / "known-good"
PORCELAIN_IDS = ("KG1", "KG2", "KG3")


def drive_porcelain(product_py: Path) -> Dict[str, str]:
    """Drive a porcelain CLI product; return {KG1,KG2,KG3: 'pass'|'fail'}.

    KG1: exit 0 on real input. KG2: stdout byte-identical to expected. KG3:
    an unknown flag exits non-zero with a usage message on stderr and empty
    stdout. Running a product that is absent/broken fails these by observation,
    never by name.
    """
    stdin = (KNOWN_GOOD / "input.txt").read_bytes()
    expected = (KNOWN_GOOD / "expected_stdout.txt").read_bytes()

    ok_run = _run([sys.executable, str(product_py)], stdin)
    bogus = _run([sys.executable, str(product_py), "--bogus"], stdin)
    kg3_ok = (
        bogus.returncode != 0
        and bogus.stdout.strip() == b""
        and b"usage" in bogus.stderr.lower()
    )
    return {
        "KG1": "pass" if ok_run.returncode == 0 else "fail",
        "KG2": "pass" if ok_run.stdout == expected else "fail",
        "KG3": "pass" if kg3_ok else "fail",
    }


def porcelain_kill_test_results() -> Dict[str, bool]:
    """Honest CLI kill-test: drive the contract against the class null artifact
    (an absent/no-op entrypoint - "empty repo", PRD B1). Every porcelain
    criterion fails against it, so none passes against the null (non-vacuous)."""
    absent = REPO / "__does_not_exist__.py"  # a genuinely absent entrypoint
    driven = drive_porcelain(absent)
    return {cid: (driven[cid] == "pass") for cid in PORCELAIN_IDS}


# --------------------------------------------------------------------------- #
# Exit blind pair (criterion 12): behaviour-preserving transform on both copies,
# a behaviour-breaking but parse-valid, non-crashing defect in one.
# --------------------------------------------------------------------------- #

# Behaviour-BREAKING string edits on the porcelain source. Each keeps the program
# parse-valid and non-crashing (it still launches and exits cleanly) while making
# one tier-1 observation wrong. The harness verifies post-mutation that the copy
# still runs and that the real gates classify it FAIL before trusting it.
_MUTATORS: List[Tuple[str, str, str]] = [
    (
        "reverse-sort",
        "    return sorted(names), count",
        "    return sorted(names, reverse=True), count",
    ),
    (
        "invert-archived-filter",
        '        if archived == "false":',
        '        if archived == "true":',
    ),
    (
        "stdout-chatter",
        "    for name in names:\n        stdout.write(name + \"\\n\")",
        "    stdout.write(\"canary-noise\\n\")\n    for name in names:\n        stdout.write(name + \"\\n\")",
    ),
    (
        "success-exit-nonzero",
        'stderr.write(f"discovered {count} repos\\n")\n    return 0',
        'stderr.write(f"discovered {count} repos\\n")\n    return 9',
    ),
    (
        "swallow-unknown-flag",
        'stderr.write(f"error: unknown flag {args[0]!r}\\n{USAGE}\\n")\n        return 2',
        "return 0",
    ),
]


def behaviour_preserving_transform(src: str, nonce: str) -> str:
    """Return `src` with a unique nonce comment top and bottom.

    Changes bytes (so no copy is byte-identical to the committed fixture or to its
    sibling) without changing behaviour: comments are inert. This defeats naive
    static comparison against the committed build; the planted defect is only
    observable by running the copy.
    """
    header = "# canary-blind-copy nonce=%s (behaviour-preserving)\n" % nonce
    footer = "\n# end canary-blind-copy nonce=%s\n" % nonce
    return header + src + footer


def apply_mutator(src: str, mutator: Tuple[str, str, str]) -> Optional[str]:
    """Apply one behaviour-breaking edit; return the mutated source or None if the
    edit does not match (so the caller tries another)."""
    _name, find, replace = mutator
    if find not in src:
        return None
    mutated = src.replace(find, replace, 1)
    if mutated == src:
        return None
    try:
        compile(mutated, "<mutant>", "exec")  # parse-valid by construction
    except SyntaxError:
        return None
    return mutated


@dataclass
class BlindExitPair:
    intact_path: Path
    mutant_path: Path
    mutant_name: str


def make_exit_blind_pair(tmp_dir: Path, rng: random.Random) -> BlindExitPair:
    """Generate the per-run exit blind pair.

    Two anonymized copies (fresh random names) of the known-good build, a
    behaviour-preserving transform on BOTH, and a fresh randomized
    behaviour-breaking defect in one. The mutant is verified to still run
    (non-crashing) and to be classified FAIL by the porcelain driver before it is
    trusted; a mutator that does not produce an observable tier-1 failure is
    rejected and another is tried.
    """
    src = (KNOWN_GOOD / "reference_implementation" / "porcelain.py").read_text(encoding="utf-8")
    intact_src = behaviour_preserving_transform(src, secrets.token_hex(6))
    intact_path = tmp_dir / ("intact_%s.py" % secrets.token_hex(8))
    intact_path.write_text(intact_src, encoding="utf-8")

    mutators = rng.sample(_MUTATORS, k=len(_MUTATORS))
    for mutator in mutators:
        base = behaviour_preserving_transform(src, secrets.token_hex(6))
        mutated = apply_mutator(base, mutator)
        if mutated is None:
            continue
        mutant_path = tmp_dir / ("mutant_%s.py" % secrets.token_hex(8))
        mutant_path.write_text(mutated, encoding="utf-8")
        driven = drive_porcelain(mutant_path)
        # Non-crashing and observably wrong: at least one tier-1 criterion FAILS,
        # and the program still produced output/exit codes (it did not traceback
        # into an unusable state on the KG1 path).
        if any(v == "fail" for v in driven.values()):
            return BlindExitPair(intact_path, mutant_path, mutator[0])
        mutant_path.unlink(missing_ok=True)
    raise HarnessError("no behaviour-breaking mutator produced an observable failure")


# --------------------------------------------------------------------------- #
# Freeze blind pair (criterion 14): one vacuous + one sound contract per run,
# randomized, driven against a real null artifact for honest kill-test results.
# --------------------------------------------------------------------------- #

_SOUND_ACTION_TEMPLATES = [
    "Run: python3 {tool} < {inp} ; capture stdout.",
    "Execute {tool} against {inp} and read its standard output.",
    "Invoke {tool} on the fixed input {inp}; record stdout bytes.",
]
_SOUND_OBS_TEMPLATES = [
    "stdout contains the token {tok}.",
    "The output prints the marker line {tok}.",
    "stdout is exactly the expected line {tok}.",
]
_VACUOUS_OBS_TEMPLATES = [
    "No line in {out} equals {tok}.",
    "{out} contains no occurrence of the token {tok}.",
    "There are zero {tok} lines in {out}.",
]


def _rand_word(rng: random.Random, prefix: str) -> str:
    return "%s-%s" % (prefix, "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8)))


def _yaml_scalar(text: str) -> str:
    """Quote a scalar for the constrained contract yaml grammar."""
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def _criterion_block(cid: str, action: str, observation: str) -> str:
    return (
        "  - id: %s\n    tier: 1\n    action: %s\n    observation: %s\n"
        % (cid, _yaml_scalar(action), _yaml_scalar(observation))
    )


@dataclass
class FreezeBlindPair:
    sound_text: str
    sound_kill: Dict[str, bool]
    vacuous_text: str
    vacuous_kill: Dict[str, bool]
    vacuous_id: str


def make_freeze_blind_pair(rng: random.Random) -> FreezeBlindPair:
    """Generate a randomized (vacuous, sound) CLI contract pair with honest,
    drive-derived kill-test results.

    The kill-test booleans are produced by actually running a no-op null artifact
    and evaluating each criterion's predicate on its real (empty) output - a
    positive-output criterion fails against the null (not vacuous); an
    absence-satisfiable criterion passes against the null (vacuous). Nothing is
    asserted by fiat; the vacuity is discovered by driving.
    """
    stdout, _stderr = _run_null_artifact()

    # Sound contract: 2 criteria, each requiring positive output the null lacks.
    sound_ids: List[str] = []
    sound_kill: Dict[str, bool] = {}
    blocks: List[str] = []
    for i in range(2):
        cid = "S%d%s" % (i, _rand_word(rng, "s")[:4])
        tok = _rand_word(rng, "tok")
        tool = _rand_word(rng, "tool") + ".py"
        inp = _rand_word(rng, "in") + ".txt"
        action = rng.choice(_SOUND_ACTION_TEMPLATES).format(tool=tool, inp=inp)
        observation = rng.choice(_SOUND_OBS_TEMPLATES).format(tok=tok)
        blocks.append(_criterion_block(cid, action, observation))
        sound_ids.append(cid)
        sound_kill[cid] = _drive_positive_against_null(stdout, tok)  # -> False
    sound_text = "class: cli\ncriteria:\n" + "".join(blocks)

    # Vacuous contract: one criterion satisfiable by absence.
    vac_id = "V%s" % _rand_word(rng, "v")[:5]
    vac_tok = _rand_word(rng, "tok")
    vac_out = _rand_word(rng, "out") + ".log"
    vac_action = "Run the tool, then scan %s for occurrences of %s." % (vac_out, vac_tok)
    vac_obs = rng.choice(_VACUOUS_OBS_TEMPLATES).format(out=vac_out, tok=vac_tok)
    vacuous_text = "class: cli\ncriteria:\n" + _criterion_block(vac_id, vac_action, vac_obs)
    vacuous_kill = {vac_id: _drive_absence_against_null(stdout, vac_tok)}  # -> True

    return FreezeBlindPair(
        sound_text=_wrap_contract(sound_text),
        sound_kill=sound_kill,
        vacuous_text=_wrap_contract(vacuous_text),
        vacuous_kill=vacuous_kill,
        vacuous_id=vac_id,
    )


def _wrap_contract(yaml_body: str) -> str:
    """Wrap a yaml body in the one-fenced-block contract-md format."""
    return "# Generated blind contract (per-run)\n\n```yaml\n%s```\n" % yaml_body


def _run_null_artifact() -> Tuple[str, str]:
    """Run a real no-op entrypoint (the CLI null artifact) and return its
    (stdout, stderr). A no-op produces nothing - the honest empty output every
    absence-satisfiable criterion is then evaluated against."""
    proc = _run([sys.executable, "-c", ""])
    return proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def _drive_positive_against_null(null_stdout: str, token: str) -> bool:
    """A positive-output criterion 'stdout contains TOKEN' evaluated against the
    null's real (empty) output: the token is absent, so it does NOT pass."""
    return token in null_stdout


def _drive_absence_against_null(null_stdout: str, token: str) -> bool:
    """An absence criterion 'no line equals TOKEN' evaluated against the null's
    real (empty) output: nothing matches, so it trivially PASSES (vacuous)."""
    return not any(line.strip() == token for line in null_stdout.splitlines())


# --------------------------------------------------------------------------- #
# Interactive driver bridge (agent-driven layer - criteria 1 & 2b).
# --------------------------------------------------------------------------- #

_GS = {"op": "getState"}


def _key(k: str) -> Dict[str, Any]:
    return {"op": "key", "key": k}


def _frames(n: int) -> Dict[str, Any]:
    return {"op": "frames", "n": n}


# Per-criterion drive programs. Tier-3 (JF5/LR5) is perceptual and NOT driven
# headlessly - the machinery's honest position (PRD "honest narrowness").
JET_PROGRAMS: Dict[str, List[Dict[str, Any]]] = {
    "JF1": [_GS],
    "JF2": [_GS, _frames(90), _GS],
    "JF3": [_GS, _key("ArrowUp"), _GS],
    "JF4": [_GS, _key(" "), _frames(90), _GS],
}
LR_PROGRAMS: Dict[str, List[Dict[str, Any]]] = {
    "LR1": [_GS],
    "LR2": [_GS, _key(" "), _GS],
    "LR3": [_GS, _key("ArrowUp"), _GS, _key("ArrowDown"), _GS],
    "LR4": [_key(" "), _GS, _key("f"), _GS],
}


def _lane_ok(value: Any) -> bool:
    return isinstance(value, int) and value in (0, 1, 2)


def classify_jet(cid: str, snaps: List[Any]) -> str:
    """Classify a jet-fighters tier-1 criterion from its snapshots."""
    try:
        if cid == "JF1":
            s = snaps[0]
            return "pass" if (s and isinstance(s.get("score"), (int, float)) and _lane_ok(s["launcher"]["lane"])) else "fail"
        if cid == "JF2":
            a, b = snaps[0], snaps[1]
            return "pass" if (a and b and a.get("jets") != b.get("jets")) else "fail"
        if cid == "JF3":
            a, b = snaps[0], snaps[1]
            return "pass" if (a and b and a["launcher"]["lane"] != b["launcher"]["lane"] and _lane_ok(b["launcher"]["lane"])) else "fail"
        if cid == "JF4":
            a, b = snaps[0], snaps[1]
            return "pass" if (a and b and (b.get("missile") is not None or b.get("score", 0) > a.get("score", 0))) else "fail"
    except (KeyError, TypeError, IndexError):
        return "fail"
    return "fail"


def classify_lr(cid: str, snaps: List[Any]) -> str:
    """Classify a Lane Runner tier-1 criterion from its snapshots."""
    try:
        if cid == "LR1":
            s = snaps[0]
            return "pass" if (s and s.get("phase") == "READY" and _lane_ok(s.get("lane")) and s.get("score") == 0) else "fail"
        if cid == "LR2":
            a, b = snaps[0], snaps[1]
            return "pass" if (a and b and a.get("phase") == "READY" and b.get("phase") == "PLAYING") else "fail"
        if cid == "LR3":
            a, b, c = snaps[0], snaps[1], snaps[2]
            return "pass" if (a and b and c and (a.get("lane") != b.get("lane") or b.get("lane") != c.get("lane"))) else "fail"
        if cid == "LR4":
            a, b = snaps[0], snaps[1]
            return "pass" if (a and b and b.get("score") == a.get("score", -99) + 1) else "fail"
    except (KeyError, TypeError, IndexError):
        return "fail"
    return "fail"


# Interactive fixture config: which criteria are tier-1 (headless-drivable) vs
# tier-3 (perceptual), their drive programs, and their classifier.
INTERACTIVE: Dict[str, Dict[str, Any]] = {
    "jet-fighters": {
        "build": "build/index.html",
        "null": "null_artifact/index.html",
        "programs": JET_PROGRAMS,
        "tier3": {"JF5": "undriven"},  # tier-1 fails first; perceptual not reached
        "classify": classify_jet,
    },
    "known-good-interactive": {
        "build": "build/index.html",
        "null": None,  # no committed null artifact; a generated stub is used
        "programs": LR_PROGRAMS,
        "tier3": {"LR5": "escalated"},
        "classify": classify_lr,
    },
}

_INTERACTIVE_NULL_STUB = (
    "<!doctype html><html><head><title>null</title></head><body>"
    "<div id='app'></div><script>'use strict';/* launches, no behaviour, "
    "no drive surface */</script></body></html>\n"
)


def node_available() -> bool:
    return shutil.which("node") is not None


def drive_interactive_build(build_path: Path, programs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Any]]:
    """Drive an interactive build via the node headless driver; return per-criterion
    snapshot lists. Raises HarnessError if node is unavailable or the driver fails."""
    if not node_available():
        raise HarnessError("node runtime not available to drive interactive build")
    request = {"criteria": [{"id": cid, "program": prog} for cid, prog in programs.items()]}
    proc = subprocess.run(
        ["node", str(DRIVER_JS), str(build_path)],
        input=json.dumps(request).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0:
        raise HarnessError("interactive driver failed: %s" % proc.stderr.decode("utf-8", "replace"))
    data = json.loads(proc.stdout.decode("utf-8"))
    return {r["id"]: r.get("snapshots", []) for r in data["results"]}


def observe_interactive_headless(fixture: str, cfg: Dict[str, Any]) -> Dict[str, str]:
    """Return the per-criterion tier-1 outcomes for a fixture's BUILD, driven
    headlessly. Tier-3 criteria are added by the caller (perceptual)."""
    build_path = CANARIES / fixture / cfg["build"]
    snaps = drive_interactive_build(build_path, cfg["programs"])
    classify: Callable[[str, List[Any]], str] = cfg["classify"]
    return {cid: classify(cid, snaps.get(cid, [])) for cid in cfg["programs"]}


def interactive_null_kill_test(fixture: str, cfg: Dict[str, Any], tmp_dir: Path) -> Dict[str, bool]:
    """Honest B1 kill test: drive every criterion against the interactive null
    artifact (a stub that launches with no behaviour). Tier-1 criteria fail
    (no drive surface); tier-3 fails too (nothing to perceive). None passes, so
    the contract is not vacuous and freeze is permitted."""
    if cfg["null"]:
        null_path = CANARIES / fixture / cfg["null"]
    else:
        null_path = tmp_dir / ("null_%s.html" % fixture)
        null_path.write_text(_INTERACTIVE_NULL_STUB, encoding="utf-8")
    snaps = drive_interactive_build(null_path, cfg["programs"])
    classify: Callable[[str, List[Any]], str] = cfg["classify"]
    kill: Dict[str, bool] = {cid: (classify(cid, snaps.get(cid, [])) == "pass") for cid in cfg["programs"]}
    for cid in cfg["tier3"]:
        kill[cid] = False  # nothing perceivable in a null stub
    return kill


# --------------------------------------------------------------------------- #
# The real gate pipeline (freeze -> readiness -> chokepoint -> verifier ->
# validate -> complete gate). Reused by every fixture that reaches cold exit.
# --------------------------------------------------------------------------- #


def _write_kill_test(path: Path, contract_hash: str, results: Dict[str, bool]) -> None:
    payload = {
        "contract_sha256": contract_hash,
        "results": [{"id": cid, "passed_against_null": passed} for cid, passed in results.items()],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _driven_results(outcomes: Dict[str, str]) -> List["vr.DrivenResult"]:
    driven: List[vr.DrivenResult] = []
    for cid, outcome in outcomes.items():
        artifact = "pending-operator-observation" if outcome == "escalated" else None
        driven.append(
            vr.DrivenResult(
                criterion_id=cid,
                outcome=outcome,
                observation="canary-driven: %s" % outcome,
                artifact_path=artifact,
            )
        )
    return driven


def run_pipeline(
    run_id: str,
    contract_src: str,
    product_path: Path,
    outcomes: Dict[str, str],
    kill_results: Dict[str, bool],
    tmp_dir: Path,
    readiness_source: str = "human",
) -> PipelineOutcome:
    """Freeze the contract and (if frozen) drive it through the real cold-exit
    pipeline against `product_path`. Returns the observed pipeline outcome."""
    contract_path = tmp_dir / ("%s.contract.md" % run_id)
    contract_path.write_text(contract_src, encoding="utf-8")
    contract_hash = fz.contract_sha256(contract_path)

    kill_path = tmp_dir / ("%s.kill.json" % run_id)
    _write_kill_test(kill_path, contract_hash, kill_results)

    freeze_result = fz.freeze(contract_path, run_id, kill_path, contract_dir=tmp_dir)
    if not freeze_result.frozen:
        return PipelineOutcome(frozen=False, freeze_reasons=list(freeze_result.reasons))

    rr.record_readiness(run_id, "ready", readiness_source, contract_dir=tmp_dir)
    spawn = sv.spawn_verifier(contract_path, product_path, contract_dir=tmp_dir)
    observations = vr.run_verifier(spawn, _driven_results(outcomes))
    record = sv.ingest_verifier_results(spawn, observations)
    result = vc.validate_path(spawn.completion_path, provenance_dir=tmp_dir)
    exit_code = cg.complete_gate(run_id, tmp_dir)
    return PipelineOutcome(
        frozen=True,
        verdict=result.verdict,
        certified=result.certified,
        complete_gate_exit=exit_code,
        criteria_results=list(record.get("criteria_results", [])),
    )


# --------------------------------------------------------------------------- #
# The coupled canary checks.
# --------------------------------------------------------------------------- #


def check_known_good_cli(tmp_dir: Path) -> CheckResult:
    """Criterion 2: the known-good CLI certifies PASS end-to-end."""
    contract_src = (KNOWN_GOOD / "contract.md").read_text(encoding="utf-8")
    product = KNOWN_GOOD / "reference_implementation" / "porcelain.py"
    outcomes = drive_porcelain(product)
    kill = porcelain_kill_test_results()
    out = run_pipeline("canary-known-good", contract_src, product, outcomes, kill, tmp_dir)
    ok = out.frozen and out.verdict == vc.VERDICT_PASS and out.complete_gate_exit == 0
    detail = "verdict=%s complete_gate=%s driven=%s" % (out.verdict, out.complete_gate_exit, outcomes)
    return CheckResult("2", "known-good CLI certifies PASS", ok, detail)


def check_exit_blind_pair(tmp_dir: Path, rng: random.Random) -> CheckResult:
    """Criterion 12: intact anonymized copy PASS, mutated copy FAIL, no identity."""
    pair = make_exit_blind_pair(tmp_dir, rng)
    contract_src = (KNOWN_GOOD / "contract.md").read_text(encoding="utf-8")
    kill = porcelain_kill_test_results()

    intact = run_pipeline(
        "blind-intact-%s" % secrets.token_hex(4), contract_src, pair.intact_path,
        drive_porcelain(pair.intact_path), kill, tmp_dir,
    )
    mutant = run_pipeline(
        "blind-mutant-%s" % secrets.token_hex(4), contract_src, pair.mutant_path,
        drive_porcelain(pair.mutant_path), kill, tmp_dir,
    )
    ok = (
        intact.verdict == vc.VERDICT_PASS
        and intact.complete_gate_exit == 0
        and mutant.verdict == vc.VERDICT_FAIL
        and mutant.complete_gate_exit != 0
    )
    detail = "mutator=%s intact=%s mutant=%s" % (pair.mutant_name, intact.verdict, mutant.verdict)
    return CheckResult("12", "exit blind pair (PASS intact / FAIL mutant)", ok, detail)


def check_vacuous_fixture_freeze(tmp_dir: Path) -> CheckResult:
    """Criterion 3: the committed vacuous fixture is rejected at freeze (B1)."""
    contract_src = (CANARIES / "vacuous-contract" / "contract.md").read_text(encoding="utf-8")
    stdout, _stderr = _run_null_artifact()
    # VC1 is absence-satisfiable ("no error-level lines in output.log"); driven
    # against the null's real (empty) output it PASSES -> vacuous.
    kill = {"VC1": _drive_absence_against_null(stdout, "ERROR")}
    out = run_pipeline("canary-vacuous", contract_src, tmp_dir, {}, kill, tmp_dir)
    ok = (not out.frozen) and any("VC1" in r and "vacuous" in r for r in out.freeze_reasons)
    detail = "frozen=%s reasons=%s" % (out.frozen, out.freeze_reasons)
    return CheckResult("3", "vacuous contract rejected at freeze", ok, detail)


def check_freeze_blind_pair(tmp_dir: Path, rng: random.Random) -> CheckResult:
    """Criterion 14: generated vacuous rejected at B1, generated sound permitted."""
    pair = make_freeze_blind_pair(rng)

    sound_id = "freeze-blind-sound-%s" % secrets.token_hex(4)
    sound_path = tmp_dir / ("%s.contract.md" % sound_id)
    sound_path.write_text(pair.sound_text, encoding="utf-8")
    sound_kill = tmp_dir / ("%s.kill.json" % sound_id)
    _write_kill_test(sound_kill, fz.contract_sha256(sound_path), pair.sound_kill)
    sound = fz.freeze(sound_path, sound_id, sound_kill, contract_dir=tmp_dir)

    vac_id = "freeze-blind-vacuous-%s" % secrets.token_hex(4)
    vac_path = tmp_dir / ("%s.contract.md" % vac_id)
    vac_path.write_text(pair.vacuous_text, encoding="utf-8")
    vac_kill = tmp_dir / ("%s.kill.json" % vac_id)
    _write_kill_test(vac_kill, fz.contract_sha256(vac_path), pair.vacuous_kill)
    vacuous = fz.freeze(vac_path, vac_id, vac_kill, contract_dir=tmp_dir)

    vac_rejected = (not vacuous.frozen) and any(pair.vacuous_id in r and "vacuous" in r for r in vacuous.reasons)
    ok = sound.frozen and vac_rejected
    detail = "sound_frozen=%s vacuous_frozen=%s vacuous_id=%s" % (sound.frozen, vacuous.frozen, pair.vacuous_id)
    return CheckResult("14", "freeze blind pair (reject vacuous / permit sound)", ok, detail)


def _interactive_outcomes(
    fixture: str, cfg: Dict[str, Any], observations: Optional[Dict[str, Dict[str, str]]]
) -> Dict[str, str]:
    """Resolve a fixture's per-criterion outcomes: agent-supplied observations win;
    else drive headlessly. Tier-3 outcomes are added from the fixture config."""
    if observations is not None and fixture in observations:
        supplied = observations[fixture]
        outcomes = {cid: supplied[cid] for cid in cfg["programs"] if cid in supplied}
        if len(outcomes) != len(cfg["programs"]):
            raise HarnessError("supplied observations for %s omit tier-1 criteria" % fixture)
    else:
        outcomes = observe_interactive_headless(fixture, cfg)
    outcomes.update(cfg["tier3"])
    return outcomes


def check_jet_fighters(tmp_dir: Path, observations: Optional[Dict[str, Dict[str, str]]]) -> CheckResult:
    """Criterion 1: the broken jet-fighters build never certifies AND its tier-1
    launch/drive failures (JF2/JF3/JF4) are DETECTED in the record."""
    cfg = INTERACTIVE["jet-fighters"]
    outcomes = _interactive_outcomes("jet-fighters", cfg, observations)
    kill = interactive_null_kill_test("jet-fighters", cfg, tmp_dir)
    contract_src = (CANARIES / "jet-fighters" / "contract.md").read_text(encoding="utf-8")
    build = CANARIES / "jet-fighters" / cfg["build"]
    out = run_pipeline("canary-jet-fighters", contract_src, build, outcomes, kill, tmp_dir)

    detected = all(out.result_for(cid) == "fail" for cid in ("JF2", "JF3", "JF4"))
    never_certifies = out.frozen and not out.certified and out.complete_gate_exit != 0
    ok = never_certifies and detected
    detail = "verdict=%s JF2=%s JF3=%s JF4=%s" % (
        out.verdict, out.result_for("JF2"), out.result_for("JF3"), out.result_for("JF4"),
    )
    return CheckResult("1", "jet-fighters never certifies (tier-1 failures detected)", ok, detail)


def check_known_good_interactive(tmp_dir: Path, observations: Optional[Dict[str, Dict[str, str]]]) -> CheckResult:
    """Criterion 2b: the working interactive build passes tier-1 and stalls ONLY
    at tier-3 escalation (awaiting operator sign-off, not certified)."""
    cfg = INTERACTIVE["known-good-interactive"]
    outcomes = _interactive_outcomes("known-good-interactive", cfg, observations)
    kill = interactive_null_kill_test("known-good-interactive", cfg, tmp_dir)
    contract_src = (CANARIES / "known-good-interactive" / "contract.md").read_text(encoding="utf-8")
    build = CANARIES / "known-good-interactive" / cfg["build"]
    out = run_pipeline("canary-known-good-interactive", contract_src, build, outcomes, kill, tmp_dir)

    tier1_pass = all(out.result_for(cid) == "pass" for cid in cfg["programs"])
    stalls_at_tier3 = out.frozen and out.verdict == vc.VERDICT_AWAITING_TIER3 and out.complete_gate_exit != 0
    ok = tier1_pass and stalls_at_tier3
    detail = "verdict=%s tier1_pass=%s" % (out.verdict, tier1_pass)
    return CheckResult("2b", "known-good interactive: tier-1 PASS, stall at tier-3", ok, detail)


def check_interactive(
    tmp_dir: Path, observations: Optional[Dict[str, Dict[str, str]]]
) -> List[CheckResult]:
    """Run the two interactive checks, or report them NOT-RUN and fail closed when
    the agent-driven layer is unavailable (no observations and no JS runtime)."""
    if observations is None and not node_available():
        reason = "interactive layer NOT RUN: no --observations file and no node runtime (fail closed)"
        return [
            CheckResult("1", "jet-fighters never certifies", False, reason, not_run=True),
            CheckResult("2b", "known-good interactive tier-1 PASS / tier-3 stall", False, reason, not_run=True),
        ]
    try:
        return [
            check_jet_fighters(tmp_dir, observations),
            check_known_good_interactive(tmp_dir, observations),
        ]
    except HarnessError as exc:
        reason = "interactive layer NOT RUN: %s (fail closed)" % exc
        return [
            CheckResult("1", "jet-fighters never certifies", False, reason, not_run=True),
            CheckResult("2b", "known-good interactive tier-1 PASS / tier-3 stall", False, reason, not_run=True),
        ]


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #


def run_all(tmp_dir: Path, observations: Optional[Dict[str, Dict[str, str]]], seed: Optional[int]) -> List[CheckResult]:
    """Run every coupled canary check in one invocation and return their results."""
    rng = random.Random(seed) if seed is not None else random.Random(secrets.randbits(64))
    results: List[CheckResult] = [
        check_known_good_cli(tmp_dir),
        check_exit_blind_pair(tmp_dir, rng),
        check_vacuous_fixture_freeze(tmp_dir),
        check_freeze_blind_pair(tmp_dir, rng),
    ]
    results.extend(check_interactive(tmp_dir, observations))
    results.sort(key=lambda r: (len(r.criterion), r.criterion))
    return results


def _load_observations(path: Optional[str]) -> Optional[Dict[str, Dict[str, str]]]:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("observations file must be a JSON object of {fixture: {criterion: outcome}}")
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Acceptance-contract canary harness (semantic layer).")
    parser.add_argument(
        "--observations",
        default=None,
        help="JSON {fixture: {criterion: pass|fail|undriven|escalated}} for the "
        "interactive fixtures (the cold agent's recorded observations). When "
        "omitted, the bundled node driver drives them headlessly; if node is also "
        "absent the interactive checks are reported NOT RUN and the harness fails closed.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed the per-run blind generators for reproducibility (default: fresh randomness each run)",
    )
    args = parser.parse_args(argv)
    observations = _load_observations(args.observations)

    import tempfile

    with tempfile.TemporaryDirectory(prefix="canary-") as td:
        results = run_all(Path(td), observations, args.seed)

    failed = [r for r in results if not r.ok]
    print("acceptance-contract canary harness")
    print("=" * 70)
    for r in results:
        flag = "NOT-RUN" if r.not_run else ("PASS" if r.ok else "FAIL")
        print("[%-7s] criterion %-3s  %s" % (flag, r.criterion, r.name))
        print("            %s" % r.detail)
    print("=" * 70)
    if failed:
        print("HARNESS RED: %d of %d coupled assertions did not hold." % (len(failed), len(results)))
        return 1
    print("HARNESS GREEN: all %d coupled assertions hold." % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
