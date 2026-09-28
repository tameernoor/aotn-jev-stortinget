"""Draws the dev and holdout sets from the cached pairs (see fetch.py) and writes
them to data/sets/dev.json and data/sets/holdout.json.

The draw is fixed by the plan: every cached pair with a non-empty reply, sorted by
id, then random.Random(SEED) draws DEV_SIZE + HOLDOUT_SIZE of them.
Random.sample() returns its picks in selection order and guarantees every
sub-slice of the result is itself a valid random sample, so the first DEV_SIZE
draws are dev and the next HOLDOUT_SIZE are holdout, with no overlap. Both files
are committed, so the same cache always reproduces the same two id lists.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .fetch import DEFAULT_SESSION, Record, load_records

SEED = 20250928
DEV_SIZE = 20
HOLDOUT_SIZE = 60

REPO_ROOT = Path(__file__).resolve().parents[2]
SETS_DIR = REPO_ROOT / "data" / "sets"


def _set_item(record: Record) -> dict:
    return {
        "id": record.id,
        "ministry": record.ministry,
        "question": record.question,
        "reply": record.reply,
    }


def draw_sets(session: str = DEFAULT_SESSION, raw_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Draw the dev and holdout sets from every cached pair in `session`: records
    with a non-empty reply, sorted by id, then random.Random(SEED) draws
    DEV_SIZE + HOLDOUT_SIZE of them; the first DEV_SIZE are dev, the rest
    holdout. Each item holds id, ministry, question text and reply text."""
    records = load_records(session, raw_dir=raw_dir)
    eligible = sorted((r for r in records if r.reply), key=lambda r: r.id)
    drawn = random.Random(SEED).sample(eligible, DEV_SIZE + HOLDOUT_SIZE)
    dev = [_set_item(r) for r in drawn[:DEV_SIZE]]
    holdout = [_set_item(r) for r in drawn[DEV_SIZE:]]
    return dev, holdout


def write_sets(dev: list[dict], holdout: list[dict], sets_dir: Path | None = None) -> None:
    """Write dev.json and holdout.json, ensure_ascii=False and indent 2, so
    Norwegian text stays readable and diffs stay small."""
    sets_dir = sets_dir if sets_dir is not None else SETS_DIR
    sets_dir.mkdir(parents=True, exist_ok=True)
    (sets_dir / "dev.json").write_text(json.dumps(dev, ensure_ascii=False, indent=2), encoding="utf-8")
    (sets_dir / "holdout.json").write_text(json.dumps(holdout, ensure_ascii=False, indent=2), encoding="utf-8")
