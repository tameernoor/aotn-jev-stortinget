import json
from pathlib import Path

from jev_stortinget.sets import DEV_SIZE, HOLDOUT_SIZE, draw_sets, write_sets

SESSION = "2024-2025"


def _pair_payload(question_id: int, reply: str | None = "A real reply with actual content.") -> dict:
    return {
        "id": question_id,
        "sporsmal_nummer": question_id,
        "sporsmal_til_minister_tittel": f"minister-{question_id % 3}",
        "besvart_av_minister_tittel": f"minister-{question_id % 3}",
        "sporsmal_fra": {
            "fornavn": "Test",
            "etternavn": f"Person{question_id}",
            "parti": {"id": "X"},
        },
        "datert_dato": "/Date(1759234887245+0200)/",
        "besvart_dato": "/Date(1759989109780+0200)/",
        "sporsmal": f"Question number {question_id}?",
        "begrunnelse": None,
        "svar": reply,
    }


def _write_cache(raw_dir: Path, payloads: list[dict]) -> None:
    cache_dir = raw_dir / SESSION
    cache_dir.mkdir(parents=True)
    for payload in payloads:
        (cache_dir / f"{payload['id']}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_draw_sets_is_deterministic(tmp_path):
    payloads = [_pair_payload(i) for i in range(1, 101)]
    _write_cache(tmp_path, payloads)

    dev1, holdout1 = draw_sets(session=SESSION, raw_dir=tmp_path)
    dev2, holdout2 = draw_sets(session=SESSION, raw_dir=tmp_path)

    assert dev1 == dev2
    assert holdout1 == holdout2


def test_draw_sets_has_the_right_sizes_and_dev_holdout_are_disjoint(tmp_path):
    payloads = [_pair_payload(i) for i in range(1, 101)]
    _write_cache(tmp_path, payloads)

    dev, holdout = draw_sets(session=SESSION, raw_dir=tmp_path)

    assert len(dev) == DEV_SIZE
    assert len(holdout) == HOLDOUT_SIZE
    dev_ids = {item["id"] for item in dev}
    holdout_ids = {item["id"] for item in holdout}
    assert len(dev_ids) == DEV_SIZE
    assert len(holdout_ids) == HOLDOUT_SIZE
    assert dev_ids.isdisjoint(holdout_ids)


def test_draw_sets_excludes_pairs_with_an_empty_reply(tmp_path):
    eligible = [_pair_payload(i) for i in range(1, 81)]
    empty = [
        _pair_payload(i, reply=reply)
        for i, reply in zip(range(1000, 1010), ["", None, "<br/>", "   ", "<p></p>"] * 2)
    ]
    _write_cache(tmp_path, eligible + empty)

    dev, holdout = draw_sets(session=SESSION, raw_dir=tmp_path)

    drawn_ids = {item["id"] for item in dev} | {item["id"] for item in holdout}
    excluded_ids = {p["id"] for p in empty}
    assert drawn_ids.isdisjoint(excluded_ids)
    assert len(dev) == DEV_SIZE
    assert len(holdout) == HOLDOUT_SIZE


def test_draw_sets_item_shape(tmp_path):
    payloads = [_pair_payload(i) for i in range(1, 101)]
    _write_cache(tmp_path, payloads)

    dev, holdout = draw_sets(session=SESSION, raw_dir=tmp_path)

    for item in dev + holdout:
        assert set(item.keys()) == {"id", "ministry", "question", "reply"}
        assert item["question"]
        assert item["reply"]


def test_write_sets_writes_readable_unicode_with_indent(tmp_path):
    dev = [{"id": 1, "ministry": "finansministeren", "question": "Hva skål vi gjøre?", "reply": "Svær"}]
    holdout: list[dict] = []

    write_sets(dev, holdout, sets_dir=tmp_path)

    dev_text = (tmp_path / "dev.json").read_text(encoding="utf-8")
    holdout_text = (tmp_path / "holdout.json").read_text(encoding="utf-8")
    assert "\\u" not in dev_text
    assert "Hva skål vi gjøre?" in dev_text
    assert dev_text.startswith("[\n  {\n")
    assert json.loads(dev_text) == dev
    assert json.loads(holdout_text) == []
