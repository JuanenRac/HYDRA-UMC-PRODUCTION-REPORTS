# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/shift.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real shift/day-boundary computation - a single source of truth for
where one calendar day or shift ends and the next begins, so two reports
built from the same real DATALAKE history can't silently disagree about
the window they're both supposedly describing. A zone is either a fixed
UTC offset (minutes east of UTC) or a named IANA zone (`Europe/Madrid`):
with a name the day and the shifts follow the local clock, so the day a
clock change happens is really 23 or 25 hours long and a report plans
exactly that much time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_EPOCH = date(1970, 1, 1)
MS_PER_HOUR = 3_600_000
MS_PER_DAY = 24 * MS_PER_HOUR


class ShiftError(ValueError):
    """A real, specific reason a shift/day window couldn't be computed."""


MAX_TIMEZONE_OFFSET_MINUTES = 14 * 60


def check_timezone_offset(minutes: int) -> None:
    """Refuse an offset that no place on Earth has (UTC-12 to UTC+14 and a margin)."""
    if abs(minutes) > MAX_TIMEZONE_OFFSET_MINUTES:
        raise ShiftError(f"the timezone offset must be between -{MAX_TIMEZONE_OFFSET_MINUTES} and {MAX_TIMEZONE_OFFSET_MINUTES} minutes")


def load_zone(name: str) -> ZoneInfo:
    """The named IANA zone, or a ShiftError that says the name is not known."""
    if not isinstance(name, str) or not name or len(name) > 64 or name.startswith(("/", ".")) or ".." in name:
        raise ShiftError("the timezone must be an IANA zone name such as Europe/Madrid")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ShiftError(f"unknown timezone {name!r}: use an IANA zone name such as Europe/Madrid") from None


def local_wall_to_utc_ms(zone: ZoneInfo, day: date, minutes_into_day: int) -> int:
    """UTC epoch ms of the wall-clock moment `minutes_into_day` after the local
    midnight that opens `day` (it may go past 24 h, into the next day). A time
    skipped by a clock change resolves to the instant the clock would have
    shown; a repeated one resolves to its first occurrence."""
    naive = datetime(day.year, day.month, day.day) + timedelta(minutes=minutes_into_day)
    local = naive.replace(tzinfo=zone, fold=0)
    delta = local.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1000


def day_window_ms(day: date, *, timezone_offset_minutes: int = 0, timezone_name: str | None = None) -> tuple[int, int]:
    """Real UTC-ms [start, end) boundaries for one calendar day, local to
    a fixed timezone offset (minutes east of UTC - negative is west) or, with
    `timezone_name`, to a named zone whose day lasts 23 or 25 hours on the
    day of a clock change."""
    if timezone_name:
        zone = load_zone(timezone_name)
        return local_wall_to_utc_ms(zone, day, 0), local_wall_to_utc_ms(zone, day, 24 * 60)
    check_timezone_offset(timezone_offset_minutes)
    local_midnight_ms = (day - _EPOCH).days * MS_PER_DAY
    start_ms = local_midnight_ms - timezone_offset_minutes * 60_000
    return start_ms, start_ms + MS_PER_DAY


@dataclass(frozen=True)
class ShiftSchedule:
    """One plant/cell's real shift definition - the source of truth line
    the diagnosis notes call for, so every report built
    against it agrees on where a shift starts and ends."""

    shift_length_hours: float
    first_shift_start_hour: float  # local hour-of-day, [0, 24), shift 0 starts here
    timezone_offset_minutes: int = 0
    timezone_name: str | None = None  # an IANA zone; when set it replaces the fixed offset

    def __post_init__(self) -> None:
        check_timezone_offset(self.timezone_offset_minutes)
        if self.timezone_name:
            load_zone(self.timezone_name)
        if not (0 < self.shift_length_hours <= 24):
            raise ShiftError("shift_length_hours must be in (0, 24]")
        if not (0 <= self.first_shift_start_hour < 24):
            raise ShiftError("first_shift_start_hour must be in [0, 24)")

    @property
    def shift_length_minutes(self) -> int:
        """The shift length in whole minutes. Floating-point hours cannot be
        trusted to divide a day exactly (24 % 4.8 is not 0 in floating
        point although five shifts of 4 h 48 min fill a day), so every
        division below is done on whole minutes."""
        return round(self.shift_length_hours * 60)

    @property
    def shifts_per_day(self) -> int:
        """Only defined when the shift length evenly divides 24h - a
        caller relying on a fixed shift count for an uneven schedule is a
        real configuration bug, not something to silently round."""
        minutes = self.shift_length_minutes
        if minutes <= 0 or (24 * 60) % minutes != 0:
            raise ShiftError("shift_length_hours must evenly divide 24 to have a fixed shifts_per_day")
        return (24 * 60) // minutes


def shift_window_ms(schedule: ShiftSchedule, day: date, shift_index: int) -> tuple[int, int]:
    """Real UTC-ms [start, end) for shift number `shift_index` (0-based)
    of the given calendar day - correctly crosses midnight into the next
    calendar day when the schedule defines a real night shift (e.g.
    22:00-06:00)."""
    if shift_index < 0:
        raise ShiftError("shift_index must be >= 0")
    if schedule.timezone_name:
        zone = load_zone(schedule.timezone_name)
        first = round(schedule.first_shift_start_hour * 60)
        length = schedule.shift_length_minutes
        return (
            local_wall_to_utc_ms(zone, day, first + shift_index * length),
            local_wall_to_utc_ms(zone, day, first + (shift_index + 1) * length),
        )
    day_start_ms, _ = day_window_ms(day, timezone_offset_minutes=schedule.timezone_offset_minutes)
    shift_length_ms = schedule.shift_length_minutes * 60_000
    first_start_ms = day_start_ms + round(schedule.first_shift_start_hour * MS_PER_HOUR)
    start_ms = first_start_ms + shift_index * shift_length_ms
    return start_ms, start_ms + shift_length_ms


def shift_index_for_timestamp(schedule: ShiftSchedule, timestamp_ms: int) -> tuple[date, int]:
    """Real inverse of shift_window_ms: which calendar day and 0-based
    shift index a real timestamp falls into. Only meaningful for a
    `shift_index` within `schedule.shifts_per_day` for that day - the
    schedule must evenly divide 24h (see `shifts_per_day`)."""
    count = schedule.shifts_per_day  # raises ShiftError for an uneven schedule
    if schedule.timezone_name:
        zone = load_zone(schedule.timezone_name)
        local = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).astimezone(zone)
        first = round(schedule.first_shift_start_hour * 60)
        guess = (local.replace(tzinfo=None) - timedelta(minutes=first)).date()
        for candidate in (guess - timedelta(days=1), guess, guess + timedelta(days=1)):
            for index in range(count):
                start, end = shift_window_ms(schedule, candidate, index)
                if start <= timestamp_ms < end:
                    return candidate, index
        raise ShiftError("the timestamp falls in no shift of the schedule")
    shift_length_ms = schedule.shift_length_minutes * 60_000
    first_start_ms = round(schedule.first_shift_start_hour * MS_PER_HOUR)
    local_ms = timestamp_ms + schedule.timezone_offset_minutes * 60_000
    shifted = local_ms - first_start_ms
    day_index = shifted // MS_PER_DAY  # Python floor division: correct for negative `shifted` too
    within_day_ms = shifted - day_index * MS_PER_DAY
    shift_index = int(within_day_ms // shift_length_ms)
    return _EPOCH + timedelta(days=day_index), shift_index
