import datetime
import functools
import hashlib
import hmac
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from re import findall, finditer, search
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlparse

import discord
import yaml
from discord.ext import commands
from i18n import t

from app.components.modals import ConfirmationModal
from app.constants import CogsConstants as cogconstants
from app.constants import Commands as commandconstants
from app.constants import Emojis as constants
from app.constants import FormConstants as formconstants
from app.constants import LogTypes as logconstants
from app.constants import supported_locales


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


@dataclass(frozen=True)
class ParsedLink:
    host: str
    path: str
    query: Dict[str, str] = field(default_factory=dict)


def parse_link(text: str) -> ParsedLink:
    """Normalize a link or domain (scheme optional, lowercase, no www.)."""
    text = str(text).strip()
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[len("www."):]
    path = parsed.path or ""
    if path.endswith("/"):
        path = path[:-1]
    return ParsedLink(host=host, path=path, query=dict(parse_qsl(parsed.query)))


def get_link_host(value: str) -> str:
    """The website of a link or domain: no scheme, no leading www., no path.
    Single source of truth for "which site is this", so copy that echoes the
    user's own link and the matcher never disagree."""
    text = str(value or "").strip()
    return parse_link(text).host if text else ""


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


def get_guild_text_channels_id(guild, channels: list) -> List[str]:
    return [
        str(discord.utils.get(guild.channels, name=channel).id) for channel in channels
    ]


def get_text_channels_by_guild(guild: discord.Guild) -> Dict[str, str]:
    return {channel.name: str(channel.id) for channel in guild.text_channels}


def get_roles_by_guild(guild: discord.Guild) -> Dict[str, str]:
    return {role.name: str(role.id) for role in guild.roles if role.name != "@everyone"}


def get_available_roles_by_guild(guild: discord.Guild) -> Dict[str, str]:
    return {
        role.name: str(role.id)
        for role in guild.roles
        if role.name != "@everyone"
        and guild.me.top_role.position > role.position
        and not role.managed
    }


def condition_allows(condition: Optional[Dict[str, Any]], value: Any) -> bool:
    """Single evaluator for YAML step `condition:` rules.

    `not_in`: the step runs unless the referenced value is in the list.
    `matches`: the step runs only when the value matches the regex.
    Both may be combined (AND). Reference: docs/form-configuration.md
    """
    if not condition:
        return True
    if value in condition.get("not_in", []):
        return False
    pattern = condition.get("matches")
    if pattern is not None and not search(pattern, str(value or "")):
        return False
    return True


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


def check_answer_message(ctx, message) -> bool:
    return message.author == ctx.author and message.channel == ctx.channel


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


def parse_settings_with_database_values(cog_data: Dict[str, str], form_steps: Dict[str, str], locale: str) -> List[Dict[str, str]]:
    response = []
    cogs_title = parse_form_steps_titles(form_steps, locale)
    icons = resolve_form_settings_icons(form_steps)
    groups = resolve_form_settings_groups(form_steps, locale)
    nested_selects = {}

    # Build maps for step conditions and design options
    step_conditions = {}
    design_options = {}
    for step in form_steps:
        if step.get("condition"):
            step_conditions[step.get("key")] = step.get("condition")
        if step.get("action") == formconstants.DESIGN_SELECT_ACTION_KEY and step.get("designs"):
            design_options[step.get("key")] = {
                d["key"]: d["label"].get(locale) or d["label"].get("en-us", d["key"])
                for d in step.get("designs", [])
            }
        if step.get("action") == formconstants.MULTI_SELECT_ACTION_KEY:
            for select in step.get("selects", []):
                label = select.get("label", {})
                nested_selects[select.get("key")] = {
                    "title": label.get(locale) or label.get("en-us") or select.get("key"),
                    "style": select.get("style"),
                }
        if step.get("action") in (
            formconstants.CONFIGURATION_CARD_ACTION_KEY,
            formconstants.SUMMARY_CARD_ACTION_KEY,
        ):
            for card_field in step.get("fields", []) or []:
                field_label = card_field.get("label")
                if card_field.get("key") and isinstance(field_label, dict):
                    nested_selects[card_field["key"]] = {
                        "title": field_label.get(locale) or field_label.get("en-us"),
                        "style": card_field.get("style"),
                    }
            for section in step.get("sections", []) or []:
                section_state_key = (section.get("state") or {}).get("value")
                section_options = section.get("options") or []
                if section_state_key and section_options \
                        and isinstance(section_options[0], dict):
                    design_options[section_state_key] = {
                        str(option.get("value")):
                            option["label"].get(locale)
                            or option["label"].get("en-us", str(option.get("value")))
                        for option in section_options
                        if isinstance(option.get("label"), dict)
                    }

    for cog_key, value in cog_data.items():
        nested_select = nested_selects.get(cog_key)
        title = cogs_title.get(cog_key) or (
            nested_select.get("title") if nested_select else None
        )
        if not title:
            continue

        # Skip settings that don't meet their condition
        condition = step_conditions.get(cog_key)
        if condition and not condition_allows(condition, cog_data.get(condition.get("key"))):
            continue

        # Convert design key to friendly label
        if cog_key in design_options and isinstance(value, str):
            value = design_options[cog_key].get(value, value)

        if isinstance(value, dict) and value.get("style") == "composition":
            value = parse_settings_with_database_values_composition(form_steps, locale, value["values"])
            response.append({
                "key": cog_key, "title": title, "value": value,
                "style": "composition", "icon": icons.get(cog_key),
                **groups.get(cog_key, {}),
            })
        else:
            response.append({
                "key": cog_key,
                "title": title,
                "value": value,
                "style": nested_select.get("style") if nested_select else None,
                "icon": icons.get(cog_key),
                **groups.get(cog_key, {}),
            })

    return response


def resolve_form_settings_groups(form_steps: List[Dict[str, Any]],
                                 locale: str) -> Dict[str, Dict[str, str]]:
    """Settings-row key -> the step that owns it, already localized.

    The YAML already groups settings: a card owns its fields, a multi-select
    owns its selects, a composition owns itself. Reading that structure gives
    the panel real sections ("Configuração de Links", "Permissões", "Seus
    Links") without a single new key in the language files."""
    groups: Dict[str, Dict[str, str]] = {}

    def label(node: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(node, dict):
            return None
        return node.get(locale) or node.get("en-us")

    for step in form_steps:
        header = step.get("header") or {}
        group = {
            "group": step.get("key"),
            "group_title": label(header.get("title")) or label(step.get("title")),
            "group_icon": step.get("emoji") or header.get("title-emoji"),
        }
        if not group["group_title"]:
            continue

        keys = [card_field.get("key") for card_field in step.get("fields", []) or []
                if isinstance(card_field, dict)]
        keys += [select.get("key") for select in step.get("selects", []) or []]
        if step.get("action") == formconstants.COMPOSITION_ACTION_KEY:
            keys.append(step.get("key"))

        for key in keys:
            if key and key not in groups:
                groups[key] = group

    return groups


def resolve_form_settings_icons(form_steps: List[Dict[str, Any]]) -> Dict[str, str]:
    """Settings-row key -> leading emoji, read only from YAML.

    Every saved setting used to render behind the same icon, which flattened
    the panel into one undifferentiated block. Resolution order (first hit
    wins): card section `icon:` (applied to every state key it owns), card
    `fields[].icon`, `selects[].icon`, then the step's own `emoji:`."""
    icons: Dict[str, str] = {}

    def claim(key: Optional[str], icon: Optional[str]) -> None:
        if key and icon and key not in icons:
            icons[key] = icon

    for step in form_steps:
        for section in step.get("sections", []) or []:
            for state_key in (section.get("state") or {}).values():
                claim(state_key, section.get("icon"))
            claim(section.get("key"), section.get("icon"))
        for card_field in step.get("fields", []) or []:
            if isinstance(card_field, dict):
                claim(card_field.get("key"), card_field.get("icon"))
        for select in step.get("selects", []) or []:
            claim(select.get("key"), select.get("icon"))
        claim(step.get("key"), step.get("emoji"))

    return icons

def parse_settings_with_database_values_composition(form_steps: Dict[str, str], locale: str, values: List[Dict[str, str]]) -> List[Dict[str, str]]:
    for value in values:
        for key, item in value.items():
            step_title = get_form_step_title_composition(form_steps, key, locale)
            if not isinstance(item, dict):
                value[key] = {"value": item, "title": step_title or key}
                continue
            item["title"] = step_title or item.get("title") or key

    return values

def get_form_step_title_composition(form_steps: Dict[str, str], key: str, locale: str) -> str:
    for item in form_steps:
        if item["action"] == "composition":
            return parse_form_steps_title_by_key(item["steps"], key, locale)

def parse_form_steps_title_by_key(form_steps: Dict[str, str], key: str, locale: str) -> Dict[str, str]:
    for item in form_steps:
        if item.get("action") == formconstants.CONFIGURATION_CARD_ACTION_KEY:
            field = next(
                (field for field in item.get("fields", []) if field.get("key") == key),
                None,
            )
            if field:
                label = field.get("label", {})
                return label.get(locale) or label.get("en-us") or key
        if item["action"] not in formconstants.NO_ACTION_LIST and item["key"] == key:
            return item["title"][locale]


# YAML `style:` values resolve here. Reference: docs/form-configuration.md
def format_values_by_style(values: Any, style: str, locale: str = None) -> str:
    if isinstance(values, (str, bool, int, float)) or values is None:
        return format_single_value(values, style, locale)
    return format_list_values(values, style)


def format_single_value(value: str, style: str, locale: str = None) -> str:
    if style == "boolean-mode":
        return _format_boolean_value(value == "custom", locale)
    if value in ("default", "custom"):
        return ml(f"buttons.summary-card.{value}-label", locale=locale) or value
    if style == "boolean":
        return _format_boolean_value(value, locale)
    if style == "mm_dd":
        from app.services.dates import format_mm_dd_label

        return format_mm_dd_label(value, locale)
    if value is None:
        return "-"

    formats = {
        "channel": f"<#{value}>",
        "role": f"<@&{value}>",
        "user": f"<@{value}>",
        "code": f"`{value}`",
        "bullet": "\n```" + "\n".join([f"{i + 1}. {v.lstrip()}" for i, v in enumerate(value.split(";"))]) + "```",
        "numbered": "\n```" + "\n".join([f"• {v.lstrip()}" for v in value.split(";")]) + "```",
    }
    return formats.get(style, value)


def _format_boolean_value(value: Any, locale: str = None) -> str:
    # Styled option values persist as strings ("False"), which are truthy.
    if isinstance(value, str):
        value = value.strip().lower() not in ("false", "no", "não", "nao", "0", "")
    if str(locale).lower() == "pt-br":
        return "Sim" if value else "Não"
    return "Yes" if value else "No"


def format_list_values(values: List[str], style: str) -> str:
    formats = {
        "channel": ", ".join([f"<#{value}>" for value in values]),
        "role": ", ".join([f"<@&{value}>" for value in values]),
        "user": ", ".join([f"<@{value}>" for value in values]),
        "code": "\n```" + "\n".join(str(value) for value in values) + "```",
        "bullet": "\n```" + "\n".join([f"• {v}" for v in values]) + "```",
        "numbered": "\n```" + "\n".join([f"{i + 1}. {v}" for i, v in enumerate(values)]) + "```",
    }
    return formats.get(style, ", ".join(values))

def parse_form_titles_descriptions(interaction: discord.Interaction, title_description: Dict[str, str]) -> str:
    settings_label = get_settings_label_by_locale(interaction.locale)

    result = f"\n\n:pencil: **{settings_label}**\n"
    for key, value in title_description.items():
        result += f"\n{constants.FRISBEE_EMOJI} **{key}**: {value}"

    return result

def get_form_settings_with_database_values(interaction: discord.Interaction, responses: List[Dict[str, str]]) -> str:
    settings_label = get_settings_label_by_locale(interaction.locale)

    result = f"\n\n:pencil: **{settings_label}**\n"
    for item in responses:
        if item.get("hidden"):
            continue
        values = item.get("value", "-")
        style = item.get("style")

        if isinstance(values, dict):
            style = values.get("style")
            values = values.get("values", "-")

        if style == "composition":
            result += f"\n{get_styled_composition_values(item['title'], values, interaction.locale)}"
        else:
            formatted_values = format_values_by_style(values, style, interaction.locale)
            if isinstance(formatted_values, str) and "\n" in formatted_values:
                result += f"\n{constants.FRISBEE_EMOJI} {item['title']}:\n**{formatted_values or '-'}**"
            else:
                result += f"\n{constants.FRISBEE_EMOJI} {item['title']}: **{formatted_values or '-'}**"

    return result

def get_styled_composition_values(title: str, values: List[Dict[str, str]], locale: str = None) -> str:
    result = ""
    for n, composition in enumerate(values):
        formatted_values = ""
        for item in composition.values():
            if not isinstance(item, dict) or item.get("hidden"):
                continue
            formatted = format_values_by_style(item.get('value'), item.get('style'), locale)
            formatted_values += f"- {item['title']}: **{formatted or '-'}**\n"
        result += f"\n{constants.FRISBEE_EMOJI} **{title} #{n+1}**\n{formatted_values}"
    return result


def get_settings_label_by_locale(locale: str) -> str:
    return ml("commands.resume.settings", locale=locale)

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


def admin_only_command():
    from app.components.embed import response_error_embed

    async def predicate(ctx):
        if not ctx.author.guild_permissions.administrator:
            embed = response_error_embed("command-permission-denied", ctx.locale)
            return await ctx.send(embed=embed, ephemeral=True)
        return True

    return commands.check(predicate)


def need_confirmation_modal(func):
    async def wrapper(*args, **kwargs):
        self_instance = args[0]
        interaction = args[1]

        locale = parse_locale(interaction.locale)
        action = interaction.data.get("custom_id")

        async def callback_with_all_args(interaction):
            await func(self_instance, interaction)

        confirmation_modal = ConfirmationModal(
            action=action,
            locale=locale,
            callback=callback_with_all_args,
        )
        return await interaction.response.send_modal(confirmation_modal)

    return wrapper


def parse_confirmation_title(action: str, locale: str) -> str:
    title = ml(f"commands.confirmation-modal.title", locale=locale)
    title = title.replace("$action", action.lower())
    return title


def parse_confirmation_desc(action: str, locale: str) -> str:
    desc = ml(f"commands.confirmation-modal.desc", locale=locale)
    desc = desc.replace("$action", action.lower())
    return desc

def split_welcome_messages(welcome_messages: str) -> List[str]:
    return welcome_messages.split(";")

def parse_welcome_messages(welcome_messages: str, member: discord.Member) -> bool:
    splited_messages = split_welcome_messages(welcome_messages)
    welcome_message = random.choice(splited_messages)

    welcome_message = welcome_message.replace("{server}", member.guild.name)
    welcome_message = welcome_message.replace("{member_count}", str(member.guild.member_count))

    if "{user}" not in welcome_message.lower():
        welcome_message += f"\n<@!{member._user.id}>"
        return welcome_message

    welcome_message = welcome_message.replace("{user}", f"<@!{member._user.id}>")

    return welcome_message
