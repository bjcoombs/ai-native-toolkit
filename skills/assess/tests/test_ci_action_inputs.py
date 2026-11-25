"""Tests for lib/ci/ci_action_inputs.py: which ``with:`` inputs are shell commands (#799)."""
from __future__ import annotations

from lib.ci.ci_action_inputs import ReadLocal, forwarded_inputs, read_inputs

_ACTION = ("runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: ${{ inputs.run }}\n"
           "    - shell: bash\n      run: |\n        ${{ inputs.after }}\n"
           "    - shell: bash\n      run: echo ${{ inputs.note }}\n")


def _local(files: dict[str, str]) -> ReadLocal:
    return files.get


def test_forwarded_inputs_are_whole_run_steps() -> None:
    assert forwarded_inputs(_ACTION) == {"run", "after"}   # echo ${{ inputs.note }} is not one


def test_a_local_runner_input_is_kept() -> None:
    text = "steps:\n  - uses: ./act\n    with:\n      run: pytest\n"
    reading = read_inputs(text, _local({"act/action.yml": _ACTION}))
    assert (reading.text, reading.declared) == (text, frozenset())


def test_a_local_action_that_does_not_forward_the_input_is_declared() -> None:
    text = "steps:\n  - uses: ./act/\n    with:\n      note: x\n      run: |\n        pytest\n"
    reading = read_inputs(text, _local({"act/action.yaml": "runs:\n  using: node20\n"}))
    assert reading.declared == {4, 5}


def test_an_absent_or_escaping_local_action_is_declared() -> None:
    for uses in ("./missing", "../up", "./${{ matrix.a }}"):
        text = f"steps:\n  - uses: {uses}\n    with:\n      run: pytest\n"
        assert read_inputs(text, _local({})).declared == {3}


def test_a_local_reusable_workflow_is_read_from_its_file() -> None:
    text = "jobs:\n  t:\n    uses: ./.github/workflows/r.yml\n    with:\n      run: pytest\n"
    files = {".github/workflows/r.yml": "jobs:\n  a:\n    steps:\n      - run: ${{ inputs.run }}\n"}
    assert read_inputs(text, _local(files)).declared == frozenset()


def test_a_command_input_of_a_listed_runner_reads_as_run() -> None:
    text = "steps:\n  - uses: nick-fields/retry@v3\n    with:\n      command: npm test\n"
    assert read_inputs(text, _local({})).text.splitlines()[3] == "      run: npm test"


def test_github_script_is_blanked_and_other_inputs_are_left() -> None:
    text = ("steps:\n  - uses: actions/github-script@v7\n    with:\n      github-token: t\n"
            "      script: |\n        pytest\n  - run: pytest\n")
    reading = read_inputs(text, _local({}))
    assert reading.text.splitlines() == [
        "steps:", "  - uses: actions/github-script@v7", "    with:", "      github-token: t",
        "", "", "  - run: pytest"]
    assert reading.declared == frozenset()


def test_with_under_a_step_without_uses_is_left_alone() -> None:
    text = "steps:\n  - name: x\n    with:\n      run: pytest\n"
    assert read_inputs(text, _local({})) == (text, frozenset())


def test_run_on_arch_and_vmactions_are_runners() -> None:
    for uses in ("uraimo/run-on-arch-action@v3", "vmactions/freebsd-vm@v1"):
        text = f"steps:\n  - uses: {uses}\n    with:\n      run: pytest\n"
        assert read_inputs(text, _local({})).declared == frozenset()
    text = "steps:\n  - uses: vmactions/freebsd-vm@v1\n    with:\n      prepare: pytest\n      script: x\n"
    assert read_inputs(text, _local({})).declared == {4}
