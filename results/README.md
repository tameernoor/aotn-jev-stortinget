# results

## tree/

The dodge tree's own output (see the root README). Written directly by
`python -m jev_stortinget tree`, both for a fresh run and for `--from
out/tree-all.json`, which rebuilds all five files below without spending
anything on Jev.

- `tree-summary.json`: verdict counts (answered, and how many went on to the
  why-not stage) and the reply-says tag counts, plus the cost and wall time of
  both stages.
- `by-minister.json`: per ministry, how many tree-eligible pairs it has and how
  many carry each reply-says tag (no data, later, someone else's job, can't
  comment, refers back); not mutually exclusive, so a ministry's tags don't
  have to add up to its pair count.
- `no-data.json`, `promised-later.json`: every pair that said, on any
  paragraph, that the data doesn't exist or that the matter is for later, with
  the ministry, the question, the deciding paragraph's quote, and whether a
  collect promise or a date came with it.
- `tree-2024-2025.json`: the per-pair values and verdict for all 3,111 pairs,
  ids and paragraph indices only, never the reply text itself.

## The rest of results/

Artifacts from this project's earlier, whole-reply pass (one broad
"did the reply answer" decomposition per pair, not narrated in the root README
any more, but still real runs kept here for anyone who wants to check them):

- `judgments-2024-2025.json`: the full session's judgments cache, in the same
  shape as `out/judgments.json`, a sha256 of `questions/pair.yaml` alongside a
  cache keyed by pair id. Each entry holds the 18 raw noul values Jev returned,
  the model that answered, and a hash of the exact `{"question", "reply"}` state
  sent. Covers all 3,234 pairs, including the 80 already asked during the dev and
  holdout rounds.
- `summary-2024-2025.json`: that run's `out/all/summary.json`, copied here:
  outcome counts, calls, input tokens, cost and wall time.
- `evaluation-dev.json`: `out/dev/evaluation.json` from the dev round, scoring
  `outcome` against `data/sets/dev-labels.json`.
- `evaluation-holdout.json`: `out/holdout/evaluation.json` from the one holdout
  run, scoring `outcome` against `data/sets/holdout-labels.json`.
- `ranking-2024-2025.json`: `out/all/ranking.json`, the per-ministry unanswered
  share and unclear count that run produced.

None of these five files is written directly into `results/` by this project's
code; they are copies of `out/`'s own output, placed here by hand so they
reproduce without spending anything on Jev, using
`--cache results/judgments-2024-2025.json` on `python -m jev_stortinget run`.
