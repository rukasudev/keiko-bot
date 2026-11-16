"""What the command checks share: the form compiler, wherever the engine keeps it."""

import ast
import importlib
import os
from typing import Any, Callable

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_HOMES = ("app/forms",)


def _defines_compile_form(path: str) -> bool:
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    return any(
        isinstance(node, ast.FunctionDef) and node.name == "compile_form"
        for node in tree.body
    )


def form_compiler() -> Callable[[str, Any], Any]:
    """The engine's `compile_form(key, raw)`, found by the file that defines it."""
    for home in ENGINE_HOMES:
        for folder, _subfolders, filenames in os.walk(os.path.join(ROOT, home)):
            for filename in sorted(filenames):
                path = os.path.join(folder, filename)
                if filename.endswith(".py") and _defines_compile_form(path):
                    dotted = os.path.relpath(path, ROOT)[: -len(".py")]
                    module = importlib.import_module(dotted.replace(os.sep, "."))
                    return module.compile_form
    raise LookupError(f"no module under {ENGINE_HOMES} defines compile_form")
