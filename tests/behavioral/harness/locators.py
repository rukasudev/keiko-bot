"""Find components on screen and dispatch clicks through the real code path.

The repo wires component callbacks in four patterns (see
docs/form-configuration.md and the harness self-tests):
1. attribute-shadowed callback (`self.callback = coro` in __init__);
2. subclass with `async def callback(self, interaction)`;
3. `@discord.ui.button` decorator (callback wrapper injects the item);
4. no item callback at all — LayoutViews route clicks through
   `View.interaction_check` reading `interaction.data["custom_id"]`.
"""
from typing import Any, Iterator, List, Optional

import discord

from app.services.utils import ml
from tests.behavioral.harness.errors import LocatorError

# Semantic aliases -> (i18n label key | None, explicit custom_ids)
_ALIASES = {
    "continue": ("buttons.confirm.label", []),
    "confirm": ("buttons.confirm.label", []),
    "cancel": ("buttons.cancel.label", ["card_cancel", "cancel_design"]),
    "back": ("buttons.back.label", ["card_back", "picker_back", "back_design"]),
    "done": (None, ["card_done"]),
    "edit": ("buttons.edit.label", []),
    "add": ("buttons.add.label", []),
    "remove": ("buttons.remove.label", []),
    "pause": ("buttons.pause.label", []),
    "unpause": ("buttons.unpause.label", []),
    "disable": ("buttons.disable.label", []),
    "help": ("buttons.help.label", []),
    "preview": ("buttons.preview.label", []),
    "history": ("buttons.history.label", []),
}

_SELECT_TYPES = (
    discord.ui.Select, discord.ui.ChannelSelect, discord.ui.RoleSelect,
    discord.ui.UserSelect, discord.ui.MentionableSelect,
)


def walk_items(view: Any) -> Iterator[Any]:
    """Depth-first over classic Views and LayoutView containers/sections."""
    if view is None:
        return
    for item in view.children:
        yield item
        for child in walk_items(item) if hasattr(item, "children") else []:
            yield child
        accessory = getattr(item, "accessory", None)
        if accessory is not None:
            yield accessory


def clickable_targets(view: Any) -> List[str]:
    """Human-readable list of what could be clicked, for error messages."""
    targets = []
    for item in walk_items(view):
        if isinstance(item, discord.ui.Button):
            custom = item.custom_id if getattr(item, "_provided_custom_id", False) else None
            targets.append(f"button label={item.label!r} custom_id={custom!r}")
        elif isinstance(item, _SELECT_TYPES):
            targets.append(f"{type(item).__name__} placeholder={item.placeholder!r}")
    return targets


def _resolve_alias(target: str, locale) -> tuple:
    """Return (labels, custom_ids) a target may match."""
    labels, custom_ids = [target], [target]
    parts = target.split(":", 1)
    if parts[0] in ("customize", "edit", "reset") and len(parts) == 2 and parts[1].isdigit():
        return [], [f"card_{'customize' if parts[0] == 'customize' else parts[0]}_{parts[1]}"]
    if parts[0] == "design" and len(parts) == 2:
        return [], [f"design_{parts[1]}"]
    if parts[0] == "option" and len(parts) == 2:
        return [parts[1]], []
    alias = _ALIASES.get(target.lower())
    if alias:
        label_key, ids = alias
        if label_key:
            label = ml(label_key, locale=str(locale))
            if label:
                labels.append(label)
        custom_ids.extend(ids)
    return labels, custom_ids


def find_button(view: Any, target: str, locale) -> discord.ui.Button:
    labels, custom_ids = _resolve_alias(target, locale)
    lowered = [label.lower() for label in labels]

    for item in walk_items(view):
        if not isinstance(item, discord.ui.Button):
            continue
        if getattr(item, "_provided_custom_id", False) and item.custom_id in custom_ids:
            return item
    for item in walk_items(view):
        if not isinstance(item, discord.ui.Button):
            continue
        if (item.label or "").lower() in lowered:
            return item
    raise LocatorError(
        f"no button matches target {target!r} "
        f"(tried labels={labels}, custom_ids={custom_ids}).\n"
        f"On screen: {clickable_targets(view)}"
    )


def find_select(view: Any, target: Optional[str]) -> Any:
    selects = [item for item in walk_items(view) if isinstance(item, _SELECT_TYPES)]
    if not selects:
        raise LocatorError(f"no select on screen. On screen: {clickable_targets(view)}")
    if target is None:
        if len(selects) == 1:
            return selects[0]
        raise LocatorError(
            f"{len(selects)} selects on screen, pass target= "
            f"(placeholders: {[s.placeholder for s in selects]})"
        )
    named = getattr(view, "selects", None)      # MultiSelectView: YAML key -> select
    if isinstance(named, dict) and target in named:
        return named[target]
    for select in selects:
        if getattr(select, "_provided_custom_id", False) and select.custom_id == target:
            return select
        if (select.placeholder or "").lower() == target.lower():
            return select
    raise LocatorError(
        f"no select matches target {target!r} "
        f"(placeholders: {[s.placeholder for s in selects]})"
    )


def _has_custom_callback(item: Any) -> bool:
    if "callback" in item.__dict__:            # pattern 1 (shadowed) and 3 (decorator wrapper)
        return True
    return type(item).callback is not discord.ui.Item.callback   # pattern 2 (subclass)


def _view_of(view_or_item: Any) -> Any:
    return getattr(view_or_item, "view", None) or view_or_item


async def dispatch_click(view: Any, button: discord.ui.Button, interaction) -> None:
    """Route the click the way discord.py's ViewStore would."""
    if _has_custom_callback(button):
        await button.callback(interaction)
        return
    owner = button.view or view
    check = type(owner).interaction_check
    base_checks = (discord.ui.View.interaction_check,
                   getattr(discord.ui.LayoutView, "interaction_check",
                           discord.ui.View.interaction_check))
    if check not in base_checks:
        await owner.interaction_check(interaction)
        return
    raise LocatorError(
        f"button {button.label!r} has no callback and its view "
        f"{type(owner).__name__} has no interaction_check router"
    )
