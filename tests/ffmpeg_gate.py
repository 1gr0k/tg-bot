"""Тесты с реальными ffmpeg/ffprobe: локально без них — пропуск, в CI — провал.

`REQUIRE_FFMPEG=1` (задаётся в ci.yml, job `test`) превращает пропуск в падение: иначе CI без
установленного ffmpeg оставался бы зелёным, а проверка с реальным ffprobe — ни разу не запущенной.
"""

import os
import shutil

import pytest


def require_ffmpeg() -> None:
    """Вызывается в начале теста: нет ffmpeg или ffprobe — skip, а при `REQUIRE_FFMPEG=1` — fail."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if not missing:
        return
    reason = f"нет в PATH: {', '.join(missing)}"
    if os.environ.get("REQUIRE_FFMPEG") == "1":
        pytest.fail(f"REQUIRE_FFMPEG=1, но {reason}", pytrace=False)
    pytest.skip(reason)
