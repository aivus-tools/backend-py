"""Regression guard: core.llm must not import google.genai at module load."""

from __future__ import annotations

import subprocess
import sys


def test_llm_import_does_not_eagerly_load_google_genai():
    """google.genai builds large pydantic schemas (~13s) on import; doing that at
    module load blocks the gunicorn --preload WSGI boot and starves the /healthz
    probe during rolling deploys. core.llm must defer it to the first real Gemini
    call. A fresh interpreter proves the import stays lazy.
    """
    code = (
        "import sys; import aivus_backend.core.llm; "
        "sys.exit(0 if 'google.genai' not in sys.modules else 1)"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        "google.genai was imported at core.llm load time "
        f"(stdout={result.stdout!r} stderr={result.stderr!r})"
    )
