import datetime
import functools
import hashlib
import hmac
from importlib import import_module
from re import finditer
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypeVar

import discord
from i18n import t
from pydantic import Field

from app.constants import CogsConstants as cogconstants
from app.constants import LogTypes as logconstants
from app.constants import supported_locales
from app.core.commands import (
    Command,
    command_for,
    commands,
    setup_feature,
)
from app.core.facts import filled
from app.core.i18n import CopyBlock, CopyText
from app.core.tokens import fact_names, plain, templated
from app.exceptions import DestinationNotFound

T = TypeVar("T")


def format_relative_time(dt: datetime.datetime) -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)

    delta = now - dt
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds} seconds ago"

    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"

    days = hours // 24
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"

    weeks = days // 7
    if weeks < 5:
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"

    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"

    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def format_discord_timestamp(created_at: Any) -> str:
    """`<t:unix:R>`: a relative time localized by each reader's client."""
    if not hasattr(created_at, "timestamp"):
        return "-"
    return f"<t:{int(created_at.timestamp())}:R>"


HTTP_LINK_PATTERN = r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
# Schemeless links need "www." or a path, so "package.json" never matches.
SCHEMELESS_LINK_PATTERN = (
    r"(?<![\w@./-])"
    r"(?:www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?"
    r"|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/\S+)"
)


def get_message_links(message: str) -> List[str]:
    text = message.lower()
    http_matches = list(finditer(HTTP_LINK_PATTERN, text))
    links = [match.group(0) for match in http_matches]
    http_spans = [match.span() for match in http_matches]

    for match in finditer(SCHEMELESS_LINK_PATTERN, text):
        start = match.start()
        if any(span_start <= start < span_end for span_start, span_end in http_spans):
            continue
        links.append(match.group(0).rstrip(".,;:!?)\"'"))

    return links


def verify_hmac_sha256(secret: str, message: str, signature: str, prefix: str = "") -> bool:
    """Whether `signature` is the prefixed SHA-256 HMAC of `message` under `secret`."""
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    expected = prefix + digest.hexdigest()
    return hmac.compare_digest(expected.encode("utf-8"), signature.encode("utf-8"))


def get_available_roles_by_guild(guild: discord.Guild) -> Dict[str, str]:
    return {
        role.name: str(role.id)
        for role in guild.roles
        if role.name != "@everyone"
        and guild.me.top_role.position > role.position
        and not role.managed
    }


def ensure_list(value: Any) -> list:
    """Coerce a persisted envelope value to a list. Single selections are
    stored as bare scalars, which breaks membership checks downstream."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def list_roles_id(roles: List[discord.Role]) -> List[int]:
    return [str(role.id) for role in roles]


def check_two_lists_intersection(x: list, y: list) -> bool:
    return bool(set(x).intersection(y))


@functools.lru_cache(maxsize=64)
def step_titles(command_key: str, locale: str = "en-us") -> Dict[str, str]:
    """Every step key of a form mapped to the title a human reads.

    Goes into what a step owns: a composition runs its own sub-steps and a card
    owns fields and sections, and those keys are the ones that show up in
    reports as `custom_link` — meaningless to anyone who has not read the YAML.
    Read from the compiled form, so a label left to its default reads as the
    label it defaults to. Cached because a form's copy cannot change at runtime.
    """
    from app.forms.form.form_yaml import CardStep, ListStep, registry

    titles: Dict[str, str] = {}

    def walk(steps: Iterable[Any]) -> None:
        for step in steps:
            titles.setdefault(step.key, step.title.get(locale))
            if isinstance(step, CardStep):
                for field in step.fields:
                    titles.setdefault(field.key, step.label_of(field).get(locale))
                for section in step.sections:
                    titles.setdefault(section.key, section.label.get(locale))
            if isinstance(step, ListStep):
                walk(step.steps)

    walk(registry.get(command_key).steps)
    return titles


def describe_step(command_key: str, step_key: str, locale: str = "en-us") -> str:
    """The step's human title, falling back to its key when it has none."""
    if not step_key:
        return "—"
    return step_titles(command_key, locale).get(step_key, step_key)


def describe_default(row: Dict[str, Any]) -> str:
    """The note that every guild runs a setting's declared default, or nothing."""
    if row.get("filled") or not row.get("default"):
        return ""
    return f" · all on the default `{row['default']}`"


async def get_translated_qualified_name(
    bot, command: discord.app_commands.commands.Command, locale: str
) -> str:
    name = await bot.tree.translator.translate(command._locale_name, locale, None)

    if command.parent is None:
        return name

    parent_name = await bot.tree.translator.translate(
        command.parent._locale_name, locale, None
    )
    names = [name, parent_name]

    if command.parent.parent is not None:
        grandparent_name = await bot.tree.translator.translate(
            command.parent.parent._locale_name, locale, None
        )
        names.append(grandparent_name)

    return " ".join(reversed(names))


def parse_locale(locale: str | discord.Locale) -> str:
    locale_str = str(locale).lower()
    lowered_supported = [s.lower() for s in supported_locales]
    if locale_str in lowered_supported:
        return locale_str
    lang_prefix = locale_str.split("-")[0]
    for supported in lowered_supported:
        if supported.startswith(lang_prefix):
            return supported
    return "en-us"


def destination(guild_id: Any, channel_id: Any) -> Tuple[Any, Any]:
    """The server and the channel a delivery goes to, or DestinationNotFound for either."""
    from app import bot

    guild = bot.get_guild(int(guild_id))
    if guild is None:
        raise DestinationNotFound(f"guild {guild_id} not found")

    channel = guild.get_channel(int(channel_id or 0))
    if channel is None:
        raise DestinationNotFound(f"channel {channel_id} of guild {guild_id} not found")
    return guild, channel


def is_guild_admin(user: Any) -> bool:
    """Whether the member holds the administrator permission in their guild."""
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def parse_valid_locale(locale: discord.Locale) -> discord.Locale:
    locale_value = str(getattr(locale, "value", locale)).lower()
    if locale_value == discord.Locale.brazil_portuguese.value.lower():
        return discord.Locale.brazil_portuguese
    return discord.Locale.american_english


def speak(interaction: Any, language: str) -> str:
    """Make `language` what every later read of the interaction's locale answers in."""
    interaction.locale = parse_valid_locale(language)
    return language


def ml(key: str, locale: str | discord.Locale) -> str:
    try:
        return t(key, locale=parse_locale(locale), default=t(key, locale="en-us"))
    except Exception:
        return t(key, locale="en-us")


class SetupRow(CopyBlock):
    """A command's row on /setup and on the greeting, read through `command_text`."""

    label: CopyText = Field(
        description="The feature's one name: its /setup row, its line on the first "
        "message, its buttons and its History.",
        examples=[{"en-us": "Link Blocker", "pt-br": "Bloqueio de Links"}],
        json_schema_extra={"role": "copy"},
    )
    description: CopyText = Field(
        description="The row's one-line pitch, drawn under the name.",
        json_schema_extra={"role": "copy"},
    )


def command_text(command: Command, path: str, locale: str | discord.Locale) -> str:
    """The text a command writes under `path` of its own copy, as `ml` resolves it."""
    return ml(f"{command.key}.{path}", locale)


def get_command_by_key(bot, key: str) -> discord.app_commands.Command:
    for command in bot.app_commands:
        if hasattr(command, "_attr") and command._attr == key:
            return command

    return None


def get_command_display_name(bot: Any, key: str, locale: str | discord.Locale) -> str:
    command = get_command_by_key(bot, key)
    valid_locale = parse_valid_locale(locale)
    valid_locale_value = getattr(valid_locale, "value", str(valid_locale))
    if command:
        extras = getattr(command, "extras", {}) or {}
        locale_extras = extras.get(valid_locale_value, {}) or {}
        command_name = locale_extras.get("locale_qualified_name")
        if command_name:
            return command_name

    feature_command = setup_feature(key)
    if feature_command:
        group = ml(f"texts.groups.{feature_command.group}", locale=valid_locale_value)
        subgroup = command_text(feature_command, "command.slash.subgroup", valid_locale_value)
        name = command_text(feature_command, "command.slash.name", valid_locale_value)
        return " ".join(part for part in (group, subgroup, name) if part)

    return str(key).replace("_", " ")


def stored_value(entry: Dict[str, Any]) -> Any:
    """The value a saved entry holds, machine-readable when it kept both."""
    return entry.get("_raw_value", entry.get("value"))


def fill(text: str, **values: Any) -> str:
    """Replace every `$name` (or `${name}`) in a localized line with the value given
    for it, each name read whole and in one pass, so a value put in is never read
    again; a line that writes a given name as `{name}` is refused."""
    written, reads = plain(text), fact_names(text)
    braced = [name for name in values if f"{{{name}}}" in written and name not in reads]
    if braced:
        raise ValueError(
            f"{text!r} writes {{{braced[0]}}}: copy names a value as ${braced[0]}"
        )
    return filled(text, values)


def join_names(names: List[str], locale: str) -> str:
    """The names as one phrase in `locale`: "A, B e C", "A, B and C"."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])}{ml('texts.list_and', locale)}{names[-1]}"


def at_least(count: int, locale: str) -> str:
    """A count Keiko stopped at, read as a floor: "5,000+", "5 mil+"."""
    return fill(
        ml("texts.count_at_least", locale),
        count=f"{count:,}",
        thousands=f"{count // 1000:,}".replace(",", "."),
    )


def fill_placeholders(
    template: str, values: Dict[str, str], required: Optional[str] = None
) -> str:
    """Fill each `{name}` of a user's template in one pass, adding `{required}` if missing."""
    if required and f"{{{required}}}" not in template:
        template += f"\n{{{required}}}"
    return templated(template, values)


def parse_command_event_description(
    description: str,
    event_date: datetime.datetime,
    interaction: discord.Interaction,
    cog_key: str,
) -> str:
    command_name = get_command_display_name(interaction.client, cog_key, interaction.locale)
    return fill(
        description,
        command_name=command_name,
        date=event_date.strftime("%Y-%m-%d %H:%M:%S") + " UTC",
        user=interaction.user.mention,
    )


def format_traceback_message(traceback: str) -> str:
    if traceback == "NoneType: None\n":
        return None

    tb = traceback
    if len(tb.split("\n")) > 15:
        tb = "\n".join(tb.split("\n")[-15:])
        tb_formatted = tb
        if len(tb_formatted) > 3000:
            tb_formatted = "...\n" + tb_formatted[-3000:]
        return tb_formatted
    return tb


def command_extensions(command: Command) -> List[str]:
    """The modules of a command's cogs that load as extensions: every cog that is no
    slash subgroup, in the declared order; a module without `setup(bot)` is refused."""
    found: List[str] = []
    for cog in command.cogs:
        module = cog.__module__
        if issubclass(cog, discord.app_commands.Group) or module in found:
            continue
        if not callable(getattr(import_module(module), "setup", None)):
            raise ValueError(
                f"{command.key!r} declares the cog {cog.__name__}, whose module {module} "
                "has no `async def setup(bot)` to add it"
            )
        found.append(module)
    return found


def extension_paths(name: str) -> List[str]:
    """The modules of the cog extension `name`: a module, a core cog or a command."""
    if name.startswith("app."):
        return [name]
    if name in cogconstants.CORE_COGS:
        return [f"app.core.{name}"]
    return command_extensions(command_for(name))


def extension_names() -> List[str]:
    """The names `extension_paths` turns into modules: the core cogs, then the key of
    every command with a cog that loads as an extension, in the declared order."""
    keys = [command.key for command in commands() if command_extensions(command)]
    return [*cogconstants.CORE_COGS, *keys]


def startup_extensions() -> List[str]:
    """The core cogs, then the extensions of every command that is not lazy, in the
    declared order."""
    found = [f"app.core.{name}" for name in cogconstants.CORE_COGS]
    for command in commands():
        if not command.lazy:
            found.extend(
                module for module in command_extensions(command) if module not in found
            )
    return found


def lazy_commands() -> List[str]:
    """The keys of the commands whose cogs load once Keiko is ready."""
    return [command.key for command in commands() if command.lazy]


async def cogs_manager(bot, mode: str, cogs: list[str], sync: bool = False) -> None:
    from app import logger

    for cog in cogs:
        for extension in extension_paths(cog):
            if mode == "unload":
                await bot.unload_extension(extension)

            elif mode == "load":
                await bot.load_extension(extension)

            elif mode == "reload":
                await bot.reload_extension(extension)
            else:
                raise ValueError("Invalid mode.")

    cogs_list = ", ".join(cogs)
    logger.info(f"Cogs {cogs_list} {mode}ed.", log_type=logconstants.COMMAND_INFO_TYPE)

    if sync:
        await bot.tree.sync()


def format_datetime_output(datetime) -> str:
    days, seconds = datetime.days, datetime.seconds
    hours = (seconds // 3600) % 24
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


def parse_log_filename_with_date(
    filename: str, year: Optional[int], month: Optional[int], day: Optional[int]
) -> Tuple[str, str]:
    if not (day and month and year):
        return filename, ""

    date = f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
    return filename, date


def split_messages(stored: Any) -> List[str]:
    """The messages a saved value holds, as a list or `;`-joined, blanks left out."""
    entries = stored if isinstance(stored, (list, tuple)) else str(stored or "").split(";")
    return [str(entry).lstrip() for entry in entries if str(entry).strip()]


def values_of(
    responses: List[Dict[str, Any]], keys: Optional[Iterable[str]] = None
) -> Dict[str, Any]:
    """A preview's answers as `{key: machine value}`, envelopes unwrapped.

    The shape belongs to the platform's `responses_for_preview`, so every
    sender reads it the same way instead of spelling the unwrap again.
    """
    wanted = set(keys) if keys is not None else None
    return {
        item["key"]: stored_value(item)
        for item in responses
        if item.get("key") and (wanted is None or item["key"] in wanted)
    }
