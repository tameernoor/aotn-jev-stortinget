"""run.py, offline only: a FakeJev stands in for Jev and the suite never touches
data/sets/holdout.json, data/sets/holdout-labels.json or any real cached pair.
Every fixture here is a tiny synthetic cache built in tmp_path.
"""

import asyncio
import json
from pathlib import Path

import pytest
from fakes import FakeJev

from jev_stortinget.jev import JevResult
from jev_stortinget.run import (
    MAX_IN_FLIGHT,
    RunError,
    _questions_hash,
    run,
)

SESSION = "2024-2025"

# A deterministic outcome for verification: asks_time confident yes, states_time
# confident yes, everything else defaults to FakeJev's 0.05 (a confident no).
ANSWERS_TIME = {"asks_time": 0.9, "states_time": 0.9}


def _pair_payload(question_id: int, reply: str | None = "A real reply with actual content.") -> dict:
    return {
        "id": question_id,
        "sporsmal_nummer": question_id,
        "sporsmal_til_minister_tittel": "helseministeren",
        "besvart_av_minister_tittel": f"minister-{question_id % 2}",
        "sporsmal_fra": {"fornavn": "Test", "etternavn": f"Person{question_id}", "parti": {"id": "X"}},
        "datert_dato": "/Date(1759234887245+0200)/",
        "besvart_dato": "/Date(1759989109780+0200)/",
        "sporsmal": f"Question number {question_id}?",
        "begrunnelse": None,
        "svar": reply,
    }


def _write_cache(raw_dir: Path, payloads: list[dict]) -> None:
    cache_dir = raw_dir / SESSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    for payload in payloads:
        (cache_dir / f"{payload['id']}.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_set(sets_dir: Path, name: str, ids: list[int]) -> None:
    sets_dir.mkdir(parents=True, exist_ok=True)
    items = [{"id": i, "ministry": "helseministeren", "question": "Q", "reply": "R"} for i in ids]
    (sets_dir / f"{name}.json").write_text(json.dumps(items), encoding="utf-8")


def run_sync(set_name, out_dir, **kwargs):
    return asyncio.run(run(set_name, out_dir=out_dir, **kwargs))


# --- results.jsonl and summary.json shape -------------------------------------------


def test_run_writes_results_and_summary_for_dev_set(tmp_path):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(1), _pair_payload(2), _pair_payload(3)])
    _write_set(sets_dir, "dev", [1, 2])
    fake = FakeJev(values=ANSWERS_TIME)

    summary = run_sync("dev", out_dir, ask=fake.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)

    assert len(fake.calls) == 2  # only the 2 dev ids, not id 3

    lines = (out_dir / "dev" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert {row["id"] for row in rows} == {1, 2}
    row = rows[0]
    assert set(row) == {
        "id",
        "number",
        "ministry",
        "answered_by",
        "mp",
        "party",
        "asked_date",
        "answered_date",
        "outcome",
        "reasons",
        "read",
        "shadow",
        "values",
    }
    assert row["outcome"] == "answered"
    assert row["reasons"] == ["gives time"]
    assert "states_time" in row["read"]
    assert "states_position" not in row["read"]  # asks_assessment was never confident or used
    assert len(row["values"]) == 18
    assert row["shadow"] == pytest.approx(0.05)

    assert set(summary) == {"counts", "calls", "input_tokens", "cost_usd", "wall_seconds", "model_ids", "cache_ignored"}
    assert summary["counts"] == {"answered": 2}
    assert summary["calls"] == 2
    assert summary["model_ids"] == ["fake-jev"]
    assert summary["cache_ignored"] is False

    summary_on_disk = json.loads((out_dir / "dev" / "summary.json").read_text(encoding="utf-8"))
    assert summary_on_disk == summary


# --- --set all uses every cached record, dev/holdout ids included -------------------


def test_run_all_uses_every_cached_record_regardless_of_set_files(tmp_path):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(i) for i in range(1, 6)])
    _write_set(sets_dir, "dev", [1, 2])
    _write_set(sets_dir, "holdout", [3])
    fake = FakeJev(values=ANSWERS_TIME)

    run_sync("all", out_dir, ask=fake.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)

    assert len(fake.calls) == 5
    lines = (out_dir / "all" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["id"] for line in lines} == {1, 2, 3, 4, 5}


# --- an empty reply is kept, flagged, and never sent to Jev -------------------------


def test_empty_reply_is_flagged_no_reply_and_never_sent(tmp_path):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(1), _pair_payload(2, reply="")])
    fake = FakeJev(values=ANSWERS_TIME)

    run_sync("all", out_dir, ask=fake.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)

    assert len(fake.calls) == 1  # id 2's empty reply was never asked about
    rows = {
        json.loads(line)["id"]: json.loads(line)
        for line in (out_dir / "all" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    }
    assert rows[2]["outcome"] == "no_reply"
    assert rows[2]["reasons"] == ["empty reply, never sent to Jev"]
    assert rows[2]["read"] == []
    assert rows[2]["shadow"] is None
    assert rows[2]["values"] == {}
    assert rows[1]["outcome"] == "answered"


# --- a set file naming an id with no cached pair is a clear error -------------------


def test_missing_id_in_set_file_raises_run_error(tmp_path):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(1)])
    _write_set(sets_dir, "dev", [1, 999])
    fake = FakeJev(values=ANSWERS_TIME)

    with pytest.raises(RunError):
        run_sync("dev", out_dir, ask=fake.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)


def test_unknown_set_name_raises_run_error(tmp_path):
    with pytest.raises(RunError):
        run_sync("bogus", tmp_path / "out", ask=FakeJev().ask, session=SESSION, raw_dir=tmp_path, sets_dir=tmp_path)


# --- the judgments cache: shared file, reused on a second run -----------------------


def test_second_run_reuses_the_cache_and_makes_no_new_calls(tmp_path):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(1)])
    _write_set(sets_dir, "dev", [1])

    first = FakeJev(values=ANSWERS_TIME)
    run_sync("dev", out_dir, ask=first.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)
    assert len(first.calls) == 1

    second = FakeJev(values=ANSWERS_TIME)
    run_sync("dev", out_dir, ask=second.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)
    assert len(second.calls) == 0

    cache_payload = json.loads((out_dir / "judgments.json").read_text(encoding="utf-8"))
    assert cache_payload["questions_hash"] == _questions_hash()
    assert set(cache_payload["cache"]) == {"1"}


def test_cache_is_shared_across_sets_not_reasked_under_all(tmp_path):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(1)])
    _write_set(sets_dir, "dev", [1])

    warm = FakeJev(values=ANSWERS_TIME)
    run_sync("dev", out_dir, ask=warm.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)

    cold = FakeJev(values=ANSWERS_TIME)
    run_sync("all", out_dir, ask=cold.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)
    assert len(cold.calls) == 0


def test_stale_questions_hash_is_ignored_and_reported(tmp_path, capsys):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(1)])
    _write_set(sets_dir, "dev", [1])
    out_dir.mkdir()
    (out_dir / "judgments.json").write_text(
        json.dumps({"questions_hash": "not-the-real-hash", "cache": {"1": {}}}), encoding="utf-8"
    )
    fake = FakeJev(values=ANSWERS_TIME)

    summary = run_sync("dev", out_dir, ask=fake.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)

    assert len(fake.calls) == 1  # the stale cache was not trusted, so the pair was asked fresh
    assert summary["cache_ignored"] is True
    assert "ignored" in capsys.readouterr().out.lower()


def test_cache_flag_seeds_without_writing_back_to_the_seed_file(tmp_path):
    raw_dir, sets_dir = tmp_path / "raw", tmp_path / "sets"
    _write_cache(raw_dir, [_pair_payload(1)])
    _write_set(sets_dir, "dev", [1])
    warm_dir, out_dir = tmp_path / "warm", tmp_path / "out"

    warm = FakeJev(values=ANSWERS_TIME)
    run_sync("dev", warm_dir, ask=warm.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)
    seed_path = warm_dir / "judgments.json"
    seed_before = seed_path.read_text(encoding="utf-8")

    second = FakeJev(values=ANSWERS_TIME)
    run_sync(
        "dev",
        out_dir,
        ask=second.ask,
        session=SESSION,
        raw_dir=raw_dir,
        sets_dir=sets_dir,
        seed_cache=seed_path,
    )

    assert len(second.calls) == 0
    assert (out_dir / "judgments.json").exists()
    assert seed_path.read_text(encoding="utf-8") == seed_before


# --- save-on-failure: the cache keeps every answer that did come back --------------


async def _selective_fail_ask(state, questions):
    """Fails for the pair whose question text says so, succeeds for every other one,
    with the same JevResult shape FakeJev returns."""
    if "fail" in state["question"]:
        raise RuntimeError("simulated Jev failure")
    judgments = {qid: {"type": "noul", "value": 0.9 if qid in ANSWERS_TIME else 0.05} for qid in questions}
    meta = {
        "model": "fake-jev",
        "request_id": None,
        "latency_ms": 1.0,
        "question_count": len(questions),
        "input_tokens": 100,
        "price_per_mtok_usd": 0.042,
        "cost_usd": 100 * 0.042 / 1_000_000,
    }
    return JevResult(judgments=judgments, meta=meta)


def test_save_on_failure_keeps_the_answers_that_did_come_back(tmp_path):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    ok_payload = _pair_payload(1)
    fail_payload = _pair_payload(2)
    fail_payload["sporsmal"] = "This one should fail?"
    _write_cache(raw_dir, [ok_payload, fail_payload])
    _write_set(sets_dir, "dev", [1, 2])

    with pytest.raises(RuntimeError, match="simulated Jev failure"):
        run_sync("dev", out_dir, ask=_selective_fail_ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)

    # results.jsonl/summary.json were never written for a failed run...
    assert not (out_dir / "dev" / "results.jsonl").exists()
    # ...but the cache itself was still saved, with the pair that did succeed.
    cache_payload = json.loads((out_dir / "judgments.json").read_text(encoding="utf-8"))
    assert set(cache_payload["cache"]) == {"1"}


# --- concurrency cap -----------------------------------------------------------------


def test_max_in_flight_is_20():
    assert MAX_IN_FLIGHT == 20


# --- Jev() is built lazily, only on an actual cache miss ----------------------------


def test_a_run_whose_cache_covers_every_pair_needs_no_key_and_builds_no_jev(tmp_path, monkeypatch):
    raw_dir, sets_dir, out_dir = tmp_path / "raw", tmp_path / "sets", tmp_path / "out"
    _write_cache(raw_dir, [_pair_payload(1)])
    _write_set(sets_dir, "dev", [1])

    warm = FakeJev(values=ANSWERS_TIME)
    run_sync("dev", out_dir, ask=warm.ask, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)
    assert len(warm.calls) == 1

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    # ask=None: run() must not build a real Jev() unless something is actually asked.
    run_sync("dev", out_dir, session=SESSION, raw_dir=raw_dir, sets_dir=sets_dir)
