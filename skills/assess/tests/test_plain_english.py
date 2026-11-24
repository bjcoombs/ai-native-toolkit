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
    english = fixed_lines()["card.step1.2"] + "\n\n" + fixed_lines()["card.step1.4"]
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


def test_critical_words_catch_every_inflection_and_only_the_named_exemption() -> None:
    from lib.plain_english import critical_words
    assert critical_words("We lost ground and fell behind.") == ["lost", "behind"]
    assert critical_words("the gate failed; coverage regressed; the team is ranked") == ["regress", "fail", "rank"]
    assert critical_words("Next: CI turns red on a broken check.") == []  # the exempt text, quoted
    assert critical_words("CI turns red on a broken test.") == ["broke"]  # not the exempt text


def test_each_exemption_still_matches_its_text() -> None:
    """#889: changing an exempt line (the maintainer's gate wording) fails here until the
    exemption is looked at again."""
    from lib.plain_english import CRITICAL_WORD_EXEMPT
    lines = fixed_lines()
    assert {k: lines.get(k) for k in CRITICAL_WORD_EXEMPT} == {k: e["text"] for k, e in CRITICAL_WORD_EXEMPT.items()}


def test_the_engine_text_holds_no_critical_word() -> None:
    """#889, #1252: the headline, the top card, gate notes, reason words and the since line, over every
    Step of the default model and every blocker state, use no word of blame or loss."""
    from lib.headline import REASON_WORDS, headline
    from lib.top_card import card_lines, since_line
    from lib.level_tally import default_model
    from lib.plain_english import critical_words
    model = default_model()
    texts = list(fixed_lines().values())
    states = [{"state": "unmet"}, {"state": "excluded", "exclusion_kind": "not_applicable"},
              *({"state": "not_measured", "reason": code} for code in REASON_WORDS)]
    for step in model.get("steps", []):
        for gate in step["gates"]:
            for state in states:
                blocker = {"kind": "gate", "gate": gate["id"], "condition": f"{gate['row']}.{gate['level']}",
                           **state}
                level = {"level_result_version": 1, "levels_version": model["version"],
                         "tally": {"met": 1, "measured": 2, "not_measured": 1}, "counted": [], "listed": [],
                         "not_measured": [], "conditions": [], "step": step["step"] - 1 or None,
                         "next_step": {"step": step["step"], "blockers": [blocker]}}
                texts += headline(level, model)["lines"]
                texts += card_lines(level, model, None)
    for since in ({"available": True, "first_run": True},
                  {"available": True, "closed_count": 2, "gates_gained": ["coverage_gate"],
                   "gates_lost": ["ci_runs_tests"]},
                  {"available": True, "closed_count": 0, "gates_gained": [], "gates_lost": []},
                  {"available": True, "closed_count": None, "gates_gained": None, "gates_lost": None}, None):
        texts.append(since_line(since) or "")
    assert len(texts) > 50
    assert {t: critical_words(t) for t in texts if critical_words(t)} == {}
