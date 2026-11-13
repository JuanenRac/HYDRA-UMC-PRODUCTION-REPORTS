# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/losses.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Where the planned time went: the OEE of a window broken down into seconds.

OEE says how much of the planned time was fully productive; this module says where the rest went, using only the numbers the OEE report
already holds (it adds no new reading):

* `availability_loss_s` - planned time with no cycle running (stops, waiting, no work).
* `speed_loss_s` - operating time spent beyond the ideal cycle time (slow cycles).
* `quality_loss_s` - the ideal time that went into parts that were not good.
* `productive_s` - the ideal time that went into good parts; it equals `planned_time_s * oee`.

When the report is consistent the four add up to the planned time. When it is not (`inconsistent_input`, more operating time than planned) the
operating time is capped at the planned time first, so the breakdown never exceeds the window, and `consistent` is false to say that the
numbers cannot be taken at face value.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .oee import OEEError, OEEReport


@dataclass(frozen=True)
class LossBreakdown:
    planned_time_s: float
    availability_loss_s: float
    speed_loss_s: float
    quality_loss_s: float
    productive_s: float
    consistent: bool

    def fractions(self) -> dict[str, float]:
        """Each part as a share of the planned time (0.0 to 1.0)."""
        return {
            "availability_loss": self.availability_loss_s / self.planned_time_s,
            "speed_loss": self.speed_loss_s / self.planned_time_s,
            "quality_loss": self.quality_loss_s / self.planned_time_s,
            "productive": self.productive_s / self.planned_time_s,
        }

    def largest_loss(self) -> str | None:
        """The name of the biggest of the three losses, or None when there is no loss at all."""
        losses = {"availability": self.availability_loss_s, "speed": self.speed_loss_s, "quality": self.quality_loss_s}
        name = max(losses, key=lambda key: losses[key])
        return name if losses[name] > 0.0 else None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = asdict(self)
        out["fractions"] = self.fractions()
        out["largest_loss"] = self.largest_loss()
        return out


def compute_loss_breakdown(report: OEEReport, *, planned_time_s: float, ideal_cycle_time_s: float) -> LossBreakdown:
    """The breakdown of [report] against the planned time and the ideal cycle time it was computed with."""
    if not math.isfinite(planned_time_s) or planned_time_s <= 0:
        raise OEEError("planned_time_s must be a finite, positive number")
    if not math.isfinite(ideal_cycle_time_s) or ideal_cycle_time_s <= 0:
        raise OEEError("ideal_cycle_time_s must be a finite, positive number")
    operating = min(report.operating_time_s, planned_time_s)
    ideal_total = ideal_cycle_time_s * report.total_count
    net = min(ideal_total, operating)
    quality_loss = net * (1.0 - report.quality)
    return LossBreakdown(
        planned_time_s=planned_time_s,
        availability_loss_s=planned_time_s - operating,
        speed_loss_s=operating - net,
        quality_loss_s=quality_loss,
        productive_s=net - quality_loss,
        consistent=not report.inconsistent_input,
    )
