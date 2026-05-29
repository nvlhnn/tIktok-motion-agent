"""Tests for pipeline.config."""

import os

from pipeline.config import (
    int_env, truthy_env,
    COLUMNS, STATUS_VALUES, STATUS_COLORS,
    ACTIVE_STATUSES, TERMINAL_STATUSES,
)


def test_int_env_default(monkeypatch):
    monkeypatch.delenv("TEST_INT_VAR", raising=False)
    assert int_env("TEST_INT_VAR", 42) == 42


def test_int_env_set(monkeypatch):
    monkeypatch.setenv("TEST_INT_VAR", "99")
    assert int_env("TEST_INT_VAR", 42) == 99


def test_int_env_empty_string(monkeypatch):
    monkeypatch.setenv("TEST_INT_VAR", "  ")
    assert int_env("TEST_INT_VAR", 42) == 42


def test_int_env_invalid(monkeypatch):
    monkeypatch.setenv("TEST_INT_VAR", "not_a_number")
    try:
        int_env("TEST_INT_VAR", 42)
        assert False, "Should have raised"
    except RuntimeError:
        pass


def test_truthy_env_default(monkeypatch):
    monkeypatch.delenv("TEST_BOOL_VAR", raising=False)
    assert truthy_env("TEST_BOOL_VAR", False) is False
    assert truthy_env("TEST_BOOL_VAR", True) is True


def test_truthy_env_true_values(monkeypatch):
    for val in ["1", "true", "True", "TRUE", "yes", "y", "on"]:
        monkeypatch.setenv("TEST_BOOL_VAR", val)
        assert truthy_env("TEST_BOOL_VAR") is True, f"Expected True for {val!r}"


def test_truthy_env_false_values(monkeypatch):
    for val in ["0", "false", "no", "off", "random"]:
        monkeypatch.setenv("TEST_BOOL_VAR", val)
        assert truthy_env("TEST_BOOL_VAR") is False, f"Expected False for {val!r}"


def test_columns_has_required_fields():
    assert "job_id" in COLUMNS
    assert "status" in COLUMNS
    assert "created_at" in COLUMNS
    assert "caption" in COLUMNS
    assert "input_image_validation" in COLUMNS


def test_status_values_consistency():
    """All STATUS_COLORS keys must match STATUS_VALUES."""
    for status in STATUS_VALUES:
        assert status in STATUS_COLORS, f"Missing color for status: {status}"


def test_active_terminal_no_overlap():
    """Active and terminal statuses should not overlap."""
    overlap = ACTIVE_STATUSES & TERMINAL_STATUSES
    assert len(overlap) == 0, f"Overlapping statuses: {overlap}"


def test_all_statuses_categorized():
    """Every status should be in either active, terminal, or uncategorized (early stages)."""
    early = {"STARTED", "NEEDS_REFERENCE_IMAGE", "UPLOADING"}
    all_categorized = ACTIVE_STATUSES | TERMINAL_STATUSES | early
    for status in STATUS_VALUES:
        assert status in all_categorized, f"Uncategorized status: {status}"
