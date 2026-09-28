"""Turns Jev's 18 noul values for one pair into an outcome, per
docs/questions-design.md section 4. The logic below is that section's code
verbatim (same order, same reasons, same thresholds), with one addition the
design's own listing does not need but run.py's output does: which ids the rules
actually read. That mirrors jev_turbine.judgments.Judgments, which the sibling
project uses the same way: reading tracks the id in `read`, in read order, each
id counted once even if the rule path reads it twice (`corrects_premise` is read
at rule 1 and, if the pair reaches rule 4, again).

The shadow `gives_what_is_asked` is never read here; it is written straight
through to the output by run.py.
"""

from __future__ import annotations

YES = 0.8
NO = 0.2

TYPES = ["amount", "time", "yes_or_no", "action", "why", "assessment", "facts"]
REPLY = {
    "amount": "states_amount",
    "time": "states_time",
    "yes_or_no": "says_yes_or_no",
    "action": "names_action",
    "why": "gives_reason",
    "assessment": "states_position",
    "facts": "gives_facts",
}

ANSWERED = "answered"
PREMISE_CORRECTED = "premise_corrected"
DEFERRED = "deferred"
POINTED_ELSEWHERE = "pointed_elsewhere"
NOT_ANSWERED = "not_answered"
UNCLEAR = "unclear"

# outcome() itself never returns this: a pair with an empty reply is never asked
# (see run.py) and so never reaches these rules at all. It lives here anyway,
# alongside the six real outcomes, because it is a value the "outcome" field in
# results.jsonl can hold, and both evaluate.py and rank.py need to know it without
# importing run.py (which itself imports evaluate.py and rank.py).
NO_REPLY = "no_reply"

OUTCOMES = (ANSWERED, PREMISE_CORRECTED, DEFERRED, POINTED_ELSEWHERE, NOT_ANSWERED, UNCLEAR)


def band(p: float) -> str:
    return "yes" if p >= YES else "no" if p <= NO else "uncertain"


class _Values:
    """Wraps the raw id -> noul value mapping so every `a[qid]` read (exactly the
    section-4 code's own access pattern) also records the id, once, in read order."""

    def __init__(self, raw: dict[str, float]):
        self._raw = raw
        self.read: list[str] = []

    def __getitem__(self, qid: str) -> float:
        if qid not in self.read:
            self.read.append(qid)
        return self._raw[qid]


def outcome(values: dict[str, float]) -> tuple[str, list[str], list[str]]:
    """values maps question id to its noul value (0..1) for the 17 nouls the rules
    can read (every id in questions/pair.yaml except the shadow
    gives_what_is_asked). Returns (outcome, reasons, read): read is the ids the
    rules actually looked at, in the order they were first read."""
    a = _Values(values)
    reasons: list[str] = []

    # 1. A confident correction wins outright.
    if band(a["corrects_premise"]) == "yes":
        return PREMISE_CORRECTED, ["corrects_premise yes"], a.read

    # 2. Which things does the question ask for? Confident asks first; if none,
    #    fall back to the uncertain ones so a hesitant question read does not
    #    end the pair by itself.
    asked = [t for t in TYPES if band(a[f"asks_{t}"]) == "yes"]
    if not asked:
        asked = [t for t in TYPES if band(a[f"asks_{t}"]) == "uncertain"]
        if asked:
            reasons.append("ask type uncertain, using " + ", ".join(asked))
    if not asked:
        return UNCLEAR, ["no ask type recognised"], a.read

    # 3. Answered if the reply gives any one of the things asked. This is an OR
    #    over the asked types: yes if any is yes, no only if all are no.
    given = [t for t in asked if band(a[REPLY[t]]) == "yes"]
    if given:
        return ANSWERED, reasons + ["gives " + ", ".join(given)], a.read
    unsure = [t for t in asked if band(a[REPLY[t]]) == "uncertain"]
    if unsure:
        return UNCLEAR, reasons + ["reply uncertain on " + ", ".join(unsure)], a.read

    # 4. Nothing asked was given. Now an uncertain correction matters.
    if band(a["corrects_premise"]) == "uncertain":
        return UNCLEAR, reasons + ["corrects_premise uncertain"], a.read

    # 5. The escape outcomes, deferred before pointed elsewhere.
    if band(a["defers"]) == "yes":
        return DEFERRED, reasons + ["defers yes"], a.read
    if band(a["points_elsewhere"]) == "yes":
        return POINTED_ELSEWHERE, reasons + ["points_elsewhere yes"], a.read
    if band(a["defers"]) == "uncertain":
        return UNCLEAR, reasons + ["defers uncertain"], a.read
    if band(a["points_elsewhere"]) == "uncertain":
        return UNCLEAR, reasons + ["points_elsewhere uncertain"], a.read

    return NOT_ANSWERED, reasons + ["nothing asked was given"], a.read
