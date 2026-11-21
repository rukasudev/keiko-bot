"""The variables a command's code fills in an admin's template, read from its syntax tree."""
import ast
from pathlib import Path
from typing import Set


def filled_names(folder: Path) -> Set[str]:
    """The names every `fill_placeholders` call of the folder passes."""
    names: Set[str] = set()
    for source in sorted(folder.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call) and _called(node) == "fill_placeholders"
            ):
                continue
            given = node.args[1] if len(node.args) > 1 else None
            if isinstance(given, ast.Dict):
                names |= {
                    key.value for key in given.keys if isinstance(key, ast.Constant)
                }
            names |= {
                keyword.value.value
                for keyword in node.keywords
                if keyword.arg == "required" and isinstance(keyword.value, ast.Constant)
            }
    return names


def _called(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""
