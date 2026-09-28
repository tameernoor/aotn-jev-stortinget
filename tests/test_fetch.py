import json
from io import BytesIO
from pathlib import Path

import pytest

from jev_stortinget import fetch as fetch_module
from jev_stortinget.fetch import (
    FetchError,
    build_record,
    fetch_list,
    fetch_pairs,
    html_to_text,
    load_records,
    parse_stortinget_date,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return BytesIO(self._payload)

    def __exit__(self, *exc):
        return False


# --- html_to_text --------------------------------------------------------------------


def test_html_to_text_strips_a_non_paragraph_tag_and_unescapes_entities():
    raw = "Første  del.<b>Andre del</b> &amp; tredje &oslash;del."
    assert html_to_text(raw) == "Første del. Andre del & tredje ødel."


def test_html_to_text_does_not_glue_words_across_a_stripped_non_paragraph_tag():
    assert html_to_text("word<b>next") == "word next"


def test_html_to_text_turns_br_and_closing_p_into_a_newline():
    assert html_to_text("word<br/>next") == "word\nnext"
    assert html_to_text("word<br>next") == "word\nnext"
    assert html_to_text("word</p>next") == "word\nnext"


def test_html_to_text_keeps_paragraph_breaks_and_collapses_intraline_whitespace():
    raw = "  Para one   has  spaces.<br>Para two.<br/><br/><br/>Para three.</p>Para four.\t\tEnd.  "
    assert html_to_text(raw) == "Para one has spaces.\nPara two.\n\nPara three.\nPara four. End."


def test_html_to_text_on_a_real_reply_with_br_tags_leaves_no_tags():
    pair = _load_fixture("pair_with_begrunnelse.json")
    text = html_to_text(pair["svar"])
    assert "<" not in text
    assert ">" not in text
    assert "  " not in text
    assert "\n" in text
    assert text == text.strip()


@pytest.mark.parametrize("value", [None, "", "   ", "<br/>", "<p></p>"])
def test_html_to_text_handles_none_blank_and_all_tag_input(value):
    assert html_to_text(value) == ""


def test_html_to_text_keeps_a_literal_less_than_that_is_not_shaped_like_a_tag():
    # Real corpus cases (ids 101779, 104928, 106232, 98470): a bare "<" used as a
    # comparison operator, not entity-escaped. A greedy `<[^>]+>` tag regex would
    # treat the next unrelated ">" anywhere later in the text as this "tag"'s
    # close and delete everything in between; requiring a letter right after "<"
    # (or "</") avoids that.
    assert html_to_text("P/B<1 og >2") == "P/B<1 og >2"
    assert html_to_text("< 50 m") == "< 50 m"


# --- parse_stortinget_date ------------------------------------------------------------


def test_parse_stortinget_date_applies_the_embedded_offset():
    assert parse_stortinget_date("/Date(1759989109780+0200)/") == "2025-10-09"
    assert parse_stortinget_date("/Date(1759234887245+0200)/") == "2025-09-30"


def test_parse_stortinget_date_handles_a_negative_offset():
    # 2025-10-09T02:00:00Z: applying a -0500 offset lands at 2025-10-08T21:00, a
    # different calendar day from the UTC instant itself.
    assert parse_stortinget_date("/Date(1759975200000-0500)/") == "2025-10-08"


def test_parse_stortinget_date_returns_none_for_missing_or_unparseable_input():
    assert parse_stortinget_date(None) is None
    assert parse_stortinget_date("") is None
    assert parse_stortinget_date("not a date") is None


# --- build_record ----------------------------------------------------------------------


def test_build_record_from_a_pair_with_no_begrunnelse():
    pair = _load_fixture("pair_no_begrunnelse.json")
    record = build_record(pair)

    assert record.id == 108815
    assert record.number == 3234
    assert record.ministry == "finansministeren"
    assert record.answered_by == "finansministeren"
    assert record.mp_name == "Sivert Bjørnstad"
    assert record.mp_party == "FrP"
    assert record.asked_date == "2025-09-30"
    assert record.answered_date == "2025-10-09"
    assert record.reasoning == ""
    assert record.question.startswith("Jeg viser til svar")
    assert "<" not in record.reply


def test_build_record_from_a_pair_with_begrunnelse():
    pair = _load_fixture("pair_with_begrunnelse.json")
    record = build_record(pair)

    assert record.id == 108798
    assert record.reasoning != ""
    assert "<" not in record.reasoning
    assert record.reply != ""
    assert "<" not in record.reply


# --- fetch_list ------------------------------------------------------------------------


def test_fetch_list_returns_the_sporsmal_liste_items(monkeypatch):
    payload = json.dumps(_load_fixture("list_response.json")).encode("utf-8")
    monkeypatch.setattr(
        fetch_module.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(payload)
    )

    items = fetch_list(session="2024-2025")

    assert len(items) == 2
    assert {item["id"] for item in items} == {108815, 108814}


def test_fetch_list_raises_fetch_error_on_network_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", boom)

    with pytest.raises(FetchError):
        fetch_list()


# --- fetch_pairs / caching --------------------------------------------------------------


def test_fetch_pairs_writes_new_pairs_to_the_cache(tmp_path, monkeypatch):
    pair = _load_fixture("pair_no_begrunnelse.json")
    payload = json.dumps(pair).encode("utf-8")
    monkeypatch.setattr(
        fetch_module.urllib.request, "urlopen", lambda *a, **kw: _FakeResponse(payload)
    )

    summary = fetch_pairs([108815], session="2024-2025", raw_dir=tmp_path)

    assert summary.fetched == 1
    assert summary.already_cached == 0
    assert summary.ok == 1
    assert summary.failed_ids == ()
    cached_path = tmp_path / "2024-2025" / "108815.json"
    assert cached_path.exists()
    assert json.loads(cached_path.read_text(encoding="utf-8"))["id"] == 108815
    # The write is atomic (tmp file + os.replace): no leftover tmp file after a
    # clean write.
    assert not (tmp_path / "2024-2025" / "108815.json.tmp").exists()


def test_write_json_atomically_leaves_no_tmp_file_and_is_readable(tmp_path):
    path = tmp_path / "108815.json"
    fetch_module._write_json_atomically(path, {"id": 108815, "svar": "hello"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"id": 108815, "svar": "hello"}
    assert not (tmp_path / "108815.json.tmp").exists()


def test_fetch_pairs_makes_no_request_for_an_already_cached_id(tmp_path, monkeypatch):
    cache_dir = tmp_path / "2024-2025"
    cache_dir.mkdir(parents=True)
    pair = _load_fixture("pair_no_begrunnelse.json")
    (cache_dir / "108815.json").write_text(json.dumps(pair), encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("should not touch the network for an already-cached id")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", boom)

    summary = fetch_pairs([108815], session="2024-2025", raw_dir=tmp_path)

    assert summary.fetched == 0
    assert summary.already_cached == 1
    assert summary.ok == 1
    assert summary.failed_ids == ()


def test_fetch_pairs_reports_a_pair_that_fails_every_retry_without_writing_a_cache_file(
    tmp_path, monkeypatch
):
    def boom(*args, **kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", boom)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda *_: None)

    summary = fetch_pairs([999999], session="2024-2025", raw_dir=tmp_path)

    assert summary.fetched == 0
    assert summary.failed_ids == (999999,)
    assert not (tmp_path / "2024-2025" / "999999.json").exists()


def _http_error(code: int, headers: dict | None = None) -> fetch_module.urllib.error.HTTPError:
    return fetch_module.urllib.error.HTTPError(
        "https://data.stortinget.no/x", code, "simulated", headers or {}, None
    )


def test_fetch_pair_backs_off_after_a_429_then_succeeds(monkeypatch):
    pair = _load_fixture("pair_no_begrunnelse.json")
    payload = json.dumps(pair).encode("utf-8")
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429)
        return _FakeResponse(payload)

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda s: sleeps.append(s))

    result = fetch_module._fetch_pair(108815)

    assert result["id"] == 108815
    assert calls["n"] == 2
    # First sleep is the 429 backoff (RATE_LIMIT_BACKOFF_SECONDS[0]); the second is
    # the post-success request pacing.
    assert sleeps[0] == fetch_module.RATE_LIMIT_BACKOFF_SECONDS[0]
    assert sleeps[1] == fetch_module.REQUEST_PACING_SECONDS


def test_fetch_pair_honours_the_retry_after_header_over_the_default_schedule(monkeypatch):
    pair = _load_fixture("pair_no_begrunnelse.json")
    payload = json.dumps(pair).encode("utf-8")
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, headers={"Retry-After": "7"})
        return _FakeResponse(payload)

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda s: sleeps.append(s))

    fetch_module._fetch_pair(108815)

    assert sleeps[0] == 7.0


def test_fetch_pair_backs_off_on_a_5xx_the_same_way_as_a_429(monkeypatch):
    pair = _load_fixture("pair_no_begrunnelse.json")
    payload = json.dumps(pair).encode("utf-8")
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(503)
        return _FakeResponse(payload)

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda s: sleeps.append(s))

    fetch_module._fetch_pair(108815)

    assert sleeps[0] == fetch_module.RATE_LIMIT_BACKOFF_SECONDS[0]


def test_fetch_pairs_caches_a_pair_that_needed_one_429_retry(tmp_path, monkeypatch):
    pair = _load_fixture("pair_no_begrunnelse.json")
    payload = json.dumps(pair).encode("utf-8")
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429)
        return _FakeResponse(payload)

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda *_: None)

    summary = fetch_pairs([108815], session="2024-2025", raw_dir=tmp_path)

    assert summary.fetched == 1
    assert summary.failed_ids == ()
    assert (tmp_path / "2024-2025" / "108815.json").exists()


def test_max_workers_and_retry_schedule_match_the_rate_limit_fix():
    assert fetch_module.MAX_WORKERS == 2
    assert fetch_module.RATE_LIMIT_BACKOFF_SECONDS == (15.0, 30.0, 60.0, 120.0)
    assert fetch_module.REQUEST_PACING_SECONDS == 0.2


def test_fetch_pair_retries_before_succeeding(monkeypatch):
    calls = {"n": 0}
    pair = _load_fixture("pair_no_begrunnelse.json")
    payload = json.dumps(pair).encode("utf-8")

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("simulated transient failure")
        return _FakeResponse(payload)

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda *_: None)

    result = fetch_module._fetch_pair(108815)

    assert result["id"] == 108815
    assert calls["n"] == 3


# --- load_records ------------------------------------------------------------------------


def test_load_records_reads_the_cache_and_sorts_by_id(tmp_path):
    cache_dir = tmp_path / "2024-2025"
    cache_dir.mkdir(parents=True)
    for name in ("pair_no_begrunnelse.json", "pair_with_begrunnelse.json"):
        pair = _load_fixture(name)
        (cache_dir / f"{pair['id']}.json").write_text(json.dumps(pair), encoding="utf-8")

    records = load_records(session="2024-2025", raw_dir=tmp_path)

    assert [r.id for r in records] == sorted(r.id for r in records)
    assert {r.id for r in records} == {108815, 108798}


def test_load_records_makes_no_request(tmp_path, monkeypatch):
    cache_dir = tmp_path / "2024-2025"
    cache_dir.mkdir(parents=True)
    pair = _load_fixture("pair_no_begrunnelse.json")
    (cache_dir / "108815.json").write_text(json.dumps(pair), encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("load_records must never touch the network")

    monkeypatch.setattr(fetch_module.urllib.request, "urlopen", boom)

    records = load_records(session="2024-2025", raw_dir=tmp_path)

    assert len(records) == 1
