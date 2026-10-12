"""Все тесты из `tests/smoke/` — дымовые, даже если в файле забыли `pytestmark`.

Без этого новый файл без маркера попал бы в обычный прогон и CI (реальная сеть, cookies).
"""

from pathlib import Path

import pytest

_SMOKE_DIR = Path(__file__).parent.resolve()


@pytest.hookimpl(tryfirst=True)  # до отбора по `-m 'not smoke'`
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Хук conftest получает элементы всей сессии, а не только своего каталога.
    for item in items:
        if _SMOKE_DIR in Path(item.path).resolve().parents:
            item.add_marker(pytest.mark.smoke)
