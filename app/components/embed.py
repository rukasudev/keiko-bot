from typing import Dict

import discord

from app.constants import KeikoIcons as icons
from app.constants import Style as constants
from app.services.utils import ml


def parse_dict_to_embed(data: Dict[str, str]) -> discord.Embed:
    """Parse dictionary to discord Embed object"""

    embed = discord.Embed(
        color=int(constants.BACKGROUND_COLOR, base=16),
        title=data["title"],
        description=data["description"],
    )

    if data.get("emoji"):
        embed.title = f"{data.get('emoji')} {embed.title}"

    if data.get("action"):
        action = data.get("action")
        default_image = icons.IMAGE_01
        embed.set_thumbnail(url=icons.ACTION_IMAGE.get(action, default_image))

    if data.get("image"):
        embed.set_image(url=data["image"])

    if data.get("footer"):
        embed.set_footer(text=data["footer"])

    return embed


def response_error_embed(error_key: str, locale: str) -> discord.Embed:
    title = ml(f"errors.{error_key}.title", locale=locale)

    embed = discord.Embed(
        color=int(constants.RED_COLOR, base=16),
        title=f"🚨 {title}",
        description=ml(f"errors.{error_key}.message", locale=locale),
    )

    return embed
