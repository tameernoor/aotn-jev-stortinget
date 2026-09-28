"""evaluate.py, entirely on a tiny synthetic fixture built in this file. Never
opens data/sets/holdout-labels.json or any other real labels file.
"""

import json

from jev_stortinget.evaluate import evaluate, load_labels

ROWS = [
    {"id": 1, "outcome": "answered", "shadow": 0.9},
    {"id": 2, "outcome": "deferred", "shadow": 0.1},
    {"id": 3, "outcome": "unclear", "shadow": 0.5},
    {"id": 4, "outcome": "not_answered", "shadow": 0.1},
    {"id": 5, "outcome": "premise_corrected", "shadow": 0.9},
]

LABELS = [
    {"id": 1, "label": "answered", "reason": "gives a date"},
    {"id": 2, "label": "not_answered", "reason": "background only", "second_choice": "deferred"},
    {"id": 3, "label": "answered", "reason": "gives a figure late in the text"},
    {"id": 4, "label": "not_answered", "reason": "discusses the topic only"},
    {"id": 5, "label": "premise_corrected", "reason": "corrects a wrong date"},
    {"id": 999, "label": "answered", "reason": "no matching row, must be skipped"},
]


def test_ids_with_no_matching_row_or_label_are_silently_skipped():
    result = evaluate(ROWS, LABELS)

    assert result["n"] == 5  # id 999 dropped, not counted


def test_overall_agreement():
    result = evaluate(ROWS, LABELS)

    # correct: 1 (answered/answered), 4 (not_answered/not_answered), 5 (premise_corrected/premise_corrected)
    # wrong: 2 (not_answered label, deferred outcome), 3 (answered label, unclear outcome)
    assert result["agreement"] == {"correct": 3, "total": 5, "accuracy": 0.6}


def test_agreement_per_label():
    result = evaluate(ROWS, LABELS)

    per_label = result["agreement_per_label"]
    assert per_label["answered"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert per_label["premise_corrected"] == {"correct": 1, "total": 1, "accuracy": 1.0}
    assert per_label["not_answered"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert per_label["deferred"] == {"correct": 0, "total": 0, "accuracy": None}
    assert per_label["pointed_elsewhere"] == {"correct": 0, "total": 0, "accuracy": None}


def test_confusion_matrix_has_unclear_as_a_column():
    result = evaluate(ROWS, LABELS)

    assert result["confusion_matrix"]["answered"] == {"answered": 1, "unclear": 1}
    assert result["confusion_matrix"]["not_answered"] == {"deferred": 1, "not_answered": 1}
    assert result["confusion_matrix"]["premise_corrected"] == {"premise_corrected": 1}


def test_agreement_with_second_choice_also_counts():
    result = evaluate(ROWS, LABELS)

    # id 2 now agrees too (outcome "deferred" == label's second_choice "deferred")
    assert result["agreement_with_second_choice"] == {"correct": 4, "total": 5, "accuracy": 0.8}


def test_binary_answered_or_not_shadow_and_outcome_side_by_side():
    result = evaluate(ROWS, LABELS)

    binary = result["binary_answered_or_not"]
    # outcome: id3's "unclear" is not "answered", so it disagrees with label "answered" -> only id3 wrong
    assert binary["outcome"] == {"correct": 4, "total": 5, "accuracy": 0.8}
    # shadow: id3's 0.5 is neither >= 0.8 nor <= 0.2, so it is its own "unclear" -> only id3 wrong
    assert binary["shadow"] == {"correct": 4, "total": 5, "accuracy": 0.8}


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


# --- load_labels() ------------------------------------------------------------------


def test_load_labels_reads_the_sets_dir_labels_file(tmp_path):
    payload = [{"id": 1, "label": "answered", "reason": "r"}]
    (tmp_path / "dev-labels.json").write_text(json.dumps(payload), encoding="utf-8")

    assert load_labels("dev", sets_dir=tmp_path) == payload


def test_load_labels_returns_empty_list_when_the_file_does_not_exist(tmp_path):
    assert load_labels("dev", sets_dir=tmp_path) == []
