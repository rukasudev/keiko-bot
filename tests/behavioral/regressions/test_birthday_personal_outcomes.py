"""The personal /birthday says in the log how it ended.

Reported from the log channel: the command posted a "Command Run" whose only
line was that it started. Nothing said whether the birthday was registered,
replaced or refused, and the replacement ran after the command's trace had
closed, so it left no trace at all.

Guaranteed: every outcome writes one line naming it, never the date, and sets
the Result; a refusal names its reason; replacing a saved birthday is a traced
unit of work of its own; and the trace says whether the person is an admin.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app import logger as logger_module
from app.constants import Commands
from app.data import birthdays as birthdays_data
from app.data import moderations as moderations_data
from app.services import reminders_birthdays as birthdays_service
from app.services import trace as trace_service

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "1532291713580925048"
USER_ID = 777


@pytest.fixture
def closed_traces():
    trace_service.clear_sinks()
    captured = []
    trace_service.register_sink(captured.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    yield captured
    logger_module.logger.removeHandler(folding)
    trace_service.clear_sinks()


@pytest.fixture
def enabled(deps, monkeypatch):
    moderations_data.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, Commands.REMINDERS_BIRTHDAY_KEY: True}
    )
    birthdays_data.upsert_birthday_config(
        GUILD_ID, channel_id="99", timezone="UTC", notification_time="14:00"
    )
    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder", lambda *a, **kw: "1"
    )
    monkeypatch.setattr(
        birthdays_service.reminders_service,
        "cleanup_reminder_if_unused",
        lambda *a, **kw: None,
    )


def interaction_for(admin=False):
    return SimpleNamespace(
        locale=discord.Locale.brazil_portuguese,
        command=SimpleNamespace(qualified_name="birthday"),
        guild=SimpleNamespace(id=int(GUILD_ID)),
        guild_id=int(GUILD_ID),
        user=SimpleNamespace(
            id=USER_ID, guild_permissions=SimpleNamespace(administrator=admin)
        ),
        response=SimpleNamespace(
            send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def run(interaction, month, day):
    from app.cogs.birthdays import Birthday

    await Birthday.birthday_personal.callback(SimpleNamespace(), interaction, month, day)


def lines(trace):
    return [line["message"] for line in trace.lines]


async def test_a_registered_birthday_says_so_in_its_trace(enabled, closed_traces):
    await run(interaction_for(), 3, 14)

    trace = closed_traces[-1]
    assert trace.result == "registered"
    assert any("birthday registered" in line for line in lines(trace))
    assert not any("03-14" in line for line in lines(trace)), "a date is personal"


async def test_a_refused_birthday_names_the_reason_not_the_date(deps, closed_traces):
    await run(interaction_for(), 2, 31)

    trace = closed_traces[-1]
    assert trace.result == "refused"
    assert any("refused: invalid-date" in line for line in lines(trace))


async def test_a_server_without_birthdays_refuses_with_its_reason(deps, closed_traces):
    await run(interaction_for(), 3, 14)

    trace = closed_traces[-1]
    assert trace.result == "refused"
    assert any("refused: reminders-birthdays-disabled" in line for line in lines(trace))


async def test_replacing_a_birthday_is_its_own_traced_unit_of_work(enabled, closed_traces):
    await run(interaction_for(), 3, 14)
    ask = interaction_for()
    await run(ask, 4, 20)

    asked = closed_traces[-1]
    assert asked.result == "asked"

    view = ask.followup.send.await_args.kwargs["view"]
    await view.confirm.callback(interaction_for())

    replaced = closed_traces[-1]
    assert replaced is not asked and replaced.result == "replaced"
    assert any("birthday replaced" in line for line in lines(replaced))
    assert birthdays_data.find_birthday_item(GUILD_ID, str(USER_ID))["date"] == "04-20"


async def test_the_trace_says_whether_the_person_is_an_admin(deps, closed_traces):
    await run(interaction_for(admin=True), 2, 31)

    assert closed_traces[-1].is_admin is True
