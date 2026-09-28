"""Every return path of rules.outcome(), against docs/questions-design.md section 4,
with synthetic noul values (never real Jev output). The shadow `gives_what_is_asked`
is deliberately left out of every fixture: its absence is itself proof the rules
never touch it (a KeyError would fail the test if they did).
"""

import pytest

from jev_stortinget.rules import (
    ANSWERED,
    DEFERRED,
    NOT_ANSWERED,
    POINTED_ELSEWHERE,
    PREMISE_CORRECTED,
    UNCLEAR,
    band,
    outcome,
)

# Every id rules.py can read, defaulted to a confident "no" (0.05). Override per test.
IDS = [
    "corrects_premise",
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
    "defers",
    "points_elsewhere",
]

YES = 0.9
NO = 0.05
UNSURE = 0.5


def V(**overrides) -> dict[str, float]:
    values = {qid: NO for qid in IDS}
    values.update(overrides)
    return values


# --- band() thresholds, inclusive per the design (YES=0.8, NO=0.2) -----------------


def test_band_thresholds_are_inclusive():
    assert band(0.8) == "yes"
    assert band(0.2) == "no"
    assert band(0.79) == "uncertain"
    assert band(0.21) == "uncertain"
    assert band(1.0) == "yes"
    assert band(0.0) == "no"


# --- rule 1: a confident correction wins outright, before anything else is read ----


def test_corrects_premise_yes_wins_outright():
    values = V(corrects_premise=YES, asks_time=YES, states_time=NO)
    result, reasons, read = outcome(values)

    assert result == PREMISE_CORRECTED
    assert reasons == ["corrects_premise yes"]
    assert read == ["corrects_premise"]  # nothing else was even looked at


# --- rule 2: no ask type recognised at all -----------------------------------------


def test_no_ask_type_recognised_is_unclear():
    values = V()  # corrects_premise no, every asks_* no
    result, reasons, read = outcome(values)

    assert result == UNCLEAR
    assert reasons == ["no ask type recognised"]
    assert read == [
        "corrects_premise",
        "asks_amount",
        "asks_time",
        "asks_yes_or_no",
        "asks_action",
        "asks_why",
        "asks_assessment",
        "asks_facts",
    ]


# --- rule 2 fallback: an uncertain ask type is used only when none is confident ----


def test_uncertain_ask_type_is_used_when_no_confident_ask_exists():
    values = V(asks_time=UNSURE, states_time=YES)
    result, reasons, read = outcome(values)

    assert result == ANSWERED
    assert reasons == ["ask type uncertain, using time", "gives time"]
    assert "states_time" in read


def test_uncertain_ask_type_fallback_can_still_end_unclear_on_the_reply_side():
    values = V(asks_time=UNSURE, states_time=UNSURE)
    result, reasons, read = outcome(values)

    assert result == UNCLEAR
    assert reasons == ["ask type uncertain, using time", "reply uncertain on time"]


# --- the decision: an uncertain sibling ask's reply branch is never read when -------
# --- another ask type is confident ---------------------------------------------------


def test_uncertain_sibling_ask_reply_branch_not_read_when_one_ask_is_confident():
    # asks_time is confident yes; asks_assessment is only uncertain, so it is never
    # added to `asked` (the confident-list is non-empty, so the uncertain fallback
    # never runs at all) and its reply sibling (states_position) is never read, even
    # though states_position itself is set to a confident yes here.
    values = V(asks_time=YES, asks_assessment=UNSURE, states_time=YES, states_position=YES)
    result, reasons, read = outcome(values)

    assert result == ANSWERED
    assert reasons == ["gives time"]
    # every asks_* is still read (the design reads all seven unconditionally)...
    assert "asks_assessment" in read
    # ...but its reply-side sibling is not, since "assessment" was never in `asked`.
    assert "states_position" not in read


def test_multiple_confident_ask_types_are_combined_with_or():
    values = V(asks_amount=YES, asks_time=YES, states_amount=NO, states_time=YES)
    result, reasons, read = outcome(values)

    assert result == ANSWERED
    assert reasons == ["gives time"]
    assert "states_amount" in read  # both asked types' reply nouls are checked
    assert "states_time" in read


# --- rule 3: answered, and rule 3's uncertain-reply unclear -------------------------


def test_confident_reply_is_answered():
    values = V(asks_action=YES, names_action=YES)
    result, reasons, read = outcome(values)

    assert result == ANSWERED
    assert reasons == ["gives action"]


def test_uncertain_reply_with_nothing_given_is_unclear():
    values = V(asks_why=YES, gives_reason=UNSURE)
    result, reasons, read = outcome(values)

    assert result == UNCLEAR
    assert reasons == ["reply uncertain on why"]


def test_uncertain_reply_on_one_asked_type_does_not_stop_a_confident_yes_on_another():
    values = V(asks_time=YES, asks_amount=YES, states_time=YES, states_amount=UNSURE)
    result, reasons, read = outcome(values)

    assert result == ANSWERED
    assert reasons == ["gives time"]
    assert "states_amount" in read  # still read, just doesn't demote the outcome


# --- the decision: an uncertain corrects_premise never demotes a confident answered ---


def test_uncertain_corrects_premise_does_not_demote_a_confident_answered():
    values = V(corrects_premise=UNSURE, asks_time=YES, states_time=YES)
    result, reasons, read = outcome(values)

    assert result == ANSWERED
    assert reasons == ["gives time"]
    assert "corrects_premise" in read  # read at rule 1, just never re-checked


def test_uncertain_corrects_premise_does_demote_once_nothing_was_given():
    values = V(corrects_premise=UNSURE, asks_time=YES, states_time=NO)
    result, reasons, read = outcome(values)

    assert result == UNCLEAR
    assert reasons == ["corrects_premise uncertain"]


# --- the decision: defers/points_elsewhere are read only once nothing was given ----


def test_defers_and_points_elsewhere_not_read_when_something_was_given():
    values = V(asks_time=YES, states_time=YES)
    _, _, read = outcome(values)

    assert "defers" not in read
    assert "points_elsewhere" not in read


def test_defers_yes_is_deferred():
    values = V(asks_time=YES, states_time=NO, defers=YES)
    result, reasons, read = outcome(values)

    assert result == DEFERRED
    assert reasons == ["defers yes"]


def test_points_elsewhere_yes_is_pointed_elsewhere_when_defers_is_no():
    values = V(asks_time=YES, states_time=NO, defers=NO, points_elsewhere=YES)
    result, reasons, read = outcome(values)

    assert result == POINTED_ELSEWHERE
    assert reasons == ["points_elsewhere yes"]


def test_defers_wins_over_points_elsewhere_when_both_are_yes():
    values = V(asks_time=YES, states_time=NO, defers=YES, points_elsewhere=YES)
    result, reasons, read = outcome(values)

    assert result == DEFERRED


def test_defers_uncertain_is_unclear():
    values = V(asks_time=YES, states_time=NO, defers=UNSURE, points_elsewhere=NO)
    result, reasons, read = outcome(values)

    assert result == UNCLEAR
    assert reasons == ["defers uncertain"]


def test_points_elsewhere_uncertain_is_unclear():
    values = V(asks_time=YES, states_time=NO, defers=NO, points_elsewhere=UNSURE)
    result, reasons, read = outcome(values)

    assert result == UNCLEAR
    assert reasons == ["points_elsewhere uncertain"]


# --- rule 8: not_answered, the final fallback ---------------------------------------


def test_not_answered_when_nothing_matched():
    values = V(asks_time=YES, states_time=NO, defers=NO, points_elsewhere=NO)
    result, reasons, read = outcome(values)

    assert result == NOT_ANSWERED
    assert reasons == ["nothing asked was given"]


# --- unread ids never affect the outcome: replace every one of them with an -------
# --- uncertain value and re-run; the decision (and read set) must not move --------

UNREAD_INDEPENDENCE_CASES = [
    V(corrects_premise=YES, asks_time=YES, states_time=NO),  # premise_corrected
    V(),  # unclear: no ask type recognised
    V(asks_time=UNSURE, states_time=YES),  # ask-type-uncertain fallback, answered
    V(asks_time=YES, states_time=YES),  # answered
    V(asks_time=YES, asks_assessment=UNSURE, states_time=YES, states_position=YES),  # answered, sibling ask ignored
    V(asks_time=YES, states_time=UNSURE),  # unclear: reply uncertain
    V(corrects_premise=UNSURE, asks_time=YES, states_time=NO),  # unclear: corrects_premise uncertain
    V(asks_time=YES, states_time=NO, defers=YES),  # deferred
    V(asks_time=YES, states_time=NO, defers=NO, points_elsewhere=YES),  # pointed_elsewhere
    V(asks_time=YES, states_time=NO, defers=NO, points_elsewhere=NO),  # not_answered
]


@pytest.mark.parametrize("values", UNREAD_INDEPENDENCE_CASES)
def test_setting_every_unread_id_to_uncertain_does_not_change_the_outcome(values):
    result, reasons, read = outcome(values)

    mutated = {qid: (v if qid in read else UNSURE) for qid, v in values.items()}
    result2, reasons2, read2 = outcome(mutated)

    assert (result2, reasons2, read2) == (result, reasons, read)
