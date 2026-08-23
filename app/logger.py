import asyncio
import logging
import os
import sys
import traceback
from typing import Optional
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

import discord

from app.config import AppConfig
from app.constants import Commands as constants_commands
from app.constants import DiscordLimits as limits
from app.constants import LogTypes as constants
from app.constants import Style as style
from app.constants import TraceTitles as titles
from app.services import debug_logs
from app.services import journey as journey_service
from app.services import trace as trace_service
from app.services.utils import format_traceback_message

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)
logging.getLogger("httpx").disabled = True
logging.getLogger('werkzeug').setLevel(logging.ERROR)


class CustomTimedRotatingFileHandler(TimedRotatingFileHandler):
    def doRollover(self):
        if not hasattr(self, "bot"):
            return

        channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
        if not channel:
            return

        yesterday_date = datetime.now() - timedelta(days=1)
        strftime_yesterday_date = yesterday_date.strftime("%Y-%m-%d")

        coroutine = channel.send(
            f":package: Here is my log file for: **{strftime_yesterday_date}**!",
            file=discord.File(self.baseFilename),
        )
        asyncio.run_coroutine_threadsafe(coroutine, self.bot.loop)
        os.remove(self.baseFilename)

        super().doRollover()


class OptionalGuildIDFormatter(logging.Formatter):
    def format(self, record):
        format_str = "[%(levelname)s] %(asctime)s"

        interaction = getattr(record, "interaction", None)
        guild_id = getattr(record, "guild_id", None)

        if interaction:
            format_str += f" (interaction_id: {interaction.id})"
        elif guild_id:
            format_str += f" (guild_id: {guild_id})"

        format_str += " - %(message)s"

        self._style = logging.PercentStyle(format_str)
        return super().format(record)


def add_handler(handler):
    log_formatter = OptionalGuildIDFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(log_formatter)
    logger.addHandler(handler)


RESERVED_LOG_PARAMS = {"exc_info", "stack_info", "stacklevel"}


def build_extra_params(**kwargs):
    return {key: value for key, value in kwargs.items() if value and key not in RESERVED_LOG_PARAMS}


def log(level, message, **kwargs):
    extra = build_extra_params(**kwargs)
    reserved = {key: kwargs[key] for key in RESERVED_LOG_PARAMS if key in kwargs}
    level(message, extra=extra, **reserved)


info = lambda message, **kwargs: log(logger.info, message, **kwargs)
warn = lambda message, **kwargs: log(logger.warning, message, **kwargs)
error = lambda message, **kwargs: log(logger.error, message, **kwargs)


CONTEXT_IDENTITY_KEYS = ("guild_id", "user_id", "channel_id")


def record_identity(record: logging.LogRecord) -> dict:
    """The ids a log record carries, from whichever source supplied them.

    A record gets them three ways — an `interaction`, an `ErrorContext` from
    `with_error_context`, or a bare `guild_id` — and both handlers need the same
    answer. Keeping the knowledge here means a new field on `ErrorContext`
    reaches the embed and the stored document together, instead of one of them
    quietly falling behind.
    """
    identity = {key: None for key in ("guild_id", "user_id", "channel_id", "interaction_id")}

    guild_id = getattr(record, "guild_id", None)
    if guild_id:
        identity["guild_id"] = str(guild_id)

    context = getattr(record, "context", None)
    if context is not None:
        values = context.to_dict() if hasattr(context, "to_dict") else context
        if isinstance(values, dict):
            for key in CONTEXT_IDENTITY_KEYS:
                if values.get(key):
                    identity[key] = str(values[key])

    interaction = getattr(record, "interaction", None)
    if interaction is not None:
        if getattr(interaction, "id", None) is not None:
            identity["interaction_id"] = str(interaction.id)
        for attribute, key in (("guild", "guild_id"), ("user", "user_id"), ("channel", "channel_id")):
            value = getattr(interaction, attribute, None)
            if value is not None and getattr(value, "id", None) is not None:
                identity[key] = str(value.id)

    return identity


NOISE_MARKERS = (
    "We are being rate limited.",
    "WebSocket closed with 1000",
)


def is_noise(record: logging.LogRecord) -> bool:
    """Records that describe the network, not the work.

    Shared by the folding handler and the Discord one: a rate-limit retry has no
    business becoming a line of somebody's command timeline either.
    """
    if record.levelno == logging.ERROR and record.exc_info:
        if NOISE_MARKERS[1] in str(record.exc_info[1]):
            return True
    return any(marker in record.getMessage() for marker in NOISE_MARKERS)


class TraceFoldingHandler(logging.Handler):
    """Turns log records into timeline lines, for whoever renders the trace.

    This used to live inside DiscordLogsHandler, which made the timeline depend
    on the admin cog being loaded: a boot that failed before the cogs had no
    timeline at all, which is exactly the run worth reading. Folding belongs to
    the bus, so it is installed with the other handlers at startup.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setLevel(logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if is_noise(record):
                return
            trace_service.add_line(record.getMessage(), record.levelno)
        except Exception:
            self.handleError(record)


class StoredLogsHandler(logging.Handler):
    """Writes every log record to Mongo, where it can be queried for 30 days.

    Installed at process start rather than with the cogs, so the records that
    explain a failed boot — the ones the Discord handler can never see, because
    it is created by a cog that a failed boot never loads — are kept too.

    Nothing here may call `logger.*`: this runs underneath logging, so a warning
    about a failed write would be recorded, fail, and warn again. Failures go to
    `sys.stderr` via `handleError`, which is what that hook exists for.
    """

    def __init__(self, config=None):
        super().__init__()
        self.setLevel(logging.INFO)
        if config is not None:
            debug_logs.configure(config)
        logger.addHandler(self)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            document = debug_logs.build_document(**self.extract(record))
            debug_logs.record(document)
        except Exception:
            self.handleError(record)

    def extract(self, record: logging.LogRecord) -> dict:
        fields = {
            "level": record.levelname,
            "message": record.getMessage(),
            "log_type": getattr(record, "log_type", None),
            "module": record.filename,
            "function": record.funcName,
            "line": record.lineno,
            "traceback_text": self.format_exception(record),
        }
        fields.update(record_identity(record))
        return fields

    def format_exception(self, record: logging.LogRecord):
        """The whole traceback, unlike the Discord embed which has to truncate."""
        if not record.exc_info:
            return None
        return "".join(traceback.format_exception(*record.exc_info))


TRACE_LEVEL_ICONS = {
    logging.WARNING: "⚠️ ",
    logging.ERROR: "❌ ",
}


def format_duration(milliseconds: int) -> str:
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}m{seconds:02d}s"


def outcome_title(outcome) -> Optional[str]:
    """Label from TraceTitles, emoji from the vocabulary the journey owns."""
    label = titles.OUTCOME_LABELS.get(outcome)
    if not label:
        return None
    return f"{journey_service.outcome_icon(outcome)} {label}"


def trace_title(trace) -> str:
    """One emoji per kind of event, and the same one every time.

    Order: a failure outranks everything, then how the work ended, then the last
    thing it did, then where it came from. The middle two are separate on
    purpose — a session that added three items and then saved ended as a save,
    but while it is open the interesting fact is the item.
    """
    if trace.has_error:
        return outcome_title(constants.TRACE_RESULT_FAILURE)

    result = trace.result
    if result and result not in titles.GENERIC_RESULTS:
        settled = outcome_title(result)
        if settled:
            return settled

    action = outcome_title(getattr(trace, "last_action", None))
    if action:
        return action

    if result:
        ongoing = outcome_title(result)
        if ongoing:
            return ongoing

    return titles.SOURCE_TITLES.get(trace.source, titles.DEFAULT)


def trace_subject(trace) -> str:
    """Slash commands read as commands; a webhook path reads as itself."""
    if trace.source in titles.SOURCE_TITLES:
        return f"`{trace.name}`"
    return f"`/{trace.name}`"


def build_trace_timeline(trace) -> str:
    lines = []
    for line in trace.lines:
        icon = TRACE_LEVEL_ICONS.get(line["levelno"], "")
        lines.append(f"`{line['ts'].strftime('%H:%M:%S')}` {icon}{line['message']}")
    if trace.truncated:
        lines.append(f"`…` +{trace.truncated} more")
    return "\n".join(lines)


def clip_head(text: str, limit: int) -> str:
    """Keep the beginning: the first line is what identifies a failure.

    The counterpart is `debug_logs.clip_tail`, which keeps the end because the
    last frames of a traceback are the ones that explain it. Same shape,
    opposite choice, so the names say which.
    """
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_trace_embed(trace) -> discord.Embed:
    """Metadata in fields, the story in the body, one label on top."""
    embed = discord.Embed(
        title=trace_title(trace),
        description=clip_head(trace_subject(trace), limits.EMBED_DESCRIPTION),
        color=(
            # The house error colour, not discord.Color.red(): every other error
            # surface in Keiko uses this one.
            discord.Colour(int(style.RED_COLOR, base=16)) if trace.has_error
            else constants.LOG_TYPE_MAP[constants.TRACE_TYPE][1]
        ),
    )

    if trace.user_id:
        embed.add_field(name="User", value=f"<@{trace.user_id}>", inline=True)
    if trace.guild_id:
        embed.add_field(name="Guild", value=f"`{trace.guild_id}`", inline=True)
    if trace.source:
        embed.add_field(name="Source", value=f"`{trace.source}`", inline=True)

    embed.add_field(name="Duration", value=format_duration(trace.duration_ms), inline=True)
    if trace.result:
        embed.add_field(name="Result", value=trace.result, inline=True)

    timeline = build_trace_timeline(trace)
    if timeline:
        embed.add_field(
            name="Timeline",
            value=clip_head(timeline, limits.EMBED_FIELD_VALUE),
            inline=False,
        )

    if trace.footnote:
        embed.add_field(
            name="Note", value=clip_head(trace.footnote, limits.EMBED_FIELD_VALUE), inline=False
        )

    label = "session" if trace.is_journey else "trace"
    embed.set_footer(
        text=f"• {label} {trace.id} | {trace.started_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return embed


class LoggerHooks:
    def __init__(
        self, config: AppConfig, file_logs: bool = False, console_logs: bool = True
    ):
        self.config = config
        self.console_logs = console_logs
        self.file_logs = file_logs

    def start(self) -> None:
        logger.addHandler(TraceFoldingHandler())
        StoredLogsHandler(self.config)

        if self.file_logs:
            self.set_timed_rotating_file_handler()
            add_handler(self.file_handler)

        if self.console_logs:
            console_handler = logging.StreamHandler(sys.stdout)
            add_handler(console_handler)

            discord.utils.setup_logging(
                handler=console_handler,
                level=self.get_application_log_level(),
            )

    def set_timed_rotating_file_handler(self) -> CustomTimedRotatingFileHandler:
        log_directory = "./logs"

        if not os.path.exists(log_directory):
            os.makedirs(log_directory)

        self.file_handler = CustomTimedRotatingFileHandler(
            f"{log_directory}/keiko_log.log", when="MIDNIGHT", encoding="utf-8"
        )
        self.file_handler.suffix = "%Y_%m_%d"
        self.file_handler.namer = lambda name: name.replace(".log.", "_") + ".log"

    def set_bot(self, bot):
        self.file_handler.bot = bot
        self.file_handler.doRollover()

    def get_application_log_level(self):
        if self.config.is_debug():
            return logging.DEBUG

        return logging.INFO


class DiscordLogsHandler(logging.Handler):
    """Routes log records to Discord, one message per unit of work.

    While a trace is open the records become lines of its timeline instead of
    separate messages; the trace sink posts the assembled result when the work
    finishes. Errors always keep a message of their own so they never wait on a
    trace that may never close. Reference: docs/analytics.md
    """

    def __init__(self, bot):
        self.bot = bot
        self._journey_messages = {}
        self._journey_tasks = set()
        self._journey_dirty = set()
        super(DiscordLogsHandler, self).__init__()
        self.setLevel(logging.INFO)
        self.setFormatter(OptionalGuildIDFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(self)
        trace_service.register_sink(self.send_trace)
        journey_service.set_publisher(self.publish_journey)
        journey_service.install()

    BOT_ACTIONS_LOG_TYPES = (
        constants.EVENT_JOIN_GUILD_TYPE,
        constants.EVENT_LEFT_GUILD_TYPE,
        constants.BOT_ACTION_TYPE,
    )

    def emit(self, record):
        self.format(record)

        if self.is_muted(record):
            return

        # The folding itself is done by TraceFoldingHandler on the bus; here we
        # only decide whether this record already has a home in a timeline.
        if trace_service.has_open_trace() and record.levelno < logging.ERROR:
            return

        log_channel = self.get_log_channel(record)
        if not log_channel:
            return

        self.schedule_send(log_channel.send(embed=self.add_embed(record)))

    def is_muted(self, record: logging.LogRecord) -> bool:
        interaction = getattr(record, "interaction", None)
        if interaction and interaction.command:
            # Inspecting a log must not log about inspecting a log.
            if interaction.command.qualified_name == "Log Inspection":
                return True

        return is_noise(record)

    def get_log_channel(self, record: logging.LogRecord):
        log_type = getattr(record, "log_type", None)

        if record.levelno == logging.ERROR:
            return self.bot.get_channel(self.bot.config.ADMIN_LOGS_ERROR_CHANNEL_ID)

        if log_type == constants.COMMAND_CALL_TYPE:
            return self.bot.get_channel(self.bot.config.ADMIN_LOGS_COMMAND_CALL_ID)

        if log_type in self.BOT_ACTIONS_LOG_TYPES and self.bot.config.ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID:
            return self.bot.get_channel(
                self.bot.config.ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID
            )

        return self.bot.get_channel(self.bot.config.ADMIN_LOGS_CHANNEL_ID)

    def schedule_send(self, coroutine) -> None:
        """Webhooks run on the Flask thread, where create_task is not safe."""
        loop = getattr(self.bot, "loop", None)
        if not loop:
            coroutine.close()
            return

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        try:
            if running is loop:
                loop.create_task(coroutine)
            else:
                asyncio.run_coroutine_threadsafe(coroutine, loop)
        except Exception:
            coroutine.close()

    def send_trace(self, trace) -> None:
        if not trace.is_noteworthy:
            return

        log_channel = self.trace_channel(trace)
        if not log_channel:
            return

        self.schedule_send(log_channel.send(embed=build_trace_embed(trace)))

    def trace_channel(self, trace):
        channel_id = (
            self.bot.config.ADMIN_LOGS_COMMAND_CALL_ID
            if trace.user_id
            else self.bot.config.ADMIN_LOGS_CHANNEL_ID
        )
        return self.bot.get_channel(channel_id)

    def publish_journey(self, journey) -> None:
        """Post the session message once, then keep editing that same message.

        Scheduled, never awaited by the caller: a step of a form must not wait
        on a Discord round trip inside the three seconds it has to answer.
        """
        if not getattr(self.bot, "loop", None):
            return

        if journey.id in self._journey_tasks:
            self._journey_dirty.add(journey.id)
            return

        self._journey_tasks.add(journey.id)
        self.schedule_send(self._render_journey(journey))

    async def _render_journey(self, journey) -> None:
        """Coalesces the edits a burst of steps would otherwise each trigger."""
        try:
            await asyncio.sleep(constants_commands.ANALYTICS_JOURNEY_DEBOUNCE_SECONDS)
            channel = self.trace_channel(journey)
            if channel:
                embed = build_trace_embed(journey)
                message = self._journey_messages.get(journey.id)
                if message is None:
                    from app.components.buttons import journey_message_view

                    self._journey_messages[journey.id] = await channel.send(
                        embed=embed, view=journey_message_view(journey.session_id)
                    )
                else:
                    await message.edit(embed=embed)
        except Exception:
            pass
        finally:
            self._journey_tasks.discard(journey.id)
            if journey.id in self._journey_dirty:
                self._journey_dirty.discard(journey.id)
                self.publish_journey(journey)
            elif journey.finished_at:
                self._journey_messages.pop(journey.id, None)

    def add_embed(self, record: logging.LogRecord):
        title, color = self.get_log_type(record)
        description = record.msg

        if not title and record.levelno == logging.ERROR:
            record.log_type = constants.APPLICATION_ERROR_TYPE
            title, color = self.get_log_type(record)
            description = self.parse_application_error_desc(record)

        embed = discord.Embed(
            title=title,
            # Discord rejects the whole message over the limit, so an oversized
            # error used to cost the very log that explained it.
            description=clip_head(str(description), limits.EMBED_DESCRIPTION),
            color=color,
        )

        self.add_fields(embed, record)

        embed.set_footer(text=f"• {record.filename} | {record.asctime}")

        return embed

    def get_log_type(self, record: logging.LogRecord):
        log_types = constants.LOG_TYPE_MAP
        log_type = getattr(record, "log_type", None)

        if not log_type and record.levelno == logging.ERROR:
            return None, None

        return log_types.get(log_type, (None, None))

    def parse_application_error_desc(self, record: logging.LogRecord):
        tb = traceback.format_exc()
        tb_formatted = format_traceback_message(tb)
        exception = record.exc_info[1] if record.exc_info else record.getMessage()
        return f"Unexpected error raised an exception: ```{exception}```\n**Path** ```{record.pathname}```\n**Traceback**```{tb_formatted or record.msg}```"

    def add_fields(self, embed: discord.Embed, record: logging.LogRecord):
        interaction: discord.Interaction = getattr(record, "interaction", None)
        guild_id = getattr(record, "guild_id", None)
        context = getattr(record, "context", None)
        identity = record_identity(record)

        if interaction:
            embed.add_field(name="Interaction ID", value=identity["interaction_id"])
            if interaction.guild:
                embed.add_field(name="Guild ID", value=identity["guild_id"])
            embed.add_field(name="User ID", value=interaction.user.mention)

            if embed.title == constants.COMMAND_CALL_TITLE:
                command_name = (
                    interaction.command.qualified_name
                    if interaction.command
                    else getattr(record, "command_name", None)
                )
                if command_name:
                    embed.description = f"Command called: **{command_name}**"

                interaction_source = getattr(record, "interaction_source", None)
                if interaction_source:
                    embed.add_field(name="Interaction Source", value=interaction_source, inline=True)

            if interaction.channel:
                embed.add_field(name="Channel ID", value=identity["channel_id"])

            if interaction.message:
                embed.add_field(name="Message ID", value=interaction.message.id)
        elif context:
            self._add_context_fields(embed, context)
        elif guild_id:
            embed.add_field(name="Guild ID", value=identity["guild_id"])

            owner_id = getattr(record, "owner_id", None)
            if owner_id:
                embed.add_field(name="User ID", value=f"<@{owner_id}>")

    def _add_context_fields(self, embed: discord.Embed, context):
        ctx_dict = context.to_dict() if hasattr(context, "to_dict") else context

        field_order = ["flow", "guild_id", "user_id", "user_name", "channel_id"]
        for key in field_order:
            if key in ctx_dict and ctx_dict[key]:
                label = key.replace("_", " ").title()
                value = str(ctx_dict[key])[:1024]
                if key == "user_id":
                    value = f"<@{ctx_dict[key]}>"
                embed.add_field(name=label, value=value, inline=True)

        for key, value in ctx_dict.items():
            if key not in field_order and value:
                label = key.replace("_", " ").title()
                embed.add_field(name=label, value=str(value)[:1024], inline=True)
