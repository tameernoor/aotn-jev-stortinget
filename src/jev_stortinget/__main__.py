"""CLI entry point.

    uv run python -m jev_stortinget fetch [--session 2024-2025]
    uv run python -m jev_stortinget tree [--from out/tree-all.json]

`fetch` pulls the session's list of written questions, then every pair not
already cached under data/raw/<session>/, at most 2 requests in flight,
retrying a failed request a few times before giving up on it; a rerun fetches
nothing new. Prints how many ids were already cached, how many were fetched
this run, how many still failed, and (reading the now-complete cache) how many
cached replies are empty.

`tree` builds the dodge tree (see tree.py): asks the seven asks_* questions
about every pair, then routes the ones with a recognised asked type back to
Jev, paragraph by paragraph, and writes results/tree/. `--from FILE` skips
Jev entirely and only rebuilds those outputs from a previous run's full
output (out/tree-all.json); without it, a fresh run needs TYPESAFE_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from .fetch import DEFAULT_SESSION, fetch_list, fetch_pairs, load_records
from .jev import AskFn
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jev_stortinget", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch the session's written questions and replies.")
    fetch_parser.add_argument("--session", default=DEFAULT_SESSION, help=f"session id (default: {DEFAULT_SESSION})")

    tree_parser = subparsers.add_parser("tree", help="Build the dodge tree and write results/tree/.")
    tree_parser.add_argument(
        "--from", dest="from_path", metavar="FILE", default=None, help="skip Jev, rebuild outputs from FILE"
    )

    return parser


def main(argv: Sequence[str] | None = None, ask: AskFn | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "fetch":
        _fetch(args.session)
    elif args.command == "tree":
        from_path = Path(args.from_path) if args.from_path is not None else None
        asyncio.run(run_tree(from_path=from_path, ask=ask))


if __name__ == "__main__":
    main()
