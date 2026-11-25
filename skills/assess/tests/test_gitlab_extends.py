"""GitLab ``extends:`` and ``except:`` in the CI trigger reader (issue #1021)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.ci.ci_checks import scan_ci_checks
from lib.ci.ci_triggers import gitlab_line_triggers

_NIGHTLY = '.nightly:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == "schedule"\n'
_MR = '.mr:\n  rules:\n    - if: $CI_PIPELINE_SOURCE == "merge_request_event"\n'
_REPRO = _NIGHTLY + "test:\n  extends: .nightly\n  script: pytest\n"


def _trigger(text: str) -> str:
    """The trigger of the ``pytest`` line in ``text``."""
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if "pytest" in line)
    return gitlab_line_triggers(text)[index]


def _enforced(tmp_path: Path, text: str) -> bool | None:
    (tmp_path / ".gitlab-ci.yml").write_text(text, encoding="utf-8")
    block = scan_ci_checks(tmp_path)
    assert block["test_or_lint_configured"] is True
    return block["enforced_check_runs"]


def test_the_issue_repro_is_not_enforced(tmp_path: Path) -> None:
    assert _trigger(_REPRO) == "not_ordinary"
    assert _enforced(tmp_path, _REPRO) is False


def test_an_unresolvable_parent_reads_null(tmp_path: Path) -> None:
    text = "include: templates.yml\ntest:\n  extends: .from_include\n  script: pytest\n"
    assert _trigger(text) == "unknown"
    assert _enforced(tmp_path, text) is None


@pytest.mark.parametrize(("text", "trigger"), [
    # Two-level chain: the schedule-only rules come from the grandparent.
    (_NIGHTLY + ".mid:\n  extends: .nightly\n  image: python\n"
     "test:\n  extends: .mid\n  script: pytest\n", "not_ordinary"),
    # The middle of the chain overrides the grandparent's rules.
    (_NIGHTLY + ".mid:\n  extends: .nightly\n  rules:\n    - when: always\n"
     "test:\n  extends: .mid\n  script: pytest\n", "ordinary"),
    # The job's own rules: override the template's.
    (_NIGHTLY + 'test:\n  extends: .nightly\n  rules:\n    - if: $CI_PIPELINE_SOURCE == "merge_request_event"\n'
     "  script: pytest\n", "ordinary"),
    # A list of parents: the last one wins, as a flow list and a block list.
    (_NIGHTLY + _MR + "test:\n  extends: [.nightly, .mr]\n  script: pytest\n", "ordinary"),
    (_NIGHTLY + _MR + "test:\n  extends:\n    - .mr\n    - .nightly\n  script: pytest\n", "not_ordinary"),
    # A job-level when: inherited from the template.
    (".manual:\n  when: manual\ntest:\n  extends: .manual\n  script: pytest\n", "not_ordinary"),
    # The template's when: is the default of the job's own rules.
    (".manual:\n  when: manual\ntest:\n  extends: .manual\n  rules:\n    - changes: [src/*]\n"
     "  script: pytest\n", "not_ordinary"),
    # only: inherited, and a template with none of the keys read.
    (".tags:\n  only: [tags]\ntest:\n  extends: .tags\n  script: pytest\n", "not_ordinary"),
    (".py:\n  image: python\ntest:\n  extends: .py\n  script: pytest\n", "ordinary"),
    # A parent that is unresolvable anywhere in the chain, a cycle, and an anchor value.
    (".mid:\n  extends: .missing\ntest:\n  extends: .mid\n  script: pytest\n", "unknown"),
    (".a:\n  extends: .b\n.b:\n  extends: .a\ntest:\n  extends: .a\n  script: pytest\n", "unknown"),
    (_NIGHTLY + "test:\n  extends: *ref\n  script: pytest\n", "unknown"),
    # A parent given inline as a flow map is not read: it must not count as a template with no keys.
    ('.nightly: {rules: [{if: $CI_PIPELINE_SOURCE == "schedule"}]}\n'
     "test:\n  extends: .nightly\n  script: pytest\n", "unknown"),
    # A parent that is an alias, or merges one, is not read either.
    (".base: &b\n  rules:\n    - if: $CI_PIPELINE_SOURCE == 'schedule'\n.t: *b\n"
     "test:\n  extends: .t\n  script: pytest\n", "unknown"),
    (".base: &b\n  rules:\n    - if: $CI_PIPELINE_SOURCE == 'schedule'\n.t:\n  <<: *b\n"
     "test:\n  extends: .t\n  script: pytest\n", "unknown"),
    # A template that also defines an anchor is read through its block keys.
    (".tpl: &tpl\n  rules:\n    - if: $CI_PIPELINE_SOURCE == \"schedule\"\n"
     "test:\n  extends: .tpl\n  script: pytest\n", "not_ordinary"),
    # A diamond reaching one only: map twice reads it once.
    (".base:\n  only:\n    refs:\n      - tags\n.a:\n  extends: .base\n.b:\n  extends: .base\n"
     "test:\n  extends: [.a, .b]\n  script: pytest\n", "not_ordinary"),
    # Two different only: maps are deep-merged by GitLab and not read here.
    (".v:\n  only:\n    variables:\n      - $X\ntest:\n  extends: .v\n  only:\n    refs:\n      - tags\n"
     "  script: pytest\n", "unknown"),
    # A flow map deep-merged with a block map is not read either.
    (".v:\n  except: {variables: [$SKIP]}\ntest:\n  extends: .v\n  except:\n    refs:\n      - tags\n"
     "  script: pytest\n", "unknown"),
    # rules: and only: meeting in one merged job: GitLab rejects the job, so it is not read.
    (_NIGHTLY + "test:\n  extends: .nightly\n  only: [branches]\n  script: pytest\n", "unknown"),
], ids=["two_level", "middle_overrides", "own_rules_override", "flow_list_last_wins",
        "block_list_last_wins", "inherited_when", "inherited_default_when", "inherited_only",
        "no_read_keys", "unresolvable_grandparent", "cycle", "anchor",
        "inline_parent", "alias_parent", "merge_key_parent", "anchored_template", "diamond_one_map", "two_maps", "flow_and_block_maps", "rules_and_only"])
def test_extends_is_resolved(text: str, trigger: str) -> None:
    assert _trigger(text) == trigger


@pytest.mark.parametrize(("keys", "trigger"), [
    ("  except: [branches]\n", "not_ordinary"),
    ("  only: [branches, merge_requests]\n  except: [merge_requests]\n", "ordinary"),
    ("  only: [merge_requests]\n  except: [merge_requests]\n", "not_ordinary"),
    ("  except: [main]\n", "ordinary"),
    ("  only: [main]\n  except: [release]\n", "unknown"),
    ("  except: [tags]\n", "ordinary"),
    ("  except:\n    refs:\n      - branches\n", "not_ordinary"),
    ("  except:\n    variables:\n      - $SKIP\n", "unknown"),
    ("  only: [tags]\n  except:\n    variables:\n      - $SKIP\n", "not_ordinary"),
    # Flow maps are not read.
    ("  except: {variables: [$SKIP]}\n", "unknown"),
    ("  except: {refs: [branches]}\n", "unknown"),
    ("  only: {refs: [tags]}\n", "unknown"),
    # Block lists, the common spelling.
    ("  except:\n    - branches\n", "not_ordinary"),
    ("  except:\n    - tags\n", "ordinary"),
    ("  only:\n    - branches\n  except:\n    - tags\n", "ordinary"),
], ids=["branches", "drops_merge_requests", "drops_all", "one_branch", "two_branch_limits",
        "tags", "refs_map", "variables", "only_tags_with_unread_except",
        "flow_variables", "flow_refs", "only_flow_refs", "block_branches", "block_tags",
        "block_only_and_except"])
def test_except_reads_with_only_semantics(keys: str, trigger: str) -> None:
    assert _trigger("test:\n" + keys + "  script: pytest\n") == trigger


def test_except_is_inherited_through_extends() -> None:
    text = ".no_branches:\n  except: [branches]\ntest:\n  extends: .no_branches\n  script: pytest\n"
    assert _trigger(text) == "not_ordinary"
