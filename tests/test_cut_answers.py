# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_cut_answers.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Answers of the job dispatcher cut in the middle. urlopen is replaced, so nothing leaves the machine."""
from __future__ import annotations

import http.client
import io
import urllib.request

import pytest

from hydra_umc_production_reports.cells import CellMapError, dispatcher_jobs, robot_cells


class CutAnswer(io.BytesIO):
    """An answer that stops in the middle of its body."""

    status = 200

    def read(self, *args):
        raise http.client.IncompleteRead(b"{", 100)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("read", [robot_cells, dispatcher_jobs])
def test_a_cut_answer_is_a_message(monkeypatch, read):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: CutAnswer())
    with pytest.raises(CellMapError, match="could not be read"):
        read("http://127.0.0.1:1")
