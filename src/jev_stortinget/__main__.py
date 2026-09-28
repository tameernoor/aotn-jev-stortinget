"""CLI entry point.

    uv run python -m jev_stortinget fetch [--session 2024-2025]
    uv run python -m jev_stortinget sets
    uv run --env-file .env python -m jev_stortinget run --set dev|holdout|all [--out out/] [--cache FILE]
    uv run python -m jev_stortinget tree [--from out/tree-all.json]

`fetch` pulls the session's list of written questions, then every pair not
already cached under data/raw/<session>/, at most 2 requests in flight,
retrying a failed request a few times before giving up on it; a rerun fetches
nothing new. Prints how many ids were already cached, how many were fetched
this run, how many still failed, and (reading the now-complete cache) how many
cached replies are empty.

`sets` draws the dev and holdout sets from the cached pairs (see sets.py) and
writes data/sets/dev.json and data/sets/holdout.json.

`run` asks Jev about every pair in the chosen set and decides the outcome in code
(see run.py for the full behaviour: the judgments cache, the cache hash guard, the
evaluation and ranking outputs). Needs TYPESAFE_API_KEY in the environment, and
only once a pair actually has to be asked.

`tree` builds the dodge tree (see tree.py): routes every part-1 pair with a
recognised asked type back to Jev, paragraph by paragraph, and writes
results/tree/. `--from FILE` skips Jev entirely and only rebuilds those outputs
from a previous run's full output (out/tree-all.json); without it, a fresh run
needs TYPESAFE_API_KEY and out/all/results.jsonl (part 1's own run).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from .fetch import DEFAULT_SESSION, fetch_list, fetch_pairs, load_records
from .jev import AskFn
from .run import DEFAULT_OUT_DIR, SET_NAMES
from .run import run as run_set
from .sets import draw_sets, write_sets
from .tree import tree as run_tree


def _fetch(session: str) -> None:
    items = fetch_list(session)
    ids = [item["id"] for item in items]
    print(f"{len(ids)} questions listed for session {session}")

    summary = fetch_pairs(ids, session=session)
    print(f"fetched {summary.fetched}, already cached {summary.already_cached}, ok {summary.ok}")
    if summary.failed_ids:
        print(f"failed after retries ({len(summary.failed_ids)}): {list(summary.failed_ids)}")

    records = load_records(session)
    empty_replies = sum(1 for r in records if not r.reply)
    print(f"cached records: {len(records)}, empty replies: {empty_replies}")


def _sets() -> None:
    dev, holdout = draw_sets()
    write_sets(dev, holdout)
    print(f"wrote {len(dev)} dev and {len(holdout)} holdout ids")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jev_stortinget", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch the session's written questions and replies.")
    fetch_parser.add_argument("--session", default=DEFAULT_SESSION, help=f"session id (default: {DEFAULT_SESSION})")

    subparsers.add_parser("sets", help="Draw the dev and holdout sets from the cache.")

    run_parser = subparsers.add_parser("run", help="Ask Jev about a set's pairs and decide the outcome in code.")
    run_parser.add_argument("--set", dest="set_name", required=True, choices=SET_NAMES, help="which set to run")
    run_parser.add_argument("--out", metavar="DIR", default=None, help=f"output folder (default: {DEFAULT_OUT_DIR})")
    run_parser.add_argument(
        "--cache", metavar="FILE", default=None, help="seed the judgments cache from FILE instead of out/judgments.json"
    )

    tree_parser = subparsers.add_parser("tree", help="Build the dodge tree and write results/tree/.")
    tree_parser.add_argument(
        "--from", dest="from_path", metavar="FILE", default=None, help="skip Jev, rebuild outputs from FILE"
    )

    return parser


def main(argv: Sequence[str] | None = None, ask: AskFn | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "fetch":
        _fetch(args.session)
    elif args.command == "sets":
        _sets()
    elif args.command == "run":
        out_dir = Path(args.out) if args.out is not None else None
        seed_cache = Path(args.cache) if args.cache is not None else None
        asyncio.run(run_set(args.set_name, out_dir=out_dir, ask=ask, seed_cache=seed_cache))
    elif args.command == "tree":
        from_path = Path(args.from_path) if args.from_path is not None else None
        asyncio.run(run_tree(from_path=from_path, ask=ask))


if __name__ == "__main__":
    main()
