"""Keyhole-signal unit tests for one finding family.

Covers the documentation, understanding and runtime run-context blocks.

The pure, git-free derivation core; end-to-end wiring (build_run_context)
is covered in test_assess_core.py. Split from the former
test_keyhole_signals.py by finding family; shared integrate() fixtures live
in keyhole_helpers.py.
"""
from __future__ import annotations

from lib import keyhole_signals as ks


# --- documentation block -----------------------------------------------------

def test_documentation_block_maps_doc_join() -> None:
    doc_join = {
        "available": True,
        "high_ccn_threshold": 10.0,
        "docs": [
            {"path": "docs/api.md", "complexity_summarised": 20.0, "freshness": -1.0,
             "doc_value": -20.0, "finding": "lying_map", "confidence": "high",
             "subject_code_count": 2, "recommendation": "fix or delete"},
            {"path": "src/hot.py", "complexity_summarised": 25.0, "freshness": 0.0,
             "doc_value": 0.0, "finding": "unexplained_complexity", "confidence": None,
             "subject_code_count": 0, "recommendation": "write contract"},
        ],
        "findings": {
            "lying_maps": [{"path": "docs/api.md"}],
            "unexplained_complexity": [{"path": "src/hot.py"}],
            "good_contracts": [],
        },
    }
    block = ks.build_documentation_block(doc_join)
    assert block["available"] is True
    assert block["freshness_by_doc"] == {"docs/api.md": -1.0}
    assert "docs/api.md" in block["complexity_coverage"]
    assert "src/hot.py" not in block["freshness_by_doc"]  # not a real doc
    assert [d["path"] for d in block["stale_doc_on_complexity"]] == ["docs/api.md"]
    assert [d["path"] for d in block["unexplained_complexity"]] == ["src/hot.py"]


def test_documentation_block_unavailable_passthrough() -> None:
    block = ks.build_documentation_block({"available": False})
    assert block["available"] is False


# --- understanding block -----------------------------------------------------

def test_understanding_block_maps_understanding() -> None:
    understanding = {
        "available": True,
        "high_ccn_threshold": 10.0,
        "modules": [
            {"path": "a.py", "human_anchor": True, "intent_source": False,
             "authorship_class": "human", "days_since_comprehension_event": 3,
             "finding": None, "recommendation": None},
            {"path": "b.py", "human_anchor": False, "intent_source": False,
             "authorship_class": "agent", "days_since_comprehension_event": None,
             "finding": "orphaned_understanding", "recommendation": "anchor"},
        ],
        "orphaned_understanding": ["b.py"],
    }
    block = ks.build_understanding_block(understanding)
    assert block["human_anchor_by_path"] == {"a.py": True, "b.py": False}
    assert block["intent_source_by_path"] == {"a.py": False, "b.py": False}
    assert block["authorship_class_by_path"] == {"a.py": "human", "b.py": "agent"}
    assert block["orphaned_understanding"] == ["b.py"]


# --- runtime block -----------------------------------------------------------

def test_runtime_block_carries_static_reachability() -> None:
    dead_code = {
        "available": True, "candidate_count": 1,
        "candidates": [{"path": "dead.py", "symbol": "f", "line": 1, "kind": "unused"}],
        "tools": [{"tool": "vulture", "status": "ran"}],
        "caveat": "static only",
    }
    observability = {"rung": 1, "reachable": {"present": False, "signals": []}}
    block = ks.build_runtime_block(dead_code, observability)
    assert block["static_reachability"]["candidate_count"] == 1
    assert block["static_reachability"]["candidates"][0]["path"] == "dead.py"
    assert block["observability_rung"] == 1
    assert block["runtime_evidence_available"] is False
