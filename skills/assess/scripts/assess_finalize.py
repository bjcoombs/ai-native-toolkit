"""LLM write-back for /assess: fill placeholders left by the deterministic core.

The deterministic core writes log.md and hotspots/*.md with placeholders for
LLM-derived content (score, maturity label, top action, per-hotspot actions).
After the LLM writes assess-report.md, it also writes finalize-input.json with
its derived values and invokes this script to update the wiki files in place.

Reads (in order; first hit wins):
    {repo_root}/.assess/.cache/finalize-input.json  (preferred - transient cache)
    {repo_root}/.assess/finalize-input.json         (legacy - written to working tree)

The input file is **consumed and deleted** on success. It carries no future
utility past the run that produced it, and leaving it in the working tree
caused noisy diffs when users committed `.assess/` (issue #39).

Updates:
    {repo_root}/.assess/log.md           (last entry's placeholders)
    {repo_root}/.assess/hotspots/*.md    (Suggested actions sections)

Writes (when the input carries an ``actions`` array):
    {repo_root}/.assess/actions.json     (durable machine-readable Top 3
                                          action contract for executor agents)

Run:
    uv run assess_finalize.py <repo_root>
"""
# /// script
# requires-python = ">=3.11"
# ///
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# Make sibling lib package importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.badge import maturity_band, score_badge, write_badge
from lib.wiki_writer import slug_for_path


class FinalizeValidationError(Exception):
    """Raised when finalize-input.json violates a run-context invariant.

    finalize is fail-closed: on any violation it raises *before writing
    anything*, so a torn, mismatched, or fabricated input can never reach the
    wiki, the badge, or the actions contract. ``main()`` catches it and exits
    non-zero with the specific violation named.

    The invariants this guards (see ``_validate_finalize_input``) are the
    contract that lets a downstream reader trust the finalised wiki: the score
    fits its denominator, the maturity label matches the score band, every
    hotspot action names a real hotspot, the input came from *this* run (run_id),
    and Layer 6 never claims proof (Present) the run never gathered (no mutation).
    """


# Canonical maturity tier keywords, used to pull the tier the LLM claimed out of
# a free-text ``maturity_label`` (which may be decorated, e.g.
# "Knowledge Base · Solid (3 applicable layers)"). No pair is a substring of
# another, so a single containment test per keyword is unambiguous.
_MATURITY_KEYWORDS = ("AI-Native", "Not Ready", "Solid", "Basic")

# The annotation the LLM must attach to Layer 6 when mutation testing never ran.
# Mirrors ``assess_core.MUTATION_NOT_RUN_ANNOTATION`` (the two scripts share no
# import, so the literal is duplicated); the finalize error names it so a caller
# knows the required remediation.
MUTATION_NOT_RUN_ANNOTATION = "truth-pressure unproven (mutation not run)"


def _finalize_log(
    assess_dir: Path,
    *,
    score: float,
    maturity_label: str,
    top_action: str,
    denominator: int = 8,
) -> None:
    """Replace placeholders in the latest log.md entry.

    Only the most recent entry (top of file after the heading) is updated.
    Prior entries are immutable historical records. ``denominator`` is 8 for a
    software repo and the applicable-layer count for a knowledge base (#224),
    so the finalised line reads ``2.5 / 3`` rather than ``2.5 / 8``.
    """
    log_path = assess_dir / "log.md"
    if not log_path.exists():
        return

    text = log_path.read_text(encoding="utf-8")
    # Replace the LAST occurrence of each placeholder - log entries are appended
    # (newest at the bottom of the file), and we want to finalize the latest run.
    # An unfinalized older entry stays untouched as historical evidence. The
    # placeholder the core writes is always "/ 8"; the finalised denominator may
    # differ for a knowledge base, so the replacement carries it explicitly.
    text = _replace_last(
        text,
        pattern=r"\*\*AI Readiness:\*\* [\d.]+ / 8 \(\(LLM fills in\)\)",
        replacement=f"**AI Readiness:** {score} / {denominator} ({maturity_label})",
    )
    text = _replace_last(
        text,
        pattern=r"\*\*Top action:\*\* Deterministic ranker not yet wired \(LLM picks Top 3\)",
        replacement=f"**Top action:** {top_action}",
    )
    log_path.write_text(text, encoding="utf-8")


def _replace_last(text: str, *, pattern: str, replacement: str) -> str:
    """Replace the LAST occurrence of pattern in text with replacement (literal).

    Uses slicing rather than re.sub so the replacement is a literal string,
    not subject to backreference interpretation.
    """
    matches = list(re.finditer(pattern, text))
    if not matches:
        return text
    last = matches[-1]
    return text[:last.start()] + replacement + text[last.end():]


def _finalize_hotspot_actions(assess_dir: Path, *, hotspot_actions: dict[str, list[str]]) -> None:
    """Rewrite the 'Suggested actions' section of each hotspot page.

    Pages whose paths are absent from hotspot_actions are left as-is. Paths in
    hotspot_actions whose pages don't exist are silently skipped (lifecycle:
    a hotspot might have graduated between LLM read and finalize).
    """
    hotspots_dir = assess_dir / "hotspots"
    if not hotspots_dir.exists():
        return

    for path, actions in hotspot_actions.items():
        slug = slug_for_path(path)
        page_path = hotspots_dir / f"{slug}.md"
        if not page_path.exists():
            continue

        page_text = page_path.read_text(encoding="utf-8")
        actions_block = "\n".join(f"- {a}" for a in actions) if actions else "- (no actions)"
        # Replace the section content between "## Suggested actions" and end-of-file (or next ## heading)
        new_text = re.sub(
            r"(## Suggested actions\s*\n\s*\n).*?(\n##\s|$)",
            lambda m: m.group(1) + actions_block + "\n" + m.group(2),
            page_text,
            count=1,
            flags=re.DOTALL,
        )
        page_path.write_text(new_text, encoding="utf-8")


# Keys every action contract entry must carry. The executor-critical pair is
# done_when (the exit criterion - without it a weak model doesn't know when to
# stop) and scope_fence (what NOT to touch - without it a weak model
# over-extends). Entries missing required keys are dropped with a warning
# rather than failing the run: a partial contract beats none, but a malformed
# entry must not reach an executor as if it were complete.
ACTION_REQUIRED_KEYS = {"rank", "action", "done_when", "scope_fence"}


def _write_actions_contract(assess_dir: Path, actions: list[dict]) -> None:
    """Write the durable machine-readable Top 3 contract to actions.json.

    Unlike finalize-input.json (consumed and deleted - transient by design),
    actions.json persists: it is the artifact an executing agent reads to know
    what to do, how to verify it, and where to stop, without parsing the
    report's markdown table.
    """
    valid = []
    for a in actions:
        if not isinstance(a, dict) or not ACTION_REQUIRED_KEYS <= set(a):
            missing = ACTION_REQUIRED_KEYS - set(a) if isinstance(a, dict) else ACTION_REQUIRED_KEYS
            print(
                f"actions.json: dropping malformed entry (missing {sorted(missing)})",
                file=sys.stderr,
            )
            continue
        valid.append(a)
    if not valid:
        return
    payload = {"schema": 1, "actions": sorted(valid, key=lambda a: a["rank"])}
    (assess_dir / "actions.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _locate_input(assess_dir: Path) -> Path:
    """Find finalize-input.json. Prefer the transient cache location; fall back
    to the legacy in-tree path for backwards compatibility.
    """
    cache_path = assess_dir / ".cache" / "finalize-input.json"
    if cache_path.exists():
        return cache_path
    legacy = assess_dir / "finalize-input.json"
    if legacy.exists():
        return legacy
    raise FileNotFoundError(
        f"finalize-input.json not found at {cache_path} or {legacy}"
    )


def _load_run_context(assess_dir: Path) -> dict:
    """Load run-context.json, the ground truth finalize reconciles against.

    Its absence is a hard failure, not a skip: finalize *reconciles* the
    LLM-authored input against the deterministic core's output, so with no
    run-context there is nothing to reconcile against and the invariants can't be
    enforced - the fail-closed choice is to refuse.
    """
    path = assess_dir / "run-context.json"
    if not path.exists():
        raise FinalizeValidationError(
            f"run-context.json missing at {path} - finalize cannot reconcile the "
            "LLM input against the deterministic core's output; refusing to write"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FinalizeValidationError(
            f"run-context.json at {path} is not valid JSON: {e}"
        ) from e


def _claimed_maturity_tier(label: str) -> str | None:
    """The canonical maturity tier named inside a (possibly decorated) label.

    Returns None when no single recognised tier is present - a custom label with
    zero or ambiguously many keywords carries too little structure to reconcile,
    so that specific check is skipped rather than false-rejected.
    """
    low = label.lower()
    hits = [k for k in _MATURITY_KEYWORDS if k.lower() in low]
    return hits[0] if len(hits) == 1 else None


def _layer_score(data: dict, layer: int) -> float | None:
    """The LLM-supplied numeric score for one layer, or None if not carried.

    ``layer_scores`` maps a layer id to its 0.0/0.5/1.0 band (Missing/Partial/
    Present). JSON object keys are strings, but an int key is tolerated too. A
    legacy input with no ``layer_scores`` returns None so the layer-cap check is
    skipped rather than firing on absent data.
    """
    scores = data.get("layer_scores")
    if not isinstance(scores, dict):
        return None
    for key in (str(layer), layer):
        if key in scores:
            try:
                return float(scores[key])
            except (TypeError, ValueError):
                return None
    return None


def _validate_run_id_match(data: dict, ctx: dict) -> None:
    """Both artifacts must be from the same run (torn-write detection).

    Only enforced when *both* carry a run_id: a legacy artifact missing the
    stamp still finalises (backward compat). A disagreement means the
    finalize-input was authored against a different run-context than the one on
    disk - a torn write finalize must refuse.
    """
    in_id = data.get("run_id")
    ctx_id = ctx.get("run_id")
    if in_id and ctx_id and in_id != ctx_id:
        raise FinalizeValidationError(
            f"run_id mismatch (torn write): finalize-input run_id {in_id!r} != "
            f"run-context run_id {ctx_id!r}; refusing to reconcile artifacts from "
            "different runs"
        )


def _validate_denominator(denominator: int, ctx: dict) -> None:
    """The finalize-input denominator must match the archetype's.

    Skipped when the archetype scan degraded (no available block / no
    denominator) - honest-degrade beats false-rejecting a run whose archetype
    couldn't be determined.
    """
    archetype = ctx.get("archetype")
    if not isinstance(archetype, dict) or not archetype.get("available"):
        return
    ctx_denominator = archetype.get("denominator")
    if ctx_denominator is None:
        return
    if int(ctx_denominator) != denominator:
        raise FinalizeValidationError(
            f"denominator mismatch: finalize-input denominator {denominator} != "
            f"run-context archetype.denominator {ctx_denominator}"
        )


def _validate_score(score: float, denominator: int) -> None:
    """A layered score can never exceed its denominator (the display ceiling)."""
    if score > denominator:
        raise FinalizeValidationError(
            f"score {score} exceeds denominator {denominator}"
        )


def _validate_maturity(score: float, denominator: int, label: str) -> None:
    """The maturity label must name the tier the score actually earns.

    Bands come from ``lib.badge.maturity_band`` (the single source of truth,
    co-located with the badge colour ratios). A label that claims a different
    tier than the score/denominator fraction earns is rejected - the guardrail
    against a run that quietly overstates (or understates) its own readiness.
    """
    claimed = _claimed_maturity_tier(label)
    if claimed is None:
        return
    expected = maturity_band(score, denominator)
    if claimed != expected:
        ratio = (score / denominator) if denominator else 0.0
        raise FinalizeValidationError(
            f"maturity_label {label!r} claims tier {claimed!r} but score "
            f"{score}/{denominator} (ratio {ratio:.3f}) is tier {expected!r}"
        )


def _validate_hotspot_actions(data: dict, ctx: dict) -> None:
    """Every hotspot_actions key must be a real top hotspot from run-context.

    A path the LLM invented (not in ``stats_summary.top_hotspots``) is a
    fabricated map: the error names the offending path so the cause is obvious.
    """
    hotspot_actions = data.get("hotspot_actions", {})
    if not isinstance(hotspot_actions, dict):
        return
    stats_summary = ctx.get("stats_summary")
    top = stats_summary.get("top_hotspots", []) if isinstance(stats_summary, dict) else []
    known = {h.get("path") for h in top if isinstance(h, dict) and h.get("path")}
    for path in hotspot_actions:
        if path not in known:
            raise FinalizeValidationError(
                f"hotspot_actions references {path!r}, which is not a top hotspot "
                f"in run-context.json (stats_summary.top_hotspots). "
                f"Known hotspots: {sorted(known)}"
            )


def _validate_layer6_cap(data: dict, ctx: dict) -> None:
    """Layer 6 cannot exceed Partial when mutation testing never ran.

    ``mutation_not_run_cap.mutation_run`` (or, for a run-context that predates
    the block, ``test_pressure.mutation_run``) is True only when the bounded
    mutation pass actually executed. Without it, a Present verdict (score > 0.5)
    for Layer 6 is an unproven self-description - the guardrail-erosion failure
    /assess exists to catch - so finalize refuses it. A legacy input carrying no
    ``layer_scores`` is exempt (nothing to check).
    """
    cap = ctx.get("mutation_not_run_cap")
    if isinstance(cap, dict):
        mutation_ran = bool(cap.get("mutation_run", False))
    else:
        tp = ctx.get("test_pressure")
        mutation_ran = bool(isinstance(tp, dict) and tp.get("mutation_run", False))
    if mutation_ran:
        return
    layer6 = _layer_score(data, 6)
    if layer6 is None:
        return
    if layer6 > 0.5:
        raise FinalizeValidationError(
            "Layer 6 cannot exceed Partial when mutation testing was not run "
            f"(scored {layer6}). Annotation required: "
            f"'{MUTATION_NOT_RUN_ANNOTATION}'"
        )


def _validate_finalize_input(data: dict, ctx: dict, *, denominator: int) -> None:
    """Run every finalize invariant. Raises FinalizeValidationError on the first
    violation, before any write - so a bad input reaches nothing.
    """
    _validate_run_id_match(data, ctx)
    _validate_denominator(denominator, ctx)
    _validate_score(float(data["score"]), denominator)
    _validate_maturity(float(data["score"]), denominator, data["maturity_label"])
    _validate_hotspot_actions(data, ctx)
    _validate_layer6_cap(data, ctx)


def finalize_run(*, assess_dir: Path) -> None:
    """Read finalize-input.json, validate it against run-context.json, apply it
    to log.md and hotspot pages, then delete the input file.

    Fail-closed: run-context.json is read and every invariant checked *before*
    any write. Any violation raises ``FinalizeValidationError`` and nothing is
    written.
    """
    input_path = _locate_input(assess_dir)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    ctx = _load_run_context(assess_dir)
    # Denominator: 8 for a software repo (the display ceiling), or the count of
    # applicable layers for a knowledge base (issue #224). Defaults to 8 so a
    # pre-archetype finalize-input.json finalises exactly as before.
    denominator = int(data.get("denominator", 8))
    _validate_finalize_input(data, ctx, denominator=denominator)
    _finalize_log(
        assess_dir,
        score=data["score"],
        maturity_label=data["maturity_label"],
        top_action=data["top_action"],
        denominator=denominator,
    )
    _finalize_hotspot_actions(assess_dir, hotspot_actions=data.get("hotspot_actions", {}))
    actions = data.get("actions")
    if isinstance(actions, list) and actions:
        _write_actions_contract(assess_dir, actions)
    # The score badge always overwrites: finalize carries the freshest
    # LLM-scored run, the strongest claim the badge is allowed to make. Stamp it
    # with the run-context run_id (when present) so the badge is traceable.
    write_badge(
        assess_dir,
        score_badge(
            data["score"], data["maturity_label"], denominator,
            run_id=ctx.get("run_id"),
        ),
    )
    # Clean up: the input file is consumed - delete from both the cache and
    # the legacy in-tree location so a stale copy can't leak into a commit.
    try:
        input_path.unlink()
    except OSError:
        pass
    legacy = assess_dir / "finalize-input.json"
    if legacy != input_path and legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: assess_finalize.py <repo_root>", file=sys.stderr)
        return 2
    repo_root = Path(sys.argv[1]).resolve()
    assess_dir = repo_root / ".assess"
    try:
        finalize_run(assess_dir=assess_dir)
    except FinalizeValidationError as e:
        # Fail-closed: a violated invariant means finalize wrote nothing. Name
        # the specific violation and exit non-zero so the run surfaces it.
        print(f"finalize refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
