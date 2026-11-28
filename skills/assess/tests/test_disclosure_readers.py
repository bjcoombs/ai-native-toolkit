"""The structural AI-disclosure readers (#1599): the read, the marker test and the act related per step.

Every fixture is invented: workflow names, scripts and markers exist only here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.ai_governance import RUBY_UNREAD, declines_review, enforces_disclosure, scan_ai_governance
from lib.ci import disclosure_shell as shell
from lib.ci.disclosure_reader import JS, PYTHON, RUBY, RUN, SHELL, Unit, env_names, step_acts, step_units
from lib.ci.disclosure_script import js_parse, py_parse
from lib.ci.disclosure_terms import EXIT, LABEL

ENV = "env:\n  PR_BODY: ${{ github.event.pull_request.body }}\n"

SCRIPT_FLOW = """name: disclosure
on: pull_request
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - env:
          BODY: ${{ github.event.pull_request.body }}
        run: python3 .github/scripts/disclosure.py
"""


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _run(body: str) -> str:
    return "run: |\n" + "".join("  " + line + "\n" for line in body.splitlines())


# --- the cases #1599 lists -----------------------------------------------------------------


@pytest.mark.parametrize("script", [
    'import os, re, sys\nbody = os.environ["BODY"]\nif not re.search(r"AI-assisted", body):\n    sys.exit(1)\n',
    'import os, re, sys\nif not re.search(rb"Assisted-by", os.environ["BODY"].encode()):\n    sys.exit(1)\n',
    "import os, sys\nbody = os.environ['BODY']\nif 'Assisted-by' not in body:\n    sys.exit('add the trailer')\n",
    'import os, re, sys\nTRAILER = re.compile(r"^Assisted-by:", re.M)\n'
    'def main():\n    text = os.getenv("BODY", "")\n    if not TRAILER.search(text):\n        raise SystemExit(1)\nmain()\n',
])
def test_a_python_script_testing_the_body_meets_it(tmp_path: Path, script: str) -> None:
    root = _repo(tmp_path, {".github/workflows/d.yml": SCRIPT_FLOW, ".github/scripts/disclosure.py": script})
    assert scan_ai_governance(root)["enforced"] is True


def test_a_body_passed_through_a_second_variable_is_still_the_body() -> None:
    step = _run('B="$PR_BODY"\nC=$B\nif ! grep -qi "Assisted-by" <<< "$C"; then exit 1; fi')
    assert enforces_disclosure(step, ENV) is True
    js = "script: |\n  const raw = process.env.PR_BODY;\n  const b = raw.trim();\n  if (!b.includes('Assisted-by')) core.setFailed('x');\n"
    assert enforces_disclosure(js, ENV) is True


@pytest.mark.parametrize("line", ['[[ "$PR_BODY" == *"Assisted-by"* ]] || exit 1',
                                  '[[ "$PR_BODY" != *"Assisted-by"* ]] && exit 1',
                                  'if [[ $PR_BODY =~ AI-assisted ]]; then :; else exit 1; fi'])
def test_a_bash_pattern_test_on_the_body_counts(line: str) -> None:
    assert enforces_disclosure(_run(line), ENV) is True


def test_an_act_belonging_to_a_command_nested_in_the_marker_branch_does_not_count() -> None:
    step = _run("if echo \"$PR_BODY\" | grep -qi 'claude'; then npm test || exit 1; fi")
    assert enforces_disclosure(step, ENV) is False


def test_a_guard_clause_counts() -> None:
    step = _run("if echo \"$PR_BODY\" | grep -qi 'Assisted-by'; then exit 0; fi\nexit 1")
    assert enforces_disclosure(step, ENV) is True
    python = "run: |\n  import os, sys\n  if 'Assisted-by' in os.environ['PR_BODY']: sys.exit(0)\n  sys.exit(1)\n"
    assert enforces_disclosure(python, ENV) is True


def test_a_semicolon_does_not_end_a_sentence_of_an_opt_out() -> None:
    assert declines_review("Do not comment on code; focus only on security issues.\n") is False


def test_a_typographic_apostrophe_is_read_in_an_opt_out() -> None:
    assert declines_review("Don’t review PRs.\n") is True


# --- what the readers cannot settle never counts -------------------------------------------


@pytest.mark.parametrize("body", [
    'case "$PR_BODY" in *Assisted-by*) ;; *) exit 1 ;; esac',  # case is not read
    "check() { grep -qi 'Assisted-by' <<< \"$PR_BODY\" || exit 1; }\ncheck",  # nor a function
    "if grep -qi 'Assisted-by' <<< \"$PR_BODY\"; then exit 1",  # an unclosed if
    "grep -qi 'Assisted-by <<< \"$PR_BODY\" || exit 1",  # an unclosed quote
])
def test_shell_the_reader_cannot_settle_reads_as_no_check(body: str) -> None:
    assert enforces_disclosure(_run(body), ENV) is False


def test_python_and_javascript_that_do_not_parse_read_as_no_check() -> None:
    assert py_parse("if 'Assisted-by' not in body sys.exit(1)") is None
    assert js_parse("if (!body.includes('AI') { core.setFailed('x') }") is None
    assert step_acts([Unit(PYTHON, "if x\n"), Unit(JS, "}")]) == set()


def test_a_heredoc_body_is_not_read_as_shell() -> None:
    step = _run("cat <<EOF\nif grep -qi 'Assisted-by' <<< \"$PR_BODY\"; then exit 1; fi\nEOF")
    assert enforces_disclosure(step, ENV) is False


def test_an_exit_quoted_in_a_message_or_a_comment_is_not_an_act() -> None:
    assert enforces_disclosure(_run("grep -qi 'Assisted-by' <<< \"$PR_BODY\" || echo 'exit 1'"), ENV) is False
    assert enforces_disclosure(_run("grep -qi 'Assisted-by' <<< \"$PR_BODY\" # || exit 1"), ENV) is False


# --- shell structure -----------------------------------------------------------------------


def test_a_group_after_the_test_counts_by_its_unconditional_commands() -> None:
    step = _run("grep -qi 'Assisted-by' <<< \"$PR_BODY\" || { echo 'add it' >&2; exit 1; }")
    assert enforces_disclosure(step, ENV) is True
    nested = _run("grep -qi 'Assisted-by' <<< \"$PR_BODY\" || { npm test || exit 1; }")
    assert enforces_disclosure(nested, ENV) is False


def test_a_body_written_through_tee_or_a_pipeline_is_held() -> None:
    step = _run("gh pr view 7 --json body | jq -r .body | tee pr.txt >/dev/null\n"
                "grep -q 'Generated-by' pr.txt && gh pr edit 7 --add-label ai-generated")
    assert step_acts(step_units(step)) == {LABEL}


def test_a_for_loop_over_the_commits_is_read() -> None:
    step = _run("for msg in \"$PR_BODY\"; do\n  if ! grep -q 'Assisted-by' <<< \"$msg\"; then exit 1; fi\ndone")
    assert enforces_disclosure(step, ENV) is True


def test_a_marker_test_of_an_unread_value_binds_nothing() -> None:
    tree = shell.parse("LOGIN=$(whoami)\ngrep -qi 'copilot' <<< \"$LOGIN\" || exit 1")
    assert tree is not None and shell.binds(tree, frozenset()) == set()
    assert shell.acts(tree, frozenset()) == set()


def test_a_stderr_redirect_holds_nothing() -> None:
    tree = shell.parse("gh pr view 1 --json body 2> err.txt\ngrep -q 'AI' err.txt || exit 1")
    assert tree is not None and shell.binds(tree, frozenset()) == set()


# --- Python and JavaScript structure -------------------------------------------------------


def test_a_python_label_call_and_a_message_exit_are_acts() -> None:
    code = ("import os, subprocess\nbody = os.environ['PR_BODY']\nif 'Generated-by' in body:\n"
            "    subprocess.run(['gh', 'pr', 'edit', '1', '--add-label', 'ai'])\n")
    assert step_acts([Unit(PYTHON, code)], env_names(ENV)) == {LABEL}


def test_a_python_marker_result_held_in_a_name_counts() -> None:
    code = "import os, sys\nok = 'Assisted-by' in os.environ['PR_BODY']\nif not ok:\n    sys.exit(2)\n"
    assert step_acts([Unit(PYTHON, code)], env_names(ENV)) == {EXIT}


def test_a_python_act_nested_in_the_branch_does_not_count() -> None:
    code = ("import os, sys\nif 'claude' in os.environ['PR_BODY']:\n    if os.path.exists('x'):\n        sys.exit(1)\n")
    assert step_acts([Unit(PYTHON, code)], env_names(ENV)) == set()


def test_an_uncalled_python_function_is_not_read() -> None:
    code = "import os, sys\ndef check():\n    if 'AI' not in os.environ['PR_BODY']:\n        sys.exit(1)\n"
    assert step_acts([Unit(PYTHON, code)], env_names(ENV)) == set()
    assert step_acts([Unit(PYTHON, code + "check()\n")], env_names(ENV)) == {EXIT}


def test_a_github_script_loop_over_the_commits_counts() -> None:
    code = ("const commits = await github.paginate(github.rest.pulls.listCommits, context.repo);\n"
            "for (const c of commits) {\n  if (!/Assisted-by:/i.test(c.commit.message)) {\n"
            "    core.setFailed(`missing trailer on ${c.sha}`);\n  }\n}\n")
    assert step_acts([Unit(JS, code)]) == {EXIT}


def test_a_destructured_body_is_held() -> None:
    code = "const { body } = context.payload.pull_request;\nif (!body.match(/AI-assisted/)) throw new Error('x');\n"
    assert step_acts([Unit(JS, code)]) == {EXIT}


def test_a_javascript_act_after_an_unrelated_test_does_not_count() -> None:
    code = ("const body = context.payload.pull_request.body;\nif (context.actor.includes('copilot')) return;\n"
            "if (!body.includes('## Summary')) core.setFailed('add a summary');\n")
    assert step_acts([Unit(JS, code)]) == set()


def test_process_exit_zero_is_not_an_act() -> None:
    code = "const body = process.env.PR_BODY;\nif (body.includes('Assisted-by')) process.exit(0);\n"
    assert step_acts([Unit(JS, code)], env_names(ENV)) == set()
    assert step_acts([Unit(JS, code.replace("exit(0)", "exit(1)"))], env_names(ENV)) == {EXIT}


# --- splitting a step ----------------------------------------------------------------------


def test_a_step_splits_into_its_run_and_script_code() -> None:
    step = "- shell: python\n  run: |\n    print(1)\n- with:\n    script: \"core.info('x')\"\n"
    assert step_units(step) == [Unit(PYTHON, "print(1)"), Unit(JS, "core.info('x')")]
    assert step_units("run: echo hi\n") == [Unit(RUN, "echo hi")]
    assert step_units("shell: pwsh\nrun: exit 1\n") == []
    assert step_units("exit 1\n") == [Unit(SHELL, "exit 1\n")]


def test_only_env_keys_reading_the_body_are_held() -> None:
    text = "env:\n  BODY: ${{ github.event.pull_request.body }}\n  TITLE: ${{ github.event.pull_request.title }}\nrun: x\n"
    assert env_names(text) == frozenset({"BODY"})


# --- reading tables ------------------------------------------------------------------------

HELD = frozenset({"PR_BODY", "body", "pr.txt"})


@pytest.mark.parametrize(("code", "acts"), [
    ("if grep -qi 'AI' <<< \"$PR_BODY\"; then :; elif true; then exit 1; fi", {EXIT}),
    ("( grep -qi 'Assisted-by' <<< \"$PR_BODY\" || exit 1 )", {EXIT}),
    ("grep -q -e 'Assisted-by' <<< \"$PR_BODY\" || exit 3", {EXIT}),
    ("echo \"$PR_BODY\" > out.txt\ngrep --regexp=Generated-by -m 1 out.txt || exit 1", {EXIT}),
    ("grep -qie claude <<< \"$PR_BODY\" || exit 1", {EXIT}),
    ("export B=\"$PR_BODY\"\ngrep -q -- 'AI' <<< \"$B\" || exit 1", {EXIT}),
    ("until false; do grep -q 'AI' <<< \"$PR_BODY\" || exit 1; done", {EXIT}),
    ("if [ \"$PR_BODY\" = \"AI\" ]; then exit 1; fi", {EXIT}),
    ("test \"Assisted-by\" != \"$PR_BODY\" && exit 1", {EXIT}),
    ("grep -qi 'AI' <<< \"$PR_BODY\" || exit 1 # a trailing comment", {EXIT}),
    ("printf '%s' \"$PR_BODY\" | grep -q \"Assisted\\\"by\\|AI\" || exit 1", {EXIT}),
    ("echo \\#plain; grep -qi 'AI' <<< \"$PR_BODY\" || exit 1", {EXIT}),
    ("cat <<-'END'\n  exit 1\n  END\ngrep -q AI pr.txt && gh pr edit --add-label=ai", {LABEL}),
    ("grep -qi 'AI' <<< \"$PR_BODY\" || exit 0", set()),
    ("grep -qi 'AI' <<< \"$PR_BODY\" || exit", set()),
    ("grep -q -f patterns.txt <<< \"$PR_BODY\" || exit 1", set()),
    ("grep -qi 'AI' || exit 1", set()),
    ("fi", set()),
    ("then", set()),
    ("echo hi >", set()),
    ("x=$(echo", set()),
    ("[[ $PR_BODY == *AI*", set()),
    ("for; do exit 1; done", set()),
    ("for x in a b\nexit 1", set()),
    ("if grep -qi AI <<< \"$PR_BODY\"; exit 1; fi", set()),
    ("while read -r line; do exit 1; done < /dev/null", set()),
    (";;", set()),
])
def test_the_shell_reader(code: str, acts: set[str]) -> None:
    assert step_acts([Unit(SHELL, code)], HELD) == acts


@pytest.mark.parametrize(("code", "acts"), [
    ("import sys\nif body == 'AI': sys.exit(1)\n", {EXIT}),
    ("if 'AI' != body: raise SystemExit('x')\n", {EXIT}),
    ("import re, sys\nif re.compile(r'Assisted-by').search(body) is None: sys.exit(True)\n", {EXIT}),
    ("import sys\nif 'AI' in body: pass\nelif len(body) > 9: pass\nelse: sys.exit(1)\n", {EXIT}),
    ("import sys\ntry:\n    if 'AI' not in body: sys.exit(1)\nexcept KeyError:\n    pass\n", {EXIT}),
    ("import sys\nwith open('pr.txt') as fh:\n    text = fh.read()\nif 'AI' not in text: sys.exit(1)\n", {EXIT}),
    ("import sys\nfor line in body.splitlines():\n    if line.startswith('Generated-by'):\n        sys.exit(1)\n", {EXIT}),
    ("import os, sys\ntext: str = os.environ['PR_BODY']\nwhile True:\n    if 'AI' in text: sys.exit(f'{text}')\n", {EXIT}),
    ("import sys\ncode = 1\nif 'AI' not in body: sys.exit(code)\n", set()),
    ("import sys\nif 'AI' not in body: sys.exit(None)\n", set()),
    ("import sys\nif x.y().search(body): sys.exit(1)\n", set()),
    ("import sys\nif 'AI' in other: sys.exit(1)\n", set()),
])
def test_the_python_reader(code: str, acts: set[str]) -> None:
    assert step_acts([Unit(PYTHON, code)], HELD) == acts


@pytest.mark.parametrize(("code", "acts"), [
    ('if (body.indexOf("AI") < 0) { core.setFailed("x") } else { }', {EXIT}),
    ('try { if (!body.includes("AI")) throw new Error("x") } catch (e) { } finally { }', {EXIT}),
    ('while (true) { if (!body?.includes("AI")) core.setFailed("x") }', {EXIT}),
    ("const ok = /AI-assisted/.test(body)\nif (!ok) core.setFailed('x')", {EXIT}),
    ("const text = body\n  .trim()\nif (!text.startsWith('Assisted-by')) core.setFailed('x')", {EXIT}),
    ("if (body.includes('AI')) return\nawait github.rest.issues.addLabels({ labels: ['x'] })", {LABEL}),
    ("const { body: text } = context.payload.pull_request; /* a note */\n"
     "if (!text.includes('AI')) { core.setFailed('x'); }", {EXIT}),
    ("if (!body.includes('AI')) process.exit(code)", set()),
    ("if (!body.includes('AI')) core.info('x')", set()),
    ("if (!body.includes(name)) core.setFailed('x')", set()),
    ("let n = 1 / 2\nif (!other.includes('AI')) core.setFailed('x')", set()),
    ("@decorator", set()),
    ("if x core.setFailed('x')", set()),
    ("{ core.setFailed('x')", set()),
    ("if (body.includes('AI') core.setFailed('x')", set()),
    ("const x = (1", set()),
])
def test_the_javascript_reader(code: str, acts: set[str]) -> None:
    assert step_acts([Unit(JS, code)], HELD) == acts


# --- a guard clause must leave the flow --------------------------------------------------


@pytest.mark.parametrize(("unit", "acts"), [
    (Unit(SHELL, "if echo \"$PR_BODY\" | grep -qi 'copilot'; then echo 'mentions copilot'; fi\n"
                 "gh pr edit \"$PR\" --add-label needs-review"), set()),
    (Unit(PYTHON, "import os, subprocess\nif 'Copilot' in os.environ['PR_BODY']: print('x')\n"
                  "subprocess.run(['gh', 'pr', 'edit', '--add-label', 'x'])\n"), set()),
    (Unit(JS, "const body = process.env.PR_BODY;\nif (body.includes('Copilot')) { core.info('x'); }\n"
              "await github.rest.issues.addLabels({ labels: ['x'] });\n"), set()),
    (Unit(SHELL, "grep -qi 'copilot' <<< \"$PR_BODY\" && echo seen\ngh pr edit --add-label x"), set()),
    (Unit(SHELL, "grep -qi 'copilot' <<< \"$PR_BODY\" && echo seen\nexit 1"), {EXIT}),  # the message idiom
    (Unit(SHELL, "grep -qi 'Assisted-by' <<< \"$PR_BODY\" && exit 0\ngh pr edit --add-label x"), {LABEL}),
    (Unit(PYTHON, "import os, subprocess\ndef main():\n    if 'Copilot' in os.environ['PR_BODY']:\n        return\n"
                  "    subprocess.run(['gh', 'pr', 'edit', '--add-label', 'x'])\nmain()\n"), {LABEL}),
    (Unit(JS, "const body = process.env.PR_BODY;\nif (body.includes('Assisted-by')) { return; }\n"
              "await github.rest.issues.addLabels({ labels: ['x'] });\n"), {LABEL}),
])
def test_a_later_act_counts_only_after_a_branch_that_leaves(unit: Unit, acts: set[str]) -> None:
    assert step_acts([unit], env_names(ENV)) == acts


def test_an_act_after_a_message_between_the_test_and_a_group_counts() -> None:
    step = _run("echo \"$PR_BODY\" | grep -qi 'assisted-by' && echo ok || { echo missing; exit 1; }")
    assert enforces_disclosure(step, ENV) is True


def test_a_python_heredoc_body_is_read_as_python() -> None:
    step = _run("python3 - <<'PY'\nimport os, sys\nif 'Assisted-by' not in os.environ['PR_BODY']:\n"
                "    sys.exit(1)\nPY")
    assert enforces_disclosure(step, ENV) is True
    assert enforces_disclosure(step.replace("python3 -", "cat"), ENV) is False


def test_a_heredoc_marker_inside_quotes_opens_no_heredoc() -> None:
    step = _run("echo \"see <<EOF docs\"\ngrep -qi 'Assisted-by' <<< \"$PR_BODY\" || exit 1\necho EOF")
    assert enforces_disclosure(step, ENV) is True


# --- review round 2: compound redirects, JavaScript chains and functions -------------------


@pytest.mark.parametrize(("code", "acts"), [
    ("while read -r l; do echo \"$l\"; done <<< \"$PR_BODY\"\ngrep -q 'Assisted-by' <<< \"$PR_BODY\" || exit 1", {EXIT}),
    ("while read -r l; do\n  if grep -q 'Assisted-by' <<< \"$l\"; then exit 1; fi\ndone < pr.txt", {EXIT}),
    ("while read -r l; do grep -q 'AI' <<< \"$l\" || exit 1; done < other.txt", set()),
    ("{ echo start; } > log.txt\nif grep -qi 'AI' pr.txt; then :; else exit 1; fi 2> err.txt", {EXIT}),
    ("( true ) > /dev/null\ngrep -qi 'AI' pr.txt || exit 1", {EXIT}),
    ("{ true; } >", set()),
])
def test_compound_commands_take_trailing_redirects(code: str, acts: set[str]) -> None:
    assert step_acts([Unit(SHELL, code)], HELD) == acts


@pytest.mark.parametrize(("code", "acts"), [
    ('body.includes("Assisted-by") || core.setFailed("missing")', {EXIT}),
    ('/Generated-by/.test(body) && await github.rest.issues.addLabels({ labels: ["ai"] })', {LABEL}),
    ('other.includes("AI") || core.setFailed("missing")', set()),
    ('body.includes("AI") || core.info("missing")', set()),
    ('function check() { if (!body.includes("AI")) core.setFailed("x"); }\ncheck();', {EXIT}),
    ('async function check() {\n  if (!body.includes("AI")) core.setFailed("x");\n}\nawait check();', {EXIT}),
    ('function check() { if (!body.includes("AI")) core.setFailed("x"); }', set()),
    ("function () { }", set()),
    ("function f() core.setFailed('x')", set()),
    ("async () => { core.setFailed('x') }", set()),
])
def test_javascript_chains_and_called_functions(code: str, acts: set[str]) -> None:
    assert step_acts([Unit(JS, code)], HELD) == acts


def test_only_a_github_script_step_has_a_javascript_script() -> None:
    assert step_units("uses: actions/github-script@v7\nwith:\n  script: core.info(1)\n") == [Unit(JS, "core.info(1)")]
    assert step_units("uses: example/ssh-action@v1\nwith:\n  script: exit 1\n") == []


@pytest.mark.parametrize("unit", [
    Unit(SHELL, "{ " * 600 + "grep -qi 'AI' pr.txt || exit 1" + "; }" * 600),
    Unit(SHELL, "( " * 600 + "grep -qi 'AI' pr.txt || exit 1" + " )" * 600),
    Unit(JS, "{" * 2000 + "if (!body.includes('AI')) core.setFailed('x')" + "}" * 2000),
    Unit(PYTHON, "(" * 5000 + ")" * 5000),
])
def test_nesting_too_deep_to_read_is_no_check_not_a_crash(unit: Unit) -> None:
    assert step_acts([unit], HELD) == set()


# --- review round 4: a message is no label, and a callback's act is its own ----------------


@pytest.mark.parametrize(("code", "acts"), [
    ("if 'Assisted-by' not in body: print('Please run: gh pr edit --add-label needs-ai-disclosure')\n", set()),
    ("import logging\nif 'Assisted-by' not in body: logging.warning('use --add-label x')\n", set()),
    ("import os\nif 'Assisted-by' not in body: os.system('gh pr edit 1 --add-label ai')\n", {LABEL}),
    ("import subprocess\nif 'AI' in body: subprocess.run(('gh', 'pr', 'edit', '--add-label=ai'))\n", {LABEL}),
    ("import subprocess\nif 'AI' in body: subprocess.run(['echo', '--add-label'])\n", set()),
    ("import subprocess\nif 'AI' in body: subprocess.run(cmd)\n", set()),
])
def test_a_python_label_is_a_process_call_running_gh(code: str, acts: set[str]) -> None:
    assert step_acts([Unit(PYTHON, code)], HELD) == acts


def test_a_javascript_act_inside_a_callback_belongs_to_the_callback() -> None:
    code = 'if (!body.includes("Assisted-by")) { files.forEach(f => { if (f.big) core.setFailed("x") }) }'
    assert step_acts([Unit(JS, code)], HELD) == set()
    assert step_acts([Unit(JS, 'if (!body.includes("AI")) { await core.setFailed("x") }')], HELD) == {EXIT}


# --- #1609: a shell given as a path, an f-string command, a Ruby script ---------------------


@pytest.mark.parametrize(("runner", "language"), [
    ("/bin/bash -e {0}", SHELL), ("/usr/bin/sh {0}", SHELL), ("/usr/bin/python3 {0}", PYTHON), ("bash", SHELL),
])
def test_a_shell_given_as_a_path_is_matched_by_its_program(runner: str, language: str) -> None:
    assert step_units(f"shell: {runner}\nrun: exit 1\n") == [Unit(language, "exit 1")]


def test_a_check_under_a_shell_path_meets_it() -> None:
    step = "shell: /bin/bash -e {0}\n" + _run("grep -qi 'Assisted-by' <<< \"$PR_BODY\" || exit 1")
    assert enforces_disclosure(step, ENV) is True


@pytest.mark.parametrize(("code", "acts"), [
    ('import os\nif "Assisted-by" not in body: os.system(f"gh pr edit {pr} --add-label needs-ai")\n', {LABEL}),
    ('import subprocess\nif "AI" in body: subprocess.run(f"gh pr edit {n} --add-label ai", shell=True)\n', {LABEL}),
    ('import subprocess\nif "AI" in body: subprocess.run(["gh", "pr", "edit", f"{n}", "--add-label", "ai"])\n', {LABEL}),
    ('import os\nif "AI" in body: os.system(f"echo gh pr edit {pr} --add-label ai")\n', set()),
    ('import os\nif "AI" in body: os.system(f"gh pr edit {flag} ai")\n', set()),
    ('import os\nif "AI" in body: os.system(f"{tool} pr edit --add-label ai")\n', set()),
    ('import os\nif "AI" in body: print(f"run gh pr edit {pr} --add-label ai")\n', set()),
])
def test_an_f_string_command_is_read_by_its_literal_parts(code: str, acts: set[str]) -> None:
    assert step_acts([Unit(PYTHON, code)], HELD) == acts


RUBY_FLOW = SCRIPT_FLOW.replace("python3 .github/scripts/disclosure.py", "ruby .github/scripts/disclosure.rb")
RUBY_CHECK = 'body = ENV.fetch("BODY", "")\nunless body.match?(/Assisted-by:/i)\n  abort "add the trailer"\nend\n'


def test_a_ruby_enforcer_reads_not_measured_rather_than_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/d.yml": RUBY_FLOW, ".github/scripts/disclosure.rb": RUBY_CHECK})
    block = scan_ai_governance(root)
    assert (block["enforced"], block["enforced_reason"], block["enforcing_workflows"]) == (None, RUBY_UNREAD, [])


def test_a_ruby_step_under_shell_ruby_reads_not_measured(tmp_path: Path) -> None:
    flow = SCRIPT_FLOW.replace("run: python3 .github/scripts/disclosure.py",
                               "shell: ruby {0}\n        run: |\n" + "".join("          " + line + "\n"
                                                                            for line in RUBY_CHECK.splitlines()))
    block = scan_ai_governance(_repo(tmp_path, {".github/workflows/d.yml": flow}))
    assert (block["enforced"], block["enforced_reason"]) == (None, RUBY_UNREAD)


def test_a_ruby_script_naming_no_marker_leaves_the_condition_unmet(tmp_path: Path) -> None:
    root = _repo(tmp_path, {".github/workflows/d.yml": RUBY_FLOW, ".github/scripts/disclosure.rb": 'puts "built"\n'})
    assert (scan_ai_governance(root)["enforced"], scan_ai_governance(root)["enforced_reason"]) == (False, None)


def test_a_read_enforcer_still_meets_it_beside_a_ruby_script(tmp_path: Path) -> None:
    check = 'import os, sys\nif "Assisted-by" not in os.environ["BODY"]:\n    sys.exit(1)\n'
    root = _repo(tmp_path, {".github/workflows/d.yml": RUBY_FLOW, ".github/scripts/disclosure.rb": RUBY_CHECK,
                            ".github/workflows/p.yml": SCRIPT_FLOW, ".github/scripts/disclosure.py": check})
    block = scan_ai_governance(root)
    assert (block["enforced"], block["enforced_reason"], block["enforcing_workflows"]) == (
        True, None, [".github/workflows/p.yml"])


def test_ruby_is_a_unit_no_reader_reads() -> None:
    assert step_units("shell: ruby {0}\nrun: abort 'AI'\n") == [Unit(RUBY, "abort 'AI'")]
    assert step_acts([Unit(RUBY, 'abort "AI" unless ENV["PR_BODY"].include?("AI")')], HELD) == set()
