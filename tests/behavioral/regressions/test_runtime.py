"""Keiko's runtime: the cogs' background loops, the bot's presence, and the prefix
commands the message listener answers, once per message at most.
"""
import ast
import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.app_commands import Choice
from discord.ext import commands, tasks

from app.commands.config.service import (
    update_activity,
    update_description,
    update_status,
)
from app.constants import Dependencies
from app.core.utils import extension_names, extension_paths, fill, ml
from app.integrations.stream_elements import StreamElementsClient
from tests.generators.moderations import recorded_server
from tests.mocks.database import fake_store
from tests.mocks.discord import create_guild, create_member, create_message

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("runtime"),
]


APP = Path(__file__).resolve().parents[3] / "app"


STARTERS = {"start", "create_task", "ensure_future"}


def _extensions():
    return list(
        dict.fromkeys(
            module for name in extension_names() for module in extension_paths(name)
        )
    )


def _loops(owner):
    return sorted(
        {
            name
            for klass in owner.__mro__
            for name, value in vars(klass).items()
            if isinstance(value, tasks.Loop)
        }
    )


def _owners():
    found = []
    for extension in _extensions():
        module = importlib.import_module(extension)
        for owner in vars(module).values():
            if (
                isinstance(owner, type)
                and issubclass(owner, commands.Cog)
                and owner.__module__ == extension
                and _loops(owner)
            ):
                found.append(pytest.param(extension, owner, id=owner.__qualname__))
    return found


OWNERS = _owners()


def _bot():
    bot = commands.Bot(
        command_prefix="!", intents=discord.Intents.none(), help_command=None
    )
    for name in ("config", "twitch", "youtube", "reminder", "notion"):
        setattr(bot, name, MagicMock())
    bot.app_commands = []
    return bot


def _task_names(owner):
    return {
        f"discord-ext-tasks: {getattr(owner, name).coro.__qualname__}"
        for name in _loops(owner)
    }


def _running(owner):
    names = _task_names(owner)
    return sorted(
        (
            task
            for task in asyncio.all_tasks()
            if task.get_name() in names and not task.done()
        ),
        key=asyncio.Task.get_name,
    )


async def _settled():
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _stops_without_waiting(monkeypatch):
    monkeypatch.setattr(Dependencies, "BLOCKING_IO_STOP_WAIT_SECONDS", 0.01)


def _sources():
    for path in sorted(APP.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _declares_a_loop(node):
    return any(
        ast.unparse(getattr(decorator, "func", decorator)) in {"tasks.loop", "loop"}
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in item.decorator_list
    )


def test_every_class_that_declares_a_loop_is_a_cog_an_extension_adds():
    """A cog starts its background loops when the bot loads it, never when it is built.

    Broke as: the /birthday cog (`app/commands/birthdays/cog.py`) started
    `reconcile_reminders` in its `__init__`, so a test that only built it (the slash
    command contract builds every cog on a bot that never logs in) left the loop running;
    its `before_loop` failed with "Client has not been properly initialised", and the
    never-retrieved failure was logged whenever the garbage collector ran, inside whatever
    test was running then: the Twitch fan-out tests, which assert no ERROR in their caplog,
    failed now and then. The analytics jobs (`app/core/jobs.py`) and the weekly digest
    (`app/commands/admin/digest.py`) still started theirs in `__init__`, the debug log
    writer (`app/core/debug_logs.py`) created its task in its constructor, and reloading
    `app.core.monitoring` failed: its unload leaves the library's `PrometheusCog` on the
    bot, so its setup added a second one and raised "already loaded".

    Shared behaviour: the loop lifecycle discord.py gives a cog, `cog_load` when the bot
    adds it and `cog_unload` when it removes it, the way `Events` and `Heartbeat` start
    theirs. Exposed by: the flaky Twitch tests of `tests/test_notifications_twitch.py`.

    Guaranteed, for every cog Keiko defines with a `tasks.loop`, found through every
    extension the bot can load (and every class under `app/` that declares one is such a
    cog): building the cog starts nothing; loading its extension,
    the production order (`setup_hook` -> `load_extension` -> `setup` -> `add_cog` ->
    `cog_load`), starts each of its loops once, a reload leaves exactly one of each
    running, and unloading stops them all. No constructor under `app/` starts a loop or
    creates a task.
    """
    declared = {
        node.name
        for _path, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _declares_a_loop(node)
    }

    assert declared and declared == {param.values[1].__name__ for param in OWNERS}


@pytest.mark.parametrize(("extension", "owner"), OWNERS)
async def test_building_a_cog_starts_none_of_its_loops(extension, owner):
    async with _bot() as bot:
        cog = owner(bot)
        await _settled()
        started = [name for name in _loops(owner) if getattr(cog, name).is_running()]
        running = _running(owner)
        for task in running:
            task.cancel()

        assert started == []
        assert running == []


@pytest.mark.parametrize(("extension", "owner"), OWNERS)
async def test_the_bot_starts_each_loop_once_it_loads_the_cog_and_stops_it_on_unload(
    extension, owner, monkeypatch
):
    monkeypatch.setitem(sys.modules, extension, sys.modules[extension])
    expected = len(_loops(owner))
    async with _bot() as bot:
        await bot.load_extension(extension)
        await _settled()
        loaded = _running(owner)

        await bot.reload_extension(extension)
        await _settled()
        reloaded = _running(owner)

        await bot.unload_extension(extension)
        await _settled()

        assert len(loaded) == expected
        assert len(reloaded) == expected and set(reloaded).isdisjoint(loaded)
        assert all(task.cancelled() for task in loaded)
        assert _running(owner) == []


def _starts_in_constructors():
    found = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "__init__":
                continue
            found += [
                f"{path.relative_to(APP.parent)}:{call.lineno} {ast.unparse(call)}"
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in STARTERS
            ]
    return found


def test_no_constructor_starts_a_loop_or_creates_a_task():
    assert _starts_in_constructors() == [], (
        "start loops and tasks in `cog_load` (or the owner's own start), never in "
        "`__init__`: an object built and never loaded leaves them running"
    )


@pytest.fixture
def keiko():
    client = discord.Client(
        intents=discord.Intents.none(),
        status=discord.Status.dnd,
        activity=discord.Activity(type=discord.ActivityType.watching, name="/help"),
    )
    client.ws = SimpleNamespace(change_presence=AsyncMock())
    return client


def _sent(keiko):
    presence = keiko.ws.change_presence.await_args.kwargs
    activity = presence["activity"]
    return presence["status"], activity.type, activity.name


async def test_a_status_change_keeps_the_activity(keiko):
    """`/config update` changes one part of Keiko's presence and keeps the other two.

    Broke as: `/config update status` sent the status alone, so Discord dropped the
    activity line until a restart; `/config update activity` and `description` rebuilt the
    activity from the one Keiko logged in with, so each undid the other, and a gateway
    reconnect brought back the boot presence, since nothing told the client what runs now.

    Shared behaviour: `update_status`, `update_activity` and `update_description`
    (`app/commands/config/service.py`) over `discord.Client.change_presence`.
    Exposed by: typing the config service under mypy strict, where the status and the
    activity turned out to be the command's choices, not discord's types.

    Guaranteed: each command sends the whole presence, the part it changes next to what
    the last command left, and a reconnect identifies with that same presence.
    """
    await update_status(keiko, Choice(name="idle", value="idle"))

    assert _sent(keiko) == ("idle", discord.ActivityType.watching, "/help")


async def test_an_activity_and_a_description_keep_each_other(keiko):
    await update_activity(keiko, Choice(name="listening", value=2))
    await update_description(keiko, "lo-fi")

    assert _sent(keiko) == ("dnd", discord.ActivityType.listening, "lo-fi")


async def test_a_reconnect_identifies_with_the_presence_set_last(keiko):
    await update_status(keiko, Choice(name="idle", value="idle"))
    await update_activity(keiko, Choice(name="playing", value=0))

    assert keiko.status is discord.Status.idle
    assert (keiko.activity.type, keiko.activity.name) == (
        discord.ActivityType.playing, "/help"
    )


def server(language, prefix):
    guild = create_guild()
    recorded_server(fake_store(), guild.id, language=language, prefix=prefix)
    return guild


def message_on(guild, content):
    author = create_member(guild, id=111, name="TestUser")
    return create_message(content=content, author=author, channel=guild.text_channels[0])


async def on_message(bot, message):
    from app.core.events import Events

    await Events(bot).on_message(message)


async def say(bot, guild, content):
    message = message_on(guild, content)
    await on_message(bot, message)
    return message


def pong(language):
    return (
        ml("help.ui.responses.ping.title", language),
        fill(ml("help.ui.responses.ping.description", language), ping=42),
    )


@pytest.mark.parametrize("language", ["pt-br", "en-us"])
async def test_ping_answers_pong_and_the_latency_in_the_servers_language(deps, language):
    """A prefix command `ping` answers on the server's prefix, in the server's language.

    Lucas (PR15 round 2, e12.4): after changing the prefix on /configurar there was
    no way to see that Keiko heard the new one, since only StreamElements answers a
    prefix and only once it is set up. `ping` is one of Keiko's own prefix commands,
    answered by the same listener path (`Events.on_message`) before any feature
    reads the message. Round 3 (item 6): it answers as the slash `/ping` does, the
    same "🏓 Pong!" embed with Keiko's gateway latency (`bot.latency`) in ms.

    Guaranteed: `<prefix>ping` gets one reply in the server's saved language with
    the /ping embed and the latency, whatever the asker's Discord language; the
    same word after another prefix gets no reply; a longer word that starts with
    ping is not ping; the slash /ping answers with the same embed.
    """
    deps.bot.latency = 0.0421
    guild = server(language, "ke?")

    message = await say(deps.bot, guild, "ke?ping")

    assert message._reply.await_count == 1
    embed = message._reply.await_args.kwargs["embed"]
    assert (embed.title, embed.description) == pong(language)


async def test_the_slash_ping_answers_with_the_same_embed(deps):
    from unittest.mock import AsyncMock, MagicMock

    from app.commands.help.ping import Ping

    deps.bot.latency = 0.0421
    interaction = MagicMock()
    interaction.locale = "pt-br"
    interaction.response.send_message = AsyncMock()

    await Ping.ping.callback(Ping(deps.bot), interaction)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert (embed.title, embed.description) == pong("pt-br")


async def test_ping_after_another_prefix_gets_no_reply(deps):
    guild = server("pt-br", "ke?")

    message = await say(deps.bot, guild, "kb!ping")

    assert message._reply.await_count == 0
    assert guild.text_channels[0].get_all_messages() == []


async def test_a_word_that_only_starts_with_ping_is_not_ping(deps):
    guild = server("pt-br", "ke?")

    message = await say(deps.bot, guild, "ke?pingado")

    assert message._reply.await_count == 0


@pytest.fixture
def streamer_has_ping(mock_cache, monkeypatch):
    """A server where StreamElements is on for a streamer with a `!ping` command."""

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "state": {"channel_id": "se-gaules"}, "enabled": True}
        return {}

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(
            lambda channel_id: [{"command": "ping", "reply": "Pong do streamer", "enabled": True}]
        ),
    )


def server_message(content):
    return message_on(server("en-us", "ke!"), content)


def refused():
    return discord.Forbidden(
        type("Response", (), {"status": 403, "reason": "Forbidden"})(),
        {"code": 50013, "message": "Missing Permissions"},
    )


async def test_a_failed_own_reply_is_not_answered_by_stream_elements(
    deps, streamer_has_ping
):
    """A message on the server's prefix is answered once at most, even when an answer fails.

    Broke as: `dispatch.message_sent` (`app/core/dispatch.py`) ran each prefix hook as
    its own part and stopped the chain only when a hook returned True. A failed part
    returns no value, so when Keiko's own reply to `ke!ping` raised (50013 Missing
    Permissions in a read-only channel) StreamElements was asked next and answered the
    same `!ping` with the streamer's command. When reading the server's prefix failed,
    the prefix hooks were skipped but help's hint still ran and read the prefix again,
    so a database blip that recovered sent the hint for a prefixed message. Before the
    registry, Keiko's reply, StreamElements and the hint were one part, and a failure
    ended it.

    Shared behaviour: `dispatch.message_sent`, which runs the prefix hooks before the
    message hooks. Consumers that exposed it: help's prefix `ping` and
    stream_elements_commands.

    Guaranteed: a prefix hook that fails ends the prefix chain, so no later hook answers
    the same message; a prefix that could not be read skips the prefix hooks; every hook
    still runs as its own part, and the message hooks (block links) still run.
    """
    deps.bot.config.PREFIX = "ke!"
    deps.bot.latency = 0.0421
    message = server_message("ke!ping")
    message._reply.side_effect = [refused(), None]

    await on_message(deps.bot, message)

    assert message._reply.await_count == 1, "only Keiko's own reply was tried"
    assert message.channel.get_all_messages() == []


async def test_a_prefix_that_could_not_be_read_answers_nothing(
    deps, streamer_has_ping, monkeypatch
):
    from app.core import events as events_module
    from app.exceptions import ServerSettingsMissing

    async def unreadable(guild_id):
        raise ServerSettingsMissing(guild_id)

    deps.bot.config.PREFIX = "ke!"
    deps.bot.latency = 0.0421
    message = server_message("ke!ping")
    monkeypatch.setattr(events_module, "server_prefix", unreadable)

    await on_message(deps.bot, message)

    assert message._reply.await_count == 0, "no Pong and no streamer's command"
    assert message.channel.get_all_messages() == []
