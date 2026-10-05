import threading
import time
from types import SimpleNamespace
from typing import Any

import aiohttp
import discord
from discord.ext.commands import Bot

from app.config import AppConfig
from app.integrations.notion import NotionIntegration
from app.integrations.reminder_webhook import ReminderWebhook
from app.integrations.twitch import TwitchClient
from app.integrations.youtube import YoutubeClient
from app.services import metrics
from app.services.utils import cogs_manager, get_cogs_folder
from app.translator import Translator


def gateway_intents() -> discord.Intents:
    """The gateway events Keiko reads: its guilds, their members, messages and their text.

    No presences: nothing reads a member's status or activity, only the bot's own.
    DMs stay, so a prefixed command sent there still gets the use-slashes hint.
    """
    return discord.Intents(
        guilds=True,
        members=True,
        guild_messages=True,
        dm_messages=True,
        message_content=True,
    )


def discord_latency() -> aiohttp.TraceConfig:
    """Times every request discord.py sends to Discord, as the `discord` dependency."""
    trace = aiohttp.TraceConfig()

    async def started(_session: Any, request: SimpleNamespace, _params: Any) -> None:
        request.started = time.perf_counter()

    async def ended(_session: Any, request: SimpleNamespace, _params: Any) -> None:
        metrics.record_dependency_latency("discord", time.perf_counter() - request.started)

    trace.on_request_start.append(started)
    trace.on_request_end.append(ended)
    trace.on_request_exception.append(ended)
    return trace


class DiscordBot(Bot):
    """
    Wraps some interactions with the discord bot API, handles running the
    CommandProcessor when commands are received from discord messages
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.guild_available = threading.Event()
        self.synced = False
        self.notion = NotionIntegration(config)
        self.twitch = TwitchClient(self)
        self.youtube = YoutubeClient(self)
        self.reminder = ReminderWebhook(self)
        self.all_cogs = {}
        super().__init__(
            command_prefix=self.config.PREFIX,
            application_id=self.config.APPLICATION_ID,
            case_insensitive=True,
            help_command=None,
            owner_id=self.config.OWNER_ID,
            intents=gateway_intents(),
            status=self.config.STATUS,
            activity=discord.Activity(
                type=self.config.ACTIVITY, name=self.config.DESCRIPTION
            ),
            http_trace=discord_latency(),
        )

    async def setup_hook(self) -> None:
        from app import lifecycle
        from app.components.buttons import JourneyRefreshButton

        lifecycle.install_blocking_io()

        # Registered as a class, not per message: a session's refresh button
        # keeps working across restarts because its id carries the session.
        self.add_dynamic_items(JourneyRefreshButton)

        await cogs_manager(self, "load", get_cogs_folder())
        await self.tree.set_translator(Translator(self))
