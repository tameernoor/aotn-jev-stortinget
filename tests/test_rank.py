"""rank.py's arithmetic, on synthetic rows (never a real run's output)."""

from jev_stortinget.rank import format_table, rank_by_ministry


def row(ministry, outcome):
    return {"id": 1, "answered_by": ministry, "outcome": outcome}


def test_unanswered_share_counts_deferred_pointed_elsewhere_not_answered():
    rows = [
        row("helseministeren", "answered"),
        row("helseministeren", "deferred"),
        row("helseministeren", "pointed_elsewhere"),
        row("helseministeren", "not_answered"),
    ]

    ranking = rank_by_ministry(rows)

    helse = ranking["ministries"]["helseministeren"]
    assert helse["n"] == 4
    assert helse["unanswered_share"] == 0.75  # 3 of the 4 decided pairs


def test_unclear_is_reported_separately_and_not_counted_as_unanswered():
    rows = [
        row("helseministeren", "answered"),
        row("helseministeren", "unclear"),
    ]

    ranking = rank_by_ministry(rows)

    helse = ranking["ministries"]["helseministeren"]
    assert helse["unclear"] == 1
    assert helse["unanswered_share"] == 0.0  # the only decided pair was answered
    assert helse["n"] == 2  # unclear still counts toward n


def test_no_reply_is_excluded_from_the_share_and_from_unclear():
    rows = [
        row("helseministeren", "answered"),
        row("helseministeren", "no_reply"),
    ]

    ranking = rank_by_ministry(rows)

    helse = ranking["ministries"]["helseministeren"]
    assert helse["n"] == 2
    assert helse["unclear"] == 0
    assert helse["unanswered_share"] == 0.0  # no_reply is neither decided nor unclear


def test_share_is_none_when_nothing_is_decided():
    rows = [row("helseministeren", "unclear"), row("helseministeren", "no_reply")]

    ranking = rank_by_ministry(rows)

    assert ranking["ministries"]["helseministeren"]["unanswered_share"] is None


def test_ministries_are_kept_separate():
    rows = [
        row("helseministeren", "not_answered"),
        row("justisministeren", "answered"),
    ]

    ranking = rank_by_ministry(rows)

    assert set(ranking["ministries"]) == {"helseministeren", "justisministeren"}
    assert ranking["ministries"]["helseministeren"]["unanswered_share"] == 1.0
    assert ranking["ministries"]["justisministeren"]["unanswered_share"] == 0.0


def test_counts_per_outcome_are_reported_per_ministry():
    rows = [row("helseministeren", "answered"), row("helseministeren", "answered"), row("helseministeren", "deferred")]

    ranking = rank_by_ministry(rows)

    assert ranking["ministries"]["helseministeren"]["counts"] == {"answered": 2, "deferred": 1}


# --- table formatting: no crash, highest share first, "n/a" for an undecided ministry ---


def test_format_table_sorts_by_share_descending_with_no_decided_pairs_last():
    ranking = {
        "ministries": {
            "low": {"n": 2, "counts": {}, "unanswered_share": 0.1, "unclear": 0},
            "high": {"n": 2, "counts": {}, "unanswered_share": 0.9, "unclear": 0},
            "none_decided": {"n": 1, "counts": {}, "unanswered_share": None, "unclear": 1},
        }
    }

    table = format_table(ranking)
    lines = table.splitlines()

    high_idx = next(i for i, line in enumerate(lines) if line.startswith("high"))
    low_idx = next(i for i, line in enumerate(lines) if line.startswith("low"))
    none_idx = next(i for i, line in enumerate(lines) if line.startswith("none_decided"))
    assert high_idx < low_idx < none_idx
    assert "n/a" in lines[none_idx]
    assert "90%" in lines[high_idx]
