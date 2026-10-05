"""Every key and every value of a stored document, at any depth."""
from typing import Any, Iterator


def keys_in(node: Any) -> Iterator[str]:
    """Every key of the mappings inside `node`, nested ones included."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from keys_in(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from keys_in(value)


def values_in(node: Any) -> Iterator[Any]:
    """Every value inside `node` that is not itself a mapping or a list."""
    if isinstance(node, dict):
        for value in node.values():
            yield from values_in(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from values_in(value)
    else:
        yield node
