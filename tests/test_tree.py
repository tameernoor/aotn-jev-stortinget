"""tree.py, offline only: FakeJev stands in for Jev, and --from never touches it
at all. Covers the paragraph split, the question ids built per asked type and
per why-not reason, every verdict() path, the routing (why-not only for a pair
route_stage did not answer, a follow-up only for the reason that actually
fired), and the aggregation into results/tree/'s five outputs.
"""

import asyncio
import json

import pytest
from fakes import ConcurrencyTrackingAsk, FakeJev

from jev_stortinget.__main__ import main
from jev_stortinget.fetch import Record
from jev_stortinget.tree import (
    MAX_IN_FLIGHT,
    MAX_PARAGRAPHS,
    build_by_minister,
    build_follow_questions,
    build_no_data,
    build_pair_record,
    build_promised_later,
    build_route_questions,
    build_summary,
    build_why_questions,
    format_by_minister_table,
    load_stage1_asked,
    paragraphs,
    reply_says_tags,
    route_hit,
    route_stage,
    tree,
    verdict,
    whynot_stage,
)

# --- paragraphs(): split, strip, cap -------------------------------------------------


def test_paragraphs_splits_on_newlines_and_drops_blank_lines():
    reply = "First.\n\n  \nSecond.\nThird.\n"
    assert paragraphs(reply) == ["First.", "Second.", "Third."]


def test_paragraphs_under_the_cap_is_unchanged():
    reply = "\n".join(f"p{i}" for i in range(1, MAX_PARAGRAPHS + 1))  # exactly 12
    assert paragraphs(reply) == [f"p{i}" for i in range(1, MAX_PARAGRAPHS + 1)]


def test_paragraphs_over_the_cap_merges_the_rest_into_the_last_one():
    reply = "\n".join(f"p{i}" for i in range(1, 16))  # 15 lines
    result = paragraphs(reply)
    assert len(result) == MAX_PARAGRAPHS
    assert result[:11] == [f"p{i}" for i in range(1, 12)]
    assert result[11] == "p12 p13 p14 p15"


# --- question building --------------------------------------------------------------


def test_build_route_questions_one_id_per_asked_type_per_paragraph():
    questions = build_route_questions(["yes_or_no", "action"], 2)
    assert set(questions) == {"yes_or_no_p1", "yes_or_no_p2", "action_p1", "action_p2"}
    for q in questions.values():
        assert q["type"] == "noul"
        assert "{p}" not in q["instructions"]  # the placeholder was filled in


def test_build_route_questions_adds_nodata_only_for_amount_or_facts():
    assert "nodata_p1" not in build_route_questions(["yes_or_no"], 1)
    assert "nodata_p1" in build_route_questions(["amount"], 1)
    assert "nodata_p1" in build_route_questions(["facts"], 1)
    # asked twice over (amount and facts both present): still one nodata id per paragraph
    with_both = build_route_questions(["amount", "facts"], 1)
    assert list(with_both).count("nodata_p1") == 1


def test_build_why_questions_all_five_reasons_every_paragraph():
    questions = build_why_questions(2)
    assert set(questions) == {
        f"{reason}_p{i}" for reason in ("nodata", "nocomment", "elsewhere", "later", "earlier") for i in (1, 2)
    }


def test_build_follow_questions_only_where_the_parent_fired():
    why_values = {
        "nodata_p1": 0.9,  # fires
        "nodata_p2": 0.1,
        "later_p1": 0.1,
        "later_p2": 0.85,  # fires
        "elsewhere_p1": 0.9,  # no follow-up mapped for elsewhere at all
    }
    follow = build_follow_questions(why_values, 2)
    assert set(follow) == {"collect_p1", "date_p2"}


# --- verdict(): every path, in priority order ----------------------------------------


def test_verdict_answered_wins_at_the_first_paragraph_that_hits():
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.1, "yes_or_no_p2": 0.85}, why=None, follow=None, n_paragraphs=2)
    assert v == {"verdict": "answered", "reasons": [], "paragraph_index": 1, "date_given": None, "collect_promised": None}


def test_verdict_reason_priority_order_nodata_before_everything():
    why = {"later_p1": 0.9, "nodata_p1": 0.85}
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.05}, why=why, follow=None, n_paragraphs=1)
    assert v["verdict"] == "nodata"
    assert v["reasons"] == ["nodata", "later"]  # both fired, in REASON_ORDER, not just the winner
    assert v["paragraph_index"] == 0


def test_verdict_reason_priority_elsewhere_before_later():
    why = {"elsewhere_p1": 0.85, "later_p1": 0.9}
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.05}, why=why, follow=None, n_paragraphs=1)
    assert v["verdict"] == "elsewhere"
    assert v["reasons"] == ["elsewhere", "later"]


def test_verdict_later_records_date_given_only_when_later_won():
    why = {"later_p1": 0.85}
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.05}, why=why, follow={"date_p1": 0.9}, n_paragraphs=1)
    assert v["verdict"] == "later"
    assert v["date_given"] is True
    assert v["collect_promised"] is None


def test_verdict_later_date_given_false_when_no_date_follow_fired():
    why = {"later_p1": 0.85}
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.05}, why=why, follow={"date_p1": 0.2}, n_paragraphs=1)
    assert v["date_given"] is False


def test_verdict_nodata_records_collect_promised_only_when_nodata_won():
    why = {"nodata_p1": 0.85}
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.05}, why=why, follow={"collect_p1": 0.9}, n_paragraphs=1)
    assert v["verdict"] == "nodata"
    assert v["collect_promised"] is True
    assert v["date_given"] is None


def test_verdict_swapped_when_nothing_in_why_fired_but_swap_did():
    v = verdict(
        asked=["yes_or_no"], values={"yes_or_no_p1": 0.05}, why={"nodata_p1": 0.1}, follow={"swap": 0.9}, n_paragraphs=1
    )
    assert v == {"verdict": "swapped", "reasons": [], "paragraph_index": None, "date_given": None, "collect_promised": None}


def test_verdict_unsure_when_an_asked_type_value_is_strictly_between_the_bands():
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.5}, why={}, follow={}, n_paragraphs=1)
    assert v["verdict"] == "unsure"


def test_verdict_boundary_value_is_not_unsure():
    # exactly NO (0.2): not strictly between, so it does not count as unsure
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.2}, why={}, follow={}, n_paragraphs=1)
    assert v["verdict"] == "not_answered"


def test_verdict_not_answered_when_nothing_fired_at_all():
    v = verdict(asked=["yes_or_no"], values={"yes_or_no_p1": 0.05}, why={}, follow={}, n_paragraphs=1)
    assert v["verdict"] == "not_answered"


def test_verdict_nodata_values_never_count_toward_unsure():
    # a nodata_p1 value in `values` (present when amount/facts was asked) must
    # never be read as an asked-type value for the unsure check.
    v = verdict(asked=["amount"], values={"amount_p1": 0.05, "nodata_p1": 0.5}, why={}, follow={}, n_paragraphs=1)
    assert v["verdict"] == "not_answered"


# --- routing: whynot only for what route_stage left open, follow only where fired ---


def _row(id_, question, paragraphs_, asked, values):
    return {"id": id_, "question": question, "paragraphs": paragraphs_, "asked": asked, "values": values}


def test_route_hit_is_none_when_no_asked_type_reads_confidently():
    assert route_hit(["yes_or_no"], {"yes_or_no_p1": 0.5}, 1) is None
    assert route_hit(["yes_or_no"], {"yes_or_no_p1": 0.9}, 1) == 0


def test_whynot_stage_leaves_an_already_answered_row_untouched_and_processes_the_rest():
    answered = _row(1, "Q1", ["p1 text"], ["yes_or_no"], {"yes_or_no_p1": 0.9})
    unanswered = _row(2, "Q2", ["p1 text"], ["yes_or_no"], {"yes_or_no_p1": 0.1})
    rows = [answered, unanswered]

    todo = [r for r in rows if route_hit(r["asked"], r["values"], len(r["paragraphs"])) is None]
    assert [r["id"] for r in todo] == [2]

    fake = FakeJev(values={"later_p1": 0.9, "date_p1": 0.95})
    asyncio.run(whynot_stage(todo, fake.ask))

    assert "why" not in answered
    assert "why" in unanswered
    assert unanswered["why"]["later_p1"] == pytest.approx(0.9)
    # only later fired, so only its own follow-up (date) was asked, no collect and no swap
    assert unanswered["follow"] == {"date_p1": pytest.approx(0.95)}
    assert len(fake.calls) == 2  # the why call, then the date follow-up


def test_whynot_stage_asks_swap_only_when_nothing_else_fired():
    row = _row(3, "Q3", ["p1 text", "p2 text"], ["yes_or_no"], {"yes_or_no_p1": 0.1, "yes_or_no_p2": 0.1})
    fake = FakeJev(values={"swap": 0.77})  # every why-not id defaults to 0.05: nothing fires

    asyncio.run(whynot_stage([row], fake.ask))

    assert row["follow"] == {"swap": pytest.approx(0.77)}
    assert len(fake.calls) == 2
    assert "reply" not in fake.calls[0]["state"]  # the why call never carries `reply`
    assert fake.calls[1]["state"]["reply"] == "p1 text\np2 text"  # only the swap call does


def test_route_stage_only_asks_about_pairs_with_a_recognised_asked_type():
    records = [
        Record(1, 1, "m", "m", "MP", "X", None, None, "Q1", "Reply one."),
        Record(2, 2, "m", "m", "MP", "X", None, None, "Q2", "Reply two."),
    ]
    asked_by_id = {1: ["yes_or_no"]}  # id 2 never entered the tree at all
    fake = FakeJev(values={"yes_or_no_p1": 0.9})

    rows = asyncio.run(route_stage(records, asked_by_id, fake.ask))

    assert [r["id"] for r in rows] == [1]
    assert len(fake.calls) == 1


def test_route_stage_respects_max_in_flight():
    records = [Record(i, i, "m", "m", "MP", "X", None, None, f"Q{i}", f"Reply {i}.") for i in range(1, 31)]
    asked_by_id = {i: ["yes_or_no"] for i in range(1, 31)}
    tracker = ConcurrencyTrackingAsk(delay=0.01)

    rows = asyncio.run(route_stage(records, asked_by_id, tracker))

    assert len(rows) == 30
    assert 1 < tracker.peak <= MAX_IN_FLIGHT


# --- load_stage1_asked() -------------------------------------------------------------


def test_load_stage1_asked_reads_asks_types_at_or_above_the_yes_band(tmp_path):
    path = tmp_path / "results.jsonl"
    lines = [
        {"id": 1, "values": {"asks_yes_or_no": 0.9, "asks_action": 0.5}},
        {"id": 2, "values": {"asks_yes_or_no": 0.1}},  # nothing recognised
        {"id": 3, "values": {"asks_amount": 0.8, "asks_time": 0.8}},  # boundary counts as yes
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    asked = load_stage1_asked(path)

    assert asked == {1: ["yes_or_no"], 3: ["amount", "time"]}


# --- aggregation -----------------------------------------------------------------


def _tree_row(**overrides):
    base = {
        "id": 1,
        "ministry": "testministeren",
        "mp": "Test Person",
        "party": "X",
        "question": "Spørsmål?",
        "asked": ["yes_or_no"],
        "paragraphs": ["Første avsnitt her.", "Andre avsnitt.", "Tredje avsnitt her nå."],
        "values": {"yes_or_no_p1": 0.05, "yes_or_no_p2": 0.05, "yes_or_no_p3": 0.05},
        "tokens": 1000,
    }
    base.update(overrides)
    return base


def test_build_pair_record_computes_word_count_before_the_answering_paragraph():
    row = _tree_row(values={"yes_or_no_p1": 0.05, "yes_or_no_p2": 0.05, "yes_or_no_p3": 0.9})
    record = build_pair_record(row)
    assert record["verdict"] == "answered"
    assert record["paragraph_index"] == 2
    assert record["word_count_before"] == 3 + 2  # "Første avsnitt her." + "Andre avsnitt."
    assert "paragraphs" not in record  # no reply text in the compact record


def test_build_pair_record_nodata_with_collect_promised():
    row = _tree_row(why={"nodata_p1": 0.9}, follow={"collect_p1": 0.95})
    record = build_pair_record(row)
    assert record["verdict"] == "nodata"
    assert record["collect_promised"] is True
    assert record["word_count_before"] == 0  # nodata fired on the first paragraph


def test_reply_says_tags_are_not_mutually_exclusive():
    row = _tree_row(why={"nodata_p1": 0.9, "later_p2": 0.85})
    assert set(reply_says_tags(row)) == {"nodata", "later"}


def test_build_summary_counts_and_cost():
    rows = [
        _tree_row(id=1, tokens=1000, values={"yes_or_no_p1": 0.9}),  # answered, no why/follow at all
        _tree_row(id=2, tokens=2000, why={"nodata_p1": 0.9}, follow={"collect_p1": 0.9}),
        _tree_row(id=3, tokens=1500, why={"later_p1": 0.9}, follow={}),  # follow asked but nothing came back
    ]
    pair_records = [build_pair_record(r) for r in rows]

    summary = build_summary(rows, pair_records)

    assert summary["n_pairs"] == 3
    assert summary["answered"] == 1
    assert summary["no_hit"] == 2
    assert summary["reply_says"]["nodata"] == 1
    assert summary["reply_says"]["later"] == 1
    assert summary["cost"]["route_stage"]["input_tokens"] == 4500
    assert summary["cost"]["whynot_stage"]["pairs"] == 2  # rows 2 and 3 reached the why-not stage
    assert summary["cost"]["whynot_stage"]["requests"] == 3  # 2 why calls + 1 non-empty follow (row 2 only)


def test_build_by_minister_counts_tags_per_ministry_not_exclusively():
    rows = [
        _tree_row(id=1, ministry="a", why={"nodata_p1": 0.9, "later_p2": 0.85}),
        _tree_row(id=2, ministry="a", why={"elsewhere_p1": 0.9}),
        _tree_row(id=3, ministry="b", values={"yes_or_no_p1": 0.9}),  # answered: no tags
    ]
    by_minister = build_by_minister(rows)["ministries"]

    assert by_minister["a"]["n"] == 2
    assert by_minister["a"]["nodata"] == 1
    assert by_minister["a"]["later"] == 1
    assert by_minister["a"]["elsewhere"] == 1
    assert by_minister["b"] == {"n": 1, "nodata": 0, "nocomment": 0, "elsewhere": 0, "later": 0, "earlier": 0}


def test_format_by_minister_table_filters_by_min_n():
    by_minister = {"ministries": {"big": {"n": 60, **{t: 0 for t in ("nodata", "nocomment", "elsewhere", "later", "earlier")}},
                                   "small": {"n": 10, **{t: 0 for t in ("nodata", "nocomment", "elsewhere", "later", "earlier")}}}}
    table = format_by_minister_table(by_minister, min_n=50)
    assert "big" in table
    assert "small" not in table


def test_build_no_data_carries_the_quote_and_collect_flag():
    row = _tree_row(why={"nodata_p1": 0.05, "nodata_p2": 0.9}, follow={"collect_p2": 0.9})
    entries = build_no_data([row])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["paragraph_index"] == 1
    assert entry["quote"] == row["paragraphs"][1]
    assert entry["collect_promised"] is True
    assert entry["ministry"] == row["ministry"]
    assert entry["question"] == row["question"]


def test_build_no_data_excludes_pairs_where_nodata_never_fired():
    row = _tree_row(why={"nodata_p1": 0.1})
    assert build_no_data([row]) == []


def test_build_promised_later_carries_the_quote_and_date_flag():
    row = _tree_row(why={"later_p1": 0.9}, follow={"date_p1": 0.2})
    entries = build_promised_later([row])
    assert len(entries) == 1
    assert entries[0]["date_given"] is False
    assert entries[0]["quote"] == row["paragraphs"][0]


# --- CLI: --from skips Jev entirely ---------------------------------------------


def test_tree_cli_from_rebuilds_outputs_without_any_ask(tmp_path, monkeypatch):
    import jev_stortinget.tree as tree_module

    from_path = tmp_path / "tree-all.json"
    from_path.write_text(
        json.dumps(
            [
                _tree_row(id=1, values={"yes_or_no_p1": 0.9}),
                _tree_row(id=2, ministry="other", why={"nodata_p1": 0.9}, follow={"collect_p1": 0.9}),
            ]
        ),
        encoding="utf-8",
    )
    results_dir = tmp_path / "results-tree"
    monkeypatch.setattr(tree_module, "DEFAULT_RESULTS_DIR", results_dir)

    main(["tree", "--from", str(from_path)])

    assert (results_dir / "tree-summary.json").exists()
    assert (results_dir / "by-minister.json").exists()
    assert (results_dir / "no-data.json").exists()
    assert (results_dir / "promised-later.json").exists()
    assert (results_dir / "tree-2024-2025.json").exists()

    summary = json.loads((results_dir / "tree-summary.json").read_text(encoding="utf-8"))
    assert summary["n_pairs"] == 2
    assert summary["answered"] == 1


def test_tree_function_from_path_never_builds_a_jev_client(tmp_path, monkeypatch):
    import jev_stortinget.tree as tree_module

    from_path = tmp_path / "tree-all.json"
    from_path.write_text(json.dumps([_tree_row()]), encoding="utf-8")

    def _boom(*args, **kwargs):
        raise AssertionError("a --from run must never build a Jev client")

    monkeypatch.setattr(tree_module, "_LazyTreeJev", _boom)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    summary = asyncio.run(
        tree(from_path=from_path, out_dir=tmp_path / "out", results_dir=tmp_path / "results")
    )
    assert summary["n_pairs"] == 1
