"""Ask Jev about every pair in a set and decide the outcome in code.

    uv run --env-file .env python -m jev_stortinget run --set dev|holdout|all [--out out/] [--cache FILE]

`--set dev` and `--set holdout` select records by the ids in data/sets/dev.json and
data/sets/holdout.json (see sets.py); `--set all` uses every cached pair (see
fetch.load_records), dev and holdout included. A pair with an empty reply is kept in
the output and flagged NO_REPLY; it is never sent to Jev (data/sets/*.json never
holds one, so this only ever happens under --set all).

One request per pair, with all 18 questions from questions/pair.yaml, at most
MAX_IN_FLIGHT in flight at once. out_dir/judgments.json caches the raw judgments per
pair id (as Jev returned them) together with a sha256 of questions/pair.yaml; on a
hash mismatch the cache is ignored and the run says so, both on stdout and in
summary.json, rather than silently serving answers to questions that have since
changed wording. The cache is shared across sets (out_dir/judgments.json, not
out_dir/<set>/judgments.json), so a pair asked once under --set dev is not asked
again under --set all.

The cache is saved in a finally, right after every in-flight request has settled
(asyncio.gather(..., return_exceptions=True)), so a run that fails partway still
keeps every answer it already had before re-raising the first error; nothing past
that point (results.jsonl, summary.json) is written for a failed run.

`--cache FILE` seeds the run from an existing judgments cache instead of
out_dir/judgments.json, without ever writing back to FILE itself; the merged result
is still written to out_dir/judgments.json as usual.

rules.outcome() is never called for a NO_REPLY pair, and rules.py's rules only ever
read 17 of the 18 ids; the 18th, the shadow `gives_what_is_asked`, is written to the
output (both as its own `shadow` field and inside `values`) but never fed to the
rules.

If data/sets/<set_name>-labels.json exists (see evaluate.py), out_dir/<set_name>/
evaluation.json is also written; if not (dev and all normally have no labels file),
evaluation is skipped, not an error. `--set all` additionally writes
out/all/ranking.json and prints its table (see rank.py).

Needs TYPESAFE_API_KEY in the environment for a real run, and only once a question is
actually asked: the real Jev() client is built lazily, on the first cache miss, so a
run whose cache already covers every pair needs no key and makes no network call at
all. Tests pass a fake `ask` straight to `run()`/`main()` instead, so the test suite
never needs a key and never calls TypeSafe.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import yaml

from .evaluate import evaluate, load_labels
from .fetch import DEFAULT_SESSION, Record, load_records
from .jev import AskFn, Jev, JevResult
from .rank import format_table, rank_by_ministry
from .rules import outcome
from .sets import SETS_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_PATH = REPO_ROOT / "questions" / "pair.yaml"
DEFAULT_OUT_DIR = REPO_ROOT / "out"

SET_NAMES = ("dev", "holdout", "all")
MAX_IN_FLIGHT = 20

NO_REPLY = "no_reply"
SHADOW_ID = "gives_what_is_asked"


class RunError(RuntimeError):
    """An unusable --set name, or a set file naming an id with no cached pair."""


# cache[str(record.id)] holds the raw judgments dict Jev returned for that pair (the
# same shape as JevResult.judgments): qid -> {"type": "noul", "value": <float>}, one
# entry per pair id ever asked. Pass a dict in (even {}) to have it filled in place.
Cache = dict[str, dict[str, dict]]


def load_questions(path: Path = QUESTIONS_PATH) -> dict[str, dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _questions_hash(path: Path = QUESTIONS_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_cache(path: Path) -> tuple[Cache, bool]:
    """Returns (cache, ignored). `ignored` is True only when `path` existed but its
    stored questions_hash did not match questions/pair.yaml's current hash, so its
    answers were not trusted or used. A `path` that does not exist at all is just an
    empty starting cache, not an ignored one."""
    if not path.exists():
        return {}, False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("questions_hash") != _questions_hash():
        return {}, True
    return payload.get("cache", {}), False


def _save_cache(path: Path, cache: Cache) -> None:
    payload = {"questions_hash": _questions_hash(), "cache": cache}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _set_ids(set_name: str, sets_dir: Path) -> list[int]:
    path = sets_dir / f"{set_name}.json"
    if not path.exists():
        raise RunError(f"{path} does not exist; run `python -m jev_stortinget sets` first")
    items = json.loads(path.read_text(encoding="utf-8"))
    return [item["id"] for item in items]


def select_records(
    set_name: str, session: str = DEFAULT_SESSION, raw_dir: Path | None = None, sets_dir: Path | None = None
) -> list[Record]:
    """Every record in `set_name`, in the id order of data/sets/<set_name>.json for
    dev/holdout, or every cached record (sorted by id, dev and holdout included) for
    all."""
    if set_name not in SET_NAMES:
        raise RunError(f"--set must be one of {SET_NAMES}, got {set_name!r}")
    records = load_records(session, raw_dir=raw_dir)
    if set_name == "all":
        return records
    ids = _set_ids(set_name, sets_dir if sets_dir is not None else SETS_DIR)
    by_id = {r.id: r for r in records}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise RunError(f"{set_name} ids not found among cached pairs (fetch first): {missing}")
    return [by_id[i] for i in ids]


class _UsageTracker:
    """Wraps an AskFn and records every JevResult.meta, so the summary can report
    calls, tokens, cost and the model ids seen, without changing the ask signature."""

    def __init__(self, ask: AskFn):
        self._ask = ask
        self.calls: list[dict] = []

    async def __call__(self, state: dict, questions: dict[str, dict]) -> JevResult:
        result = await self._ask(state, questions)
        self.calls.append(result.meta)
        return result


class _LazyJev:
    """Stands in for a real Jev() until the first pair actually needs asking. A run
    whose cache already covers every pair never calls this, so it never builds a
    client and never needs TYPESAFE_API_KEY."""

    def __init__(self):
        self._jev: Jev | None = None

    async def __call__(self, state: dict, questions: dict[str, dict]) -> JevResult:
        if self._jev is None:
            self._jev = Jev()
        return await self._jev.ask(state, questions)

    async def aclose(self) -> None:
        if self._jev is not None:
            await self._jev.aclose()


async def _ask_one(
    record: Record, ask: AskFn, questions: dict[str, dict], cache: Cache, semaphore: asyncio.Semaphore
) -> None:
    key = str(record.id)
    if key in cache:
        return
    async with semaphore:
        state = {"question": record.question, "reply": record.reply}
        result = await ask(state, questions)
    cache[key] = result.judgments


async def _ask_all(records: Sequence[Record], ask: AskFn, questions: dict[str, dict], cache: Cache) -> None:
    """Ask every record not already in `cache`, at most MAX_IN_FLIGHT in flight.
    Waits for every in-flight request to settle (gather with return_exceptions=True)
    before re-raising the first error, so a caller that saves `cache` in a finally
    around this call keeps every answer that did come back."""
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
    tasks = [_ask_one(record, ask, questions, cache, semaphore) for record in records]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


def _flat_values(raw: dict[str, dict]) -> dict[str, float]:
    return {qid: judgment["value"] for qid, judgment in raw.items()}


def _result_row(record: Record, raw: dict[str, dict] | None) -> dict:
    base = {
        "id": record.id,
        "number": record.number,
        "ministry": record.ministry,
        "answered_by": record.answered_by,
        "mp": record.mp_name,
        "party": record.mp_party,
        "asked_date": record.asked_date,
        "answered_date": record.answered_date,
    }
    if raw is None:
        return {
            **base,
            "outcome": NO_REPLY,
            "reasons": ["empty reply, never sent to Jev"],
            "read": [],
            "shadow": None,
            "values": {},
        }
    values = _flat_values(raw)
    result, reasons, read = outcome(values)
    return {
        **base,
        "outcome": result,
        "reasons": reasons,
        "read": read,
        "shadow": values.get(SHADOW_ID),
        "values": values,
    }


def _write_results_jsonl(path: Path, rows: Sequence[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _summarise(rows: Sequence[dict], usage_calls: list[dict], wall_seconds: float, cache_ignored: bool) -> dict:
    counts = Counter(row["outcome"] for row in rows)
    model_ids = sorted({c["model"] for c in usage_calls if c.get("model")})
    return {
        "counts": dict(counts),
        "calls": len(usage_calls),
        "input_tokens": sum(c["input_tokens"] for c in usage_calls),
        "cost_usd": sum(c["cost_usd"] for c in usage_calls),
        "wall_seconds": wall_seconds,
        "model_ids": model_ids,
        "cache_ignored": cache_ignored,
    }


def _format_summary(set_name: str, summary: dict) -> str:
    lines = []
    if summary["cache_ignored"]:
        lines.append(
            "Cache ignored: questions/pair.yaml does not match the hash stored with "
            "the cache, so every pair was asked fresh."
        )
    lines.append(f"Set: {set_name}")
    lines.append("Outcome counts:")
    for name, count in sorted(summary["counts"].items()):
        lines.append(f"  {name}: {count}")
    lines.append(
        f"Jev calls: {summary['calls']}, input tokens: {summary['input_tokens']}, "
        f"cost: ${summary['cost_usd']:.6f}, wall time: {summary['wall_seconds']:.2f}s"
    )
    if summary["model_ids"]:
        lines.append(f"Model(s): {', '.join(summary['model_ids'])}")
    return "\n".join(lines)


async def run(
    set_name: str,
    out_dir: Path | None = None,
    ask: AskFn | None = None,
    seed_cache: Path | None = None,
    session: str = DEFAULT_SESSION,
    raw_dir: Path | None = None,
    sets_dir: Path | None = None,
) -> dict:
    """Run the full pipeline once for one set: select its records, ask Jev for
    every pair not already cached, decide each outcome in code (rules.outcome),
    write out_dir/<set_name>/results.jsonl and out_dir/<set_name>/summary.json,
    print and return the summary.

    If `ask` is None, a single real Jev() is built lazily, the first time a pair
    actually needs asking (needs TYPESAFE_API_KEY), and closed once done; a run
    whose cache already covers every pair needs no key and builds no client at all.
    """
    out_dir = out_dir if out_dir is not None else DEFAULT_OUT_DIR
    lazy_jev: _LazyJev | None = None
    if ask is None:
        lazy_jev = _LazyJev()
        ask = lazy_jev
    tracker = _UsageTracker(ask)

    try:
        records = select_records(set_name, session=session, raw_dir=raw_dir, sets_dir=sets_dir)
        askable = [r for r in records if r.reply]

        out_dir.mkdir(parents=True, exist_ok=True)
        set_out_dir = out_dir / set_name
        set_out_dir.mkdir(parents=True, exist_ok=True)

        cache_path = out_dir / "judgments.json"
        load_path = seed_cache if seed_cache is not None else cache_path
        cache, cache_ignored = _load_cache(load_path)

        questions = load_questions()
        started = time.perf_counter()
        try:
            await _ask_all(askable, tracker, questions, cache)
        finally:
            _save_cache(cache_path, cache)
        wall_seconds = time.perf_counter() - started

        rows = [_result_row(record, cache.get(str(record.id)) if record.reply else None) for record in records]
        _write_results_jsonl(set_out_dir / "results.jsonl", rows)

        summary = _summarise(rows, tracker.calls, wall_seconds, cache_ignored)
        (set_out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        labels = load_labels(set_name, sets_dir=sets_dir)
        if labels:
            evaluation = evaluate(rows, labels)
            (set_out_dir / "evaluation.json").write_text(
                json.dumps(evaluation, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        if set_name == "all":
            ranking = rank_by_ministry(rows)
            (set_out_dir / "ranking.json").write_text(
                json.dumps(ranking, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(format_table(ranking))

        print(_format_summary(set_name, summary))
        return summary
    finally:
        if lazy_jev is not None:
            await lazy_jev.aclose()
