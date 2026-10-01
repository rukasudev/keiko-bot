"""The release path: the runtime it builds and what it lets reach production.

The image, CI, the release check and the lint targets each named their own
Python, and the library under the bot was a discord.py master commit. These
tests read every place that names the runtime, so the image, the checks and
the suite cannot drift apart again.
"""
import re
import tomllib
from pathlib import Path

import discord
import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def _workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def _setup_python_versions(name):
    return {
        f"{name} {job_name}": step["with"]["python-version"]
        for job_name, job in _workflow(name)["jobs"].items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/setup-python")
    }


def test_every_place_that_names_the_python_version_names_the_same_one():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    image = re.search(r"^FROM python:(\S+)", (ROOT / "Dockerfile").read_text(), re.M)
    named = {
        "Dockerfile": image.group(1),
        "ruff": pyproject["tool"]["ruff"]["target-version"].replace("py3", "3."),
        "mypy": pyproject["tool"]["mypy"]["python_version"],
        **_setup_python_versions("ci.yml"),
        **_setup_python_versions("release.yml"),
    }

    assert len(named) == 5, named
    assert len(set(named.values())) == 1, named


def test_the_suite_runs_on_the_discord_py_release_the_image_installs():
    pins = [
        line
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.startswith("discord.py")
    ]

    assert len(pins) == 1 and re.fullmatch(r"discord\.py==\d+\.\d+\.\d+", pins[0]), pins
    assert pins[0] == f"discord.py=={discord.__version__}", (
        "the virtualenv runs another discord.py than the image: reinstall requirements"
    )
