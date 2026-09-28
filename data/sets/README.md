# data/sets

Source: Stortinget (data.stortinget.no).

Holds the dev and holdout sets drawn by `sets.py`, and the independent reader's
labels for each.

- `dev.json`, `holdout.json`: 20 and 60 pairs, each with `id`, `ministry`,
  `question` and `reply`, drawn once from the cached pairs in `data/raw/` with a
  fixed seed (see `sets.py`). The question and reply text is Stortinget's own,
  extracted from `data/raw/`.
- `dev-labels.json`, `holdout-labels.json`: the independent reader's label for
  each id, scored against `outcome` by `evaluate.py` (see the root README's "How
  the evaluation stays honest").
