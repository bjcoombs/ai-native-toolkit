"""GitLab CI for the secrets guard, read as GitLab merges it (#1173, #1204).

``lib.secrets_guard`` reads a GitLab job's scan line by line. A job whose keys come from more
than one place (the root file over an included job, an ``extends:`` parent, a ``default:``
script) read wrong that way: an included scan job turned off by a root ``when: never`` read as a
scan, and a visible job that runs a hidden template's scan through ``extends:`` read as none.
This module builds on the shared resolver, ``lib.ci_includes.merged_config`` with
``lib.ci_merged_view`` (see ``lib/docs/ci_includes.md``):

- ``scan_texts(config, resolution, hides)`` returns each text to read with the lines to hold
  back: the root file and each included file with their merged jobs blanked, then the rendered
  merged jobs (renamed ``job<n>``, effective keys only). A merged job's scan counts only when
  the resolver settled it: ``unresolved is None`` and, while an include that could hold a job
  was not read (``hides``), ``not open``. Otherwise its lines are held: not measured, never met.
  A job defined in one file only is held the same way: one from an include under ``rules:``
  (``conditional_include``), and any job while ``hides`` (an unread include could override it). GitLab ``rules:`` are still not evaluated:
  the line reader holds any job that has them.
- ``template_state(config)`` reads GitLab's Secret-Detection template (or component, or Auto
  DevOps) over the merged ``secret_detection`` job: ``absent``, ``runs``, ``off`` or
  ``unevaluated``. A template under an include with ``rules:`` is unevaluated; another include
  under ``rules:`` (SAST, say) is not the template and leaves it as it is. Its keys follow GitLab's precedence (a root key over an included one, maps
  deep-merged): ``rules:``, ``only:`` or ``except:`` leave it unevaluated, a job-level ``when:
  never``/``manual`` turns it off, ``always``/``on_success`` or none runs. An unresolved job is
  unevaluated. ``SECRET_DETECTION_DISABLED`` (GitLab's switch) is read only where GitLab applies
  it to the job: the pipeline's top-level ``variables:`` in any file, and the job's own merged
  ``variables:`` (which win), and the root file's top-level value over an included file's. A value under another job, or under ``rules:`` variables, no
  longer turns the scan off; one inside ``workflow:`` may or may not apply, so it reads
  unevaluated. A truthy value (``true``, ``1``, ``yes``) turns it off.

Inward-only: ``lib.ci_includes``, ``lib.ci_merged_view``, ``lib.secrets_guard_lines``. Tests:
``tests/test_secrets_guard.py``, ``tests/test_secrets_guard_resolved.py``.
"""
from __future__ import annotations

import re

from lib.ci_include_items import include_items
from lib.ci_includes import MergedConfig, Resolution
from lib.ci_merged_view import merged_jobs, merged_view
from lib.secrets_guard_lines import block_end, code_lines

GITLAB_CI = ".gitlab-ci.yml"
# GitLab's template (Auto DevOps includes it) or its CI/CD component (``.../secret-detection@<v>``).
# Matched only against an include item (``names_template``), never a file's text or a ``local:`` path.
GITLAB_TEMPLATE = re.compile(r"(?:^|/)(?:Secret-Detection(?:\.latest)?|Auto-DevOps)\.gitlab-ci\.yml$", re.IGNORECASE)
GITLAB_COMPONENT = re.compile(r"/secret-detection@\S+$", re.IGNORECASE)
_DISABLED_KEY = re.compile(r"""^\s*['"]?SECRET_DETECTION_DISABLED['"]?\s*:\s*(.*)$""")
_VALUE_KEY = re.compile(r"^\s*value\s*:\s*(.*)$")
_TRUTHY = frozenset({"true", "1", "yes"})
_CONDITION_KEYS = frozenset({"rules", "only", "except"})
_RUNS = frozenset({"always", "on_success"})
_OFF = frozenset({"never", "manual"})


def names_template(kind: str, source: str) -> bool:
    """Whether an include item is GitLab's template: a ``template:`` item named by
    ``GITLAB_TEMPLATE``, or a ``component:`` item named by ``GITLAB_COMPONENT``. A ``local:``,
    ``project:`` or ``remote:`` file named like the template is read like any other file (#1204)."""
    source = source.strip().strip("'\"")
    if kind == "template":
        return GITLAB_TEMPLATE.search(source) is not None
    return kind == "component" and GITLAB_COMPONENT.search(source) is not None


def _scalar(value: str) -> str:
    return value.strip().strip("'\"").strip().lower()


def _disabled_values(lines: list[str], start: int, end: int) -> list[bool]:
    """Each ``SECRET_DETECTION_DISABLED`` in ``lines[start:end]``, read truthy or not; its
    ``value:`` form (``SECRET_DETECTION_DISABLED: {value: ...}`` as a block) included."""
    out: list[bool] = []
    for i in range(start, end):
        m = _DISABLED_KEY.match(lines[i])
        if not m:
            continue
        value = m.group(1).strip()
        if not value:
            indent = len(lines[i]) - len(lines[i].lstrip())
            value = next((v.group(1) for ln in lines[i + 1:block_end(lines, i, indent)]
                          if (v := _VALUE_KEY.match(ln))), "")
        elif value.startswith("{"):
            value = next(iter(re.findall(r"value\s*:\s*([^,}]*)", value)), "")
        out.append(_scalar(value) in _TRUTHY)
    return out


def _top_block(lines: list[str], key: str) -> tuple[int, int] | None:
    start = next((i for i, ln in enumerate(lines) if re.match(rf"{key}\s*:", ln)), None)
    return None if start is None else (start, block_end(lines, start, 0))


def _switch(config: MergedConfig) -> str | None:
    """``off``, ``unevaluated`` or None from ``SECRET_DETECTION_DISABLED`` (module docstring)."""
    sources = [code_lines(text) for text in config.sources]
    job = config.jobs.get("secret_detection")
    own: list[bool] = []
    for k in (job.keys.get("variables", ()) if job is not None else ()):
        own += _disabled_values(sources[k.source], k.start, k.end)
    if own:
        return "off" if own[-1] else None   # the job's own variables win over the pipeline's
    pipeline: list[list[bool]] = []
    for lines in sources:
        block = _top_block(lines, "variables")
        pipeline.append(_disabled_values(lines, *block) if block is not None else [])
    # The root file merges last, so its value wins; else any included file's.
    if pipeline[0][-1:] == [True] or (not pipeline[0] and any(any(v) for v in pipeline[1:])):
        return "off"
    for lines in sources:
        flow = _top_block(lines, "workflow")
        if flow is not None and any(_disabled_values(lines, *flow)):
            return "unevaluated"
    return None


def _conditional(resolution: Resolution, named: list[int]) -> bool:
    """Whether the template may sit in an include under ``rules:``: an unread one that is the
    template's own entry (never read; matched by ``names_template``, so an unrelated template
    such as SAST under ``rules:`` does not count, #1204), or every read file that names it. An
    unread slot with no item text could be the template and counts."""
    unread = any(slot.file is None and slot.conditional and slot.jobs is None
                 and (slot.include is None or names_template(*slot.include.split(": ", 1)))
                 for slot in resolution.slots)
    read = {slot.file + 1 for slot in resolution.slots if slot.file is not None and slot.conditional}
    return unread or set(named) <= read


def template_state(config: MergedConfig, resolution: Resolution) -> str:
    """GitLab's Secret-Detection template over the merged pipeline (module docstring)."""
    named = [s for s, text in enumerate(config.sources)
             if any(names_template(item.kind, item.source) for item in include_items(text)[0])]
    if not named:
        return "absent"
    switch = _switch(config)
    if switch == "off":
        return "off"
    job = config.jobs.get("secret_detection")
    shape = "runs"
    if job is not None:
        if job.unresolved is not None or _CONDITION_KEYS & set(job.keys):
            shape = "unevaluated"
        elif "when" in job.keys:
            last = job.keys["when"][-1]
            when = _scalar(config.sources[last.source].splitlines()[last.start].split(":", 1)[1])
            shape = "runs" if when in _RUNS else "off" if when in _OFF else "unevaluated"
    unsure = switch == "unevaluated" or _conditional(resolution, named)
    return "unevaluated" if unsure and shape == "runs" else shape


def scan_texts(config: MergedConfig, resolution: Resolution,
               hides: bool) -> list[tuple[str, str, frozenset[int]]]:
    """(label, text, held lines) for each text the line reader reads (module docstring)."""
    view = merged_view(config)
    jobs = merged_jobs(config)
    merged = {job.name for job in jobs}
    held: dict[int, set[int]] = {}
    for job in config.jobs.values():
        if (job.unresolved is not None or (job.open and hides)) and job.name not in merged:
            for d in job.defs:
                held.setdefault(d.source, set()).update(range(d.start, d.end))
    out: list[tuple[str, str, frozenset[int]]] = []
    for s, text in enumerate(view.texts):
        label = GITLAB_CI if s == 0 else f"{GITLAB_CI} include (line {resolution.files[s - 1].line})"
        out.append((label, text, frozenset(held.get(s, ()))))
    if jobs:
        rendered = view.rendered.splitlines()
        starts = {ln[:-1]: i for i, ln in enumerate(rendered) if re.fullmatch(r"job\d+:", ln)}
        unsure = set(view.unsettled)
        for n, job in enumerate(jobs):
            start = starts.get(f"job{n}")
            if start is not None and job.open and hides:
                unsure.update(range(start, block_end(rendered, start, 0)))
        out.append((f"{GITLAB_CI} (merged jobs)", view.rendered, frozenset(unsure)))
    return out
