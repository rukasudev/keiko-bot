from typing import Dict

import discord

from app.constants import KeikoIcons as icons
from app.constants import Style as constants
from app.services.utils import ml


def parse_form_dict_to_embed(data: Dict[str, str], locale: str, manager: bool = False) -> discord.Embed:
    """Parse dictionary to discord Embed object"""
    embed = discord.Embed(
        color=int(constants.BACKGROUND_COLOR, base=16),
        title=data["title"][locale],
        description=data["description"][locale],
    )

    if data.get("emoji"):
        embed.title = f"{data.get('emoji')} {embed.title}"

    if data.get("action"):
        action = data.get("action")
        default_image = icons.IMAGE_01
        embed.set_thumbnail(url=icons.ACTION_IMAGE.get(action, default_image))

    if manager:
        embed.set_thumbnail(url=icons.IMAGE_01)

    if data.get("image"):
        embed.set_image(url=data["image"])

    if data.get("footer"):
        embed.set_footer(text=data["footer"][locale])

    return embed


def response_embed(multilang_key: str, locale: str, color: str = None, footer: bool = False) -> discord.Embed:
    title = ml(f"{multilang_key}.title", locale)

    embed = discord.Embed(
        color=(int(constants.BACKGROUND_COLOR, base=16) if not color else color),
        title=title,
        description=ml(f"{multilang_key}.message", locale=locale),
    )

    if footer:
        embed.set_footer(text=f"• {ml(f'{multilang_key}.footer', locale)}")

    return embed


def default_welcome_embed(title: str, message: str, footer: str = None, image: str=None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        color=(int(constants.BACKGROUND_COLOR, base=16)),
        description=message,
    )

    if footer:
        embed.set_footer(text=f"• {footer}")

    embed.set_image(url=image)

    return embed


def response_error_embed(error_key: str, locale: str) -> discord.Embed:
    title = ml(f"errors.{error_key}.title", locale=locale)

    embed = discord.Embed(
        color=int(constants.RED_COLOR, base=16),
        title=f"🚨 {title}",
        description=ml(f"errors.{error_key}.message", locale=locale),
    )

    return embed


def report_embed(
    multilang_key: str,
    locale: str,
    title: str,
    description: str,
    command: str,
    atachments: str = "",
) -> discord.Embed:
    embed_title = ml(f"{multilang_key}.dm-message.title", locale)

    embed = discord.Embed(
        color=(int(constants.BACKGROUND_COLOR, base=16)),
        title=embed_title,
    )

    embed.add_field(name=ml(f"{multilang_key}.dm-message.fields.title", locale), value=title)
    embed.add_field(name=ml(f"{multilang_key}.dm-message.fields.command", locale), value=command)

    if atachments:
        embed.add_field(name=ml(f"{multilang_key}.dm-message.fields.atachments", locale), value=atachments)
    else:
        no_atachments = ml(f"{multilang_key}.dm-message.fields.no-atachments", locale)
        embed.add_field(name=ml(f"{multilang_key}.dm-message.fields.atachments", locale), value=no_atachments)

    embed.add_field(name=ml(f"{multilang_key}.dm-message.fields.description", locale), value=description)

    embed.set_thumbnail(url=icons.IMAGE_01)
    embed.set_footer(text=f"• {ml(f'{multilang_key}.dm-message.footer', locale)}")

    return embed
