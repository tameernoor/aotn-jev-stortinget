"""The `run` subcommand added to __main__.py, offline with a FakeJev."""

import json
from pathlib import Path

from fakes import FakeJev

from jev_stortinget.__main__ import main

SESSION = "2024-2025"


def _pair_payload(question_id: int) -> dict:
    return {
        "id": question_id,
        "sporsmal_nummer": question_id,
        "sporsmal_til_minister_tittel": "helseministeren",
        "besvart_av_minister_tittel": "helseministeren",
        "sporsmal_fra": {"fornavn": "Test", "etternavn": "Person", "parti": {"id": "X"}},
        "datert_dato": "/Date(1759234887245+0200)/",
        "besvart_dato": "/Date(1759989109780+0200)/",
        "sporsmal": "Hva er status?",
        "begrunnelse": None,
        "svar": "Status er god.",
    }


def test_run_subcommand_requires_set(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["run"])


def test_run_subcommand_writes_output_for_all_set(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    (raw_dir / SESSION).mkdir(parents=True)
    (raw_dir / SESSION / "1.json").write_text(json.dumps(_pair_payload(1)), encoding="utf-8")

    # run.py's REPO_ROOT-derived paths (raw_dir, sets_dir) are fixed at import time,
    # so route around them the same way the sibling project's CLI test does for its
    # own data dir: monkeypatch the module-level default fetch.RAW_DIR the CLI's
    # `run` path reads through load_records()'s own default.
    import jev_stortinget.fetch as fetch_module

    monkeypatch.setattr(fetch_module, "RAW_DIR", raw_dir)

    out_dir = tmp_path / "out"
    fake = FakeJev(values={"asks_time": 0.9, "states_time": 0.9})

    main(["run", "--set", "all", "--out", str(out_dir)], ask=fake.ask)

    assert (out_dir / "all" / "results.jsonl").exists()
    assert (out_dir / "all" / "summary.json").exists()
    assert (out_dir / "all" / "ranking.json").exists()
    assert len(fake.calls) == 1
