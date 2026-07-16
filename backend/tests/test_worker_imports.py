"""Guard the taskiq worker's import order.

The worker entrypoint imports ``unstash.tasks`` first — an order nothing
else exercises. A fresh interpreter reproduces it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"


def test_tasks_package_imports_first_in_a_fresh_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import unstash.tasks"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(_SRC)},
    )
    assert result.returncode == 0, result.stderr
