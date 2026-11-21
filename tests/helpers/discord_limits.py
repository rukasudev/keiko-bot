"""Discord's Components V2 limits: a message holds at most 40 components and 4000
characters of text, and no custom id twice; Discord refuses the whole message
otherwise, which the fake transport would accept."""
import discord

from tests.behavioral.harness.locators import walk_items

MAX_COMPONENTS = 40
MAX_TOTAL_TEXT = 4000


def assert_within_limits(view, label: str) -> None:
    """`view` holds at most 40 components and 4000 characters of text."""
    items = list(walk_items(view))
    assert len(items) <= MAX_COMPONENTS, (
        f"{label}: {len(items)} components exceed Discord's limit of {MAX_COMPONENTS}"
    )
    total_text = sum(
        len(item.content or "")
        for item in items
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert total_text <= MAX_TOTAL_TEXT, (
        f"{label}: {total_text} chars of TextDisplay exceed {MAX_TOTAL_TEXT}"
    )


def assert_ids_are_unique(view, label: str) -> None:
    """Discord refuses a whole message when two components share a custom id."""
    ids = [
        item.custom_id
        for item in walk_items(view)
        if getattr(item, "custom_id", None) is not None
    ]
    duplicated = {found for found in ids if ids.count(found) > 1}
    assert not duplicated, f"{label}: {len(duplicated)} custom id(s) used twice"
