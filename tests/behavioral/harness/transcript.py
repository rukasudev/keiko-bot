"""Render the ordered event log as a human-readable transcript."""
from typing import Any, Dict, List

_TRUNCATE = 90


def _short(text: Any) -> str:
    if text is None:
        return ""
    text = " ".join(str(text).split())
    return text if len(text) <= _TRUNCATE else text[: _TRUNCATE - 3] + "..."


def _component_line(component: Dict[str, Any]) -> str:
    kind = component.get("type")
    if kind == "button":
        action = f" action={component['action']}" if component.get("action") else ""
        return f"[button \"{component.get('label')}\"{action} {component.get('style')}]"
    if kind and kind.endswith("select"):
        return f"[{kind} \"{_short(component.get('placeholder'))}\"]"
    if kind == "text":
        return f"[text \"{_short(component.get('content'))}\"]"
    if kind == "media":
        return f"[media x{component.get('count')}]"
    children = component.get("children")
    if children is not None:
        inner = " ".join(_component_line(c) for c in children)
        return f"[{kind} {inner}]"
    return f"[{kind}]"


def format_event(event: Dict[str, Any]) -> str:
    seq = f"[{event['seq']:02d}]"
    actor = event["actor"].upper().ljust(4)
    kind = event["kind"]
    parts: List[str] = [f"{seq} {actor} {kind}"]

    if event.get("message"):
        parts.append(event["message"])
    if event.get("target"):
        parts.append(f"\"{event['target']}\"")
    if event.get("values") is not None:
        parts.append(f"values={event['values']}")
    if event.get("fields") is not None:
        parts.append(f"fields={event['fields']}")
    if event.get("ephemeral"):
        parts.append("ephemeral")
    if event.get("components_v2"):
        parts.append("CV2")
    if event.get("step"):
        parts.append(f"| step={event['step']}")

    line = " ".join(parts)
    detail_lines: List[str] = []

    embed = event.get("embed")
    if embed:
        embed_bits = [f"embed \"{_short(embed.get('title'))}\""]
        if embed.get("description"):
            embed_bits.append(f"desc \"{_short(embed['description'])}\"")
        detail_lines.append(" ".join(embed_bits))
    if event.get("content"):
        detail_lines.append(f"content \"{_short(event['content'])}\"")
    modal = event.get("modal")
    if modal:
        labels = [f.get("label") for f in modal.get("fields", [])]
        detail_lines.append(f"modal \"{_short(modal.get('title'))}\" fields={labels}")
    components = event.get("components")
    if components:
        detail_lines.append("components: " + " ".join(_component_line(c) for c in components))

    for detail in detail_lines:
        line += f"\n       {detail}"
    return line


def format_transcript(events: List[Dict[str, Any]]) -> str:
    return "\n".join(format_event(event) for event in events)
