"""Components V2 building blocks every Keiko card shares."""

from __future__ import annotations

from typing import Any

import discord

from app.constants import Style


def container(accent: discord.Colour | None = None) -> discord.ui.Container[Any]:
    """The frame of a card, in the house colour unless told otherwise."""
    colour = accent or discord.Colour(int(Style.BACKGROUND_COLOR, 16))
    return discord.ui.Container(accent_colour=colour)


def header(
    frame: discord.ui.Container[Any],
    title: str,
    intro: str = "",
    thumbnail: str = "",
    note: str = "",
) -> None:
    """The `## title` block, its intro and a subtext note, with a picture beside it."""
    lines = [f"## {title}"]
    if intro:
        lines.append(intro)
    if note:
        lines.append(f"-# {note}")
    if thumbnail:
        frame.add_item(
            discord.ui.Section(*lines, accessory=discord.ui.Thumbnail(thumbnail))
        )
        return
    for line in lines:
        frame.add_item(discord.ui.TextDisplay(line))


def row(
    frame: discord.ui.Container[Any],
    body: str,
    accessory: discord.ui.Button[Any] | None = None,
    separated: bool = True,
) -> None:
    """One block of text with its button beside it, under a separator by default."""
    if separated:
        frame.add_item(discord.ui.Separator())
    if accessory is None:
        frame.add_item(discord.ui.TextDisplay(body))
        return
    frame.add_item(discord.ui.Section(body, accessory=accessory))


def footer(frame: discord.ui.Container[Any], text: str) -> None:
    """A separator and the `-#` subtext line that closes a card."""
    if not frame.children or not isinstance(frame.children[-1], discord.ui.Separator):
        frame.add_item(discord.ui.Separator())
    frame.add_item(discord.ui.TextDisplay(f"-# {text}"))
