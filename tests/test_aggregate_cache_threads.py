# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_aggregate_cache_threads.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""The server answers several reports at once with one client: the cache of aggregates is shared by those threads and
must neither fail nor lose count when it is full and every thread asks for another window."""
from __future__ import annotations

import threading

from hydra_umc_production_reports.datalake_client import DatalakeClient

SERIES = [{"sourceId": "robot-1", "field": "value", "count": 3, "firstTimestamp": 1, "lastTimestamp": 9}]


def make_client() -> DatalakeClient:
    client = DatalakeClient("http://127.0.0.1:1", cache_ttl_seconds=0.0, cache_entries=2)

    def fake_get(path: str, query: dict) -> object:
        if path == "/series":
            return SERIES
        return [{"bucketStart": query["start"], "value": 1.0, "count": 1}]

    client._get = fake_get  # type: ignore[method-assign]
    return client


def test_many_threads_asking_for_different_windows_never_fail_and_the_counters_add_up() -> None:
    client = make_client()
    failures: list[BaseException] = []
    rounds, workers = 200, 8

    def work(number: int) -> None:
        try:
            for turn in range(rounds):
                # half of the asks repeat a window (a hit), half are new ones (a miss that evicts)
                start = (turn % 5) if turn % 2 else number * 1000 + turn
                client.aggregate(source_id="robot-1", kind="k", field="value", start=start, before=start + 10, bucket_ms=10)
        except BaseException as error:  # noqa: BLE001 - the test reports whatever a thread raised
            failures.append(error)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not failures, failures[:1]
    assert client.cache_hits + client.cache_misses == rounds * workers
    assert len(client._cache) <= 2


def test_the_cache_is_only_touched_while_its_lock_is_held() -> None:
    """Deterministic form of the same rule: every read, write and eviction happens under the lock."""
    from collections import OrderedDict

    client = make_client()
    client.cache_ttl_seconds = 1000.0
    touched: list[str] = []

    class Watched(OrderedDict):
        def _check(self, what: str) -> None:
            assert client._cache_lock.locked(), f"{what} without the lock"
            touched.append(what)

        def get(self, *args, **kwargs):  # type: ignore[override]
            self._check("get")
            return super().get(*args, **kwargs)

        def move_to_end(self, *args, **kwargs):  # type: ignore[override]
            self._check("move_to_end")
            return super().move_to_end(*args, **kwargs)

        def popitem(self, *args, **kwargs):  # type: ignore[override]
            self._check("popitem")
            return super().popitem(*args, **kwargs)

        def __setitem__(self, key, value):  # type: ignore[override]
            self._check("set")
            super().__setitem__(key, value)

    client._cache = Watched()  # type: ignore[assignment]
    for start in (1, 2, 3, 3, 1):
        client.aggregate(source_id="robot-1", kind="k", field="value", start=start, before=start + 10, bucket_ms=10)
    assert {"get", "set", "move_to_end", "popitem"} <= set(touched)
