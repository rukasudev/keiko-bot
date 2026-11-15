"""An error embed never shows a copy key: each error it names has a title and a message.

Broke as: pressing the default roles sync button while the feature is off answered a
red embed titled `🚨 errors.command-default-roles-disactivated.title` (after the copy
moved into the command, `🚨 default_roles.errors.disactivated.title`, since renamed
`deactivated`): the error wrote
only its `message`, and `response_error_embed` reads `<error>.title` and
`<error>.message`, so the missing title came back as its own key. Shared behaviour:
`response_error_embed` (`app/ui/embed.py`), which every command and the form adapter
call with an error key. Consumer that exposed it: `set_on_default_roles_sync` in
`app/commands/roles/service.py`. Guaranteed: that embed shows its real title in both
locales, and every error key `app/` passes to `response_error_embed` as a literal, or
under a literal prefix, resolves both its title and its message in both locales.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.constants import Style
from app.core import i18n
from app.core.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

APP = Path(__file__).resolve().parents[3] / "app"
LOCALES = ("en-us", "pt-br")


@pytest.mark.parametrize(
    "locale,title",
    [("en-us", "🚨 Default Roles Disabled"), ("pt-br", "🚨 Cargos Padrão Desativados")],
)
async def test_the_roles_sync_of_a_server_without_the_feature_shows_its_real_title(
    mock_cache, locale, title
):
    from app.commands.roles import service

    mock_cache.return_value = None
    interaction = MagicMock(guild=MagicMock(id=10), locale=locale)
    interaction.followup.send = AsyncMock()

    await service.set_on_default_roles_sync(interaction)

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == title
    assert "default_roles" not in embed.title and ".title" not in embed.title
    assert embed.color.value == int(Style.RED_COLOR, 16)
    message = ml("default_roles.errors.deactivated.message", locale)
    assert embed.description.startswith(f"{message}\n")


def _error_keys():
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else ""
            name = func.id if isinstance(func, ast.Name) else name
            if name != "response_error_embed":
                continue
            where = f"{path.relative_to(APP.parent)}:{node.lineno}"
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                yield where, argument.value, False
            elif isinstance(argument, ast.JoinedStr) and isinstance(
                argument.values[0], ast.Constant
            ):
                yield where, argument.values[0].value, True


def _blocks(key, prefixed, locale):
    if not prefixed:
        return [key if "." in key else f"errors.{key}"]
    loaded = i18n.keys(locale)
    return sorted(
        {
            key + found[len(key) :].split(".")[0]
            for found in loaded
            if found.startswith(key)
        }
    )


ERROR_KEYS = list(_error_keys())


def test_the_scan_finds_the_error_keys_of_the_commands():
    found = {key for _where, key, _prefixed in ERROR_KEYS}
    assert "default_roles.errors.deactivated" in found
    assert "setup.errors.refused_" in found


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize(
    "where,key,prefixed", ERROR_KEYS, ids=[where for where, _key, _p in ERROR_KEYS]
)
def test_every_error_key_resolves_its_title_and_its_message(
    where, key, prefixed, locale
):
    loaded = i18n.keys(locale)
    blocks = _blocks(key, prefixed, locale)
    missing = [
        f"{block}.{part}"
        for block in blocks
        for part in ("title", "message")
        if not isinstance(loaded.get(f"{block}.{part}"), str)
    ]

    assert blocks, f"{where}: no error is written under {key}"
    assert missing == [], f"{where}: write {missing} in {locale}"


def _command_error_blocks():
    from app.core.commands import commands

    loaded = i18n.keys("en-us")
    for command in commands():
        prefix = f"{command.key}.errors."
        blocks = {key.rsplit(".", 1)[0] for key in loaded if key.startswith(prefix)}
        for block in sorted(blocks):
            yield block


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("block", list(_command_error_blocks()))
def test_every_error_a_command_writes_draws_its_real_title(block, locale):
    """Broke as: default roles filed the panel's lost-roles warning under
    `errors.missing-permissions`, an "error" with a message and no title, so any
    reader that drew it as the error it claims to be would show the admin
    `🚨 default_roles.errors.missing-permissions.title`. Shared behaviour: a
    command's `errors` block, which `response_error_embed`, the forms and the
    validators name by its block. Consumer that exposed it: the default roles
    panel (`DefaultRolesFeature.open`). Guaranteed: every block a command writes
    under `errors` is an error with a title and a message, so it draws with its
    real title; a text that is no error lives in the block that draws it.
    """
    from app.ui.embed import response_error_embed

    loaded = i18n.keys(locale)
    missing = [
        part for part in ("title", "message")
        if not isinstance(loaded.get(f"{block}.{part}"), str)
    ]
    embed = response_error_embed(block, locale)

    assert missing == [], f"{block} is no error: it writes no {missing}"
    assert block not in (embed.title or "") and ".title" not in (embed.title or "")
