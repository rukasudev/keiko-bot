import datetime
import functools
import hashlib
import hmac
import os
import random
from pathlib import Path
from re import finditer
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypeVar

import discord
import yaml
from i18n import t

from app.constants import CogsConstants as cogconstants
from app.constants import Commands as commandconstants
from app.constants import FormConstants as formconstants
from app.constants import LogTypes as logconstants
from app.constants import supported_locales

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

def verify_twitch_signature(request, twitch_secret_key: str):
    message = request.headers['Twitch-Eventsub-Message-Id'] + request.headers['Twitch-Eventsub-Message-Timestamp'] + request.get_data(as_text=True)
    secret = twitch_secret_key.encode('utf-8')
    signature = hmac.new(secret, message.encode('utf-8'), hashlib.sha256).hexdigest()
    expected_signature = request.headers['Twitch-Eventsub-Message-Signature'].split('=')[1]

    return hmac.compare_digest(signature, expected_signature)

# Loads app/languages/form/<key>.yml (cached: restart to pick up YAML edits).
# Schema reference: docs/form-configuration.md
@functools.cache
def parse_form_yaml_to_dict(key: str) -> Dict[str, str]:
    file = Path.joinpath(
        Path().absolute(), "app", "languages", "form", key.lower()
    )
    with open(f"{file}.yml", "r") as f:
        yaml_object = yaml.safe_load(f)
        return yaml_object.get("steps")


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

    Goes deeper than `parse_form_steps_titles`: a composition runs its own
    sub-steps and a card owns fields, and those keys are the ones that show up
    in reports as `custom_link` — meaningless to anyone who has not read the
    YAML. Cached because a form's copy cannot change at runtime.
    """
    titles: Dict[str, str] = {}

    def label(node: Dict[str, Any]) -> Optional[str]:
        for holder in (node.get("title"), node.get("label"), (node.get("header") or {}).get("title")):
            if isinstance(holder, dict):
                text = holder.get(locale) or holder.get("en-us")
                if text:
                    return text
        return None

    def children(step: Dict[str, Any]) -> List[Any]:
        """`fields:` is a list on a card and a locale map on a button step."""
        found = []
        for name in ("fields", "selects", "sections"):
            value = step.get(name)
            if isinstance(value, list):
                found += value
        return found

    def walk(steps: Any) -> None:
        if not isinstance(steps, list):
            return
        for step in steps:
            if not isinstance(step, dict):
                continue
            key, text = step.get("key"), label(step)
            if key and text:
                titles.setdefault(key, text)

            for child in children(step):
                if isinstance(child, dict) and child.get("key"):
                    child_text = label(child)
                    if child_text:
                        titles.setdefault(child["key"], child_text)

            walk(step.get("steps"))

    walk(list(parse_form_yaml_to_dict(command_key)))
    return titles


def describe_step(command_key: str, step_key: str, locale: str = "en-us") -> str:
    """The step's human title, falling back to its key when it has none."""
    if not step_key:
        return "—"
    return step_titles(command_key, locale).get(step_key, step_key)


def describe_default(row: Dict[str, Any]) -> str:
    """Zero filled plus a declared default is not the same as zero used.

    Nobody stores `block_links / mode`, and every guild runs `block_all` because
    that is what the YAML says an unset value means. Read without this, the line
    proposes deleting the setting that decides how the feature behaves. Lives
    next to `describe_step` because both admin surfaces render through it and
    neither owns it.
    """
    if row.get("filled") or not row.get("default"):
        return ""
    return f" · all on the default `{row['default']}`"


def parse_form_steps_titles(form_steps: List[Dict[str, str]], locale: str) -> Dict[str, str]:
    return {
        item["key"]: item["title"][locale]
        for item in form_steps
        if not item.get("hidden") and item["action"] not in formconstants.NO_ACTION_LIST
    }


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


def parse_locale(locale: str) -> str:
    locale_str = str(locale).lower()
    lowered_supported = [s.lower() for s in supported_locales]
    if locale_str in lowered_supported:
        return locale_str
    lang_prefix = locale_str.split("-")[0]
    for supported in lowered_supported:
        if supported.startswith(lang_prefix):
            return supported
    return "en-us"


def is_guild_admin(user: Any) -> bool:
    """Whether the member holds the administrator permission in their guild."""
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def parse_valid_locale(locale: discord.Locale) -> discord.Locale:
    locale_value = getattr(locale, "value", str(locale))
    if locale_value == discord.Locale.brazil_portuguese.value:
        return discord.Locale.brazil_portuguese
    return discord.Locale.american_english


def ml(key: str, locale: str):
    try:
        return t(key, locale=parse_locale(locale), default=t(key, locale="en-us"))
    except Exception:
        return t(key, locale="en-us")


def get_command_by_key(bot, key: str) -> discord.app_commands.Command:
    for command in bot.app_commands:
        if hasattr(command, "_attr") and command._attr == key:
            return command

    return None


def get_command_display_name(bot, key: str, locale: discord.Locale) -> str:
    command = get_command_by_key(bot, key)
    valid_locale = parse_valid_locale(locale)
    valid_locale_value = getattr(valid_locale, "value", str(valid_locale))
    if command:
        extras = getattr(command, "extras", {}) or {}
        locale_extras = extras.get(valid_locale_value, {}) or {}
        command_name = locale_extras.get("locale_qualified_name")
        if command_name:
            return command_name

    feature_command = commandconstants.FEATURE_COMMANDS.get(key)
    if feature_command:
        group = ml(f"commands.groups.{feature_command['group']}", locale=valid_locale_value)
        namespace = feature_command["namespace"]
        subgroup = ml(f"commands.commands.{namespace}.subgroup", locale=valid_locale_value)
        name = ml(f"commands.commands.{namespace}.name", locale=valid_locale_value)
        return " ".join(part for part in (group, subgroup, name) if part)

    return str(key).replace("_", " ")


def stored_value(entry: Dict[str, Any]) -> Any:
    """The value a saved entry holds, machine-readable when it kept both."""
    return entry.get("_raw_value", entry.get("value"))


def fill(text: str, **values: Any) -> str:
    """Replace every `$name` in a localized line with the value given for it."""
    for name, value in values.items():
        text = text.replace(f"${name}", str(value))
    return text


def parse_command_event_description(
    description: str,
    event_date: datetime.datetime,
    interaction: discord.Interaction,
    cog_key: str,
) -> str:
    command_name = get_command_display_name(interaction.client, cog_key, interaction.locale)
    setup_command = ml("commands.commands.setup.name", locale=interaction.locale)
    description = description.replace("$command_name", command_name)
    description = description.replace("$setup_command", setup_command)
    description = description.replace(
        "$date", event_date.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    )
    description = description.replace("$user", interaction.user.mention)
    return description


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


def get_cogs_folder(directory: str = "app/cogs") -> List[str]:
    cogs = []

    for filename in os.listdir(directory):
        if filename.startswith("_"):
            continue

        if filename.endswith(".py"):
            filename = filename[:-3]

        if filename in cogconstants.LAZY_LOAD_COGS:
            continue

        # base cogs is a special case
        if "base" in directory:
            filename = f"base.{filename}"

        if filename == "base":
            cogs = cogs + get_cogs_folder(f"{directory}/{filename}")
            continue

        cogs.append(filename)

    return cogs


async def cogs_manager(bot, mode: str, cogs: list[str], sync: bool = False) -> None:
    from app import logger

    for cog in cogs:
        cog = f"app.cogs.{cog}" if "app" not in cog else cog

        if mode == "unload":
            await bot.unload_extension(cog)

        elif mode == "load":
            await bot.load_extension(cog)

        elif mode == "reload":
            await bot.reload_extension(cog)
        else:
            raise ValueError("Invalid mode.")

    cogs_list = ", ".join(cogs).replace("base.", "")
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
    filename: str, year: str, month: str, day: str
) -> Tuple[str, str]:
    if not (day and month and year):
        return filename, ""

    date = f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
    return filename, date


def split_welcome_messages(welcome_messages: str) -> List[str]:
    return welcome_messages.split(";")

def render_welcome_message(welcome_message: str, member: discord.Member) -> str:
    welcome_message = welcome_message.replace("{server}", member.guild.name)
    welcome_message = welcome_message.replace("{member_count}", str(member.guild.member_count))

    if "{user}" not in welcome_message.lower():
        return welcome_message + f"\n<@!{member._user.id}>"

    return welcome_message.replace("{user}", f"<@!{member._user.id}>")

def parse_welcome_messages(welcome_messages: str, member: discord.Member) -> str:
    splited_messages = split_welcome_messages(welcome_messages)

    return render_welcome_message(random.choice(splited_messages), member)


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
