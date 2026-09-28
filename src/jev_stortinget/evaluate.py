"""Score run.py's decided outcomes against an independent reader's labels.

`evaluate(rows, labels)` works on the same shapes run.py already produces and
reads: `rows` is results.jsonl's rows (one per pair, holding at least `id`,
`outcome` and `shadow`), `labels` is one set's labels file (one per id, holding at
least `id` and `label`, from {answered, premise_corrected, deferred,
pointed_elsewhere, not_answered}, and optionally `second_choice`, a label that also
counts as agreement). Only ids present in both are scored; a label with no matching
row (or the reverse) is silently left out, the same "silently skipped, not counted
as wrong" convention jev_turbine's evaluate.py uses for its own exclusions.

Reports, per the plan: agreement of `outcome` with `label`, overall and per label; a
confusion matrix with `unclear` as a column, since it is an outcome value that can
occur; agreement when `second_choice` also counts; and the shadow noul
(`gives_what_is_asked`) scored as answered-or-not (>= 0.8 means answered, <= 0.2
means not, otherwise unclear) against the label collapsed the same way (answered or
premise_corrected vs the rest), next to the same binary view of `outcome` itself, so
a README can compare "decomposition vs labels" against "shadow vs labels" on the
same pairs (see docs/questions-design.md section 3).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from .rules import ANSWERED, PREMISE_CORRECTED
from .sets import SETS_DIR

LABELS = ("answered", "premise_corrected", "deferred", "pointed_elsewhere", "not_answered")

BINARY_ANSWERED = "answered"
BINARY_NOT_ANSWERED = "not_answered"
BINARY_UNCLEAR = "unclear"

SHADOW_YES = 0.8
SHADOW_NO = 0.2

NOTE = (
    "label is the independent reader's judgment; outcome is rules.outcome() run "
    "over Jev's answers. The binary view collapses answered and premise_corrected "
    "into 'answered' and everything else (including unclear/no_reply) into "
    "'not_answered', except a shadow value strictly between 0.2 and 0.8, which is "
    "its own 'unclear' and never counted as agreeing."
)


def load_labels(set_name: str, sets_dir: Path | None = None) -> list[dict]:
    """Every labelled id for `set_name` (data/sets/<set_name>-labels.json), or []
    if that file does not exist (dev and all are not required to have one)."""
    sets_dir = sets_dir if sets_dir is not None else SETS_DIR
    path = sets_dir / f"{set_name}-labels.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _binary_label(label: str) -> str:
    return BINARY_ANSWERED if label in (ANSWERED, PREMISE_CORRECTED) else BINARY_NOT_ANSWERED


def _binary_outcome(outcome: str) -> str:
    if outcome in (ANSWERED, PREMISE_CORRECTED):
        return BINARY_ANSWERED
    return BINARY_NOT_ANSWERED  # unclear and no_reply both count as "not answered"


def _binary_shadow(value: float | None) -> str:
    if value is None:
        return BINARY_UNCLEAR
    if value >= SHADOW_YES:
        return BINARY_ANSWERED
    if value <= SHADOW_NO:
        return BINARY_NOT_ANSWERED
    return BINARY_UNCLEAR


def _agreement(pairs: Sequence[tuple[str, str]]) -> dict:
    total = len(pairs)
    correct = sum(1 for expected, got in pairs if expected == got)
    return {"correct": correct, "total": total, "accuracy": correct / total if total else None}


def evaluate(rows: Sequence[dict], labels: Sequence[dict]) -> dict:
    by_id = {row["id"]: row for row in rows}
    scored = [(label, by_id[label["id"]]) for label in labels if label["id"] in by_id]

    agreement = _agreement([(label["label"], row["outcome"]) for label, row in scored])

    agreement_per_label = {
        name: _agreement(
            [(label["label"], row["outcome"]) for label, row in scored if label["label"] == name]
        )
        for name in LABELS
    }

    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for label, row in scored:
        confusion[label["label"]][row["outcome"]] += 1

    # "outcome in {label, second_choice}" is an OR against two different expected
    # values, which _agreement's single-pair-per-item shape cannot express, so this
    # one is a plain boolean sum instead.
    second_choice_correct = sum(
        1
        for label, row in scored
        if row["outcome"] == label["label"] or (label.get("second_choice") and row["outcome"] == label["second_choice"])
    )
    agreement_with_second_choice = {
        "correct": second_choice_correct,
        "total": len(scored),
        "accuracy": second_choice_correct / len(scored) if scored else None,
    }

    binary_shadow = _agreement(
        [(_binary_label(label["label"]), _binary_shadow(row.get("shadow"))) for label, row in scored]
    )
    binary_outcome = _agreement(
        [(_binary_label(label["label"]), _binary_outcome(row["outcome"])) for label, row in scored]
    )

    return {
        "note": NOTE,
        "n": len(scored),
        "agreement": agreement,
        "agreement_per_label": agreement_per_label,
        "confusion_matrix": {expected: dict(got_counts) for expected, got_counts in confusion.items()},
        "agreement_with_second_choice": agreement_with_second_choice,
        "binary_answered_or_not": {
            "shadow": binary_shadow,
            "outcome": binary_outcome,
        },
    }
