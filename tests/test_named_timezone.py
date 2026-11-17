# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_named_timezone.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""A named zone makes days and shifts follow the local clock, clock changes included."""
from __future__ import annotations

from datetime import date

import pytest

from hydra_umc_production_reports.api import ParamError, _window
from hydra_umc_production_reports.scheduled import ScheduleError, parse_config, period_window
from hydra_umc_production_reports.shift import (
    MS_PER_HOUR,
    ShiftError,
    ShiftSchedule,
    day_window_ms,
    shift_index_for_timestamp,
    shift_window_ms,
)

MADRID = "Europe/Madrid"


def _hours(window: tuple[int, int]) -> float:
    return (window[1] - window[0]) / MS_PER_HOUR


def test_an_ordinary_day_is_24_hours_and_starts_at_local_midnight() -> None:
    start, end = day_window_ms(date(2026, 1, 15), timezone_name=MADRID)
    assert _hours((start, end)) == 24
    # Madrid is UTC+1 in January: its midnight is 23:00 UTC of the day before.
    assert start == day_window_ms(date(2026, 1, 15), timezone_offset_minutes=60)[0]


def test_the_day_clocks_go_forward_lasts_23_hours_and_the_day_they_go_back_25() -> None:
    assert _hours(day_window_ms(date(2026, 3, 29), timezone_name=MADRID)) == 23
    assert _hours(day_window_ms(date(2026, 10, 25), timezone_name=MADRID)) == 25
    assert _hours(day_window_ms(date(2026, 10, 26), timezone_name=MADRID)) == 24


def test_a_fixed_offset_still_gives_24_hours_on_those_days() -> None:
    assert _hours(day_window_ms(date(2026, 3, 29), timezone_offset_minutes=60)) == 24


def test_consecutive_days_leave_no_gap_and_no_overlap_across_the_change() -> None:
    first = day_window_ms(date(2026, 3, 28), timezone_name=MADRID)
    second = day_window_ms(date(2026, 3, 29), timezone_name=MADRID)
    third = day_window_ms(date(2026, 3, 30), timezone_name=MADRID)
    assert first[1] == second[0] and second[1] == third[0]


def test_shifts_start_at_local_hours_and_the_shift_over_the_change_is_shorter_or_longer() -> None:
    schedule = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=6.0, timezone_name=MADRID)
    spring = [_hours(shift_window_ms(schedule, date(2026, 3, 29), i)) for i in range(3)]
    assert sum(spring) == 24 and sorted(spring) == [8, 8, 8]  # 06:00 begins after the change
    night = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=22.0, timezone_name=MADRID)
    assert _hours(shift_window_ms(night, date(2026, 3, 28), 0)) == 7  # 22:00 to 06:00 across the change
    assert _hours(shift_window_ms(night, date(2026, 10, 24), 0)) == 9


def test_every_instant_lands_in_exactly_one_shift_across_both_changes() -> None:
    schedule = ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=22.0, timezone_name=MADRID)
    for day in (date(2026, 3, 28), date(2026, 3, 29), date(2026, 10, 24), date(2026, 10, 25)):
        for index in range(3):
            start, end = shift_window_ms(schedule, day, index)
            for probe in (start, start + 1, end - 1):
                assert shift_index_for_timestamp(schedule, probe) == (day, index)


def test_the_zone_works_west_of_utc_and_with_half_hour_offsets() -> None:
    assert _hours(day_window_ms(date(2026, 3, 8), timezone_name="America/New_York")) == 23
    assert _hours(day_window_ms(date(2026, 11, 1), timezone_name="America/New_York")) == 25
    assert _hours(day_window_ms(date(2026, 6, 1), timezone_name="Asia/Kolkata")) == 24


def test_an_unknown_or_hostile_zone_name_is_refused() -> None:
    for bad in ("Mars/Olympus", "../etc/passwd", "/etc/localtime", ".hidden", "x" * 80):
        with pytest.raises(ShiftError):
            day_window_ms(date(2026, 1, 1), timezone_name=bad)
        with pytest.raises(ShiftError):
            ShiftSchedule(shift_length_hours=8.0, first_shift_start_hour=0.0, timezone_name=bad)


def test_the_request_window_takes_a_zone_and_plans_the_real_hours() -> None:
    start, end, planned = _window({"day": "2026-03-29", "timezone": MADRID})
    assert planned == 23 * 3600 and end - start == 23 * 3600 * 1000
    start, end, planned = _window({"day": "2026-10-25", "timezone": MADRID})
    assert planned == 25 * 3600
    _, _, shift_planned = _window({"day": "2026-03-28", "timezone": MADRID, "shift": "0", "firstShiftStartHour": "22"})
    assert shift_planned == 7 * 3600


def test_the_request_refuses_a_zone_together_with_an_offset_and_a_bad_zone() -> None:
    with pytest.raises(ParamError):
        _window({"day": "2026-03-29", "timezone": MADRID, "tzOffsetMinutes": "60"})
    with pytest.raises(ParamError):
        _window({"day": "2026-03-29", "timezone": "Nowhere/Land"})


def test_the_scheduled_configuration_takes_a_zone_and_period_windows_follow_it() -> None:
    config = parse_config({"timezone": MADRID})
    assert config.timezone == MADRID
    start, end = period_window("daily", date(2026, 3, 30), timezone_name=MADRID)
    assert _hours((start, end)) == 23  # the day that ended at that midnight was 29 March
    with pytest.raises(ScheduleError):
        parse_config({"timezone": MADRID, "timezone_offset_minutes": 60})
    with pytest.raises(ScheduleError):
        parse_config({"timezone": "Nowhere/Land"})
    with pytest.raises(ScheduleError):
        parse_config({"timezone": 5})
