# results

## tree/

The dodge tree's output (see the root README). `tree-summary.json`, `by-minister.json`,
`no-data.json`, `promised-later.json` and `tree-2024-2025.json` are written both by a
fresh run of `python -m jev_stortinget tree` and by `--from out/tree-all.json`, which
rebuilds them without spending anything on Jev. `asks-2024-2025.json` is written only
by a fresh run, from its own stage 1; nothing reads it back. `blind-check.json` is the
exception: not written by this project's code at all.

- `asks-2024-2025.json`: the last fresh run's stage-1 values, id to its seven asks_* nouls, for all 3,234 fetched pairs.
- `tree-summary.json`: verdict counts, reply-says tag counts, cost and wall time (a fresh run's own measured numbers, or the recorded run's when rebuilt with `--from`).
- `by-minister.json`: per ministry, `n`, `no_hit`, and each reply-says tag.
- `no-data.json`: every pair that said the data doesn't exist, with the deciding paragraph.
- `promised-later.json`: every pair that said the matter is for later, with the deciding paragraph.
- `tree-2024-2025.json`: the per-pair values and verdict for all 3,111 tree-eligible pairs.
- `blind-check.json`: an independent blind check by a separate reader, not written by this project's code.
