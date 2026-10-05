"""The errors report of the logs tool counts every record at its level and above.

Reported in the v1 review: `logs errors` grouped only the records whose level was
exactly ERROR, so the CRITICAL a refused start logs ("Discord refused to start the
bot", `lifecycle._report`) never appeared in the default report, the one record that
says the bot is not running at all.

Shared behaviour: `store.signatures` and the `errors` subcommand of `tools/keiko/logs`.

Guaranteed: the default report holds ERROR and CRITICAL; `--level WARNING` adds the
warnings; nothing below the level asked for appears.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.constants import DBConfigs  # noqa: E402
from tools.keiko.logs import cli, store  # noqa: E402

pytestmark = pytest.mark.unit

REFUSED_START = f"{DBConfigs.FATAL_START_PHRASE}: LoginFailure: Improper token has been passed."


@pytest.fixture
def connection(tmp_path):
    connection = store.connect(str(tmp_path / "logs.db"))
    store.insert_entries(connection, [
        {"ts": "2026-10-01T10:00:00", "level": "CRITICAL", "message": REFUSED_START},
        {"ts": "2026-10-01T10:01:00", "level": "ERROR", "message": "Connection refused"},
        {"ts": "2026-10-01T10:02:00", "level": "WARNING", "message": "Slow reply"},
        {"ts": "2026-10-01T10:03:00", "level": "INFO", "message": "All good"},
    ], "day.log")
    return connection


def signatures(connection, **options):
    return sorted(row["signature"] for row in store.signatures(connection, **options))


def test_the_default_report_holds_a_refused_start(connection):
    assert signatures(connection) == sorted([REFUSED_START, "Connection refused"])


def test_a_lower_level_adds_what_is_above_it_too(connection):
    assert signatures(connection, level="WARNING") == sorted(
        [REFUSED_START, "Connection refused", "Slow reply"]
    )


def test_the_errors_command_asks_for_error_and_above_by_default(tmp_path, capsys):
    path = str(tmp_path / "logs.db")
    store.insert_entries(store.connect(path), [
        {"ts": "2026-10-01T10:00:00", "level": "CRITICAL", "message": REFUSED_START},
    ], "day.log")

    assert cli.main(["logs", "--db", path, "errors"]) == 0

    assert DBConfigs.FATAL_START_PHRASE in capsys.readouterr().out
