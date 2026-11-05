# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_server_identity.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The identity of HYDRA-UMC-SERVER (server_identity.py): off by default; on, nothing is open, not even the health path."""
from __future__ import annotations

import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from hydra_umc_production_reports import server_identity
from hydra_umc_production_reports.api import ReportsServer
from hydra_umc_production_reports.datalake_client import DatalakeClient

KEY = b"a-signing-key-of-the-server-0123456789"


@contextmanager
def running(key: bytes | None, health_open: bool = False) -> Iterator[str]:
    server = ReportsServer(("127.0.0.1", 0), DatalakeClient("http://127.0.0.1:1"), identity_key=key)
    server.health_open = health_open
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _status(url: str, bearer: str | None = None) -> int:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"} if bearer else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def test_off_by_default_the_stats_are_open_on_loopback() -> None:
    with running(None) as base:
        assert _status(base + "/stats") == 200


def test_with_the_identity_on_not_even_stats_health_or_metrics_are_open() -> None:
    with running(KEY, health_open=True) as base:
        for path in ("/stats", "/health", "/metrics"):
            assert _status(base + path) == 401, path
        viewer = server_identity.sign_token(KEY, role="viewer")
        assert _status(base + "/stats", viewer) == 200
        assert _status(base + "/health", viewer) == 200
        assert _status(base + "/stats", server_identity.sign_token(b"another-key-of-more-than-32-characters!")) == 401
        assert _status(base + "/stats", server_identity.sign_token(KEY, ttl_seconds=-5)) == 401


def test_the_probe_of_a_container_signs_its_own_token(tmp_path: Path) -> None:
    key_file = tmp_path / "identity.key"
    key_file.write_text(KEY.decode(), encoding="utf-8")
    with running(KEY, health_open=True) as base:
        port = int(base.rsplit(":", 1)[1])
        assert server_identity.probe(port, "/health", str(key_file)) is True
        assert server_identity.probe(port, "/health", None) is False  # no key file: no credential
    assert server_identity.ENV_IDENTITY_KEY_FILE == "HYDRA_UMC_PRODUCTION_REPORTS_IDENTITY_KEY_FILE"


def test_the_copy_of_the_shared_module_is_the_canonical_one() -> None:
    canonical = Path(__file__).resolve().parents[2] / "HYDRA-UMC-SDK" / "tools" / "canonical" / "server_identity.py"
    if not canonical.is_file():
        pytest.skip("the SDK checkout is not next to this repository")
    assert Path(server_identity.__file__).read_bytes().replace(b"\r\n", b"\n") == canonical.read_bytes().replace(b"\r\n", b"\n")
