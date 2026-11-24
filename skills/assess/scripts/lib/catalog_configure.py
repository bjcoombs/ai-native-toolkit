"""The "Configure the checks" section of ``docs/checks.html`` (#1236): fixed prose and examples.

``CONFIGURE`` holds one ``ConfigureOption`` per way a team tunes the checks, in page order:
owner exclusions, a named profile, a custom maturity model, managed settings and the
regression gate in CI. Each has a title, its prose paragraphs, its copy-paste examples and the
references it links. The prose is plain text with two marks, `` `code` `` and ``[text](url)``,
which ``lib.catalog_html.inline`` renders; ``tests/test_catalog_html.py`` runs the
plain-English lint (``lib.plain_english.lint``) and the vocabulary guard over every paragraph.

The syntax is taken from the code, not the issue text: ``[checks] exclude`` from
``lib/owner_exclusions.py``, ``[agent_ops] managed_settings`` and ``[gate] fail_on_gate_loss``
from ``lib/assess_config.py``, the Action inputs from ``action.yml`` and the GitLab inputs
from ``templates/assess-gate.yml``. ``options(version)`` stamps the toolkit version from
``.claude-plugin/plugin.json`` into the CI pins, so the release PR's regen moves them.
``EXAMPLE_EXCLUDED`` names the condition the exclusion example uses; the tests check it is
still a condition id of the default model.
"""
from __future__ import annotations

from typing import TypedDict

BLOB = "https://github.com/bjcoombs/ai-native-toolkit/blob/main/"
ISSUES = "https://github.com/bjcoombs/ai-native-toolkit/issues/"
EXAMPLE_EXCLUDED = "layer-6-coverage.1"
# The checkout pin the README's Action example uses.
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1"


class Example(TypedDict):
    label: str  # the file or place the snippet goes
    code: str


class ConfigureOption(TypedDict):
    id: str  # the HTML anchor, cfg-<id>
    title: str
    paragraphs: list[str]
    examples: list[Example]
    references: list[tuple[str, str]]  # (link text, url)


INTRO = ("Each option below changes what a run checks or how CI acts on it. "
         "Settings in `.assess/config.toml` are committed, so every run and every teammate reads the same ones.")


def options(version: str) -> list[ConfigureOption]:
    """Every configure option, with ``version`` (``3.0.0``) in the CI pins."""
    tag = f"v{version}"
    return [
        {"id": "exclude", "title": "Leave out a check for your repository",
         "paragraphs": [
             "Add a `[checks]` table to `.assess/config.toml` and list each condition id to leave out. "
             "The ids are the ones on the cards above.",
             "Each entry needs a `reason`. An entry without one is skipped with a warning, and the check stays in force.",
             "`kind` is one of `not_applicable`, `measured_elsewhere`, `tool_unsupported`, `false_positive` "
             "or `other`, the default.",
             "An excluded condition earns nothing and costs nothing. "
             "A Step gate it covers stays unmet while the exclusion stands."],
         "examples": [{"label": ".assess/config.toml", "code": (
             "[checks]\n"
             "exclude = [\n"
             f'  {{ id = "{EXAMPLE_EXCLUDED}", kind = "measured_elsewhere",\n'
             '    reason = "Coverage is enforced in the release pipeline repository." },\n'
             "]")}],
         "references": [("Owner exclusions in the scoring model", BLOB + "skills/assess/references/scoring-model.md"),
                        ("#910", ISSUES + "910")]},
        {"id": "profile", "title": "Choose a profile",
         "paragraphs": [
             "A run scores against the default maturity model unless you name another.",
             "`--profile handover` is the bar a production team expects before it owns a repository. "
             "`--profile citizen` is a lighter bar while a citizen developer builds.",
             "A profile run leaves the committed `.assess/prior-level.json` baseline as the default model's. "
             "The CI gate keeps comparing default runs."],
         "examples": [{"label": "Claude Code", "code": "/assess --profile handover"}],
         "references": [("Named profiles, #1150", ISSUES + "1150")]},
        {"id": "levels", "title": "Write your own model",
         "paragraphs": [
             "`--levels FILE` scores against a maturity model you write, in the format of `levels/default.toml`.",
             "`/fleet --levels FILE` reads the same file, so one repository gets the same Step from both."],
         "examples": [{"label": "Claude Code", "code": "/assess --levels .assess/our-model.toml"}],
         "references": [("The default model", BLOB + "skills/assess/levels/default.toml"),
                        ("Build-your-own-practice guide, #1151", ISSUES + "1151")]},
        {"id": "managed-settings", "title": "Count central guardrails",
         "paragraphs": [
             "Claude Code managed settings live on the machine, not in the repository, so a run leaves them out by default.",
             "Pass `--managed-settings`, or set `managed_settings = true` under `[agent_ops]`, "
             "to count them toward the agent permissions practice.",
             "A central deny rule then counts, graded as listed. "
             "The config key is read by single-repository runs only; `/fleet` takes the flag."],
         "examples": [{"label": "Claude Code", "code": "/assess --managed-settings"},
                      {"label": ".assess/config.toml", "code": "[agent_ops]\nmanaged_settings = true"}],
         "references": [("The /assess skill", BLOB + "skills/assess/SKILL.md"), ("#1153", ISSUES + "1153")]},
        {"id": "gate", "title": "Turn on the regression gate in CI",
         "paragraphs": [
             "`fail_on_gate_loss` turns a pull request red when a Step gate your committed baseline held now reads unmet. "
             "An example is CI no longer running the tests.",
             "The baseline is `.assess/prior-level.json`, which every `/assess` run writes. "
             "Commit your `.assess/` directory first; until then the job passes and says so.",
             "Set `fail_on_gate_loss = true` under `[gate]` in `.assess/config.toml` for every CI host. "
             "On the command line, `assess_gate.py --fail-on-gate-loss` is the same switch.",
             "The GitHub Action is warn-only by default; its `fail_on_gate_loss` input turns the gate on.",
             "The GitLab CI template turns the gate on by default. "
             "Pass `fail_on_gate_loss: false` to report a lost gate and keep the job green."],
         "examples": [
             {"label": ".assess/config.toml", "code": "[gate]\nfail_on_gate_loss = true"},
             {"label": ".github/workflows/assess-gate.yml (steps)", "code": (
                 "steps:\n"
                 f"  - uses: {CHECKOUT}\n"
                 "    with:\n"
                 "      fetch-depth: 0   # full history for churn and the baseline\n"
                 f"  - uses: bjcoombs/ai-native-toolkit@{tag}\n"
                 "    with:\n"
                 "      config: .assess/config.toml\n"
                 "      fail_on_gate_loss: 'true'")},
             {"label": ".gitlab-ci.yml", "code": (
                 "include:\n"
                 f"  - remote: https://raw.githubusercontent.com/bjcoombs/ai-native-toolkit/{tag}/templates/assess-gate.yml\n"
                 "    inputs:\n"
                 "      config: .assess/config.toml\n"
                 "      fail_on_gate_loss: true")}],
         "references": [("Use as a GitHub Action", BLOB + "README.md#use-as-a-github-action"),
                        ("Use in GitLab CI", BLOB + "README.md#use-in-gitlab-ci"),
                        ("action.yml inputs", BLOB + "action.yml"),
                        ("GitLab template inputs", BLOB + "templates/assess-gate.yml")]},
    ]
