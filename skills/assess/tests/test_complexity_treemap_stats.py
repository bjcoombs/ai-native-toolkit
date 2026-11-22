"""complexity-treemap.py's stats sidecar: field naming, hotspot rank, per-function
ccn, estimated tokens, code/data maxima and the distribution blocks.

Split from test_complexity_treemap.py (#720); the stubbed loader lives there.
"""
from __future__ import annotations

import json

import pytest
from test_complexity_treemap import _load_treemap


@pytest.fixture(scope="module")
def treemap():
    return _load_treemap()


def test_write_stats_uses_commits_field_and_balanced_rank(treemap, tmp_path):
    """The per-file churn count is emitted as `commits` (what consumers read),
    and the balanced composite ranks a moderately-complex active file above a
    very-complex frozen one (issue #47, observation 2 + 5)."""
    root = tmp_path
    frozen_complex = root / "frozen.go"   # high ccn, barely touched
    active_moderate = root / "active.go"  # moderate ccn, churning
    files = [
        (frozen_complex, 800, 1396.0, "lizard"),
        (active_moderate, 300, 140.0, "lizard"),
    ]
    aux_data = {frozen_complex: 1, active_moderate: 45}
    out = root / "stats.json"
    # The composite now includes a sqrt(est_tokens) size axis. Hold size equal
    # so this test isolates the churn axis it is about (issue #47); otherwise
    # frozen.go's larger size would confound the ranking.
    tokens = {frozen_complex: 1000, active_moderate: 1000}
    treemap.write_stats(files, aux_data, "commits (last 12mo)", root, out,
                        tokens_by_path=tokens)

    stats = json.loads(out.read_text())
    assert "plugin_version" in stats
    hotspots = stats["top_hotspots"]
    # Balanced composite: the active moderate-complexity file leads.
    assert hotspots[0]["path"] == "active.go"
    # Field is `commits`, not the legacy `churn`.
    for h in hotspots:
        assert "commits" in h
        assert "churn" not in h
    by_path = {h["path"]: h for h in hotspots}
    assert by_path["active.go"]["commits"] == 45
    assert by_path["frozen.go"]["commits"] == 1


def test_write_stats_records_churn_degenerate_flag(treemap, tmp_path):
    """Issue #172: the stats sidecar carries ``churn_degenerate`` so the report
    and a reader know the saturation axis / commits column is inactive. Default
    is False; passing the flag records True."""
    root = tmp_path
    f = root / "a.go"
    out = root / "stats.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], {f: 1}, "commits (all-time)",
                        root, out)
    assert json.loads(out.read_text())["churn_degenerate"] is False

    treemap.write_stats([(f, 100, 5.0, "lizard")], {f: 1}, "commits (all-time)",
                        root, out, churn_degenerate=True)
    assert json.loads(out.read_text())["churn_degenerate"] is True


def test_write_stats_commits_none_without_git(treemap, tmp_path):
    """No churn data (no git) -> commits is None, distinct from a real 0."""
    root = tmp_path
    f = root / "a.go"
    out = root / "stats.json"
    treemap.write_stats([(f, 100, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    assert stats["top_hotspots"][0]["commits"] is None


def test_write_stats_separates_aggregate_from_per_function_ccn(treemap, tmp_path):
    """Issue #58: the file-level aggregate ccn (sum of per-function complexity)
    must be labelled as an aggregate and never conflated with the per-function
    value a linter threshold gates. A file summing to ccn 136 whose worst
    single function is only 13 is NOT a per-function violation."""
    root = tmp_path
    f = root / "service_modules.go"   # the issue's actual offender shape
    out = root / "stats.json"
    # 13 functions whose complexities sum to 136 (the reported aggregate),
    # worst single function = 13 (under a cyclop:15 threshold).
    fn_ccns = [13.0, 13.0, 12.0, 12.0, 11.0, 11.0, 10.0, 10.0,
               9.0, 9.0, 8.0, 8.0, 10.0]
    assert sum(fn_ccns) == 136.0
    treemap.write_stats(
        [(f, 800, 136.0, "lizard")], {f: 3}, "commits (last 12mo)", root, out,
        fn_ccn_by_path={f: fn_ccns},
    )
    stats = json.loads(out.read_text())

    # The aggregate block self-labels and the per-function block is separate.
    assert stats["ccn"]["basis"] == "file-aggregate"
    assert stats["ccn"]["max"] == 136.0
    assert stats["fn_ccn"]["basis"] == "per-function"
    assert stats["fn_ccn"]["function_count"] == 13
    assert stats["fn_ccn"]["max"] == 13.0   # worst function, not the sum

    row = stats["top_complex"][0]
    assert row["ccn"] == 136.0              # aggregate preserved for the hue
    assert row["ccn_basis"] == "file-aggregate"
    assert row["max_fn_ccn"] == 13.0        # the per-function truth for Layer 3


def test_write_stats_scc_file_has_null_max_fn_ccn(treemap, tmp_path):
    """scc reports file-level complexity with no function breakdown, so a
    scc-scored file carries max_fn_ccn=null - the report must not invent a
    per-function value it never measured."""
    root = tmp_path
    f = root / "report.sql"
    out = root / "stats.json"
    # No fn_ccn_by_path entry for this path -> scc-style, per-function unknown.
    treemap.write_stats([(f, 400, 50.0, "scc")], None, None, root, out,
                        fn_ccn_by_path={})
    stats = json.loads(out.read_text())
    assert stats["top_complex"][0]["max_fn_ccn"] is None
    assert stats["fn_ccn"]["function_count"] == 0


def test_hotspot_rank_favours_per_function_offender(treemap, tmp_path):
    """Issue #115: for a class-per-file language the hotspot composite must rank
    on the worst single function, not the file aggregate, so a broad coordinator
    class can't bury a genuinely complex single method.

    Real shape from the first Java/JVM run: a coordinator at aggregate ccn 107
    whose worst method is only 14 (not a violation) out-ranked a DAO at ccn 28
    that is one complex method. With equal churn the DAO must now lead."""
    root = tmp_path
    coordinator = root / "Coordinator.java"  # broad: ccn 107, worst method 14
    dao = root / "Dao.java"                   # one genuinely complex method
    files = [
        (coordinator, 600, 107.0, "lizard"),
        (dao, 200, 28.0, "lizard"),
    ]
    # Many small methods summing to 107, worst single = 14.
    coordinator_fns = [14.0, 13.0, 12.0, 11.0, 10.0, 10.0, 9.0,
                       9.0, 8.0, 6.0, 5.0]
    assert sum(coordinator_fns) == 107.0
    dao_fns = [28.0]  # the single complex method is the whole file's ccn
    aux_data = {coordinator: 5, dao: 5}  # equal churn isolates the ccn re-weight
    out = root / "stats.json"
    # Equal est_tokens too, so the new sqrt(est_tokens) size axis doesn't
    # confound the per-function re-weight this test is about (issue #115).
    tokens = {coordinator: 1000, dao: 1000}
    treemap.write_stats(
        files, aux_data, "commits (last 12mo)", root, out,
        fn_ccn_by_path={coordinator: coordinator_fns, dao: dao_fns},
        tokens_by_path=tokens,
    )
    stats = json.loads(out.read_text())
    hotspots = stats["top_hotspots"]

    # The true per-function offender ranks at or above the coordinator class.
    assert hotspots[0]["path"] == "Dao.java"
    # The aggregate is still reported faithfully - only the ranking changed.
    by_path = {h["path"]: h for h in hotspots}
    assert by_path["Coordinator.java"]["ccn"] == 107.0
    assert by_path["Coordinator.java"]["max_fn_ccn"] == 14.0
    # The complexity-only rank (treemap hue) stays aggregate-driven.
    assert stats["top_complex"][0]["path"] == "Coordinator.java"


def test_hotspot_rank_unchanged_for_single_function_per_file(treemap, tmp_path):
    """Must-not-regress guard (issue #115): for single-function-per-file
    languages (Python/Go) the aggregate is the worst function, so the
    per-function re-weight is a no-op and the existing ranking is preserved -
    the more-complex-and-equally-churned file still leads."""
    root = tmp_path
    complex_go = root / "complex.go"   # one big function, ccn 40
    simple_go = root / "simple.go"     # one small function, ccn 8
    files = [
        (complex_go, 300, 40.0, "lizard"),
        (simple_go, 120, 8.0, "lizard"),
    ]
    # aggregate == worst function: the per-function weight collapses to aggregate.
    aux_data = {complex_go: 10, simple_go: 10}
    out = root / "stats.json"
    treemap.write_stats(
        files, aux_data, "commits (last 12mo)", root, out,
        fn_ccn_by_path={complex_go: [40.0], simple_go: [8.0]},
    )
    stats = json.loads(out.read_text())
    hotspots = stats["top_hotspots"]

    # Ranking is unchanged: the genuinely complex file still leads.
    assert hotspots[0]["path"] == "complex.go"
    # And it matches the aggregate-only rank - no per-function divergence here.
    assert stats["top_complex"][0]["path"] == "complex.go"


def test_effective_ccn_collapses_to_aggregate_without_per_function_data(treemap):
    """`_effective_ccn` returns the raw aggregate when there is no per-function
    signal (scc files: max_fn_ccn is None), and when the worst function already
    equals the aggregate (single-function file) - the two no-regression paths."""
    assert treemap._effective_ccn(50.0, None) == 50.0   # scc: no breakdown
    assert abs(treemap._effective_ccn(40.0, 40.0) - 40.0) < 1e-9  # single fn
    # A coordinator (aggregate >> worst fn) is pulled below its aggregate but
    # never below the worst function itself.
    eff = treemap._effective_ccn(107.0, 14.0)
    assert 14.0 < eff < 107.0


# --- Estimated tokens as the keyhole size unit (PRD 2026-06) -----------------

def test_est_token_count_is_chars_over_four(treemap, tmp_path):
    """est_tokens = ceil(len(text)/4) for a real on-disk file."""
    f = tmp_path / "a.py"
    f.write_text("x" * 800, encoding="utf-8")  # 800 chars -> 200 tokens
    assert treemap.est_token_count(f, 0) == 200


def test_est_token_count_falls_back_to_loc_when_unreadable(treemap, tmp_path):
    """An unreadable/absent file estimates from loc - a conservative floor that
    can't inflate a benign file's rank."""
    missing = tmp_path / "gone.py"
    assert treemap.est_token_count(missing, 137) == 137


def test_write_stats_emits_est_tokens_per_row_and_uses_real_text(treemap, tmp_path):
    """Each row carries est_tokens; for on-disk files it is the char-based
    estimate, not the loc (a dense file reads higher than its line count)."""
    root = tmp_path
    dense = root / "dense.py"
    dense.write_text("y" * 4000, encoding="utf-8")  # 1000 est tokens
    out = root / "stats.json"
    treemap.write_stats([(dense, 50, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    row = stats["top_hotspots"][0]
    assert row["est_tokens"] == 1000          # chars/4, not the 50 loc
    assert row["loc"] == 50                    # loc preserved alongside
    assert stats["est_tokens"]["total"] == 1000


def test_hotspot_score_includes_token_factor(treemap, tmp_path):
    """With ccn and churn held equal, the larger file (more estimated tokens)
    ranks first - the size axis is live in the composite."""
    root = tmp_path
    big = root / "big.go"
    small = root / "small.go"
    files = [(big, 200, 30.0, "lizard"), (small, 200, 30.0, "lizard")]
    aux_data = {big: 10, small: 10}
    tokens = {big: 40000, small: 4000}  # same ccn + churn, 10x size
    out = root / "stats.json"
    treemap.write_stats(files, aux_data, "commits (last 12mo)", root, out,
                        tokens_by_path=tokens)
    hotspots = json.loads(out.read_text())["top_hotspots"]
    assert hotspots[0]["path"] == "big.go"


def test_big_but_simple_stable_file_does_not_top_on_size_alone(treemap, tmp_path):
    """PRD validation guard: a large-but-simple-stable file (a long config or
    data table - low ccn, no churn, huge size) must NOT top the hotspot list.
    The sqrt bounding keeps its single big axis from dominating a genuine
    complex+churning hotspot."""
    root = tmp_path
    data_table = root / "data.json"      # huge, trivial, frozen
    hotspot = root / "engine.go"         # complex AND churning, modest size
    files = [(data_table, 5000, 1.0, "lizard"), (hotspot, 300, 100.0, "lizard")]
    aux_data = {data_table: 0, hotspot: 30}
    tokens = {data_table: 200000, hotspot: 5000}  # data table 40x bigger
    out = root / "stats.json"
    treemap.write_stats(files, aux_data, "commits (last 12mo)", root, out,
                        tokens_by_path=tokens)
    hotspots = json.loads(out.read_text())["top_hotspots"]
    assert hotspots[0]["path"] == "engine.go"  # complexity+churn beats raw size


def test_keyhole_budget_rollup_counts_files_and_subtrees(treemap, tmp_path):
    """The est_tokens.budget block reports the repo total plus how many files
    and top-level subtrees exceed one context-window keyhole."""
    root = tmp_path
    over_file = root / "giant.py"           # single file over budget
    a1 = root / "pkg_a" / "x.py"            # pkg_a subtree over budget in sum
    a2 = root / "pkg_a" / "y.py"
    small = root / "tiny.py"
    files = [
        (over_file, 10, 1.0, "lizard"),
        (a1, 10, 1.0, "lizard"),
        (a2, 10, 1.0, "lizard"),
        (small, 10, 1.0, "lizard"),
    ]
    tokens = {over_file: 250000, a1: 150000, a2: 120000, small: 100}
    out = root / "stats.json"
    treemap.write_stats(files, None, None, root, out, tokens_by_path=tokens)
    budget = json.loads(out.read_text())["est_tokens"]["budget"]
    assert budget["budget"] == treemap.CONTEXT_WINDOW_BUDGET_TOKENS
    assert budget["total"] == 250000 + 150000 + 120000 + 100
    assert budget["files_over_budget"] == 1           # only giant.py alone
    # pkg_a (270k) and giant.py-as-its-own-subtree (250k) both exceed 200k.
    assert budget["subtrees_over_budget"] == 2
    over_names = {s["path"] for s in budget["over_budget_subtrees"]}
    assert "pkg_a" in over_names and "giant.py" in over_names


def test_est_tokens_are_post_artifact_filter(treemap, tmp_path):
    """est_tokens are computed only over the files passed in (already filtered
    by collect), so a filtered bundle never inflates the totals or budget."""
    root = tmp_path
    real = root / "real.py"
    real.write_text("z" * 400, encoding="utf-8")  # 100 tokens
    out = root / "stats.json"
    # The bundle is simply absent from `files` (collect dropped it) - the
    # totals reflect only the surviving file.
    treemap.write_stats([(real, 20, 5.0, "lizard")], None, None, root, out)
    stats = json.loads(out.read_text())
    assert stats["est_tokens"]["total"] == 100


def test_code_data_maxima_split_by_scc_language(treemap, tmp_path):
    root = tmp_path
    code = root / "app.py"
    code.write_text("x = 1\n" * 30)
    data = root / "big.json"
    data.write_text('{"k": 1}\n' * 500)
    conf = root / "settings.yaml"
    conf.write_text("k: 1\n" * 100)
    out = root / "stats.json"
    langs = {data: "JSON", conf: "YAML"}
    treemap.write_stats(
        [(code, 30, 4.0, "lizard"), (data, 500, 0.0, "scc"),
         (conf, 100, 0.0, "scc")],
        None, None, root, out, languages_by_path=langs)
    stats = json.loads(out.read_text())
    rows = {r["path"]: r for r in stats["top_large"]}
    assert stats["loc"]["max"] == 500
    assert stats["loc"]["max_code"] == rows["app.py"]["loc"] == 30
    assert stats["loc"]["max_data"] == rows["big.json"]["loc"] == 500
    assert stats["est_tokens"]["max_code"] == rows["app.py"]["est_tokens"]
    assert stats["est_tokens"]["max_data"] == rows["big.json"]["est_tokens"]
    assert stats["schema_version"] >= 3


def test_code_data_maxima_empty_side_reports_zero(treemap, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    out = tmp_path / "stats.json"
    treemap.write_stats([(f, 1, 1.0, "lizard")], None, None, tmp_path, out)
    stats = json.loads(out.read_text())
    assert stats["loc"]["max_code"] == 1
    assert stats["loc"]["max_data"] == 0
    assert stats["est_tokens"]["max_data"] == 0


def test_write_stats_distribution_blocks_pin_percentiles_and_totals(
        treemap, tmp_path):
    """Characterise every distribution block write_stats emits: p50/p95/max
    (numpy's linear interpolation) and totals for loc, est_tokens, ccn,
    fn_ccn and churn, plus scoring_coverage. Pins the arithmetic before
    the function is split into helpers."""
    a, b, c = tmp_path / "a.py", tmp_path / "b.py", tmp_path / "c.ex"
    files = [(a, 10, 2.0, "lizard"), (b, 30, 8.0, "lizard"),
             (c, 50, 20.0, "scc")]
    tokens = {a: 100, b: 300, c: 500}
    out = tmp_path / "stats.json"
    treemap.write_stats(
        files, {a: 1, b: 3}, "commits (last 12mo)", tmp_path, out,
        fn_ccn_by_path={a: [1.0, 1.0], b: [2.0, 6.0]},
        tokens_by_path=tokens)
    # The numpy stub breaks pytest.approx, so round instead.
    stats = json.loads(out.read_text(),
                       parse_float=lambda v: round(float(v), 9))
    assert stats["files_scored"] == 3
    assert stats["scoring_coverage"] == {"lizard": 2, "scc": 1}
    assert stats["loc"] == {"p50": 30.0, "p95": 48.0, "max": 50.0,
                            "max_code": 50.0, "max_data": 0.0, "total": 90}
    tok = dict(stats["est_tokens"])
    budget = tok.pop("budget")
    assert tok == {"p50": 300.0, "p95": 480.0, "max": 500.0,
                   "max_code": 500.0, "max_data": 0.0, "total": 900}
    assert budget["total"] == 900
    assert budget["files_over_budget"] == 0
    assert stats["ccn"] == {"basis": "file-aggregate", "p50": 8.0,
                            "p95": 18.8, "max": 20.0}
    fn = stats["fn_ccn"]
    assert fn["source"] == [{"name": "lizard", "approximate": False}]
    assert fn["function_count"] == 4
    assert (fn["p50"], fn["p95"], fn["max"]) == (1.5, 5.4, 6.0)
    # c.ex has no churn entry, so it counts as 0 commits.
    assert stats["churn"] == {"p50": 1.0, "p95": 2.8,
                              "max": 3.0}
    assert [r["path"] for r in stats["top_large"]] == ["c.ex", "b.py", "a.py"]
    rows = {r["path"]: r for r in stats["top_large"]}
    assert rows["c.ex"]["commits"] == 0
    assert rows["c.ex"]["max_fn_ccn"] is None
    assert rows["b.py"]["max_fn_ccn"] == 6.0
    assert "_score" not in rows["a.py"]


def test_write_stats_empty_file_list_reports_zeroes(treemap, tmp_path):
    """No scored files: every distribution reads 0 and every top list is empty,
    rather than raising on max() of an empty sequence."""
    out = tmp_path / "stats.json"
    treemap.write_stats([], None, None, tmp_path, out)
    stats = json.loads(out.read_text())
    assert stats["files_scored"] == 0
    assert stats["loc"]["max"] == stats["loc"]["p95"] == 0.0
    assert stats["ccn"]["max"] == 0.0
    assert stats["fn_ccn"]["function_count"] == 0
    assert stats["fn_ccn"]["source"] == []
    assert stats["fn_ccn"]["backend_by_language"] == {}
    assert stats["churn"] is None
    assert stats["top_hotspots"] == stats["top_complex"] == []
