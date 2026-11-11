"""The memory soak runs only when asked for: `make test`, the CI and the gate never
pick it up, whatever marker expression they pass, and `make soak` runs it."""

import configparser
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_in_root(*arguments):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/behavioral/soak",
            "-q",
            "-p",
            "no:cacheprovider",
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout


def test_the_default_run_deselects_the_soak():
    ini = configparser.ConfigParser()
    ini.read(os.path.join(ROOT, "pytest.ini"))

    assert '-m "not soak"' in ini["pytest"]["addopts"]
    assert "soak:" in ini["pytest"]["markers"]
    assert "1 deselected" in pytest_in_root("--collect-only")


def test_another_marker_expression_still_skips_the_soak():
    assert "1 skipped" in pytest_in_root("-m", "not integration")


def test_make_soak_runs_the_soak_through_the_tool():
    with open(os.path.join(ROOT, "Makefile"), encoding="utf-8") as handle:
        makefile = handle.read()

    assert "soak:\n\t$(ACTIVATE_VENV) && python -m tools.keiko soak" in makefile
