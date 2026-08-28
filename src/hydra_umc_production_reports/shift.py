# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/shift.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real shift/day-boundary computation - a single source of truth for
where one calendar day or shift ends and the next begins, so two reports
built from the same real DATALAKE history can't silently disagree about
the window they're both supposedly describing. DST transitions are not
handled - this project's timezones are fixed UTC offsets, documented as a
real limitation in mejoras_futuras.txt rather than silently assumed away.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

_EPOCH = date(1970, 1, 1)
MS_PER_HOUR = 3_600_000
MS_PER_DAY = 24 * MS_PER_HOUR


class ShiftError(ValueError):
    """A real, specific reason a shift/day window couldn't be computed."""


def day_window_ms(day: date, *, timezone_offset_minutes: int = 0) -> tuple[int, int]:
    """Real UTC-ms [start, end) boundaries for one calendar day, local to
    a fixed timezone offset (minutes east of UTC - negative is west)."""
    local_midnight_ms = (day - _EPOCH).days * MS_PER_DAY
    start_ms = local_midnight_ms - timezone_offset_minutes * 60_000
    return start_ms, start_ms + MS_PER_DAY


@dataclass(frozen=True)
class ShiftSchedule:
    """One plant/cell's real shift definition - the source of truth line
    651-652 of the promotion audit calls for, so every report built
    against it agrees on where a shift starts and ends."""

    shift_length_hours: float
    first_shift_start_hour: float  # local hour-of-day, [0, 24), shift 0 starts here
    timezone_offset_minutes: int = 0

    def __post_init__(self) -> None:
        if not (0 < self.shift_length_hours <= 24):
            raise ShiftError("shift_length_hours must be in (0, 24]")
        if not (0 <= self.first_shift_start_hour < 24):
            raise ShiftError("first_shift_start_hour must be in [0, 24)")

    @property
    def shifts_per_day(self) -> int:
        """Only defined when shift_length_hours evenly divides 24h - a
        caller relying on a fixed shift count for an uneven schedule is a
        real configuration bug, not something to silently round."""
        if (24 % self.shift_length_hours) != 0:
            raise ShiftError("shift_length_hours must evenly divide 24 to have a fixed shifts_per_day")
        return int(24 // self.shift_length_hours)


def shift_window_ms(schedule: ShiftSchedule, day: date, shift_index: int) -> tuple[int, int]:
    """Real UTC-ms [start, end) for shift number `shift_index` (0-based)
    of the given calendar day - correctly crosses midnight into the next
    calendar day when the schedule defines a real night shift (e.g.
    22:00-06:00)."""
    if shift_index < 0:
        raise ShiftError("shift_index must be >= 0")
    day_start_ms, _ = day_window_ms(day, timezone_offset_minutes=schedule.timezone_offset_minutes)
    shift_length_ms = round(schedule.shift_length_hours * MS_PER_HOUR)
    first_start_ms = day_start_ms + round(schedule.first_shift_start_hour * MS_PER_HOUR)
    start_ms = first_start_ms + shift_index * shift_length_ms
    return start_ms, start_ms + shift_length_ms


def shift_index_for_timestamp(schedule: ShiftSchedule, timestamp_ms: int) -> tuple[date, int]:
    """Real inverse of shift_window_ms: which calendar day and 0-based
    shift index a real timestamp falls into. Only meaningful for a
    `shift_index` within `schedule.shifts_per_day` for that day - the
    schedule must evenly divide 24h (see `shifts_per_day`)."""
    schedule.shifts_per_day  # raises ShiftError for an uneven schedule
    shift_length_ms = round(schedule.shift_length_hours * MS_PER_HOUR)
    first_start_ms = round(schedule.first_shift_start_hour * MS_PER_HOUR)
    local_ms = timestamp_ms + schedule.timezone_offset_minutes * 60_000
    shifted = local_ms - first_start_ms
    day_index = shifted // MS_PER_DAY  # Python floor division: correct for negative `shifted` too
    within_day_ms = shifted - day_index * MS_PER_DAY
    shift_index = int(within_day_ms // shift_length_ms)
    return _EPOCH + timedelta(days=day_index), shift_index
