from typing import Any, Dict, List, Optional


def merge_composition_item_by_nested_value(
    items: List[Dict[str, Any]],
    new_item: Dict[str, Any],
    nested_key: str,
    edited_index: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Upsert a composition item by a nested ``{"value": ...}`` field."""
    new_value = _nested_value(new_item, nested_key)
    if not new_value:
        if edited_index is not None and 0 <= edited_index < len(items):
            items[edited_index] = new_item
            return items
        items.append(new_item)
        return items

    existing_index = next(
        (
            i for i, item in enumerate(items)
            if _nested_value(item, nested_key) == new_value and i != edited_index
        ),
        None,
    )

    if existing_index is not None:
        items[existing_index] = new_item
        if edited_index is not None and 0 <= edited_index < len(items) and edited_index != existing_index:
            del items[edited_index]
        return items

    if edited_index is not None and 0 <= edited_index < len(items):
        items[edited_index] = new_item
        return items

    items.append(new_item)
    return items


def _nested_value(item: Dict[str, Any], key: str) -> Optional[str]:
    value = item.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    return str(value) if value is not None else None
