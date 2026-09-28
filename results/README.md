# results

Holds the artifacts behind this project's `## Measured` section: one real run
against the whole 2024-2025 session, plus the dev and holdout evaluations against
the independent reader's labels.

- `judgments-2024-2025.json`: the full session's judgments cache, in the same
  shape as `out/judgments.json`, a sha256 of `questions/pair.yaml` alongside a
  cache keyed by pair id. Each entry holds the 18 raw noul values Jev returned,
  the model that answered, and a hash of the exact `{"question", "reply"}` state
  sent. Covers all 3,234 pairs, including the 80 already asked during the dev and
  holdout rounds.
- `summary-2024-2025.json`: that same run's `out/all/summary.json`, copied here:
  outcome counts, calls, input tokens, cost and wall time.
- `evaluation-dev.json`: `out/dev/evaluation.json` from the dev round, scoring
  `outcome` against `data/sets/dev-labels.json`.
- `evaluation-holdout.json`: `out/holdout/evaluation.json` from the one holdout
  run, scoring `outcome` against `data/sets/holdout-labels.json`.
- `ranking-2024-2025.json`: `out/all/ranking.json`, the per-ministry unanswered
  share and unclear count the full run produced.

None of these files is written directly into `results/` by this project's code;
they are copies of `out/`'s own output, placed here by hand so the numbers in the
root README reproduce without spending anything on Jev. See the root README's "How
to fetch and run" for `--cache results/judgments-2024-2025.json`.
