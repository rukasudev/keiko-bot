"""Run the memory soak (`tests/behavioral/soak`) for a number of iterations or minutes.

python -m tools.keiko soak                       # 600 iterations, about 3 minutes
python -m tools.keiko soak --minutes 600 --report soak.csv
"""

import argparse
import os
from typing import List, Optional

import pytest

SETTINGS = {
    "iterations": "KEIKO_SOAK_ITERATIONS",
    "minutes": "KEIKO_SOAK_MINUTES",
    "warmup": "KEIKO_SOAK_WARMUP",
    "checkpoint": "KEIKO_SOAK_CHECKPOINT",
    "max_bytes": "KEIKO_SOAK_MAX_BYTES",
    "report": "KEIKO_SOAK_REPORT",
}


def parse(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.keiko soak")
    parser.add_argument("--iterations", type=int, help="iterations after the warm-up")
    parser.add_argument("--minutes", type=float, help="run this long instead")
    parser.add_argument("--warmup", type=int, help="iterations before checkpoints")
    parser.add_argument("--checkpoint", type=int, help="iterations between checkpoints")
    parser.add_argument("--max-bytes", type=float, help="bytes kept per iteration")
    parser.add_argument("--report", help="CSV file the curve is written to")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    options = parse(argv or [])
    for name, variable in SETTINGS.items():
        value = getattr(options, name)
        if value is not None:
            os.environ[variable] = str(value)
    return int(
        pytest.main(
            [
                "tests/behavioral/soak",
                "-m",
                "soak",
                "-q",
                "-s",
                "-p",
                "no:cacheprovider",
                "-p",
                "no:logging",
                "-p",
                "no:warnings",
            ]
        )
    )
