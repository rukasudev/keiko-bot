"""The release path: the runtime it builds and what it lets reach production.

The image, CI, the release check and the lint targets each named their own
Python, and the library under the bot was a discord.py master commit. These
tests read every place that names the runtime, so the image, the checks and
the suite cannot drift apart again.
"""

import re
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
import yaml

from app.constants import DBConfigs

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


def _deploy():
    return _workflow("release.yml")["jobs"]["deploy"]


def _deploy_script():
    return "\n".join(step.get("run", "") for step in _deploy()["steps"])


def test_a_pre_release_never_reaches_production():
    jobs = _workflow("release.yml")["jobs"]

    assert set(jobs) == {"check", "build", "deploy"}
    assert "!github.event.release.prerelease" in jobs["check"]["if"], (
        "a beta published as a pre-release would deploy"
    )
    assert jobs["build"]["needs"] == "check" and "if" not in jobs["build"], (
        "the build is skipped with the check, never by a condition of its own"
    )
    assert jobs["deploy"]["needs"] == "build"
    assert "needs.build.result == 'success'" in jobs["deploy"]["if"], (
        "the deploy follows a build that succeeded"
    )
    assert "github.event_name == 'workflow_dispatch'" in jobs["deploy"]["if"], (
        "or a version run again by hand, which builds nothing"
    )


def test_the_deploy_lets_the_bot_stop_before_removing_it():
    script = _deploy_script()

    assert re.search(r'docker stop -t "\$STOP_GRACE_SECONDS" keiko-bot', script)
    assert "docker rm -f" not in script, "a SIGKILL loses the queued events and logs"
    assert script.index("docker stop") < script.index("docker rm")
    assert '--stop-timeout "$STOP_GRACE_SECONDS"' in script
    assert int(_deploy()["env"]["STOP_GRACE_SECONDS"]) > 0


def test_the_container_answers_only_on_localhost_and_inside_a_memory_cap():
    script = _deploy_script()

    assert re.findall(r"-p (\S+)", script) == [
        "127.0.0.1:5000:5000",
        "127.0.0.1:8000:8000",
    ], "nginx and Prometheus reach the bot through localhost; nobody else should"
    assert '--memory "$MEMORY_LIMIT"' in script
    assert '--memory-swap "$MEMORY_LIMIT"' in script, "no swap beyond the cap"
    assert re.fullmatch(r"\d+[mg]", _deploy()["env"]["MEMORY_LIMIT"])


def test_the_container_is_told_the_version_it_runs():
    workflow = _workflow("release.yml")

    assert '-e APP_VERSION="$VERSION"' in _deploy_script()
    assert "github.event.release.tag_name" in workflow["env"]["VERSION"]
    assert "inputs.version" in workflow["env"]["VERSION"]


FAKE_DOCKER = r"""#!/bin/bash
polls=$(cat "$FAKE_STATE/polls")
case "$1" in
  logs)
    echo $(( $(cat "$FAKE_STATE/reads") + 1 )) > "$FAKE_STATE/reads"
    if [ -n "$LOGS_FAIL" ]; then
      echo "Error response from daemon: No such container: keiko-bot" >&2
      exit 1
    fi
    echo "[2026-10-01 00:00:01] [INFO    ] root: MongoDB: OK"
    if [ -n "$REFUSED_AT" ] && [ "$polls" -ge "$REFUSED_AT" ]; then
      echo "[2026-10-01 00:00:05] [CRITICAL] root: $LOGGED_REFUSED: LoginFailure:" \
        "Improper token has been passed."
      seq -f "traceback line %g ........................................" 1 6000
    fi
    if [ -n "$READY_AT" ] && [ "$polls" -ge "$READY_AT" ]; then
      echo "[2026-10-01 00:00:09] [INFO    ] root: $LOGGED_READY"
    fi
    ;;
  inspect)
    restarts=0
    if [ -n "$RESTART_AT" ] && [ "$polls" -ge "$RESTART_AT" ]; then restarts=1; fi
    case "$3" in
      "{{.RestartCount}}") echo "$restarts" ;;
      "{{.State.Status}} {{.RestartCount}}") echo "running $restarts" ;;
      *) echo "unexpected inspect format: $3" >&2; exit 2 ;;
    esac
    ;;
  *) echo "unexpected docker call: $*" >&2; exit 2 ;;
esac
"""

FAKE_SLEEP = r"""#!/bin/bash
echo $(( $(cat "$FAKE_STATE/polls") + 1 )) > "$FAKE_STATE/polls"
echo $(( $(cat "$FAKE_STATE/slept") + $1 )) > "$FAKE_STATE/slept"
"""


def _check_step():
    steps = _deploy()["steps"]
    [check] = [step for step in steps if step["name"] == "Check the bot stayed up"]
    return check


@pytest.fixture
def deploy_check(tmp_path):
    """Runs the check step's own script with `bash -e`, as the runner does for a step
    with no `shell:`, against a fake `docker` and `sleep` on PATH. The fake bot logs
    the phrases of the constants; `sleep` only counts polls and seconds."""
    step = _check_step()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("docker", FAKE_DOCKER), ("sleep", FAKE_SLEEP)):
        (bin_dir / name).write_text(source)
        (bin_dir / name).chmod(0o755)

    def run(flags=("-e",), **scenario):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        for counter in ("polls", "slept", "reads"):
            (state / counter).write_text("0")
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_STATE": str(state),
            "LOGGED_READY": DBConfigs.READY_PHRASE,
            "LOGGED_REFUSED": DBConfigs.FATAL_START_PHRASE,
            **{name: str(value) for name, value in step.get("env", {}).items()},
            **{name: str(value) for name, value in scenario.items()},
        }
        result = subprocess.run(
            ["bash", *flags, "-c", step["run"]],
            env=env, capture_output=True, text=True, timeout=30,
        )
        return SimpleNamespace(
            code=result.returncode,
            out=result.stdout,
            polls=int((state / "polls").read_text()),
            slept=int((state / "slept").read_text()),
            reads=int((state / "reads").read_text()),
        )

    return run


def test_the_check_waits_for_less_than_a_refused_start_waits_to_restart():
    env = _check_step()["env"]
    settle = int(env["SETTLE_SECONDS"])
    deadline = int(env["READY_DEADLINE_SECONDS"])

    assert env["READY"] == DBConfigs.READY_PHRASE
    assert env["REFUSED_START"] == DBConfigs.FATAL_START_PHRASE
    assert 30 <= settle < deadline < DBConfigs.FATAL_START_BACKOFF_SECONDS


def test_the_check_passes_once_the_bot_is_ready_and_has_stayed_up(deploy_check):
    result = deploy_check(READY_AT=1)

    assert result.code == 0, result.out
    assert result.out.rstrip().endswith("running 0"), result.out
    assert result.slept >= int(_check_step()["env"]["SETTLE_SECONDS"]), (
        "it watches the restart count for as long as the old 30 s wait did"
    )


@pytest.mark.parametrize(
    "flags", [("-e",), ("-e", "-o", "pipefail")], ids=["errexit", "pipefail"]
)
def test_the_check_fails_at_once_on_a_refused_start(deploy_check, flags):
    """The refused line is followed by 300 KiB of traceback, so `grep -m 1` stops
    reading early; with pipefail that kills the writer, which `|| true` absorbs."""
    result = deploy_check(flags, REFUSED_AT=1)

    assert result.code == 1
    assert f"{DBConfigs.FATAL_START_PHRASE}: LoginFailure" in result.out, result.out
    assert "docker stop keiko-bot" in result.out
    assert result.polls == 1, "on the poll that saw the line"


def test_the_check_fails_on_a_restart_before_the_bot_is_ready(deploy_check):
    result = deploy_check(RESTART_AT=2)

    assert result.code == 1
    assert "restarted 1 time" in result.out and "MongoDB: OK" in result.out, result.out


def test_the_check_fails_on_a_restart_right_after_the_bot_is_ready(deploy_check):
    """A crash right after READY (the events cog loads more cogs then, and the memory
    cap is not confirmed yet) failed the old 30 s check and must fail this one."""
    result = deploy_check(READY_AT=0, RESTART_AT=3)

    assert result.code == 1, result.out
    assert "restarted 1 time" in result.out


def test_the_check_fails_at_the_deadline_with_the_last_lines(deploy_check):
    deadline = int(_check_step()["env"]["READY_DEADLINE_SECONDS"])

    result = deploy_check()

    assert result.code == 1
    assert f"not ready after {deadline} s" in result.out and "MongoDB: OK" in result.out
    assert result.slept == deadline
    assert result.reads == result.polls + 1, "the log is read once per poll"


def test_the_check_says_so_when_docker_logs_itself_fails(deploy_check):
    result = deploy_check(LOGS_FAIL=1)

    assert result.code == 1
    assert (
        "docker logs keiko-bot failed: Error response from daemon: "
        "No such container: keiko-bot" in result.out
    ), result.out
