"""Normalize Discord objects into stable, user-visible dicts.

Rules:
- never snapshot raw Discord objects;
- auto-generated custom_ids (random per construction) are excluded — only
  ids explicitly provided by production code appear as "action";
- session-coded ids (`k:<session>:<revision>:<action>`, the new engine's
  codec) are excluded the same way: they change on every render by design;
- timestamps are dropped; URLs reduced to their basename.
"""
import os
from typing import Any, Dict, List, Optional

import discord


def _basename(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return os.path.basename(url.split("?")[0])


def normalize_embed(embed: Optional[discord.Embed]) -> Optional[Dict[str, Any]]:
    if embed is None:
        return None
    return {
        "title": embed.title,
        "description": embed.description,
        "fields": [
            {"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields
        ],
        "footer": embed.footer.text if embed.footer else None,
        "color": embed.colour.value if embed.colour else None,
        "thumbnail": _basename(embed.thumbnail.url if embed.thumbnail else None),
        "image": _basename(embed.image.url if embed.image else None),
    }


SESSION_ID_PREFIX = "k:"


def is_session_coded(custom_id: Optional[str]) -> bool:
    return bool(custom_id) and custom_id.startswith(SESSION_ID_PREFIX)


def _provided_custom_id(item: Any) -> Optional[str]:
    if not getattr(item, "_provided_custom_id", False):
        return None
    if is_session_coded(item.custom_id):
        return None
    return item.custom_id


_SELECT_KINDS = (
    (discord.ui.ChannelSelect, "channel_select"),
    (discord.ui.RoleSelect, "role_select"),
    (discord.ui.UserSelect, "user_select"),
    (discord.ui.MentionableSelect, "mentionable_select"),
    (discord.ui.Select, "select"),
)


def _select_kind(item: Any) -> str:
    """What the admin sees: the native select kind, whatever subclass drew it."""
    for base, kind in _SELECT_KINDS:
        if isinstance(item, base):
            return kind
    return type(item).__name__.lower()


def _normalize_select(item: Any) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {
        "type": _select_kind(item),
        "placeholder": item.placeholder,
        "action": _provided_custom_id(item),
        "min": int(item.min_values),
        "max": int(item.max_values),
        "disabled": item.disabled,
    }
    if isinstance(item, discord.ui.Select):
        normalized["options"] = [
            {"label": o.label, "value": o.value, "default": o.default}
            for o in item.options
        ]
    return normalized


def normalize_item(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, discord.ui.Button):
        return {
            "type": "button",
            "label": item.label,
            "action": _provided_custom_id(item),
            "style": item.style.name,
            "disabled": item.disabled,
        }
    if isinstance(item, (discord.ui.Select, discord.ui.ChannelSelect,
                         discord.ui.RoleSelect, discord.ui.UserSelect,
                         discord.ui.MentionableSelect)):
        return _normalize_select(item)
    if isinstance(item, discord.ui.TextDisplay):
        return {"type": "text", "content": item.content}
    if isinstance(item, discord.ui.MediaGallery):
        return {"type": "media", "count": len(item.items)}
    if isinstance(item, discord.ui.Separator):
        return None  # purely visual
    if isinstance(item, discord.ui.Thumbnail):
        return {"type": "thumbnail", "media": _basename(getattr(item.media, "url", None))}
    children = getattr(item, "children", None)
    if children is not None:
        normalized_children = [n for n in (normalize_item(c) for c in children) if n]
        accessory = getattr(item, "accessory", None)
        if accessory is not None:
            accessory_normalized = normalize_item(accessory)
            if accessory_normalized:
                normalized_children.append(accessory_normalized)
        return {"type": type(item).__name__.lower(), "children": normalized_children}
    return {"type": type(item).__name__.lower()}


def normalize_components(view: Any) -> List[Dict[str, Any]]:
    if view is None:
        return []
    return [n for n in (normalize_item(item) for item in view.children) if n]


def normalize_modal(modal: discord.ui.Modal) -> Dict[str, Any]:
    fields = []
    for child in modal.children:
        inner = child
        # master-only discord.ui.Label wraps the actual input component
        if hasattr(child, "component"):
            inner = child.component
        if isinstance(inner, discord.ui.TextInput):
            fields.append({
                "label": inner.label if isinstance(inner, discord.ui.TextInput) else getattr(child, "text", None),
                "required": inner.required,
                "style": inner.style.name,
                "max_length": inner.max_length,
                "default": inner.default or "",
            })
        else:
            fields.append({"type": type(inner).__name__.lower()})
    return {"title": modal.title, "fields": fields}
