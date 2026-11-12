# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_dockerfile.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# The Dockerfile agrees with the rest of the repository: the program it starts is the one the package installs, it
# copies files that exist, listens on the port of the systemd unit, runs without root and keeps the token out of the
# image. Docker itself is not needed.
# =============================================================================
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
INSTRUCTIONS = [line for line in DOCKERFILE.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_the_entrypoint_is_the_script_of_the_package() -> None:
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    entry = next(line for line in INSTRUCTIONS if line.startswith("ENTRYPOINT"))
    assert re.search(r'\["([^"]+)"\]', entry).group(1) in scripts


def test_everything_it_copies_exists() -> None:
    for line in INSTRUCTIONS:
        if line.startswith("COPY "):
            *sources, _target = line.split()[1:]
            for source in sources:
                assert (ROOT / source).exists(), source


def test_it_listens_on_the_port_of_the_service_unit_and_every_interface() -> None:
    unit = next((ROOT / "systemd").glob("*.service")).read_text(encoding="utf-8")
    port = re.search(r"--port (\d+)", unit).group(1)
    assert f"EXPOSE {port}" in DOCKERFILE
    cmd = next(line for line in INSTRUCTIONS if line.startswith("CMD ["))
    assert '"--addr", "0.0.0.0"' in cmd and f'"--port", "{port}"' in cmd


def test_it_runs_without_root_and_holds_no_secret() -> None:
    assert any(line.startswith("USER ") and "root" not in line for line in INSTRUCTIONS)
    assert not any(line.startswith(("ENV ", "ARG ")) and "TOKEN" in line.upper() for line in INSTRUCTIONS)


def test_the_health_check_does_not_depend_on_a_closed_route() -> None:
    check = next(line for line in INSTRUCTIONS if line.startswith("HEALTHCHECK"))
    assert "/health" not in check and "create_connection" in check
