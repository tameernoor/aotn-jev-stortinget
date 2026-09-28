"""The dodge tree: route every pair back to Jev, paragraph by paragraph.

    uv run python -m jev_stortinget tree [--from out/tree-all.json]

Three narrowing stages:

1. Ask (`ask_stage`, questions/asks.yaml): the seven asks_* nouls about the
   question text alone. Only pairs where one reads >= 0.8 enter the tree.
2. Route (`route_stage`): the reply is split into paragraphs (paragraphs(),
   capped at MAX_PARAGRAPHS) and, in one request per pair, every paragraph is
   asked whether it gives each asked type (plus a nodata check on every
   paragraph when amount or facts was asked).
3. Why not (`whynot_stage`): for a pair no paragraph answered, every
   paragraph is asked five why-not nouls in one request, then a second
   request asks a date/collect follow-up for whichever fired, or a
   pair-level swap question if nothing fired at all.

`verdict()` turns one pair's values into a five-way outcome, in priority
order: answered, a why-not reason, swapped, unsure, not_answered.
`reply_says_tags()` is a separate, non-exclusive question used only for
aggregate reporting: did a reason fire on *any* paragraph, regardless of
which one `verdict()` picked.

At most MAX_IN_FLIGHT requests in flight, each retried up to MAX_ATTEMPTS
times. A request that fails every retry is logged, one line per stage, and
never read as a confident "no": a lost follow-up request sets `follow_error`
on its row instead of leaving `follow` looking like an empty, all-no answer.

`--from FILE` skips Jev entirely and rebuilds results/tree/ from a previous
run's full output (`out/tree-all.json`, gitignored, not part of the
repository); stage 1's asked types are read back from the committed
`results/tree/asks-2024-2025.json`, never from a fresh classify run.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typesafe_sdk as ts
import yaml

from .fetch import DEFAULT_SESSION, Record, load_records
from .jev import PRICE_PER_MTOK_USD, AskFn, Jev, JevResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "out"
DEFAULT_RESULTS_DIR = REPO_ROOT / "results" / "tree"
DEFAULT_STAGE1_PATH = DEFAULT_RESULTS_DIR / "asks-2024-2025.json"
ASKS_QUESTIONS_PATH = REPO_ROOT / "questions" / "asks.yaml"

MAX_PARAGRAPHS = 12
MAX_IN_FLIGHT = 20
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 2.0
TIMEOUT_SECONDS = 60

YES = 0.8
NO = 0.2

ASKED_TYPES = ("amount", "time", "yes_or_no", "action", "why", "assessment", "facts")
NODATA_TRIGGERS = ("amount", "facts")  # asked types that also get a stage-2 nodata check
REASON_ORDER = ("nodata", "nocomment", "elsewhere", "later", "earlier")  # verdict() priority

ANSWERED = "answered"
SWAPPED = "swapped"
UNSURE = "unsure"
NOT_ANSWERED = "not_answered"

# The real run's own totals. The route stage's per-row `tokens` lets its cost
# be recomputed from any loaded file, but the why-not stage's per-request
# tokens were only ever kept in a running total during the real run, so its
# cost and both stages' wall time are kept here as constants instead.
ROUTE_STAGE_SECONDS = 96
WHYNOT_STAGE_SECONDS = 98
WHYNOT_STAGE_COST_USD = 0.385

# --- question templates -----------------------------------------------------

def _q(instructions: str, true: str, false: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": true, "false": false}}

def load_ask_questions(path: Path = ASKS_QUESTIONS_PATH) -> dict[str, dict]:
    """The seven asks_* questions from questions/asks.yaml, unchanged."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))

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

# --- paragraph split ---------------------------------------------------------

def paragraphs(reply: str) -> list[str]:
    """Non-empty lines, at most MAX_PARAGRAPHS: the 12th and every line after
    it merge into one final paragraph instead of being dropped."""
    ps = [line.strip() for line in reply.split("\n") if line.strip()]
    if len(ps) > MAX_PARAGRAPHS:
        return ps[: MAX_PARAGRAPHS - 1] + [" ".join(ps[MAX_PARAGRAPHS - 1 :])]
    return ps

def _paragraph_state(question: str, ps: Sequence[str]) -> dict:
    return {"question": question} | {f"p{i + 1}": p for i, p in enumerate(ps)}

# --- question building --------------------------------------------------------

def build_route_questions(asked: Sequence[str], n_paragraphs: int) -> dict[str, dict]:
    """One noul per paragraph per asked type, plus a nodata check on every
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
    """`collect` where `nodata` fired, `date` where `later` fired; empty if
    neither did (the caller adds `swap` in that case, pair-level not per-paragraph)."""
    follow: dict[str, dict] = {}
    for reason, (name, template) in FOLLOW_TEMPLATES.items():
        for i in range(n_paragraphs):
            if why_values.get(f"{reason}_p{i + 1}", 0.0) >= YES:
                follow[f"{name}_p{i + 1}"] = _paragraph_question(template, f"p{i + 1}")
    return follow

# --- stage 1's asked types -----------------------------------------------------

def _asked_types(values: dict[str, float]) -> list[str]:
    return [t for t in ASKED_TYPES if values.get(f"asks_{t}", 0.0) >= YES]

def load_stage1_asked(path: Path = DEFAULT_STAGE1_PATH) -> dict[int, list[str]]:
    """id -> asked types (asks_* >= 0.8), from the committed stage-1 values
    (id -> its seven asks_* values). A pair with none recognised never enters the tree."""
    data = json.loads(path.read_text(encoding="utf-8"))
    asked = {int(id_str): _asked_types(values) for id_str, values in data.items()}
    return {id_: types for id_, types in asked.items() if types}

# --- the async stages ----------------------------------------------------------

async def _ask_with_retries(
    ask: AskFn, state: dict, questions: dict[str, dict], semaphore: asyncio.Semaphore
) -> JevResult | None:
    """At most MAX_ATTEMPTS tries, MAX_IN_FLIGHT held via `semaphore` across
    the whole retry loop. None if every attempt failed."""
    async with semaphore:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await ask(state, questions)
            except Exception:
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_SLEEP_SECONDS)
    return None

async def _ask_stage_one(record: Record, questions: dict[str, dict], ask: AskFn, semaphore: asyncio.Semaphore):
    result = await _ask_with_retries(ask, {"question": record.question}, questions, semaphore)
    return record.id, (None if result is None else {k: v["value"] for k, v in result.judgments.items()})

async def ask_stage(records: Sequence[Record], ask: AskFn) -> dict[int, dict[str, float]]:
    """Stage 1: the seven asks_* questions, one request per pair, at most
    MAX_IN_FLIGHT in flight. Only the ids that answered are returned."""
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
    questions = load_ask_questions()
    pairs = await asyncio.gather(*(_ask_stage_one(r, questions, ask, semaphore) for r in records))
    failed = [id_ for id_, values in pairs if values is None]
    if failed:
        print(f"ask stage: {len(failed)} of {len(pairs)} pairs failed after every retry: {failed}")
    return {id_: values for id_, values in pairs if values is not None}

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
    """One request per pair in `asked_by_id`, at most MAX_IN_FLIGHT in
    flight. A row whose request failed after every retry comes back as
    `{"id": ..., "error": True}` rather than a confident no."""
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
    pool = [r for r in records if r.id in asked_by_id]
    rows = list(await asyncio.gather(*(_route_one(r, asked_by_id[r.id], ask, semaphore) for r in pool)))
    failed = [row["id"] for row in rows if row.get("error")]
    if failed:
        print(f"route stage: {len(failed)} of {len(rows)} pairs failed after every retry: {failed}")
    return rows

def route_hit(asked: Sequence[str], values: dict[str, float], n_paragraphs: int) -> int | None:
    """The 0-based index of the first paragraph where any asked type reads
    confidently yes, or None if none does. Only a None goes on to the why-not stage."""
    for i in range(n_paragraphs):
        if any(values.get(f"{t}_p{i + 1}", 0.0) >= YES for t in asked):
            return i
    return None

async def _whynot_one(row: dict, ask: AskFn, semaphore: asyncio.Semaphore) -> None:
    """Mutates `row` in place: adds `why` and `follow` (`follow` may be
    empty), or `error: True` if the why-not request itself failed. If the
    follow-up request failed instead (`date`, `collect` or `swap`, whichever
    `why` earned), `follow_error: True` is set and `follow` stays `{}`: a
    caller must read that as unknown, never as a confident no."""
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
    that need it: those with no route_hit() at all."""
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
    await asyncio.gather(*(_whynot_one(row, ask, semaphore) for row in rows))
    failed = [row["id"] for row in rows if row.get("error")]
    follow_failed = [row["id"] for row in rows if row.get("follow_error")]
    if failed or follow_failed:
        print(
            f"why-not stage: {len(failed)} of {len(rows)} pairs failed after every retry {failed}; "
            f"{len(follow_failed)} follow-up requests failed {follow_failed}"
        )

# --- verdict -------------------------------------------------------------------

def _fired(paragraph_values: dict[str, float], prefix: str, n_paragraphs: int) -> bool:
    return any(paragraph_values.get(f"{prefix}_p{i + 1}", 0.0) >= YES for i in range(n_paragraphs))

def _fired_index(paragraph_values: dict[str, float], prefix: str, n_paragraphs: int) -> int | None:
    for i in range(n_paragraphs):
        if paragraph_values.get(f"{prefix}_p{i + 1}", 0.0) >= YES:
            return i
    return None

def _verdict_result(
    kind: str,
    reasons: Sequence[str] = (),
    paragraph_index: int | None = None,
    date_given: bool | None = None,
    collect_promised: bool | None = None,
    swap_check_failed: bool = False,
) -> dict[str, Any]:
    return {
        "verdict": kind,
        "reasons": list(reasons),
        "paragraph_index": paragraph_index,
        "date_given": date_given,
        "collect_promised": collect_promised,
        "swap_check_failed": swap_check_failed,
    }

def verdict(
    asked: Sequence[str],
    values: dict[str, float],
    why: dict[str, float] | None,
    follow: dict[str, float] | None,
    n_paragraphs: int,
    follow_failed: bool = False,
) -> dict[str, Any]:
    """The five-way verdict, in priority order: (1) `answered` if any
    paragraph reads confidently yes on an asked type; (2) else the first of
    nodata, nocomment, elsewhere, later, earlier with any paragraph
    confidently yes in `why` (REASON_ORDER; `reasons` keeps every one that
    fired, not just the chosen one); (3) else `swapped` if `follow["swap"]`
    reads confidently yes; (4) else `unsure` if any asked-type value in
    `values` is strictly between NO and YES; (5) else `not_answered`.

    `date_given`/`collect_promised` are set only for the reason that won
    (else None). `follow_failed` (the row's own `follow_error`) means the
    follow-up request failed outright: at step 2 that keeps those two at
    None rather than a false "no"; at step 3 the follow-up asked only
    `swap`, so step 3 is skipped and `swap_check_failed` is True instead.
    """
    why = why or {}
    follow = follow or {}

    hit = route_hit(asked, values, n_paragraphs)
    if hit is not None:
        return _verdict_result(ANSWERED, paragraph_index=hit)

    fired_reasons = [r for r in REASON_ORDER if _fired(why, r, n_paragraphs)]
    if fired_reasons:
        chosen = fired_reasons[0]
        date_given = None if follow_failed or chosen != "later" else _fired(follow, "date", n_paragraphs)
        collect_promised = None if follow_failed or chosen != "nodata" else _fired(follow, "collect", n_paragraphs)
        return _verdict_result(
            chosen,
            reasons=fired_reasons,
            paragraph_index=_fired_index(why, chosen, n_paragraphs),
            date_given=date_given,
            collect_promised=collect_promised,
        )

    # Nothing in `why` fired, so the one follow-up asked was `swap`:
    # `follow_failed` here means specifically that it failed.
    if not follow_failed and follow.get("swap", 0.0) >= YES:
        return _verdict_result(SWAPPED)

    if any(NO < v < YES for k, v in values.items() if k.split("_p", 1)[0] in asked):
        return _verdict_result(UNSURE, swap_check_failed=follow_failed)

    return _verdict_result(NOT_ANSWERED, swap_check_failed=follow_failed)

# --- aggregation -----------------------------------------------------------------

REPLY_SAYS = REASON_ORDER  # the tags used for non-exclusive "did this fire anywhere" reporting

def reply_says_tags(row: dict) -> list[str]:
    """Every why-not reason that fired on any paragraph, regardless of which
    one verdict() picked."""
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
        "swap_check_failed": v["swap_check_failed"],
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
    """Per ministry: `n` (tree-eligible pairs), `no_hit` (how many had no
    paragraph give what was asked, the right denominator for the tags below,
    not `n`), and each reply-says tag fired anywhere (not mutually exclusive)."""
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

def _tagged_list(rows: Sequence[dict], reason: str, follow_prefix: str, flag_name: str) -> list[dict]:
    out = []
    for row in rows:
        n = len(row["paragraphs"])
        why = row.get("why") or {}
        idx = _fired_index(why, reason, n)
        if idx is None:
            continue
        follow = row.get("follow") or {}
        # A failed follow-up leaves `follow` empty; read as unknown, never a confident no.
        flag_value = None if row.get("follow_error") else _fired(follow, follow_prefix, n)
        out.append(
            {
                "id": row["id"],
                "ministry": row["ministry"],
                "question": row["question"],
                "paragraph_index": idx,
                "quote": row["paragraphs"][idx],  # the whole deciding paragraph, not an excerpt
                flag_name: flag_value,
            }
        )
    return out

def build_no_data(rows: Sequence[dict]) -> list[dict]:
    return _tagged_list(rows, "nodata", "collect", "collect_promised")

def build_promised_later(rows: Sequence[dict]) -> list[dict]:
    return _tagged_list(rows, "later", "date", "date_given")

# --- loading / running ------------------------------------------------------------

def load_tree_all(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))

class _LazyTreeJev:
    """Stands in for a real Jev() until the first request actually needs
    asking. A --from run never touches this at all."""

    def __init__(self) -> None:
        self._jev: Jev | None = None

    async def __call__(self, state: dict, questions: dict[str, dict]) -> JevResult:
        if self._jev is None:
            self._jev = Jev(ts.AsyncTypeSafeClient(timeout=TIMEOUT_SECONDS))
        return await self._jev.ask(state, questions)

    async def aclose(self) -> None:
        if self._jev is not None:
            await self._jev.aclose()

async def _run_fresh(ask: AskFn, session: str, raw_dir: Path | None, save_to: Path | None) -> list[dict]:
    records = load_records(session, raw_dir=raw_dir)
    ask_values = await ask_stage(records, ask)
    asked_by_id = {id_: types for id_, values in ask_values.items() if (types := _asked_types(values))}
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
    and reading stage 1's asked types back from `stage1_path`; otherwise a
    fresh run against `ask`, or a real Jev() built lazily), then write every
    results/tree/ output. Prints and returns the summary."""
    out_dir = out_dir if out_dir is not None else DEFAULT_OUT_DIR
    results_dir = results_dir if results_dir is not None else DEFAULT_RESULTS_DIR
    stage1_path = stage1_path if stage1_path is not None else DEFAULT_STAGE1_PATH

    if from_path is not None:
        rows = load_tree_all(from_path)
        asked_by_id = load_stage1_asked(stage1_path)
        for row in rows:
            row["asked"] = asked_by_id[row["id"]]
    else:
        lazy: _LazyTreeJev | None = None
        if ask is None:
            lazy = _LazyTreeJev()
            ask = lazy
        try:
            rows = await _run_fresh(ask, session, raw_dir, out_dir / "tree-all.json")
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
