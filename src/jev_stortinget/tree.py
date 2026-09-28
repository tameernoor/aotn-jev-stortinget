"""The dodge tree: route every pair back to Jev, paragraph by paragraph, instead
of asking one judgment about the whole reply.

    uv run python -m jev_stortinget tree [--from out/tree-all.json]

Three stages, each a separate, narrower question than the last:

1. Classify (already run, `out/all/results.jsonl`, the `asks_*` nouls) decides
   what the question asks for. Only pairs where at least one `asks_*` read
   >= 0.8 enter the tree at all.
2. Route (`route_stage`): the reply is split into paragraphs (`paragraphs()`,
   capped at MAX_PARAGRAPHS, the rest merged into the last one) and, in one
   request per pair, every paragraph is asked whether it gives each asked type,
   plus a `nodata` noul on every paragraph when amount or facts was asked.
3. Why not (`whynot_stage`): for a pair where no paragraph confidently gave any
   asked type, every paragraph is asked five why-not nouls (nodata, later,
   elsewhere, earlier, nocomment) in one request, then a second request asks
   `date` for a paragraph where `later` fired and `collect` for one where
   `nodata` fired, plus a pair-level `swap` question if nothing fired at all.

`verdict()` turns one pair's values into a five-way outcome: answered, a
why-not reason, swapped, unsure or not_answered, in that priority order, with
the deciding paragraph's index and the word count of every paragraph before
it, kept for `later` and `nodata` in particular (`date_given`,
`collect_promised`).

Aggregate reporting (by-minister, no-data, promised-later) uses a different,
non-exclusive question: for a given reason, did it fire on *any* paragraph of
this reply, regardless of which reason `verdict()` picked (nodata is checked
first, so it never differs there, but a reply can say both "later" and
"someone else's job", and both counts should see it). `reply_says_tags()` is
that check; it never consults `verdict()`.

Concurrency, retries and timeout: at most MAX_IN_FLIGHT requests in flight,
each request retried up to MAX_ATTEMPTS times two seconds apart,
`ts.AsyncTypeSafeClient(timeout=TIMEOUT_SECONDS)`. A request that still fails
after every retry never reads as a confident "no": `route_stage` and
`whynot_stage` each print the ids they lost at the end of the stage, and a
failed follow-up request sets `follow_error` on its row rather than leaving
`follow` looking like an empty, all-no answer.

Cost bookkeeping: the route stage's token count is on every row (`tokens`), so
its cost, question count and pair count are recomputed from whatever file is
loaded. The why-not stage's per-request tokens were only ever kept in an
in-memory running total in `whynot.py`, never written back onto a row, so they
cannot be recovered from `out/tree-all.json` after the fact; WHYNOT_STAGE_*
below are that stage's own real totals, kept as constants for the same reason
jev.py pins PRICE_PER_MTOK_USD: a fact about a specific real run, not something
every load of the file can derive. Wall time is never stored per row for either
stage, so both ROUTE_STAGE_SECONDS and WHYNOT_STAGE_SECONDS are constants too.

`--from FILE` skips Jev entirely and only rebuilds `results/tree/` from a
previous run's full output (see `load_tree_all`); the real run behind the
numbers in the README is `out/tree-all.json`, gitignored like the rest of
`out/`, so it is not part of the repository, only this machine's copy of it.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typesafe_sdk as ts

from .fetch import DEFAULT_SESSION, Record, load_records
from .jev import PRICE_PER_MTOK_USD, AskFn, Jev, JevResult
from .rules import TYPES as ASKED_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "out"
DEFAULT_STAGE1_RESULTS = DEFAULT_OUT_DIR / "all" / "results.jsonl"
DEFAULT_RESULTS_DIR = REPO_ROOT / "results" / "tree"

MAX_PARAGRAPHS = 12
MAX_IN_FLIGHT = 20
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 2.0
TIMEOUT_SECONDS = 60

YES = 0.8
NO = 0.2

NODATA_TRIGGERS = ("amount", "facts")  # asked types that also get a stage-2 nodata check
REASON_ORDER = ("nodata", "nocomment", "elsewhere", "later", "earlier")  # verdict() priority

ANSWERED = "answered"
SWAPPED = "swapped"
UNSURE = "unsure"
NOT_ANSWERED = "not_answered"

# The real run's own totals (see the module docstring): not recoverable from
# out/tree-all.json, so kept as constants rather than recomputed.
ROUTE_STAGE_SECONDS = 96
WHYNOT_STAGE_SECONDS = 98
WHYNOT_STAGE_COST_USD = 0.385


# --- question templates ---------------------------------------------------------

def _q(instructions: str, true: str, false: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}


# type -> (instructions with a "{p}" placeholder, true criterion, false criterion)
ASKED_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "amount": (
        "Does `{p}` state a number, amount or estimate for what `question` asks to be counted, measured or costed?",
        "The paragraph gives a figure for the very thing asked, for example a count, a sum in kroner, a share or "
        "an estimate ('om lag 550 millioner kroner', '327 dommer', '12 prosent').",
        "The paragraph gives no figure, or only figures about something else than what was asked (a budget total, "
        "a different year or a different group), or says figures are not available.",
    ),
    "time": (
        "Does `{p}` state a date, year, season or deadline for when the thing `question` asks about will happen or "
        "happened?",
        "A concrete time for the thing asked, for example 'i 2026', 'innen utgangen av året', 'i vårsesjonen', "
        "'fra 1. januar', 'før sommeren'.",
        "No time, or only a vague one ('så snart som mulig', 'på et senere tidspunkt', 'fortløpende'), or a time "
        "for something else than what was asked.",
    ),
    "yes_or_no": (
        "Does `{p}` say yes or no to what `question` asks, in words or plainly in substance?",
        "The paragraph confirms or rejects the thing asked, in words ('Ja', 'Nei', 'Det stemmer ikke') or plainly "
        "in substance ('regjeringen vil ikke foreslå', 'jeg er enig i at', 'det er ikke aktuelt').",
        "The paragraph neither confirms nor rejects the thing asked, for example it gives background, goals, "
        "rules or a process.",
    ),
    "action": (
        "Does `{p}` name a concrete measure that the minister or government will take or has taken on what "
        "`question` asks about?",
        "A specific step with an actor, for example 'jeg har bedt Helsedirektoratet om å', 'regjeringen foreslår "
        "50 millioner til', 'departementet sender forslaget på høring i høst'.",
        "Only intentions or values ('vi følger situasjonen nøye', 'jeg er opptatt av'), measures by others, or "
        "measures about something else.",
    ),
    "why": (
        "Does `{p}` give a cause or reason for the thing `question` asks why about?",
        "The paragraph explains why the thing asked happened or is the case ('fordi', 'årsaken er', 'dette skyldes').",
        "No explanation of the thing asked.",
    ),
    "assessment": (
        "Does `{p}` state the minister's own view or assessment on what `question` asks about?",
        "A plain position on the matter asked ('jeg mener at', 'jeg vurderer det slik at', 'det er min vurdering at').",
        "General values or goals not tied to the matter asked ('jeg er opptatt av at'), or a description without a "
        "position.",
    ),
    "facts": (
        "Does `{p}` give the facts, status or list that `question` asks for?",
        "The specific information asked for, such as a status, a list, names or what happened.",
        "Background, rules or goals instead of the information asked, or information about something else.",
    ),
}

# Asked (and paid for, its tokens are in the route stage's own cost) because
# amount/facts warrant checking every paragraph for it, but its value is never
# read below: the why-not stage's own "nodata" (WHY_TEMPLATES) is the one that
# actually decides the nodata reason and tag.
NODATA_STAGE2_TEMPLATE = (
    "Does `{p}` say that the figures or information asked for are not available, not registered, not collected, "
    "or cannot be produced?",
    "For example 'har ikke tall for', 'registreres ikke', 'finnes ikke statistikk over', 'kan ikke hentes ut', "
    "'har ikke oversikt over'.",
    "The paragraph does not say that information is missing.",
)

# reason -> (instructions with a "{p}" placeholder, true criterion, false criterion)
WHY_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "nodata": (
        "Does `{p}` say that figures or information asked for in `question` are not available, not registered, "
        "not collected or cannot be produced?",
        "For example 'har ikke tall for', 'registreres ikke', 'finnes ikke statistikk over', 'kan ikke hentes ut', "
        "'har ikke oversikt over', 'dei registrerer ikkje'.",
        "The paragraph does not say that information is missing.",
    ),
    "later": (
        "Does `{p}` say that the matter `question` asks about will be considered, assessed, decided or followed "
        "up later, or awaits a report, review or process?",
        "For example 'vil bli vurdert', 'vi kommer tilbake til', 'saken er til behandling', 'avventer utvalgets "
        "rapport', 'vil vurdere i forbindelse med budsjettet'.",
        "The paragraph does not say the matter will be dealt with later.",
    ),
    "elsewhere": (
        "Does `{p}` say that another body than the minister or ministry, such as a municipality, county, agency, "
        "health trust, company or the EU, is responsible for or decides the matter `question` asks about?",
        "For example 'det er kommunen som har ansvaret', 'dette er et ansvar for de regionale helseforetakene', "
        "'Statens vegvesen avgjør', 'det er opp til selskapet'.",
        "The paragraph does not place responsibility with another body, or only mentions a body as a source of "
        "information.",
    ),
    "earlier": (
        "Does `{p}` refer the reader to an earlier reply, a white paper, a budget proposal or another document "
        "instead of repeating its content?",
        "For example 'jeg viser til mitt svar på spørsmål nr. 1194', 'jeg viser til Prop. 1 S', 'som omtalt i "
        "Meld. St. 9'.",
        "No such reference, or a document is cited while its content is also given.",
    ),
    "nocomment": (
        "Does `{p}` say the minister cannot or will not comment on the matter, for example because it is an "
        "individual case, confidential, or before a court?",
        "For example 'jeg kan ikke kommentere enkeltsaker', 'underlagt taushetsplikt', 'saken er til behandling i "
        "domstolene'.",
        "The paragraph does not decline to comment.",
    ),
}

# reason -> (follow-up id, (instructions with "{p}", true, false))
FOLLOW_TEMPLATES: dict[str, tuple[str, tuple[str, str, str]]] = {
    "nodata": (
        "collect",
        (
            "Does `{p}` say that the missing figures or information will be collected, looked into or produced "
            "later?",
            "For example 'vi vil se på muligheten for å registrere', 'jeg har bedt direktoratet om å kartlegge'.",
            "No such promise.",
        ),
    ),
    "later": (
        "date",
        (
            "Does `{p}` give a date, year, season or named occasion for when the matter will be dealt with?",
            "For example 'i løpet av 2025', 'i statsbudsjettet for 2026', 'før sommeren', 'i vårsesjonen'.",
            "No time is given, or only 'på et senere tidspunkt' or 'fortløpende'.",
        ),
    ),
}

SWAP_QUESTION = _q(
    "Does `reply` give a number, a date or a yes or no about something other than what `question` asks for?",
    "For example the question asks for the number of patients in one region and the reply gives the national "
    "budget, or asks when and the reply says what has already been done.",
    "The reply gives no such figure, date or yes/no, or gives it for exactly what was asked.",
)


def _paragraph_question(template: tuple[str, str, str], p: str) -> dict:
    instructions, true, false = template
    return _q(instructions.format(p=p), true, false)


# --- paragraph split -----------------------------------------------------------------


def paragraphs(reply: str) -> list[str]:
    """Split a reply into non-empty lines, at most MAX_PARAGRAPHS: the 12th and
    every line after it merge into one final paragraph instead of being dropped."""
    ps = [line.strip() for line in reply.split("\n") if line.strip()]
    if len(ps) > MAX_PARAGRAPHS:
        return ps[: MAX_PARAGRAPHS - 1] + [" ".join(ps[MAX_PARAGRAPHS - 1 :])]
    return ps


def _paragraph_state(question: str, ps: Sequence[str]) -> dict:
    return {"question": question} | {f"p{i + 1}": p for i, p in enumerate(ps)}


# --- question building -----------------------------------------------------------


def build_route_questions(asked: Sequence[str], n_paragraphs: int) -> dict[str, dict]:
    """One noul per paragraph for each asked type, plus a nodata check on every
    paragraph when amount or facts was asked (see NODATA_TRIGGERS)."""
    wants_nodata = any(t in NODATA_TRIGGERS for t in asked)
    questions: dict[str, dict] = {}
    for i in range(n_paragraphs):
        p = f"p{i + 1}"
        for t in asked:
            questions[f"{t}_{p}"] = _paragraph_question(ASKED_TEMPLATES[t], p)
        if wants_nodata:
            questions[f"nodata_{p}"] = _paragraph_question(NODATA_STAGE2_TEMPLATE, p)
    return questions


def build_why_questions(n_paragraphs: int) -> dict[str, dict]:
    """All five why-not nouls, on every paragraph."""
    return {
        f"{reason}_p{i + 1}": _paragraph_question(WHY_TEMPLATES[reason], f"p{i + 1}")
        for reason in REASON_ORDER
        for i in range(n_paragraphs)
    }


def build_follow_questions(why_values: dict[str, float], n_paragraphs: int) -> dict[str, dict]:
    """`collect` for a paragraph where `nodata` fired, `date` for one where
    `later` fired; empty if neither did (the caller adds `swap` in that case,
    since that is pair-level, not per-paragraph)."""
    follow: dict[str, dict] = {}
    for reason, (name, template) in FOLLOW_TEMPLATES.items():
        for i in range(n_paragraphs):
            if why_values.get(f"{reason}_p{i + 1}", 0.0) >= YES:
                follow[f"{name}_p{i + 1}"] = _paragraph_question(template, f"p{i + 1}")
    return follow


# --- stage 1's asked types, for pairs already decided ----------------------------


def load_stage1_asked(path: Path = DEFAULT_STAGE1_RESULTS) -> dict[int, list[str]]:
    """id -> the asked types (asks_* >= 0.8) from the classify stage's own
    results.jsonl. A pair with no asked type recognised at all never enters
    the tree."""
    asked: dict[int, list[str]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            values = row.get("values") or {}
            types = [t for t in ASKED_TYPES if values.get(f"asks_{t}", 0.0) >= YES]
            if types:
                asked[row["id"]] = types
    return asked


# --- the two async stages ---------------------------------------------------------


async def _ask_with_retries(
    ask: AskFn, state: dict, questions: dict[str, dict], semaphore: asyncio.Semaphore
) -> JevResult | None:
    """At most MAX_ATTEMPTS tries for one request, MAX_IN_FLIGHT held via
    `semaphore` across the whole retry loop. None if every attempt failed."""
    async with semaphore:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await ask(state, questions)
            except Exception:
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_SLEEP_SECONDS)
    return None


async def _route_one(record: Record, asked: list[str], ask: AskFn, semaphore: asyncio.Semaphore) -> dict:
    ps = paragraphs(record.reply)
    state = _paragraph_state(record.question, ps)
    questions = build_route_questions(asked, len(ps))
    result = await _ask_with_retries(ask, state, questions, semaphore)
    if result is None:
        return {"id": record.id, "error": True}
    return {
        "id": record.id,
        "ministry": record.answered_by,
        "mp": record.mp_name,
        "party": record.mp_party,
        "question": record.question,
        "asked": asked,
        "paragraphs": ps,
        "values": {k: v["value"] for k, v in result.judgments.items()},
        "tokens": result.meta["input_tokens"],
    }


async def route_stage(records: Sequence[Record], asked_by_id: dict[int, list[str]], ask: AskFn) -> list[dict]:
    """One request per pair in `asked_by_id` (pairs with no recognised asked type
    never enter the tree), at most MAX_IN_FLIGHT in flight. Prints the ids of any
    pair whose request failed after every retry (see `_ask_with_retries`); the
    caller still gets those rows back, each with `error: True` and nothing else,
    so it can decide whether to drop them."""
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
    pool = [r for r in records if r.id in asked_by_id]
    rows = list(await asyncio.gather(*(_route_one(r, asked_by_id[r.id], ask, semaphore) for r in pool)))
    failed = [row["id"] for row in rows if row.get("error")]
    if failed:
        print(f"route stage: {len(failed)} of {len(rows)} pairs failed after every retry: {failed}")
    return rows


def route_hit(asked: Sequence[str], values: dict[str, float], n_paragraphs: int) -> int | None:
    """The 0-based index of the first paragraph where any asked type reads
    confidently yes (rule 1 of verdict()), or None if none does. This is what
    selects a row for the why-not stage: only a None here goes on to it."""
    for i in range(n_paragraphs):
        if any(values.get(f"{t}_p{i + 1}", 0.0) >= YES for t in asked):
            return i
    return None


async def _whynot_one(row: dict, ask: AskFn, semaphore: asyncio.Semaphore) -> None:
    """Mutates `row` in place: adds `why` and `follow` (`follow` may be empty),
    or `error: True` if the why-not request itself failed after every retry.

    If the *follow-up* request failed instead (it asks `date`, `collect` or
    `swap`, whichever of those `why` actually earned), `follow_error: True` is
    set and `follow` stays `{}`. Callers must treat that `{}` as unknown, not
    as a confident no on `date`, `collect` or `swap`: `verdict()` and the
    no-data/promised-later builders both check `follow_error` before reading
    `follow` for exactly this reason."""
    ps = row["paragraphs"]
    n = len(ps)
    state = _paragraph_state(row["question"], ps)

    why_result = await _ask_with_retries(ask, state, build_why_questions(n), semaphore)
    if why_result is None:
        row["error"] = True
        return
    why_values = {k: v["value"] for k, v in why_result.judgments.items()}

    follow_questions = build_follow_questions(why_values, n)
    follow_state = state
    if not any(v >= YES for v in why_values.values()):
        follow_state = {**state, "reply": "\n".join(ps)}
        follow_questions = {**follow_questions, "swap": SWAP_QUESTION}

    follow_values: dict[str, float] = {}
    if follow_questions:
        follow_result = await _ask_with_retries(ask, follow_state, follow_questions, semaphore)
        if follow_result is not None:
            follow_values = {k: v["value"] for k, v in follow_result.judgments.items()}
        else:
            row["follow_error"] = True

    row["why"] = why_values
    row["follow"] = follow_values


async def whynot_stage(rows: Sequence[dict], ask: AskFn) -> None:
    """Mutates every row in `rows` in place. The caller passes only the rows
    that need it: those with no route_hit() at all (see route_stage). Prints
    the ids of any pair whose why-not request failed outright, and separately
    the ids of any pair whose follow-up request failed (see `_whynot_one`)."""
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
    await asyncio.gather(*(_whynot_one(row, ask, semaphore) for row in rows))
    failed = [row["id"] for row in rows if row.get("error")]
    if failed:
        print(f"why-not stage: {len(failed)} of {len(rows)} pairs failed after every retry: {failed}")
    follow_failed = [row["id"] for row in rows if row.get("follow_error")]
    if follow_failed:
        print(f"why-not stage: {len(follow_failed)} pairs' follow-up request failed after every retry: {follow_failed}")


# --- verdict -----------------------------------------------------------------------


def _fired(paragraph_values: dict[str, float], prefix: str, n_paragraphs: int) -> bool:
    return any(paragraph_values.get(f"{prefix}_p{i + 1}", 0.0) >= YES for i in range(n_paragraphs))


def _fired_index(paragraph_values: dict[str, float], prefix: str, n_paragraphs: int) -> int | None:
    for i in range(n_paragraphs):
        if paragraph_values.get(f"{prefix}_p{i + 1}", 0.0) >= YES:
            return i
    return None


def verdict(
    asked: Sequence[str],
    values: dict[str, float],
    why: dict[str, float] | None,
    follow: dict[str, float] | None,
    n_paragraphs: int,
    follow_failed: bool = False,
) -> dict[str, Any]:
    """The five-way verdict, in this exact priority order:

    1. `answered` if any paragraph reads confidently yes on an asked type.
    2. Otherwise the first of nodata, nocomment, elsewhere, later, earlier with
       any paragraph confidently yes in `why` (REASON_ORDER); `reasons` keeps
       every one of the five that fired, not just the chosen one.
    3. Otherwise `swapped` if `follow["swap"]` reads confidently yes.
    4. Otherwise `unsure` if any asked-type value in `values` is strictly
       between NO and YES.
    5. Otherwise `not_answered`.

    Returns a dict with `verdict`, `reasons` (only non-empty in case 2),
    `paragraph_index` (the deciding paragraph for case 1 and 2, else None) and,
    only for the reason that actually won, `date_given` (verdict == "later")
    or `collect_promised` (verdict == "nodata"); the other of the two is always
    None, and both are None for every other verdict.

    `follow_failed` (from a row's own `follow_error`, see `_whynot_one`) means
    the follow-up request that would have carried `date`/`collect` never came
    back: `date_given`/`collect_promised` are then None (unknown), never False,
    since an empty `follow` from a failed request must not read as "no".
    """
    why = why or {}
    follow = follow or {}

    hit = route_hit(asked, values, n_paragraphs)
    if hit is not None:
        return {
            "verdict": ANSWERED,
            "reasons": [],
            "paragraph_index": hit,
            "date_given": None,
            "collect_promised": None,
        }

    fired_reasons = [r for r in REASON_ORDER if _fired(why, r, n_paragraphs)]
    if fired_reasons:
        chosen = fired_reasons[0]
        date_given = None if follow_failed or chosen != "later" else _fired(follow, "date", n_paragraphs)
        collect_promised = None if follow_failed or chosen != "nodata" else _fired(follow, "collect", n_paragraphs)
        return {
            "verdict": chosen,
            "reasons": fired_reasons,
            "paragraph_index": _fired_index(why, chosen, n_paragraphs),
            "date_given": date_given,
            "collect_promised": collect_promised,
        }

    if follow.get("swap", 0.0) >= YES:
        return {"verdict": SWAPPED, "reasons": [], "paragraph_index": None, "date_given": None, "collect_promised": None}

    if any(NO < v < YES for k, v in values.items() if k.split("_p", 1)[0] in asked):
        return {"verdict": UNSURE, "reasons": [], "paragraph_index": None, "date_given": None, "collect_promised": None}

    return {
        "verdict": NOT_ANSWERED,
        "reasons": [],
        "paragraph_index": None,
        "date_given": None,
        "collect_promised": None,
    }


# --- aggregation ---------------------------------------------------------------------

REPLY_SAYS = REASON_ORDER  # the tags used for non-exclusive "did this fire anywhere" reporting


def reply_says_tags(row: dict) -> list[str]:
    """Every why-not reason that fired on any paragraph of this reply,
    regardless of which one verdict() picked (nodata always wins there when it
    fires, since it is first in REASON_ORDER, but a reply can say both "later"
    and "someone else's job", and both should count)."""
    why = row.get("why") or {}
    n = len(row["paragraphs"])
    return [r for r in REPLY_SAYS if _fired(why, r, n)]


def build_pair_record(row: dict) -> dict:
    """The compact per-pair record for tree-2024-2025.json: ids, paragraph
    indices and values, never the reply text itself."""
    ps = row["paragraphs"]
    n = len(ps)
    v = verdict(row["asked"], row["values"], row.get("why"), row.get("follow"), n, bool(row.get("follow_error")))
    word_count_before = None
    if v["paragraph_index"] is not None:
        word_count_before = sum(len(p.split()) for p in ps[: v["paragraph_index"]])
    return {
        "id": row["id"],
        "asked": row["asked"],
        "n_paragraphs": n,
        "values": row["values"],
        "why": row.get("why") or {},
        "follow": row.get("follow") or {},
        "verdict": v["verdict"],
        "reasons": v["reasons"],
        "paragraph_index": v["paragraph_index"],
        "word_count_before": word_count_before,
        "date_given": v["date_given"],
        "collect_promised": v["collect_promised"],
    }


def build_summary(rows: Sequence[dict], pair_records: Sequence[dict]) -> dict:
    answered = sum(1 for r in pair_records if r["verdict"] == ANSWERED)
    route_tokens = sum(row["tokens"] for row in rows)
    route_cost = route_tokens * PRICE_PER_MTOK_USD / 1_000_000
    whynot_pairs = sum(1 for row in rows if "why" in row)
    whynot_requests = whynot_pairs + sum(1 for row in rows if row.get("follow"))
    return {
        "n_pairs": len(rows),
        "answered": answered,
        "no_hit": len(rows) - answered,  # went on to the why-not stage
        "reply_says": {tag: sum(1 for row in rows if tag in reply_says_tags(row)) for tag in REPLY_SAYS},
        "cost": {
            "route_stage": {
                "pairs": len(rows),
                "paragraph_questions": sum(len(row["values"]) for row in rows),
                "input_tokens": route_tokens,
                "cost_usd": round(route_cost, 4),
                "wall_seconds": ROUTE_STAGE_SECONDS,
            },
            "whynot_stage": {
                "pairs": whynot_pairs,
                "requests": whynot_requests,
                "cost_usd": WHYNOT_STAGE_COST_USD,
                "wall_seconds": WHYNOT_STAGE_SECONDS,
            },
            "total_usd": round(route_cost + WHYNOT_STAGE_COST_USD, 4),
        },
    }


def build_by_minister(rows: Sequence[dict]) -> dict:
    """Per ministry: how many tree-eligible pairs it has (`n`), how many of
    those had no paragraph give what was asked (`no_hit`), and how many carry
    each reply-says tag fired anywhere. The tags are only ever looked for
    among `no_hit`'s pairs (see reply_says_tags()), not all of `n`, so `n` is
    the wrong denominator for them; `no_hit` is the right one, and the tag
    counts do not have to add up to it either, since a pair can carry more
    than one tag."""
    ministries: dict[str, dict] = {}
    for row in rows:
        entry = ministries.setdefault(row["ministry"], {"n": 0, "no_hit": 0, **{tag: 0 for tag in REPLY_SAYS}})
        entry["n"] += 1
        if "why" in row:
            entry["no_hit"] += 1
        for tag in reply_says_tags(row):
            entry[tag] += 1
    return {"ministries": ministries}


def format_by_minister_table(by_minister: dict, min_n: int = 50) -> str:
    rows = {m: d for m, d in by_minister["ministries"].items() if d["n"] >= min_n}
    header = f"{'ministry':<45} {'n':>5} {'no_hit':>7} " + " ".join(f"{tag:>10}" for tag in REPLY_SAYS)
    lines = [header, "-" * len(header)]
    for ministry in sorted(rows):
        d = rows[ministry]
        lines.append(
            f"{ministry:<45} {d['n']:>5} {d['no_hit']:>7} " + " ".join(f"{d[tag]:>10}" for tag in REPLY_SAYS)
        )
    return "\n".join(lines)


QUOTE_MAX_CHARS = 300

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# The same example phrases already quoted in WHY_TEMPLATES's own criteria,
# reused here to find the sentence that actually carries the reason instead of
# quoting the whole paragraph in no-data.json/promised-later.json.
NODATA_SIGNAL_PHRASES = (
    "har ikke tall for",
    "registreres ikke",
    "finnes ikke statistikk over",
    "kan ikke hentes ut",
    "har ikke oversikt over",
    "dei registrerer ikkje",
)
LATER_SIGNAL_PHRASES = (
    "vil bli vurdert",
    "vi kommer tilbake til",
    "saken er til behandling",
    "avventer utvalgets rapport",
    "vil vurdere i forbindelse med budsjettet",
)


def _trim_quote(paragraph: str, signal_phrases: Sequence[str]) -> str:
    """The first sentence containing one of `signal_phrases` (a case-insensitive
    substring match), else the first QUOTE_MAX_CHARS characters with a trailing
    ellipsis. A paragraph can run to several hundred words; the list files only
    need enough of it to show the reason, not the whole reply."""
    for sentence in _SENTENCE_SPLIT_RE.split(paragraph.strip()):
        lowered = sentence.lower()
        if any(phrase in lowered for phrase in signal_phrases):
            return sentence
    if len(paragraph) <= QUOTE_MAX_CHARS:
        return paragraph
    return paragraph[:QUOTE_MAX_CHARS].rstrip() + "…"


def _tagged_list(
    rows: Sequence[dict], reason: str, follow_prefix: str, flag_name: str, signal_phrases: Sequence[str]
) -> list[dict]:
    out = []
    for row in rows:
        n = len(row["paragraphs"])
        why = row.get("why") or {}
        idx = _fired_index(why, reason, n)
        if idx is None:
            continue
        follow = row.get("follow") or {}
        # A failed follow-up (see _whynot_one) leaves `follow` empty; that must
        # read as unknown here too, never as a confident no.
        flag_value = None if row.get("follow_error") else _fired(follow, follow_prefix, n)
        out.append(
            {
                "id": row["id"],
                "ministry": row["ministry"],
                "question": row["question"],
                "paragraph_index": idx,
                "quote": _trim_quote(row["paragraphs"][idx], signal_phrases),
                flag_name: flag_value,
            }
        )
    return out


def build_no_data(rows: Sequence[dict]) -> list[dict]:
    return _tagged_list(rows, "nodata", "collect", "collect_promised", NODATA_SIGNAL_PHRASES)


def build_promised_later(rows: Sequence[dict]) -> list[dict]:
    return _tagged_list(rows, "later", "date", "date_given", LATER_SIGNAL_PHRASES)


# --- loading / running --------------------------------------------------------------


def load_tree_all(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


class _LazyTreeJev:
    """Stands in for a real Jev() until the first request actually needs
    asking, built with the tree's own TIMEOUT_SECONDS rather than the SDK
    default. A --from run never touches this at all."""

    def __init__(self) -> None:
        self._jev: Jev | None = None

    async def __call__(self, state: dict, questions: dict[str, dict]) -> JevResult:
        if self._jev is None:
            self._jev = Jev(ts.AsyncTypeSafeClient(timeout=TIMEOUT_SECONDS))
        return await self._jev.ask(state, questions)

    async def aclose(self) -> None:
        if self._jev is not None:
            await self._jev.aclose()


async def _run_fresh(
    ask: AskFn,
    session: str,
    raw_dir: Path | None,
    stage1_path: Path,
    save_to: Path | None,
) -> list[dict]:
    records = load_records(session, raw_dir=raw_dir)
    asked_by_id = load_stage1_asked(stage1_path)
    rows = await route_stage(records, asked_by_id, ask)
    rows = [row for row in rows if "error" not in row]
    todo = [row for row in rows if route_hit(row["asked"], row["values"], len(row["paragraphs"])) is None]
    await whynot_stage(todo, ask)
    if save_to is not None:
        save_to.parent.mkdir(parents=True, exist_ok=True)
        save_to.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _format_summary(summary: dict) -> str:
    cost = summary["cost"]
    lines = [
        f"{summary['n_pairs']} pairs: {summary['answered']} answered, {summary['no_hit']} went to the why-not stage.",
        "Reply-says tags (a reply can carry more than one): "
        + ", ".join(f"{tag} {n}" for tag, n in summary["reply_says"].items()),
        f"Route stage: {cost['route_stage']['pairs']} pairs, {cost['route_stage']['paragraph_questions']} "
        f"paragraph questions, ${cost['route_stage']['cost_usd']:.4f}, {cost['route_stage']['wall_seconds']}s.",
        f"Why-not stage: {cost['whynot_stage']['pairs']} pairs, {cost['whynot_stage']['requests']} requests, "
        f"${cost['whynot_stage']['cost_usd']:.4f}, {cost['whynot_stage']['wall_seconds']}s.",
        f"Total: ${cost['total_usd']:.4f}",
    ]
    return "\n".join(lines)


async def tree(
    from_path: Path | None = None,
    out_dir: Path | None = None,
    results_dir: Path | None = None,
    ask: AskFn | None = None,
    session: str = DEFAULT_SESSION,
    raw_dir: Path | None = None,
    stage1_path: Path | None = None,
) -> dict:
    """Build the dodge tree's rows (from `from_path` if given, skipping Jev
    entirely; otherwise a fresh run against `ask`, or a real Jev() built lazily
    with TIMEOUT_SECONDS), then write every results/tree/ output. Prints and
    returns the summary."""
    out_dir = out_dir if out_dir is not None else DEFAULT_OUT_DIR
    results_dir = results_dir if results_dir is not None else DEFAULT_RESULTS_DIR
    stage1_path = stage1_path if stage1_path is not None else DEFAULT_STAGE1_RESULTS

    if from_path is not None:
        rows = load_tree_all(from_path)
    else:
        lazy: _LazyTreeJev | None = None
        if ask is None:
            lazy = _LazyTreeJev()
            ask = lazy
        try:
            rows = await _run_fresh(ask, session, raw_dir, stage1_path, out_dir / "tree-all.json")
        finally:
            if lazy is not None:
                await lazy.aclose()

    pair_records = [build_pair_record(row) for row in rows]
    summary = build_summary(rows, pair_records)
    by_minister = build_by_minister(rows)

    results_dir.mkdir(parents=True, exist_ok=True)
    _write_json(results_dir / "tree-summary.json", summary)
    _write_json(results_dir / "by-minister.json", by_minister)
    _write_json(results_dir / "no-data.json", build_no_data(rows))
    _write_json(results_dir / "promised-later.json", build_promised_later(rows))
    _write_json(results_dir / "tree-2024-2025.json", pair_records)

    print(format_by_minister_table(by_minister))
    print(_format_summary(summary))
    return summary
