import datetime
import functools
import os
from pathlib import Path
from re import findall
from typing import Any, Dict, List, Tuple

import discord
import yaml
from discord.app_commands import Command
from discord.ext import commands
from i18n import t

from app.components.modals import ConfirmationModal
from app.constants import CogsConstants as cogconstants
from app.constants import Emojis as constants
from app.constants import LogTypes as logconstants


def check_message_has_link(message: str, allowed_links: List[str]) -> List[str]:
    links = [
        link
        for link in findall(
            "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
            message.content.lower(),
        )
        if not any(allowed.lower() in link.lower() for allowed in allowed_links)
    ]

    return links

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
        if item["key"] not in ["form", "confirm"]
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


def keiko_command(
    *,
    name: str = "",
    description: str = "...",
    nsfw: bool = False,
    auto_locale_strings: bool = True,
    extras: Dict[Any, Any] = None,
):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            await func(self, interaction, *args, **kwargs)

        return Command(
            name=name if name != "" else func.__name__,
            description=description,
            callback=wrapper,
            parent=None,
            nsfw=nsfw,
            auto_locale_strings=auto_locale_strings,
            extras=extras,
        )

    return decorator


def parse_settings_with_database_values(
    cog_data: List[Dict[str, str]], form_steps: Dict[str, str], locale: str
) -> List[Dict[str, str]]:
    cogs_title = parse_form_steps_titles(form_steps, locale)
    response = [
        {"title": cogs_title[cog_key], "value": value}
        for cog_key, value in cog_data.items()
        if cogs_title.get(cog_key)
    ]
    return response


def format_values_by_style(values: Any, style: str) -> str:
    if isinstance(values, str):
        return format_single_value(values, style)
    else:
        return format_list_values(values, style)

def format_single_value(value: str, style: str) -> str:
    formats = {
        "boolean": "Yes" if value else "No",
        "channel": f"<#{value}>",
        "role": f"<@&{value}>",
        "user": f"<@{value}>",
        "bullet": "\n```" + "\n".join([f"{i + 1}. {v}" for i, v in enumerate(value.split("; "))]) + "```",
        "numbered": "\n```" + "\n".join([f"• {v}" for v in value.split("; ")]) + "```",
    }
    return formats.get(style, value)

def format_list_values(values: List[str], style: str) -> str:
    formats = {
        "channel": ", ".join([f"<#{value}>" for value in values]),
        "role": ", ".join([f"<@&{value}>" for value in values]),
        "user": ", ".join([f"<@{value}>" for value in values]),
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
        values = item.get("value", "-")
        style = item.get("style")

        if isinstance(values, dict):
            style = values.get("style")
            values = values.get("values", "-")

        formatted_values = format_values_by_style(values, style)
        result += f"\n{constants.FRISBEE_EMOJI} {item['title']}: **{formatted_values}**"

    return result

def get_settings_label_by_locale(locale: str) -> str:
    return ml("commands.resume.settings", locale=locale)

def parse_locale(locale: str) -> str:
    return str(locale).lower()


def ml(key: str, locale: str):
    try:
        return t(key, locale=parse_locale(locale), default=t(key, locale="en-us"))
    except Exception:
        return t(key, locale="en-us")


def get_command_by_key(bot, key: str) -> discord.app_commands.Command:
    for command in bot.app_commands:
        if command._attr == key:
            return command

    return None


def parse_command_event_description(
    description: str,
    event_date: datetime.datetime,
    interaction: discord.Interaction,
    cog_key: str,
) -> str:
    command = get_command_by_key(interaction.client, cog_key)
    command_name = command.extras[interaction.locale.value].get("locale_qualified_name")
    description = description.replace("$command_name", command_name)
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
    hours = days * 24 + seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    return f"{days}d {hours}h {minutes}m {seconds}s"


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

def parse_welcome_message(welcome_message: str, member: discord.Member) -> bool:
    welcome_message = welcome_message.replace("{server}", member.guild.name)
    welcome_message = welcome_message.replace("{member_count}", str(member.guild.member_count))

    if "{user}" not in welcome_message.lower():
        welcome_message += f"\n{member.mention}"
        return welcome_message

    welcome_message = welcome_message.replace("{user}", member.mention)

    return welcome_message
