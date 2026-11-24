"""The plain-English lint (lib/plain_english.py, #1174)."""
from __future__ import annotations

from lib.plain_english import IDIOMS, MAX_WORDS, fixed_lines, lint


def test_every_fixed_user_facing_line_is_plain_english() -> None:
    lines = fixed_lines()
    assert len(lines) > 30  # the tables and the rendered summary for every Step
    assert {k: lint(v) for k, v in lines.items() if lint(v)} == {}


def test_a_sentence_over_the_limit_fails_and_one_at_it_passes() -> None:
    assert MAX_WORDS == 25
    thirty = " ".join(["word"] * 30) + "."
    assert [i["reason"] for i in lint(thirty)] == ["too_long"]
    assert lint(" ".join(["word"] * 25) + ".") == []
    # each sentence is measured on its own, and a backticked span counts as one word
    assert lint(("Short. " * 10) + "Run `pytest --cov --cov-fail-under=80 tests/unit tests/integration` now.") == []


def test_an_idiom_or_a_phrasal_verb_fails_as_a_whole_word() -> None:
    assert [i["reason"] for i in lint("Set up the CI job.")] == ["idiom:set up"]
    assert [i["reason"] for i in lint("This is low-hanging fruit.")] == ["idiom:low-hanging fruit"]
    assert lint("The setup is done.") == []  # "setup" is not "set up"
    assert lint("Run `make set up` once.") == []  # code is not prose
    assert lint("Run `cd x; set up` now.") == []  # a ; inside code ends no sentence
    assert [i["reason"] for i in lint("Set\nup the CI job.")] == ["idiom:set up"]  # across a line wrap
    assert "set up" in IDIOMS


def test_a_polish_translation_keeps_ids_paths_and_links_and_the_template_puts_it_under_the_english() -> None:
    """#1174: the English stays first and verbatim; ids, paths, commands and links stay as written."""
    from pathlib import Path
    from lib.plain_english import untranslated_tokens
    english = fixed_lines()["summary.step1.1"] + "\n\n" + fixed_lines()["call_to_action.step1"]
    polish = ("Dalej: dodaj do repozytorium plik instrukcji, który czytają agenci, na przykład AGENTS.md "
              "lub CLAUDE.md. Wtedy osiągniesz Step 2: agent otwiera pull requesty, a człowiek przegląda każdy z nich."
              "\n\n**Zacznij tutaj:** [Dodaj plik instrukcji, na przykład AGENTS.md lub CLAUDE.md]"
              "(https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md#step-gates).")
    assert untranslated_tokens(english, polish) == []
    # a translation that renames a file or drops the link fails
    assert untranslated_tokens(english, polish.replace("AGENTS.md", "AGENCI.md")) == ["AGENTS.md"]
    assert untranslated_tokens(english, polish.replace("AGENTS.md", "OLD_AGENTS.md")) == ["AGENTS.md"]
    assert untranslated_tokens(english, polish.split("(https")[0]) == [
        "https://github.com/bjcoombs/ai-native-toolkit/blob/main/docs/what-we-measure.md#step-gates"]
    template = (Path(__file__).resolve().parents[2] / "assess-findings" / "SKILL.md").read_text(encoding="utf-8")
    summary = template.index("<paste `headline.summary_markdown`")
    label = template.index("<only when the user works in a language other than English: `Translation (<language>):`")
    assert summary < label  # the translation follows the English
    rule = template[template.index("**Translation for a reader in another language"):]
    assert "English stays first and verbatim" in rule and "untranslated_tokens" in rule
    assert all(name in rule for name in ("run-context.json", "badge.json", "log.md"))  # machine-read stays English


def test_every_manifest_prose_string_except_the_whys_is_plain_english() -> None:
    """#1186: titles, summaries, fixes, actions, meanings and next steps pass; whys wait for approval."""
    from lib.plain_english import APPROVED_WHYS, manifest_lines
    lines = manifest_lines()
    assert len(lines) > 300
    assert not [k for k in lines if (k.endswith(".why") or ".whys." in k) and k not in APPROVED_WHYS]
    assert {k: [i["reason"] for i in lint(v)] for k, v in lines.items() if lint(v)} == {}
