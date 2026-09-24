# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/provenance.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Where a report's numbers came from: which source and series, which time
window, how many samples, and which version of this program computed it."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provenance:
    source_id: str
    kind: str
    fields: tuple[str, ...]
    window_start_ms: int
    window_end_ms: int
    points_used: int
    generator_version: str
