"""Manager view: the links I blocked, with a member filter.

Two screens, the same hand-off RemoveItem already uses for its member picker:
the paginated list answers its own interaction, and the filter button swaps it
for a native user select. A select injected into PaginationView was rejected:
that view calls `select.update()` unconditionally, which only its help select
implements, and page-derived options would hide members off the current page.
"""
from typing import Optional

import discord

from app import logger
from app.components.buttons import GenericButton
from app.components.select_views import UserSelectView
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import Style
from app.services.utils import ml, parse_locale
from app.views.pagination import PaginationView

RECORDS_PER_PAGE = 4


def _bm(key: str, locale: str) -> str:
    return ml(f"commands.commands.commons.block-links-manager.{key}", locale=locale)


async def send_blocked_links_message(
    interaction: discord.Interaction, user_id: Optional[str] = None
) -> None:
    from app.services.block_links import (
        get_blocked_link_records,
        parse_blocked_link_records,
    )

    locale = parse_locale(interaction.locale)
    records = get_blocked_link_records(str(interaction.guild_id), user_id=user_id)

    if not records:
        empty_key = "blocked-list.filter.empty" if user_id else "blocked-list.embed.empty"
        embed = discord.Embed(
            title=_bm("blocked-list.embed.title", locale),
            description=_bm(empty_key, locale),
            color=int(Style.BACKGROUND_COLOR, base=16),
        )
        embed.set_thumbnail(url=KeikoIcons.IMAGE_02)
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    view = PaginationView(
        interaction,
        title=_bm("blocked-list.embed.title", locale),
        description=_bm("blocked-list.embed.description", locale),
        data=parse_blocked_link_records(records, locale),
        sep=RECORDS_PER_PAGE,
    )
    view.add_item(_filter_button(locale, filtered=bool(user_id)))
    await view.send(ephemeral=True)


def _filter_button(locale: str, filtered: bool) -> GenericButton:
    """Row 1 keeps the filter off the pagination arrows: without an explicit
    row, discord.py first-fits it into the free fifth slot of row 0."""
    if filtered:
        return GenericButton(
            label=_bm("blocked-list.filter.all-label", locale),
            callback=show_everyone,
            style=discord.ButtonStyle.grey,
            emoji="🔎",
            row=1,
        )
    return GenericButton(
        label=_bm("blocked-list.filter.label", locale),
        callback=show_member_picker,
        style=discord.ButtonStyle.grey,
        emoji="🔎",
        row=1,
    )


async def show_everyone(interaction: discord.Interaction) -> None:
    await send_blocked_links_message(interaction)


async def show_member_picker(interaction: discord.Interaction) -> None:
    locale = parse_locale(interaction.locale)

    async def on_selected(select_interaction: discord.Interaction) -> None:
        selected = picker.get_response()
        if isinstance(selected, (list, tuple)):
            selected = selected[0] if selected else None
        if not selected:
            return
        # The confirm button hands over a fresh interaction, so the filtered
        # list can answer it with a message of its own.
        await send_blocked_links_message(select_interaction, user_id=str(selected))

    picker = UserSelectView(
        callback=on_selected, locale=locale, required=True, unique=True
    )
    embed = discord.Embed(
        title=_bm("blocked-list.filter.title", locale),
        description=_bm("blocked-list.filter.description", locale),
        color=int(Style.BACKGROUND_COLOR, base=16),
    )
    embed.set_thumbnail(url=KeikoIcons.IMAGE_02)
    await interaction.response.edit_message(embed=embed, view=picker)


async def on_error(interaction: discord.Interaction, error: Exception) -> None:
    logger.error(
        f"Blocked links view error: {type(error).__name__}: {error}",
        interaction=interaction,
        log_type=logconstants.COMMAND_ERROR_TYPE,
        exc_info=True,
    )
