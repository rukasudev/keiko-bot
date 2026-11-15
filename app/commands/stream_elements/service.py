"""StreamElements: a streamer's chat commands answered on the server's prefix."""

import random
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Dict, Optional, TypedDict

import discord

import app as app_module
from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import Style as style_constants
from app.constants import ViewConstants as view_constants
from app.core import analytics, cache
from app.core.commands import MessageSent
from app.core.servers import server_language
from app.core.threads import to_thread
from app.core.trace import outside_any_trace
from app.core.utils import fill, ml
from app.data import data
from app.exceptions import ErrorContext, StreamElementsUnavailable
from app.forms import open_feature
from app.forms.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.forms.form.form_state import Answer
from app.forms.form.lookups import Lookup
from app.forms.form.manager import PanelExtras
from app.forms.form.responses.summary import unwrap
from app.integrations.stream_elements import StreamElementsClient
from app.ui.embed import response_error_embed
from app.ui.records import RecordsBrowser


async def manager(interaction: discord.Interaction) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, "stream_elements_commands")


async def answer_prefix(sent: MessageSent) -> bool:
    """Answer the streamer's command a message on the server's prefix calls."""
    if sent.guild_id is None:
        return False
    await check_message(sent.guild_id, sent.message, sent.prefix)
    return False


async def check_message(guild_id: str, message: discord.Message, prefix: str) -> None:
    """Answer a message that calls one of the streamer's StreamElements commands."""
    cogs = await cache.read_cog_data(guild_id, "stream_elements_commands")

    if not cogs:
        return

    streamer = cogs.get("streamer")
    if not streamer:
        return

    command = message.content.split(" ")[0].replace(prefix, "")

    context = ErrorContext.from_message(
        flow="stream_elements",
        message=message,
        command_length=len(command),
        streamer=streamer,
    )

    try:
        locale = await server_language(guild_id)
        channel_id = await channel_of(
            guild_id, streamer, (cogs.get("state") or {}).get("channel_id")
        )

        if command == "commands":
            await reply_command_list(channel_id, message, streamer, locale)
            return

        reply = await to_thread(
            get_reply_in_cache_or_populate, channel_id, command, message.author
        )
        if not reply:
            return

        await message.reply(
            embed=create_response_embed(
                command, reply, message.author, streamer, prefix, locale
            )
        )
        analytics.record_value(guild_id, "stream_elements_commands")
    except StreamElementsUnavailable as unavailable:
        await to_thread(warn_unavailable, streamer, unavailable)
        if command == "commands":
            await message.reply(
                embed=response_error_embed(
                    "stream_elements_commands.errors.unavailable", locale
                )
            )
    except Exception as e:
        logger.error(
            f"Failed in stream_elements: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def channel_of(guild_id: str, streamer: str, channel_id: Optional[str]) -> str:
    """The streamer's StreamElements channel id, found and kept when not yet saved."""
    if channel_id:
        return str(channel_id)

    found = await to_thread(_channel_or_recent_failure, streamer)
    await keep_channel(guild_id, found)
    return found


async def keep_channel(guild_id: str, channel_id: str) -> None:
    """Keep the server's StreamElements channel id as what Keiko learned, then drop its
    cache."""
    key = "stream_elements_commands"
    await data().guild_cogs.set_cog_state(guild_id, key, {"channel_id": channel_id})
    await to_thread(cache.remove_cog_cache_by_guild, guild_id, key)


def _channel_or_recent_failure(streamer: str) -> str:
    failed_key = constants.REDIS_STREAM_ELEMENTS_CHANNEL_THROTTLE.format(
        streamer=streamer
    )
    failed = cache.get_data_from_redis(failed_key)
    if failed:
        raise StreamElementsUnavailable(str(failed.get("error")))

    try:
        return find_channel(streamer)
    except StreamElementsUnavailable as unavailable:
        cache.set_data_in_redis_with_expiration(
            failed_key,
            {"error": str(unavailable)},
            constants.STREAM_ELEMENTS_CHANNEL_FAILED_SECONDS,
        )
        raise


def find_channel(streamer: str) -> str:
    """The StreamElements channel id of a streamer's username."""
    try:
        info = StreamElementsClient.get_channel_info(streamer)
    except Exception as error:
        raise StreamElementsUnavailable(
            f"channel of {streamer}: {type(error).__name__}: {error}"
        ) from error

    channel_id = info.get("_id") if isinstance(info, dict) else None
    if not channel_id:
        raise StreamElementsUnavailable(f"no channel for {streamer}: {_status(info)}")
    return str(channel_id)


def warn_unavailable(streamer: str, error: StreamElementsUnavailable) -> None:
    """Tell the log channel StreamElements failed, once per streamer while marked."""
    claimed = cache.claim_redis_key(
        constants.REDIS_STREAM_ELEMENTS_WARNED_THROTTLE.format(streamer=streamer),
        constants.STREAM_ELEMENTS_FAILURE_SECONDS,
    )
    if not claimed:
        return

    with outside_any_trace():
        logger.warn(
            f"StreamElements unavailable for {streamer}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def _status(answer: object) -> str:
    if isinstance(answer, dict):
        return (
            f"answered {answer.get('statusCode')} {answer.get('error') or ''}".strip()
        )
    return f"answered {type(answer).__name__}"


async def reply_command_list(
    channel_id: str, message: discord.Message, streamer: str, locale: str
) -> None:
    """The command list in `locale` as the reply to the message that asked for it,
    nothing while there is none; its two lookups on a thread."""
    from app import bot

    commands = await to_thread(
        get_commands_in_cache_or_populate, channel_id, message.author
    )
    if not commands:
        return

    user_info = await to_thread(bot.twitch.get_user_info, streamer)
    icon = (user_info or {}).get("profile_image_url")
    await command_list(streamer, commands).reply(message, commands, locale, icon)


async def send_commands_view(interaction: discord.Interaction) -> None:
    """The paginated command list, opened from the manager panel."""
    guild_id = str(interaction.guild_id)
    cogs = await cache.read_cog_data(guild_id, "stream_elements_commands", manager=True)
    streamer = str((cogs or {}).get("streamer", ""))
    try:
        channel_id = await channel_of(
            guild_id, streamer, (cogs or {}).get("channel_id")
        )
        commands = await to_thread(
            get_commands_in_cache_or_populate, channel_id, interaction.user
        )
    except StreamElementsUnavailable as unavailable:
        await to_thread(warn_unavailable, streamer, unavailable)
        await interaction.response.send_message(
            embed=response_error_embed(
                "stream_elements_commands.errors.unavailable", str(interaction.locale)
            ),
            ephemeral=True,
        )
        return

    await command_list(streamer, commands).send(interaction)


def command_list(streamer: str, commands: Mapping[str, str]) -> RecordsBrowser:
    """The streamer's commands as the list the panel and `ks!commands` draw."""

    async def fetch(
        _interaction: discord.Interaction, _user_id: Optional[str]
    ) -> list[tuple[str, str]]:
        return list(commands.items())

    return RecordsBrowser(
        fetch,
        _as_fields,
        sep=view_constants.COMMANDS_PAGE_SIZE,
        copy="stream_elements_commands.records.commands",
        values={"streamer": streamer},
    )


def _as_fields(
    records: list[tuple[str, str]], _interaction: discord.Interaction
) -> Dict[str, str]:
    return dict(records)


def create_response_embed(
    command: str,
    reply: str,
    user: discord.abc.User,
    streamer: str,
    prefix: str,
    locale: str,
) -> discord.Embed:
    """A StreamElements reply, signed by whoever asked, pointing at the command list."""
    embed = discord.Embed(
        description=reply,
        color=(int(style_constants.BACKGROUND_COLOR, base=16)),
    )
    embed.set_author(name=f"!{command}", icon_url=user.display_avatar.url)
    footer = fill(
        ml("stream_elements_commands.messages.reply_footer", locale),
        command=f"{prefix}commands",
        streamer=streamer,
    )
    embed.set_footer(text=f"• {footer}")

    return embed


def parse_placeholders(reply: str, user: discord.abc.User) -> str:
    """A StreamElements reply with the variables Keiko knows filled, the rest as `X`."""
    for name in ("$(touser)", "${touser}"):
        reply = reply.replace(name, user.mention)
    for name in ("$(count)", "${count}"):
        reply = reply.replace(name, "X")
    for pattern in (r"\$\(random\.(\d+)-(\d+)\)", r"\$\{random\.(\d+)-(\d+)\}"):
        reply = _fill_random(reply, pattern)
    for pattern in (r"\$\(count\.(\d+)\)", r"\$\{count\.(\d+)\}"):
        count_match = re.search(pattern, reply)
        if count_match:
            reply = reply.replace(count_match.group(0), "X")
    return reply.replace("/me", user.mention).replace("$(user)", user.mention)


def _fill_random(reply: str, pattern: str) -> str:
    random_match = re.search(pattern, reply)
    if not random_match:
        return reply
    start = int(random_match.group(1))
    end = int(random_match.group(2))
    return reply.replace(random_match.group(0), str(random.randint(start, end)))


def get_commands_in_cache_or_populate(
    channel_id: str, user: discord.abc.User
) -> Dict[str, str]:
    """The streamer's enabled commands as `!name`, each reply in the asker's words."""
    return {
        f"!{command}": parse_placeholders(reply, user)
        for command, reply in _channel_commands(channel_id).items()
    }


def get_reply_in_cache_or_populate(
    channel_id: str, command: str, user: discord.abc.User
) -> Optional[str]:
    """The reply to one command in the asker's words; None when there is no such one."""
    reply = _channel_commands(channel_id).get(command)
    return parse_placeholders(reply, user) if reply else None


def _channel_commands(channel_id: str) -> Dict[str, str]:
    """The streamer's enabled commands by name, with their replies as written: cached a
    day, a streamer with none for a few minutes, a failure never (it raises)."""
    cache_key = constants.CACHE_STREAM_ELEMENTS_COMMANDS.format(channel_id=channel_id)
    cached = cache.get_data_from_redis(cache_key)
    if cached:
        kept: Dict[str, str] = cached["commands"]
        return kept

    try:
        fetched = StreamElementsClient.get_chat_commands(channel_id)
    except Exception as error:
        raise StreamElementsUnavailable(
            f"commands of {channel_id}: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(fetched, list):
        raise StreamElementsUnavailable(f"commands of {channel_id}: {_status(fetched)}")

    commands = {
        command.get("command"): command.get("reply")
        for command in fetched
        if command.get("enabled")
    }
    seconds = (
        constants.STREAM_ELEMENTS_COMMANDS_CACHE_SECONDS
        if commands
        else constants.STREAM_ELEMENTS_NO_COMMANDS_CACHE_SECONDS
    )
    cache.set_data_in_redis_with_expiration(cache_key, {"commands": commands}, seconds)
    return commands


class StreamElementsFeature(GenericCogFeature):
    """Commands fetched from a streamer's StreamElements channel."""

    def __init__(self) -> None:
        super().__init__("stream_elements_commands")

    async def open(self, context: OpenContext) -> Opened:
        """The document, how many commands are loaded, and a way to read them."""
        opened = await super().open(context)
        if opened.document is None:
            return opened
        title, info = await self._loaded(opened.document, context)
        return Opened(
            document=opened.document,
            panel=PanelExtras(
                info=info,
                info_title=title,
                info_action=self.panel_button("commands", "💡", context.locale),
                enabled=opened.panel.enabled,
            ),
        )

    async def _loaded(
        self, document: Mapping[str, Any], context: OpenContext
    ) -> tuple[str, str]:
        """How many commands the panel says it answers, none, or that it could not
        tell, as the title and the paragraph of its `panel.info`."""
        locale = context.locale
        streamer = str(unwrap(document.get("streamer")) or "")
        try:
            channel_id = await channel_of(
                context.guild_id,
                streamer,
                (document.get("state") or {}).get("channel_id"),
            )
            commands = await to_thread(
                get_commands_in_cache_or_populate, channel_id, context.member
            )
        except StreamElementsUnavailable as unavailable:
            await to_thread(warn_unavailable, streamer, unavailable)
            return self.panel_info_copy(locale, variant="unknown")
        except Exception:
            return self.panel_info_copy(locale, variant="unknown")
        if not commands:
            title, _count = self.panel_info_copy(locale)
            return title, command_list(streamer, commands).empty_text(locale)
        return self.panel_info_copy(
            locale, values={"count": len(commands), "streamer": streamer}
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics, and the command list on a paginated message of its own."""

        async def commands(interaction: Any, _responses: Any) -> None:
            await send_commands_view(interaction)

        return {
            **super().asides(),
            "commands": AsideAction(commands, own_response=True),
        }

    async def prefetch(
        self, lookup: Lookup, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """The streamer's Twitch user and how many StreamElements commands it has."""
        found: dict[str, Mapping[str, Any]] = {}
        if "twitch" in lookup.services:
            twitch = app_module.bot.twitch
            user_id = await to_thread(twitch.get_user_id_from_login, lookup.value)
            image = await to_thread(_profile_image, lookup.value)
            found["twitch"] = {"user_id": user_id, "profile_image": image}
        if "stream_elements" in lookup.services:
            try:
                found["stream_elements"] = await to_thread(
                    _enabled_commands, lookup.value, context.prefix
                )
            except Exception:
                pass
        return found

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Store the document, then keep its streamer's StreamElements channel id."""
        written = await super().commit_setup(payload, context)
        document = await _with_channel(
            context.guild_id, written.document or {}, payload["answers"]
        )
        return replace(written, document=document)

    async def commit_edit(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Merge the edit; a new streamer brings its own StreamElements channel id."""
        written = await super().commit_edit(payload, context)
        document = written.document or {}
        if document.get("streamer") == context.document.get("streamer"):
            return written
        document = await _with_channel(context.guild_id, document, payload["answers"])
        return replace(written, document=document)


def _profile_image(streamer: str) -> str:
    try:
        info = app_module.bot.twitch.get_user_info(streamer) or {}
        return str(info.get("profile_image_url") or "")
    except Exception:
        return ""


class CommandsFound(TypedDict):
    """A streamer's StreamElements channel and the commands it answers, as a lookup."""

    channel_id: str
    enabled_commands: int
    top_commands: str
    keiko_commands: str


def _enabled_commands(streamer: str, prefix: str) -> CommandsFound:
    channel_id = find_channel(streamer)
    commands = StreamElementsClient.get_chat_commands(channel_id) or []
    enabled = [command for command in commands if command.get("enabled")]
    named = [command.get("command") for command in enabled if command.get("command")]
    sample = named[: view_constants.COMMANDS_PREVIEW_LIMIT]
    return {
        "channel_id": channel_id,
        "enabled_commands": len(enabled),
        "top_commands": ", ".join(f"`!{name}`" for name in sample),
        "keiko_commands": ", ".join(f"`{prefix}{name}`" for name in sample),
    }


async def _with_channel(
    guild_id: str, document: Mapping[str, Any], answers: Mapping[str, Answer]
) -> dict[str, Any]:
    looked_up = answers.get("stream_elements_channel_id")
    channel_id = looked_up.raw if looked_up is not None else None
    if not channel_id:
        channel_id = await to_thread(find_channel, str(document.get("streamer", "")))

    await keep_channel(guild_id, str(channel_id))
    state = {**(document.get("state") or {}), "channel_id": str(channel_id)}
    return {**document, "state": state}


FEATURE = StreamElementsFeature()
