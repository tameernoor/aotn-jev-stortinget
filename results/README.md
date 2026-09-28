# results

## tree/

The dodge tree's output (see the root README). Every file but `blind-check.json`
is written by `python -m jev_stortinget tree`, both for a fresh run and for
`--from out/tree-all.json`, which rebuilds them without spending anything on Jev.

- `asks-2024-2025.json`: stage 1's raw values, id to its seven asks_* nouls, for all 3,234 fetched pairs.
- `tree-summary.json`: verdict counts, reply-says tag counts, cost and wall time.
- `by-minister.json`: per ministry, `n`, `no_hit`, and each reply-says tag.
- `no-data.json`: every pair that said the data doesn't exist, with the deciding paragraph.
- `promised-later.json`: every pair that said the matter is for later, with the deciding paragraph.
- `tree-2024-2025.json`: the per-pair values and verdict for all 3,111 tree-eligible pairs.
- `blind-check.json`: an independent blind check by a separate reader, not written by this project's code.
