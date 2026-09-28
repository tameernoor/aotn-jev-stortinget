"""CLI entry point.

    uv run python -m jev_stortinget fetch [--session 2024-2025]
    uv run python -m jev_stortinget sets

`fetch` pulls the session's list of written questions, then every pair not
already cached under data/raw/<session>/, at most 4 requests in flight,
retrying a failed request a few times before giving up on it; a rerun fetches
nothing new. Prints how many ids were already cached, how many were fetched
this run, how many still failed, and (reading the now-complete cache) how many
cached replies are empty.

`sets` draws the dev and holdout sets from the cached pairs (see sets.py) and
writes data/sets/dev.json and data/sets/holdout.json.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .fetch import DEFAULT_SESSION, fetch_list, fetch_pairs, load_records
from .sets import draw_sets, write_sets


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

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "fetch":
        _fetch(args.session)
    elif args.command == "sets":
        _sets()


if __name__ == "__main__":
    main()
