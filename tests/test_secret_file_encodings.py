# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_secret_file_encodings.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""A secret file saved by an editor of Windows with a byte order mark holds the same secret; a file in another encoding is refused, never guessed."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hydra_umc_production_reports import main as cli

SECRET = "s3cret-0123456789-abcdefghij-0123456789"


class SecretFileEncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.bom = Path(folder.name, "bom.token")
        self.bom.write_bytes(b"\xef\xbb\xbf" + SECRET.encode("ascii") + b"\r\n")  # what Notepad calls "UTF-8 with BOM"
        self.utf16 = Path(folder.name, "utf16.token")
        self.utf16.write_bytes((SECRET + "\r\n").encode("utf-16"))  # what `>` of Windows PowerShell writes

    def _run(self, path: Path) -> tuple[int | None, list[str], str]:
        class Started(Exception):
            pass

        seen: list[str] = []

        def server(*_args, **kwargs):
            seen.append(kwargs["job_dispatcher_token"])
            raise Started

        errors = io.StringIO()
        code = None
        with mock.patch.object(cli, "ReportsServer", server), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            try:
                code = cli.main(["--job-dispatcher-token-file", str(path)])
            except Started:
                pass
        return code, seen, errors.getvalue()

    def test_the_dispatcher_token_ignores_a_byte_order_mark(self) -> None:
        self.assertEqual(self._run(self.bom)[1], [SECRET])

    def test_the_dispatcher_token_refuses_another_encoding(self) -> None:
        code, seen, errors = self._run(self.utf16)
        self.assertEqual((code, seen), (2, []))
        self.assertIn("--job-dispatcher-token-file", errors)


if __name__ == "__main__":
    unittest.main()
