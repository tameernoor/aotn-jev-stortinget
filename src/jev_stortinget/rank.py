"""Rank ministries by share of unanswered written questions, for `--set all`.

Per the plan's "Headline metric": the unanswered share counts deferred,
pointed_elsewhere and not_answered as a share of the pairs with a decided outcome
(answered, premise_corrected, deferred, pointed_elsewhere or not_answered).
`unclear` and `no_reply` are never decided outcomes; unclear is reported separately
beside the share and never folded into either side, and no_reply (an empty reply,
never sent to Jev) is excluded from both the share and the unclear count, the same
way an undecided pair is silently left out rather than counted as either.

A ministry is attributed by `answered_by` (the minister who actually answered), not
`sporsmal_til_minister_tittel`, per docs/questions-design.md: a share of pairs are
transferred to another minister as "rette vedkommende" before being answered.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from .rules import ANSWERED, DEFERRED, NOT_ANSWERED, POINTED_ELSEWHERE, PREMISE_CORRECTED, UNCLEAR

DECIDED = (ANSWERED, PREMISE_CORRECTED, DEFERRED, POINTED_ELSEWHERE, NOT_ANSWERED)
UNANSWERED = (DEFERRED, POINTED_ELSEWHERE, NOT_ANSWERED)


def rank_by_ministry(rows: Sequence[dict]) -> dict:
    """rows: results.jsonl's rows (id, answered_by, outcome, ...). Returns
    {"ministries": {ministry: {n, counts, unanswered_share, unclear}}}, one entry
    per distinct `answered_by`."""
    by_ministry: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_ministry[row["answered_by"]].append(row)

    ministries: dict[str, dict] = {}
    for ministry, ministry_rows in by_ministry.items():
        counts = Counter(row["outcome"] for row in ministry_rows)
        decided = sum(counts[o] for o in DECIDED)
        unanswered = sum(counts[o] for o in UNANSWERED)
        ministries[ministry] = {
            "n": len(ministry_rows),
            "counts": dict(counts),
            "unanswered_share": unanswered / decided if decided else None,
            "unclear": counts.get(UNCLEAR, 0),
        }
    return {"ministries": ministries}


def _sort_key(item: tuple[str, dict]) -> tuple[bool, float, str]:
    ministry, data = item
    share = data["unanswered_share"]
    # Ministries with no decided pairs (share is None) sort last, below every real share.
    return (share is None, -(share or 0.0), ministry)


def format_table(ranking: dict) -> str:
    header = f"{'ministry':<55} {'n':>5} {'unanswered':>11} {'unclear':>8}"
    lines = [header, "-" * len(header)]
    for ministry, data in sorted(ranking["ministries"].items(), key=_sort_key):
        share = data["unanswered_share"]
        share_str = f"{share:.0%}" if share is not None else "n/a"
        lines.append(f"{ministry:<55} {data['n']:>5} {share_str:>11} {data['unclear']:>8}")
    return "\n".join(lines)
