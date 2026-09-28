"""Fetches Stortinget's written questions and ministers' replies for one session
(data.stortinget.no, open JSON, no login) and caches each pair as
data/raw/<session>/<id>.json so a rerun fetches nothing new.

`fetch_list` reads the session's list of questions (one small call, never cached:
it is cheap to refetch and is only used to enumerate ids). `fetch_pairs` then
fetches every pair not already cached, at most MAX_WORKERS requests in flight,
pausing REQUEST_PACING_SECONDS between one request and the next on the same
worker, and retrying a failed request up to MAX_RETRIES times before giving up
on it. data.stortinget.no rate-limits in bursts with no Retry-After header most
of the time, so a 429 or 5xx response backs off much longer than any other
failure (15s, 30s, 60s, 120s, or the server's own Retry-After value when it
sends one) rather than burning through retries in a few seconds. A pair that
still fails after every retry is reported in the returned FetchSummary and
never written to disk (a failed fetch is never cached as an empty or partial
file, so a later rerun retries it, and only it). Progress prints every
PROGRESS_INTERVAL pairs.

`build_record` turns one pair's raw JSON into a `Record`: the HTML text fields
(sporsmal, begrunnelse, svar) become plain text and Stortinget's
/Date(<epoch ms>[+-]<offset>)/ strings become ISO dates. `load_records` reads
every cached pair for a session back into `Record`s without touching the network,
for sets.py and anything downstream that needs the normalised data.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

DEFAULT_SESSION = "2024-2025"

LIST_URL = "https://data.stortinget.no/eksport/skriftligesporsmal?sesjonid={session}&format=json"
PAIR_URL = "https://data.stortinget.no/eksport/enkeltsporsmal?NSporsmalId={question_id}&format=json"
USER_AGENT = "jev-stortinget (aotn example; github.com/tameernoor/aotn-jev-stortinget)"

MAX_WORKERS = 2
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = (15.0, 30.0, 60.0, 120.0)
REQUEST_PACING_SECONDS = 0.2
REQUEST_TIMEOUT_SECONDS = 30
PROGRESS_INTERVAL = 200

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"

_PARAGRAPH_BREAK_RE = re.compile(r"<br\s*/?>|</p\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_LINE_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{2,}")
_DATE_RE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")


class FetchError(RuntimeError):
    """Raised when the question list itself cannot be fetched, or a single pair
    still fails after MAX_RETRIES attempts."""


@dataclass(frozen=True)
class Record:
    """One normalised written-question / reply pair."""

    id: int
    number: int
    ministry: str
    answered_by: str
    mp_name: str
    mp_party: str
    asked_date: str | None
    answered_date: str | None
    question: str
    reasoning: str
    reply: str


@dataclass(frozen=True)
class FetchSummary:
    """Result of a fetch_pairs() call. `ok` is every id now cached, whether it was
    fetched this run or already on disk from a previous one."""

    fetched: int
    already_cached: int
    failed_ids: tuple[int, ...]

    @property
    def ok(self) -> int:
        return self.fetched + self.already_cached


def html_to_text(value: str | None) -> str:
    """Turn a Stortinget HTML text field (`sporsmal`, `begrunnelse`, `svar`) into
    plain text, keeping paragraph breaks instead of flattening them away.

    `<br>`, `<br/>` and `</p>` become a newline; any other tag (only a handful of
    stray `<a href=...>`/`</a>`/`<b>`/`</b>` in this corpus) is stripped to a
    single space instead, so removing one never glues two words together. Tag
    matching requires a letter right after `<` or `</`, since the corpus is not
    reliably entity-escaped and has real literal "<" characters that are not
    tags at all (comparisons like "P/B<1" or "< 50 m" in a table): a greedy
    `<[^>]+>` would treat the next unrelated ">" anywhere later in the text as
    this tag's close and delete everything in between.

    After that: HTML entities are unescaped, spaces and tabs collapse to one
    within each line, each line is stripped, runs of 2+ newlines collapse to
    exactly one blank line, and the whole result is stripped at the ends. None
    or a blank/all-tag value becomes "" (an empty reply is kept as "", not
    skipped)."""
    if not value:
        return ""
    text = _PARAGRAPH_BREAK_RE.sub("\n", value)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    lines = [_LINE_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def parse_stortinget_date(value: str | None) -> str | None:
    """Parse a Stortinget `/Date(<epoch ms>[+-]<offset HHMM>)/` string into an ISO
    date (YYYY-MM-DD) in that timestamp's own offset. Returns None for a missing
    or unparseable value."""
    if not value:
        return None
    match = _DATE_RE.match(value)
    if not match:
        return None
    millis = int(match.group(1))
    offset = timedelta()
    offset_str = match.group(2)
    if offset_str:
        sign = 1 if offset_str[0] == "+" else -1
        offset = sign * timedelta(hours=int(offset_str[1:3]), minutes=int(offset_str[3:5]))
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=millis) + offset
    return moment.date().isoformat()


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def fetch_list(session: str = DEFAULT_SESSION) -> list[dict]:
    """Fetch the session's list of written questions. Returns the raw
    `sporsmal_liste` items (id, sporsmal_nummer, minister titles, sporsmal_fra,
    dates, ...); not cached, since it is one small call and is only used to
    enumerate the ids to fetch pairs for."""
    url = LIST_URL.format(session=session)
    try:
        payload = _get_json(url)
    except (OSError, json.JSONDecodeError) as exc:
        raise FetchError(f"could not fetch the question list for session {session}: {exc}") from exc
    return payload["sporsmal_liste"]


def _cache_dir(session: str, raw_dir: Path | None) -> Path:
    return (raw_dir if raw_dir is not None else RAW_DIR) / session


def _pair_path(cache_dir: Path, question_id: int) -> Path:
    return cache_dir / f"{question_id}.json"


def _write_json_atomically(path: Path, payload: dict) -> None:
    """Write `payload` to `path` as JSON, atomically: write to a sibling temp
    file first, then os.replace() it into place, so a crash or interruption
    mid-write never leaves a truncated or partial cache file behind."""
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value (seconds, or an HTTP date) into seconds to
    wait. Returns None if there is no header or it cannot be parsed, so the
    caller falls back to its own backoff schedule."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - datetime.now(timezone.utc)).total_seconds(), 0.0)


def _retry_delay(attempt: int, exc: Exception) -> float:
    """Seconds to wait before the next attempt. A 429 or 5xx response backs off
    much longer than any other failure: the server's own Retry-After value if it
    sent one, otherwise RATE_LIMIT_BACKOFF_SECONDS (15s, 30s, 60s, 120s, holding
    at 120s for any further attempt). Any other failure (a connection error, a
    timeout, bad JSON) uses a short, linearly growing backoff instead."""
    if isinstance(exc, urllib.error.HTTPError) and (exc.code == 429 or exc.code >= 500):
        retry_after = _parse_retry_after((exc.headers or {}).get("Retry-After"))
        if retry_after is not None:
            return retry_after
        index = min(attempt, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
        return RATE_LIMIT_BACKOFF_SECONDS[index]
    return RETRY_BACKOFF_SECONDS * (attempt + 1)


def _fetch_pair(question_id: int) -> dict:
    """Fetch one pair, retrying up to MAX_RETRIES times (so MAX_RETRIES + 1
    attempts total); see _retry_delay for how long each retry waits. On success,
    also pauses REQUEST_PACING_SECONDS before returning, so a worker never fires
    its next request immediately after this one. Raises FetchError if every
    attempt fails."""
    url = PAIR_URL.format(question_id=question_id)
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            payload = _get_json(url)
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(_retry_delay(attempt, exc))
            continue
        time.sleep(REQUEST_PACING_SECONDS)
        return payload
    raise FetchError(f"question {question_id}: {last_error}") from last_error


def fetch_pairs(
    ids: Sequence[int], session: str = DEFAULT_SESSION, raw_dir: Path | None = None
) -> FetchSummary:
    """Fetch every id in `ids` not already cached at <raw_dir>/<session>/<id>.json
    (raw_dir defaults to data/raw), at most MAX_WORKERS requests in flight. A pair
    that still fails after retries is listed in the result and never written to
    disk, so a rerun retries exactly the ids that failed and nothing else."""
    cache_dir = _cache_dir(session, raw_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    todo = [i for i in ids if not _pair_path(cache_dir, i).exists()]
    already_cached = len(ids) - len(todo)
    fetched = 0
    failed: list[int] = []
    processed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_pair, i): i for i in todo}
        for future in as_completed(futures):
            question_id = futures[future]
            try:
                payload = future.result()
            except FetchError:
                failed.append(question_id)
            else:
                _write_json_atomically(_pair_path(cache_dir, question_id), payload)
                fetched += 1
            processed += 1
            if processed % PROGRESS_INTERVAL == 0:
                print(
                    f"fetch progress: {processed}/{len(todo)} pairs processed "
                    f"(ok {fetched}, failed {len(failed)})",
                    flush=True,
                )

    return FetchSummary(fetched=fetched, already_cached=already_cached, failed_ids=tuple(sorted(failed)))


def build_record(payload: dict) -> Record:
    """Turn one pair's raw JSON (a data.stortinget.no enkeltsporsmal response,
    cached or freshly fetched) into a normalised Record."""
    mp = payload.get("sporsmal_fra") or {}
    party = (mp.get("parti") or {}).get("id") or ""
    mp_name = " ".join(part for part in (mp.get("fornavn"), mp.get("etternavn")) if part)
    return Record(
        id=payload["id"],
        number=payload["sporsmal_nummer"],
        ministry=payload.get("sporsmal_til_minister_tittel") or "",
        answered_by=payload.get("besvart_av_minister_tittel") or "",
        mp_name=mp_name,
        mp_party=party,
        asked_date=parse_stortinget_date(payload.get("datert_dato")),
        answered_date=parse_stortinget_date(payload.get("besvart_dato")),
        question=html_to_text(payload.get("sporsmal")),
        reasoning=html_to_text(payload.get("begrunnelse")),
        reply=html_to_text(payload.get("svar")),
    )


def load_records(session: str = DEFAULT_SESSION, raw_dir: Path | None = None) -> list[Record]:
    """Load every cached pair for `session` from <raw_dir>/<session>/*.json into
    Records, sorted by id. Reads only the cache; never touches the network."""
    cache_dir = _cache_dir(session, raw_dir)
    records = [build_record(json.loads(path.read_text(encoding="utf-8"))) for path in cache_dir.glob("*.json")]
    return sorted(records, key=lambda r: r.id)
