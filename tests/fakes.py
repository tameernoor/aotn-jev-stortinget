"""A stand-in for Jev in tests. Answers every question it is asked, records every call.
Never calls TypeSafe."""

import asyncio

from jev_stortinget.jev import JevResult


def answers(**values) -> dict:
    """Judgments in the shape jev.py returns. A float is a noul; a dict passes
    through unchanged (for a specific value shape)."""
    out = {}
    for qid, value in values.items():
        out[qid] = value if isinstance(value, dict) else {"type": "noul", "value": float(value)}
    return out


def _meta(question_count: int) -> dict:
    return {
        "model": "fake-jev",
        "request_id": None,
        "latency_ms": 1.0,
        "question_count": question_count,
        "input_tokens": 100,
        "price_per_mtok_usd": 0.042,
        "cost_usd": 100 * 0.042 / 1_000_000,
    }


class FakeJev:
    """values maps question id to a fixed noul value (see answers()); every call
    gets the same answers, defaulting to 0.05 (a confident no) for any question id
    not in values. error, if set, is raised on every call instead of answering.
    drop_id, if set, is left out of the answer (for testing a Jev response with the
    wrong set of ids)."""

    def __init__(self, values: dict | None = None, error: Exception | None = None, drop_id: str | None = None):
        self.values = values or {}
        self.error = error
        self.drop_id = drop_id
        self.calls: list[dict] = []

    async def ask(self, state: dict, questions: dict[str, dict]) -> JevResult:
        self.calls.append({"state": state, "questions": questions})
        if self.error is not None:
            raise self.error
        judgments = {
            qid: {"type": "noul", "value": float(self.values.get(qid, 0.05))}
            for qid in questions
            if qid != self.drop_id
        }
        return JevResult(judgments=judgments, meta=_meta(len(questions)))


class ConcurrencyTrackingAsk:
    """A fake `ask` that records how many calls were in flight at once (`peak`), to
    assert a concurrency cap is actually enforced rather than merely documented.
    Each call sleeps `delay` seconds (a real await, so other calls genuinely run
    concurrently instead of this one running to completion before the next
    starts) and answers every question 0.05 (a confident no)."""

    def __init__(self, delay: float = 0.01):
        self.delay = delay
        self.current = 0
        self.peak = 0
        self.calls = 0

    async def __call__(self, state: dict, questions: dict[str, dict]) -> JevResult:
        self.calls += 1
        self.current += 1
        self.peak = max(self.peak, self.current)
        try:
            await asyncio.sleep(self.delay)
            judgments = {qid: {"type": "noul", "value": 0.05} for qid in questions}
            return JevResult(judgments=judgments, meta=_meta(len(questions)))
        finally:
            self.current -= 1
