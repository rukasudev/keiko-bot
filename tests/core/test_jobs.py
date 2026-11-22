"""Keiko's own loops: what is due by now reaches the command that owns its kind, each
kind on its own, the longest overdue first, and nothing is handed early."""

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.constants import Commands
from app.core.commands import Command
from app.core.jobs import hand_due_schedules
from app.data import data
from app.data.keiko.schedules import schedule_id

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

Handler = Callable[[Sequence[Mapping[str, Any]]], Any]


def recorder(handed: dict[str, list[list[str]]], kind: str) -> Handler:
    async def due(schedules: Sequence[Mapping[str, Any]]) -> None:
        handed.setdefault(kind, []).append([schedule["_id"] for schedule in schedules])

    return due


async def save(kind: str, subject: str, due_at: datetime) -> None:
    await data().keiko_schedules.upsert_schedule(
        schedule_id(kind, subject), kind, None, due_at=due_at
    )


async def test_the_schedules_due_by_now_reach_their_command_oldest_first(declare):
    handed: dict[str, list[list[str]]] = {}
    declare(
        Command("lives", schedules={"live": recorder(handed, "live")}),
        Command("roles", schedules={"role": recorder(handed, "role")}),
    )
    await save("live", "later", NOW + timedelta(minutes=1))
    await save("live", "now", NOW)
    await save("live", "late", NOW - timedelta(hours=1))
    await save("role", "due", NOW - timedelta(minutes=1))
    await save("birthday", "unowned", NOW - timedelta(days=1))

    await hand_due_schedules(NOW)

    assert handed == {"live": [["live:late", "live:now"]], "role": [["role:due"]]}


async def test_a_pass_hands_at_most_its_limit_and_leaves_the_rest_for_the_next(
    declare, monkeypatch
):
    handed: dict[str, list[list[str]]] = {}
    declare(Command("lives", schedules={"live": recorder(handed, "live")}))
    monkeypatch.setattr(Commands, "SCHEDULES_DUE_LIMIT", 2)
    for minutes in (3, 2, 1):
        await save("live", f"{minutes}", NOW - timedelta(minutes=minutes))

    await hand_due_schedules(NOW)

    assert handed == {"live": [["live:3", "live:2"]]}


async def test_a_kind_whose_command_fails_never_stops_the_next(declare, caplog):
    handed: dict[str, list[list[str]]] = {}

    async def broken(_schedules: Sequence[Mapping[str, Any]]) -> None:
        raise RuntimeError("the live could not be read")

    declare(
        Command("lives", schedules={"live": broken}),
        Command("roles", schedules={"role": recorder(handed, "role")}),
    )
    await save("live", "1", NOW)
    await save("role", "1", NOW)

    with caplog.at_level(logging.ERROR):
        await hand_due_schedules(NOW)

    assert handed == {"role": [["role:1"]]}
    assert any("due live failed" in record.getMessage() for record in caplog.records)


async def test_nothing_is_read_while_no_command_runs_a_kind(declare, monkeypatch):
    read: list[Any] = []

    async def find_due_schedules(*args: Any) -> list[Any]:
        read.append(args)
        return []

    declare(Command("plain"))
    monkeypatch.setattr(
        data().keiko_schedules, "find_due_schedules", find_due_schedules
    )

    await hand_due_schedules(NOW)

    assert read == []
