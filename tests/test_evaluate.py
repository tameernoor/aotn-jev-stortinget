"""evaluate.py, entirely on a tiny synthetic fixture built in this file. Never
opens data/sets/holdout-labels.json or any other real labels file.
"""

import json

import pytest

from jev_stortinget.evaluate import EvaluateError, evaluate, load_labels

ROWS = [
    {"id": 1, "outcome": "answered", "shadow": 0.9},
    {"id": 2, "outcome": "deferred", "shadow": 0.1},
    {"id": 3, "outcome": "unclear", "shadow": 0.5},
    {"id": 4, "outcome": "not_answered", "shadow": 0.1},
    {"id": 5, "outcome": "premise_corrected", "shadow": 0.9},
    {"id": 6, "outcome": "unclear", "shadow": 0.5},
    {"id": 7, "outcome": "no_reply", "shadow": None},
]

LABELS = [
    {"id": 1, "label": "answered", "reason": "gives a date"},
    {"id": 2, "label": "not_answered", "reason": "background only", "second_choice": "deferred"},
    {"id": 3, "label": "answered", "reason": "gives a figure late in the text"},
    {"id": 4, "label": "not_answered", "reason": "discusses the topic only"},
    {"id": 5, "label": "premise_corrected", "reason": "corrects a wrong date"},
    # ids 6 and 7 are the fix's own cases: an outcome the rules could not decide
    # (unclear) or never even asked about (no_reply), labelled not_answered by the
    # reader. Neither is the same claim as a confident not_answered, and must not
    # silently agree just because deferred/pointed_elsewhere/not_answered/unclear
    # all used to collapse into the same binary bucket.
    {"id": 6, "label": "not_answered", "reason": "unclear outcome, reader says not answered"},
    {"id": 7, "label": "not_answered", "reason": "no_reply outcome, reader says not answered"},
]


def test_n_counts_every_labelled_id():
    result = evaluate(ROWS, LABELS)

    assert result["n"] == 7


def test_overall_agreement():
    result = evaluate(ROWS, LABELS)

    # correct: 1, 4, 5. wrong: 2 (deferred != not_answered), 3 (unclear != answered),
    # 6 (unclear != not_answered), 7 (no_reply != not_answered)
    assert result["agreement"] == {"correct": 3, "total": 7, "accuracy": 3 / 7}


def test_agreement_per_label():
    result = evaluate(ROWS, LABELS)

    per_label = result["agreement_per_label"]
    assert per_label["answered"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert per_label["premise_corrected"] == {"correct": 1, "total": 1, "accuracy": 1.0}
    assert per_label["not_answered"] == {"correct": 1, "total": 4, "accuracy": 0.25}
    assert per_label["deferred"] == {"correct": 0, "total": 0, "accuracy": None}
    assert per_label["pointed_elsewhere"] == {"correct": 0, "total": 0, "accuracy": None}


def test_confusion_matrix_has_unclear_and_no_reply_as_columns():
    result = evaluate(ROWS, LABELS)

    assert result["confusion_matrix"]["answered"] == {"answered": 1, "unclear": 1}
    assert result["confusion_matrix"]["not_answered"] == {
        "deferred": 1,
        "not_answered": 1,
        "unclear": 1,
        "no_reply": 1,
    }
    assert result["confusion_matrix"]["premise_corrected"] == {"premise_corrected": 1}


def test_agreement_with_second_choice_also_counts():
    result = evaluate(ROWS, LABELS)

    # id 2 now agrees too (outcome "deferred" == label's second_choice "deferred")
    assert result["agreement_with_second_choice"] == {"correct": 4, "total": 7, "accuracy": 4 / 7}


# --- the fix: unclear and no_reply are their own binary bucket, like the shadow ----


def test_unclear_outcome_never_silently_agrees_with_a_not_answered_label():
    rows = [{"id": 1, "outcome": "unclear", "shadow": 0.5}]
    labels = [{"id": 1, "label": "not_answered", "reason": "r"}]

    result = evaluate(rows, labels)

    assert result["binary_answered_or_not"]["outcome"] == {"correct": 0, "total": 1, "accuracy": 0.0}


def test_no_reply_outcome_never_silently_agrees_with_a_not_answered_label():
    rows = [{"id": 1, "outcome": "no_reply", "shadow": None}]
    labels = [{"id": 1, "label": "not_answered", "reason": "r"}]

    result = evaluate(rows, labels)

    assert result["binary_answered_or_not"]["outcome"] == {"correct": 0, "total": 1, "accuracy": 0.0}


def test_binary_answered_or_not_shadow_and_outcome_side_by_side():
    result = evaluate(ROWS, LABELS)

    binary = result["binary_answered_or_not"]
    # wrong on 3 (answered label, unclear outcome/shadow), 6 (not_answered label,
    # unclear outcome/shadow) and 7 (not_answered label, no_reply outcome / no shadow)
    assert binary["outcome"] == {"correct": 4, "total": 7, "accuracy": 4 / 7}
    assert binary["shadow"] == {"correct": 4, "total": 7, "accuracy": 4 / 7}


def test_shadow_thresholds_are_inclusive():
    rows = [
        {"id": 1, "outcome": "answered", "shadow": 0.8},  # exactly the yes threshold
        {"id": 2, "outcome": "not_answered", "shadow": 0.2},  # exactly the no threshold
    ]
    labels = [
        {"id": 1, "label": "answered", "reason": "r"},
        {"id": 2, "label": "not_answered", "reason": "r"},
    ]

    result = evaluate(rows, labels)

    assert result["binary_answered_or_not"]["shadow"] == {"correct": 2, "total": 2, "accuracy": 1.0}


def test_missing_shadow_value_counts_as_unclear_not_a_crash():
    rows = [{"id": 1, "outcome": "not_answered"}]  # no "shadow" key at all (a no_reply row)
    labels = [{"id": 1, "label": "answered", "reason": "r"}]

    result = evaluate(rows, labels)

    assert result["binary_answered_or_not"]["shadow"] == {"correct": 0, "total": 1, "accuracy": 0.0}


def test_empty_labels_gives_none_accuracy_not_a_crash():
    result = evaluate(ROWS, [])

    assert result["n"] == 0
    assert result["agreement"] == {"correct": 0, "total": 0, "accuracy": None}
    assert result["agreement_with_second_choice"]["accuracy"] is None


# --- the fix: a labelled id with no matching row raises, it is never dropped -------


def test_a_row_with_no_label_is_silently_left_out():
    rows = ROWS + [{"id": 8, "outcome": "answered", "shadow": 0.9}]  # id 8 has no label

    result = evaluate(rows, LABELS)

    assert result["n"] == 7  # id 8 simply never entered the scoring


def test_a_labelled_id_missing_from_the_results_raises():
    rows = [row for row in ROWS if row["id"] != 1]  # id 1 is labelled but now has no row
    labels = [label for label in LABELS if label["id"] in {1, 2}]

    with pytest.raises(EvaluateError, match=r"\[1\]"):
        evaluate(rows, labels)


def test_all_labelled_ids_missing_are_listed_in_the_error():
    with pytest.raises(EvaluateError, match=r"\[1, 2, 3, 4, 5, 6, 7\]"):
        evaluate([], LABELS)


# --- load_labels() ------------------------------------------------------------------


def test_load_labels_reads_the_sets_dir_labels_file(tmp_path):
    payload = [{"id": 1, "label": "answered", "reason": "r"}]
    (tmp_path / "dev-labels.json").write_text(json.dumps(payload), encoding="utf-8")

    assert load_labels("dev", sets_dir=tmp_path) == payload


def test_load_labels_returns_empty_list_when_the_file_does_not_exist(tmp_path):
    assert load_labels("dev", sets_dir=tmp_path) == []
