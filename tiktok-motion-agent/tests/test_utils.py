"""Tests for pipeline.utils."""

import datetime as dt
from zoneinfo import ZoneInfo

from pipeline.utils import (
    now_utc, iso, sheet_col, safe_name, safe_cache_name,
    parse_hhmm, indonesia_pretty_datetime, parse_indonesia_pretty_datetime,
    entry_duration_seconds,
)


def test_now_utc_has_timezone():
    ts = now_utc()
    assert ts.tzinfo is not None


def test_iso_drops_microseconds():
    ts = dt.datetime(2026, 1, 15, 12, 30, 45, 123456, tzinfo=dt.timezone.utc)
    result = iso(ts)
    assert ".123456" not in result
    assert "2026-01-15T12:30:45" in result


def test_sheet_col_basic():
    assert sheet_col(0) == "A"
    assert sheet_col(1) == "B"
    assert sheet_col(25) == "Z"
    assert sheet_col(26) == "AA"
    assert sheet_col(27) == "AB"


def test_safe_name_sanitizes():
    assert safe_name("hello world!@#") == "hello-world"
    assert safe_name("file.txt") == "file.txt"
    assert safe_name("") == "file"


def test_safe_cache_name():
    assert safe_cache_name("https://example.com/@user") == "https_example.com_user"
    assert safe_cache_name("") == "default"


def test_parse_hhmm_valid():
    assert parse_hhmm("08:30") == (8, 30)
    assert parse_hhmm("0:00") == (0, 0)
    assert parse_hhmm("23:59") == (23, 59)


def test_parse_hhmm_invalid():
    try:
        parse_hhmm("25:00")
        assert False, "Should have raised"
    except RuntimeError:
        pass


def test_indonesia_pretty_datetime_format():
    ts = dt.datetime(2026, 5, 15, 10, 30, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    result = indonesia_pretty_datetime(ts)
    assert result == "2026-05-15 10:30 WIB"


def test_parse_indonesia_pretty_datetime_roundtrip():
    ts = dt.datetime(2026, 5, 15, 10, 30, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    pretty = indonesia_pretty_datetime(ts)
    parsed = parse_indonesia_pretty_datetime(pretty)
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 5
    assert parsed.day == 15


def test_parse_indonesia_pretty_datetime_iso():
    result = parse_indonesia_pretty_datetime("2026-05-15T10:30:00+07:00")
    assert result is not None
    assert result.year == 2026


def test_parse_indonesia_pretty_datetime_empty():
    assert parse_indonesia_pretty_datetime("") is None
    assert parse_indonesia_pretty_datetime(None) is None


def test_entry_duration_seconds():
    assert entry_duration_seconds({"duration": 15.5}) == 15.5
    assert entry_duration_seconds({"duration": "30"}) == 30.0
    assert entry_duration_seconds({}) is None
