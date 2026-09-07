# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_shift.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from datetime import date

import pytest

from hydra_umc_production_reports.shift import (
    MS_PER_DAY,
    ShiftError,
    ShiftSchedule,
    day_window_ms,
    shift_index_for_timestamp,
    shift_window_ms,
)


def test_day_window_ms_is_exactly_24h_wide() -> None:
    start, end = day_window_ms(date(2026, 8, 28))
    assert end - start == MS_PER_DAY


def test_day_window_ms_shifts_with_timezone_offset() -> None:
    utc_start, _ = day_window_ms(date(2026, 8, 28))
    east_start, _ = day_window_ms(date(2026, 8, 28), timezone_offset_minutes=120)
    west_start, _ = day_window_ms(date(2026, 8, 28), timezone_offset_minutes=-300)
    # East of UTC: local midnight happens EARLIER in UTC time.
    assert east_start == utc_start - 120 * 60_000
    # West of UTC: local midnight happens LATER in UTC time.
    assert west_start == utc_start + 300 * 60_000


def test_shift_schedule_rejects_out_of_range_length() -> None:
    with pytest.raises(ShiftError):
        ShiftSchedule(shift_length_hours=0.0, first_shift_start_hour=0.0)
    with pytest.raises(ShiftError):
        ShiftSchedule(shift_length_hours=25.0, first_shift_start_hour=0.0)


def test_shift_schedule_rejects_out_of_range_start_hour() -> None:
    with pytest.raises(ShiftError):
        ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=24.0)
    with pytest.raises(ShiftError):
        ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=-1.0)


def test_shifts_per_day_for_a_clean_3x8_schedule() -> None:
    schedule = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=6.0)
    assert schedule.shifts_per_day == 3


def test_shifts_per_day_raises_for_an_uneven_schedule() -> None:
    # 7h doesn't evenly divide 24h - a real config bug, not silently rounded.
    schedule = ShiftSchedule(shift_length_hours=7.0, first_shift_start_hour=0.0)
    with pytest.raises(ShiftError):
        _ = schedule.shifts_per_day


def test_night_shift_genuinely_crosses_midnight() -> None:
    # 3x8 schedule starting at 06:00: shift 2 (22:00-06:00) must extend
    # PAST this calendar day's own boundary into the next real day.
    day = date(2026, 8, 28)
    schedule = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=6.0)
    _, day_end_ms = day_window_ms(day)
    shift2_start_ms, shift2_end_ms = shift_window_ms(schedule, day, 2)
    assert shift2_start_ms < day_end_ms  # starts before midnight (22:00)
    assert shift2_end_ms > day_end_ms  # ends after midnight (06:00 next day)
    assert shift2_end_ms - shift2_start_ms == 8 * 60 * 60 * 1000


def test_consecutive_shifts_have_no_gap_or_overlap() -> None:
    day = date(2026, 8, 28)
    schedule = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=6.0)
    _, shift0_end = shift_window_ms(schedule, day, 0)
    shift1_start, _ = shift_window_ms(schedule, day, 1)
    assert shift0_end == shift1_start


def test_shift_index_for_timestamp_round_trips_through_shift_window_ms() -> None:
    # For every real shift of a real (timezone-shifted) schedule, a
    # timestamp drawn from the middle of that shift's own window must
    # resolve back to the exact same (day, shift_index).
    day = date(2026, 8, 28)
    schedule = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=6.0, timezone_offset_minutes=120)
    for shift_index in range(schedule.shifts_per_day):
        start_ms, end_ms = shift_window_ms(schedule, day, shift_index)
        midpoint_ms = (start_ms + end_ms) // 2
        got_day, got_shift_index = shift_index_for_timestamp(schedule, midpoint_ms)
        assert got_day == day
        assert got_shift_index == shift_index


def test_shift_index_for_timestamp_rejects_uneven_schedule() -> None:
    schedule = ShiftSchedule(shift_length_hours=5.0, first_shift_start_hour=0.0)
    with pytest.raises(ShiftError):
        shift_index_for_timestamp(schedule, 0)


def test_shift_window_ms_rejects_negative_shift_index() -> None:
    schedule = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=0.0)
    with pytest.raises(ShiftError):
        shift_window_ms(schedule, date(2026, 8, 28), -1)
