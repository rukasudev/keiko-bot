from typing import List

import discord

from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import default_allowed_links
from app.exceptions import ErrorContext
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)

from .utils import check_two_lists_intersection, get_message_links, list_roles_id


def remove_allowed_links(links: List[str], allowed_links: List[str]) -> List[str]:
    allowed_link_list = []

    for allowed_link in allowed_links:
        allowed_link_list.append(default_allowed_links[allowed_link.lower()])

    for link in links:
        link = link.replace("www.", "")

        if link.endswith("/"):
            link = link[:-1]

        if link in allowed_link_list:
            links.remove(link)

    return links


async def check_message(guild_id: str, message: discord.Message) -> None:
    """Command service to check if exists link on message"""
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)

    if not cogs:
        return

    message_author_has_allowed_role = check_two_lists_intersection(
        list_roles_id(message.author.roles),
        cogs[constants.BLOCK_LINKS_ALLOWED_ROLES_KEY].get("values"),
    )

    if message_author_has_allowed_role:
        return

    allowed_chats = cogs[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY].get("values")
    allowed_links = cogs[constants.BLOCK_LINKS_ALLOWED_LINKS_KEY]

    if isinstance(allowed_links, str):
        allowed_links = [allowed_links]

    message_links = get_message_links(message.content)
    parsed_links = remove_allowed_links(message_links, allowed_links)
    message_chat = str(message.channel.id)

    if parsed_links and message_chat not in allowed_chats:
        context = ErrorContext.from_message(
            flow="block_links",
            message=message,
            blocked_links=parsed_links[:3],
        )

        try:
            await message.delete()
            await message.channel.send(
                cogs[constants.BLOCK_LINKS_ANSWER_KEY], delete_after=5
            )
        except Exception as e:
            logger.error(
                f"Failed to block link: {type(e).__name__}: {e}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=context,
                exc_info=True,
            )
            raise


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    cogs = cache.get_cog_data_or_populate(
        guild_id, constants.BLOCK_LINKS_KEY, manager=True
    )

    if cogs == None:
        return await send_command_form_message(interaction, constants.BLOCK_LINKS_KEY)

    await send_command_manager_message(interaction, constants.BLOCK_LINKS_KEY, cogs)
