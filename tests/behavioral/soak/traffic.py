"""One soak iteration: what a day of Keiko does, in miniature, on the real code paths.

Every canonical admin path (the golden paths, one per iteration in turn), setups
and panels opened and left on one of 75 servers by a member never seen before,
messages through the `on_message` listener (a blocked link, the prefix `ping`, a
StreamElements command, the default prefix in a server and in a DM, plain text),
a member joining (default roles and the welcome),
a slash command counted, log records with and without a trace, the analytics and
debug-log flushes, and the form sweep. Only Discord, Mongo and Redis are fake.
"""

import asyncio
import copy
import datetime
import itertools
import logging
from collections import Counter
from types import SimpleNamespace
from typing import Any, Iterator, List, Tuple
from unittest.mock import Mock

import discord

from app import logger
from app.exceptions import ErrorContext
from app.core import analytics, debug_logs, trace
from app.settings.discord.callbacks import RUNTIME
from tests.behavioral.golden.paths import all_paths
from tests.behavioral.golden.paths import block_links as block_links_path
from tests.behavioral.golden.paths import default_roles as default_roles_path
from tests.behavioral.golden.paths import stream_elements_commands as stream_path
from tests.behavioral.golden.paths import welcome_messages as welcome_path
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks.discord import (
    MockChannel,
    create_guild,
    create_member,
    create_message,
)

SERVERS = 75
FIRST_SERVER = 900_000_000
FIRST_MEMBER = 10_000_000
PREFIX = "ke!"
FAR_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)

SAVED = {
    "block_links": block_links_path.ENABLED,
    "welcome_messages": welcome_path.ENABLED,
    "default_roles": default_roles_path.ENABLED,
    "stream_elements_commands": stream_path.ENABLED,
}
ABANDONED_SETUPS = ("notifications_twitch", "reminders_birthday")
PANELS = ("welcome_messages", "block_links")


class SentMessage:
    """A message Keiko posted in an admin channel: editable, reactable, nothing kept."""

    def __init__(self, ids: Iterator[int]) -> None:
        self.id = next(ids)

    async def edit(self, **_kwargs: Any) -> "SentMessage":
        return self

    async def add_reaction(self, _emoji: Any) -> None:
        return None


class AdminChannel:
    """An admin log channel that accepts every post and remembers none."""

    def __init__(self, ids: Iterator[int]) -> None:
        self.ids = ids
        self.posts = 0

    async def send(self, *_args: Any, **_kwargs: Any) -> SentMessage:
        self.posts += 1
        return SentMessage(self.ids)


def admin_bot(loop: asyncio.AbstractEventLoop) -> SimpleNamespace:
    """The bot the Discord log handler posts through, on the running loop."""
    ids = itertools.count(1)
    channels = {channel_id: AdminChannel(ids) for channel_id in (1, 2, 3, 4)}
    return SimpleNamespace(
        loop=loop,
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=channels.get,
        channels=channels,
    )


class Traffic:
    """Drives one iteration after another against the same process state."""

    def __init__(self, scenario_factory: Any, deps: Any) -> None:
        self.factory = scenario_factory
        self.deps = deps
        self.paths: List[Tuple[Any, str]] = [
            (path, locale) for path in all_paths() for locale in path.locales
        ]
        self.failures: List[str] = []
        self.answered: Counter[str] = Counter()

    async def iteration(self, number: int) -> None:
        """One iteration: a golden path, a server's day, the flushes and the sweep."""
        guild_id = FIRST_SERVER + number % SERVERS
        member_id = FIRST_MEMBER + number
        await self._golden_path(number)
        self._forget_databases()
        guild = self._server(guild_id)
        await self._forms(guild, member_id, number)
        await self._listeners(guild, member_id)
        self._logs(guild_id, member_id, number)
        await self._settle()
        await analytics.flush()
        await debug_logs.flush()
        await RUNTIME.sweep(FAR_FUTURE)
        await self._settle()
        self._forget_databases()
        self._forget_mock_calls()

    async def _golden_path(self, number: int) -> None:
        path, locale = self.paths[number % len(self.paths)]
        try:
            scenario = await path.run(self.factory, self.deps, locale)
            await scenario.finish()
        except Exception as error:
            self.failures.append(f"{path.id}.{locale}: {type(error).__name__}: {error}")

    def _server(self, guild_id: int) -> Any:
        guild = create_guild(id=guild_id, channels=["exempt", "welcome", "chat"])
        mongo = self.deps.mongo_client
        recorded_server(mongo, guild_id, language="pt-br", prefix=PREFIX)
        for form, saved in SAVED.items():
            document = copy.deepcopy(saved)
            document["guild_id"] = str(guild_id)
            seed_cog(mongo, form, document)
        return guild

    async def _forms(self, guild: Any, member_id: int, number: int) -> None:
        admin = create_member(guild, id=member_id, name=f"Admin{member_id}")
        setup = ABANDONED_SETUPS[number % len(ABANDONED_SETUPS)]
        panel = PANELS[number % len(PANELS)]
        for key, steps in ((setup, 1), (panel, 0)):
            try:
                scenario = self.factory(locale="pt-br", guild=guild, user=admin)
                await scenario.start_command(key)
                for _ in range(steps):
                    await scenario.confirm()
                await scenario.finish()
            except Exception as error:
                failure = f"{type(error).__name__}: {error}"
                self.failures.append(f"{key} on {guild.id}: {failure}")

    async def _listeners(self, guild: Any, member_id: int) -> None:
        from app.cogs.events import Events

        events = Events(self.deps.bot)
        author = create_member(guild, id=member_id, name=f"Member{member_id}")
        _exempt, welcome, chat = guild.text_channels
        messages = {
            "plain": "oi pessoal, tudo bem?",
            "blocked": "olha isso https://spam-site.com/promo",
            "ping": f"{PREFIX}ping",
            "stream_elements": f"{PREFIX}mouse",
            "default_prefix": "ks!ajuda",
        }
        for kind, content in messages.items():
            sent = len(chat.get_all_messages())
            message = create_message(chat, author, content)
            await self._run(events.on_message(message))
            answers = message._reply.await_count + message._delete.await_count
            self.answered[kind] += answers + len(chat.get_all_messages()) - sent

        direct = MockChannel(id=member_id, name="dm")
        await self._run(events.on_message(create_message(direct, author, "ks!ajuda")))
        self.answered["hint"] += len(direct.get_all_messages())

        joining = create_member(guild, id=member_id + 1, name=f"Joined{member_id}")
        joining._user = SimpleNamespace(id=joining.id)
        await self._run(events.on_member_join(joining))
        self.answered["welcome"] += len(welcome.get_all_messages())
        self.answered["roles"] += joining._add_roles.await_count
        await self._run(events.on_interaction(self._slash_command(guild, author)))

    @staticmethod
    def _slash_command(guild: Any, author: Any) -> SimpleNamespace:
        return SimpleNamespace(
            type=discord.InteractionType.application_command,
            command=SimpleNamespace(_attr="block_links", qualified_name="block links"),
            guild_id=guild.id,
            channel_id=guild.text_channels[0].id,
            user=author,
            extras={},
        )

    async def _run(self, work: Any) -> None:
        try:
            await work
        except Exception as error:
            self.failures.append(f"listener: {type(error).__name__}: {error}")

    @staticmethod
    def _logs(guild_id: int, member_id: int, number: int) -> None:
        with trace.trace_scope("soak", guild_id=guild_id, user_id=member_id):
            logger.info(f"soak step {number}", log_type="command_info")
            logger.warn(f"soak warning {number}", log_type="command_warn")
        logger.info(f"soak line {number} outside any trace", guild_id=guild_id)
        logger.error(
            f"soak session error {number}",
            log_type="command_error",
            context=ErrorContext(
                flow="form_block_links",
                guild_id=str(guild_id),
                user_id=str(member_id),
                extra={"session_id": f"soak{number}"},
            ),
        )
        logging.getLogger("discord.gateway").warning("a foreign line, kept out")

    @staticmethod
    async def _settle() -> None:
        for _ in range(5):
            await asyncio.sleep(0)

    def _forget_databases(self) -> None:
        mongo = self.deps.mongo_client
        for database in mongo._databases.values():
            for collection in database._collections.values():
                collection._data.clear()
        redis = self.deps.redis_client
        redis._data.clear()
        redis._expirations.clear()

    def _forget_mock_calls(self) -> None:
        from app.services import welcome_messages
        from app.core import images

        for holder in (
            self.deps.bot,
            welcome_messages.create_banner,
            welcome_messages.draw_banner,
            images.upload_asset,
        ):
            if isinstance(holder, Mock):
                holder.reset_mock()
        for api in (self.deps.twitch, self.deps.youtube, self.deps.stream_elements):
            for name, value in vars(api).items():
                if isinstance(value, list) and (
                    name.endswith("_calls") or name == "_subscriptions"
                ):
                    value.clear()
