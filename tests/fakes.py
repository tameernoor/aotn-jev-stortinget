"""A stand-in for Jev in tests. Answers every question it is asked, records every call.
Never calls TypeSafe."""

from jev_stortinget.jev import JevResult


def answers(**values) -> dict:
    """Judgments in the shape jev.py returns. A float is a noul; a dict passes
    through unchanged (for a specific value shape)."""
    out = {}
    for qid, value in values.items():
        out[qid] = value if isinstance(value, dict) else {"type": "noul", "value": float(value)}
    return out


class FakeJev:
    """values maps question id to a fixed noul value (see answers()); every call
    gets the same answers, defaulting to 0.05 (a confident no) for any question id
    not in values. error, if set, is raised on every call instead of answering."""

    def __init__(self, values: dict | None = None, error: Exception | None = None):
        self.values = values or {}
        self.error = error
        self.calls: list[dict] = []

    async def ask(self, state: dict, questions: dict[str, dict]) -> JevResult:
        self.calls.append({"state": state, "questions": questions})
        if self.error is not None:
            raise self.error
        judgments = {qid: {"type": "noul", "value": float(self.values.get(qid, 0.05))} for qid in questions}
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
