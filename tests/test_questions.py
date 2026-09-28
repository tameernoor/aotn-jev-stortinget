"""questions/pair.yaml must equal docs/questions-design.md section 2's YAML block
character for character (the file was extracted straight from that block, not
retyped). This test parses the committed file and checks its shape; it does not
re-check byte equality against the doc (a doc edit is not this file's job to catch),
only that what ships is valid and matches the design's 18 ids."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = REPO_ROOT / "questions" / "pair.yaml"

EXPECTED_IDS = {
    "asks_amount",
    "asks_time",
    "asks_yes_or_no",
    "asks_action",
    "asks_why",
    "asks_assessment",
    "asks_facts",
    "states_amount",
    "states_time",
    "says_yes_or_no",
    "names_action",
    "gives_reason",
    "states_position",
    "gives_facts",
    "corrects_premise",
    "defers",
    "points_elsewhere",
    "gives_what_is_asked",
}

SHADOW_ID = "gives_what_is_asked"


def _load() -> dict:
    return yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))


def test_pair_yaml_has_exactly_the_18_ids_from_the_design():
    questions = _load()

    assert len(questions) == 18
    assert set(questions) == EXPECTED_IDS


def test_every_question_is_a_noul_with_string_true_false_criteria():
    questions = _load()

    for qid, spec in questions.items():
        assert spec["type"] == "noul", qid
        assert isinstance(spec["instructions"], str) and spec["instructions"], qid
        criteria = spec["criteria"]
        assert isinstance(criteria["true"], str), qid  # "true"/"false" stayed strings, not booleans
        assert isinstance(criteria["false"], str), qid


def test_shadow_question_is_present_and_about_both_texts():
    questions = _load()

    shadow = questions[SHADOW_ID]
    assert "reply" in shadow["instructions"]
    assert "question" in shadow["instructions"]


def test_file_matches_the_design_doc_s_yaml_block_character_for_character():
    design = (REPO_ROOT / "docs" / "questions-design.md").read_text(encoding="utf-8")
    lines = design.splitlines()
    start = lines.index("```yaml") + 1
    end = lines.index("```", start)
    expected = "\n".join(lines[start:end]) + "\n"

    assert QUESTIONS_PATH.read_text(encoding="utf-8") == expected
