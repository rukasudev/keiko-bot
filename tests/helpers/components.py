"""What a Discord message shows, read off its components: the texts of a Components V2
card, top to bottom, and the labels of the buttons a message carries."""

import discord

from tests.behavioral.harness.locators import walk_items


def card_texts(view):
    """The texts a Components V2 message shows, top to bottom."""
    return [
        item.content for item in walk_items(view) if isinstance(item, discord.ui.TextDisplay)
    ]


def button_labels(message):
    """The label of each button `message` carries, in the order it shows them."""
    return [
        item.label
        for item in message.registered_items
        if isinstance(item, discord.ui.Button)
    ]
