"""Unit tests for ChronoFlex timer application."""

import tkinter as tk
import threading
from unittest.mock import MagicMock

import pytest

from chronoflex import ChronoFlex, InvalidRangeError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def root():
    """Create a Tk root window for testing, destroyed after each test."""
    root = tk.Tk()
    root.withdraw()  # hide the window
    yield root
    root.destroy()


@pytest.fixture
def app(root):
    """Create a ChronoFlex instance with a real Tk root."""
    return ChronoFlex(root)


# ===========================================================================
# _format_time
# ===========================================================================

class TestFormatTime:
    """Tests for ChronoFlex._format_time."""

    def test_zero(self, app):
        assert app._format_time(0) == "00:00"

    def test_seconds_only(self, app):
        assert app._format_time(45) == "00:45"

    def test_one_minute(self, app):
        assert app._format_time(60) == "01:00"

    def test_minutes_and_seconds(self, app):
        # 5 minutes 30 seconds
        assert app._format_time(330) == "05:30"

    def test_one_hour(self, app):
        assert app._format_time(3600) == "01:00:00"

    def test_hours_minutes_seconds(self, app):
        # 2h 15m 7s
        assert app._format_time(2 * 3600 + 15 * 60 + 7) == "02:15:07"

    def test_one_day(self, app):
        assert app._format_time(86400) == "1d 00:00:00"

    def test_days_hours_minutes_seconds(self, app):
        # 3d 4h 5m 6s
        total = 3 * 86400 + 4 * 3600 + 5 * 60 + 6
        assert app._format_time(total) == "3d 04:05:06"

    def test_float_truncated_to_int(self, app):
        assert app._format_time(65.9) == "01:05"

    def test_large_value(self, app):
        # 100 days
        assert app._format_time(100 * 86400) == "100d 00:00:00"

    def test_59_seconds(self, app):
        assert app._format_time(59) == "00:59"

    def test_59_minutes_59_seconds(self, app):
        assert app._format_time(59 * 60 + 59) == "59:59"

    def test_23_hours_59_minutes_59_seconds(self, app):
        assert app._format_time(23 * 3600 + 59 * 60 + 59) == "23:59:59"


# ===========================================================================
# _sanitize_int
# ===========================================================================

class TestSanitizeInt:
    """Tests for ChronoFlex._sanitize_int."""

    def test_valid_integer_unchanged(self, app, root):
        entry = tk.Entry(root)
        entry.insert(0, "42")
        app._sanitize_int(entry)
        assert entry.get() == "42"

    def test_empty_string_becomes_zero(self, app, root):
        entry = tk.Entry(root)
        entry.insert(0, "")
        app._sanitize_int(entry)
        assert entry.get() == "0"

    def test_non_numeric_becomes_zero(self, app, root):
        entry = tk.Entry(root)
        entry.insert(0, "abc")
        app._sanitize_int(entry)
        assert entry.get() == "0"

    def test_float_string_becomes_zero(self, app, root):
        entry = tk.Entry(root)
        entry.insert(0, "3.14")
        app._sanitize_int(entry)
        assert entry.get() == "0"

    def test_negative_integer_unchanged(self, app, root):
        entry = tk.Entry(root)
        entry.insert(0, "-5")
        app._sanitize_int(entry)
        assert entry.get() == "-5"

    def test_whitespace_only_becomes_zero(self, app, root):
        entry = tk.Entry(root)
        entry.insert(0, "   ")
        app._sanitize_int(entry)
        assert entry.get() == "0"

    def test_leading_trailing_spaces_with_valid_int(self, app, root):
        entry = tk.Entry(root)
        entry.insert(0, "  10  ")
        app._sanitize_int(entry)
        assert entry.get() == "  10  "


# ===========================================================================
# _get_precise_seconds
# ===========================================================================

class TestGetPreciseSeconds:
    """Tests for ChronoFlex._get_precise_seconds."""

    def test_all_zeros(self, app):
        for key in app.precise_entries:
            app.precise_entries[key].delete(0, "end")
            app.precise_entries[key].insert(0, "0")
        assert app._get_precise_seconds() == 0

    def test_seconds_only(self, app):
        app.precise_entries["s"].delete(0, "end")
        app.precise_entries["s"].insert(0, "30")
        assert app._get_precise_seconds() == 30

    def test_minutes_only(self, app):
        app.precise_entries["m"].delete(0, "end")
        app.precise_entries["m"].insert(0, "5")
        assert app._get_precise_seconds() == 300

    def test_hours_only(self, app):
        app.precise_entries["h"].delete(0, "end")
        app.precise_entries["h"].insert(0, "2")
        assert app._get_precise_seconds() == 7200

    def test_days_only(self, app):
        app.precise_entries["d"].delete(0, "end")
        app.precise_entries["d"].insert(0, "1")
        assert app._get_precise_seconds() == 86400

    def test_combined_units(self, app):
        app.precise_entries["d"].delete(0, "end")
        app.precise_entries["d"].insert(0, "1")
        app.precise_entries["h"].delete(0, "end")
        app.precise_entries["h"].insert(0, "2")
        app.precise_entries["m"].delete(0, "end")
        app.precise_entries["m"].insert(0, "3")
        app.precise_entries["s"].delete(0, "end")
        app.precise_entries["s"].insert(0, "4")
        expected = 86400 + 2 * 3600 + 3 * 60 + 4
        assert app._get_precise_seconds() == expected

    def test_negative_values_clamped_to_zero(self, app):
        app.precise_entries["m"].delete(0, "end")
        app.precise_entries["m"].insert(0, "-5")
        assert app._get_precise_seconds() == 0

    def test_non_numeric_treated_as_zero(self, app):
        app.precise_entries["m"].delete(0, "end")
        app.precise_entries["m"].insert(0, "abc")
        assert app._get_precise_seconds() == 0

    def test_empty_entry_treated_as_zero(self, app):
        app.precise_entries["m"].delete(0, "end")
        app.precise_entries["m"].insert(0, "")
        assert app._get_precise_seconds() == 0


# ===========================================================================
# _clamp_random_range
# ===========================================================================

class TestClampRandomRange:
    """Tests for ChronoFlex._clamp_random_range."""

    def test_valid_range_returns_tuple(self, app):
        app.rand_min_entry.delete(0, "end")
        app.rand_min_entry.insert(0, "5")
        app.rand_max_entry.delete(0, "end")
        app.rand_max_entry.insert(0, "10")
        lo, hi = app._clamp_random_range()
        assert lo == 5
        assert hi == 10

    def test_updates_widgets_to_clamped_values(self, app):
        app.rand_min_entry.delete(0, "end")
        app.rand_min_entry.insert(0, "0")
        app.rand_max_entry.delete(0, "end")
        app.rand_max_entry.insert(0, "100")
        app._clamp_random_range()
        assert app.rand_min_entry.get() == "1"
        assert app.rand_max_entry.get() == "60"

    def test_min_greater_than_max_swapped(self, app):
        app.rand_min_entry.delete(0, "end")
        app.rand_min_entry.insert(0, "30")
        app.rand_max_entry.delete(0, "end")
        app.rand_max_entry.insert(0, "5")
        lo, hi = app._clamp_random_range()
        assert lo == 5
        assert hi == 30

    def test_invalid_min_raises_error(self, app):
        app.rand_min_entry.delete(0, "end")
        app.rand_min_entry.insert(0, "abc")
        with pytest.raises(InvalidRangeError):
            app._clamp_random_range()

    def test_invalid_max_raises_error(self, app):
        app.rand_max_entry.delete(0, "end")
        app.rand_max_entry.insert(0, "xyz")
        with pytest.raises(InvalidRangeError):
            app._clamp_random_range()

    def test_empty_entries_use_defaults(self, app):
        app.rand_min_entry.delete(0, "end")
        app.rand_min_entry.insert(0, "")
        app.rand_max_entry.delete(0, "end")
        app.rand_max_entry.insert(0, "")
        lo, hi = app._clamp_random_range()
        assert lo >= 1
        assert hi <= 60


# ===========================================================================
# _get_random_seconds
# ===========================================================================

class TestGetRandomSeconds:
    """Tests for ChronoFlex._get_random_seconds (pure computation)."""

    def test_returns_seconds_and_message(self, app):
        total, msg = app._get_random_seconds(5, 10)
        assert isinstance(total, int)
        assert total >= 5 * 60
        assert total <= 10 * 60
        assert "Random pick" in msg

    def test_single_value_range(self, app):
        total, msg = app._get_random_seconds(7, 7)
        assert total == 7 * 60
        assert "7 minutes" in msg

    def test_singular_minute(self, app):
        total, msg = app._get_random_seconds(1, 1)
        assert total == 60
        assert "1 minute" in msg
        assert "minutes" not in msg

    def test_message_contains_range(self, app):
        _, msg = app._get_random_seconds(3, 9)
        assert "(3–9)" in msg


# ===========================================================================
# InvalidRangeError
# ===========================================================================

class TestInvalidRangeError:
    """Tests for the InvalidRangeError exception class."""

    def test_is_value_error(self):
        assert issubclass(InvalidRangeError, ValueError)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(InvalidRangeError, match="bad range"):
            raise InvalidRangeError("bad range")

    def test_message_preserved(self):
        try:
            raise InvalidRangeError("test message")
        except InvalidRangeError as e:
            assert str(e) == "test message"
