"""Contract: slash-command copy in commands.*.yml must satisfy Discord's
API limits, which are only enforced at tree-sync time (bot startup).

Incident (2026-07-27): the block-links `desc:` was rewritten past 100
characters; every test stayed green because the suites enter at the
service seam and never build the command tree — the bot then failed to
start with "description_localizations.*: Must be between 1 and 100 in
length" for every Discord locale.
"""
import pytest
import yaml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("command_copy")]

LOCALES = ["en-us", "pt-br"]
DESCRIPTION_LIMIT = 100   # Discord: command/group descriptions are 1..100
NAME_LIMIT = 32           # Discord: command/group names are 1..32, no spaces
# Context-menu commands (see app/cogs/base/translate.py, log.py): Discord
# allows mixed case and spaces in THEIR names, unlike slash commands.
CONTEXT_MENU_NAMESPACES = {
    "translate-message", "log-inspection", "user-inspect", "block-links-check",
}


def _commands_yaml(locale: str) -> dict:
    path = f"app/languages/commands/commands.{locale}.yml"
    with open(path, "r") as handle:
        return yaml.safe_load(handle)[locale]


def _walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}" if path else str(key))
    else:
        yield path, node


@pytest.mark.parametrize("locale", LOCALES)
def test_command_descriptions_fit_discord_limit(locale):
    data = _commands_yaml(locale)
    tree_copy = {"commands": data.get("commands", {}), "groups": data.get("groups", {})}
    # Only `commands.<ns>.desc` feeds slash-command descriptions (see
    # app/translator.py); deeper `.desc` keys are embed/UI copy.
    violations = [
        (path, len(str(value)))
        for path, value in _walk(tree_copy)
        if path.startswith("commands.") and path.endswith(".desc")
        and path.count(".") == 2
        and not (1 <= len(str(value)) <= DESCRIPTION_LIMIT)
    ]
    assert not violations, (
        f"commands.{locale}.yml has descs outside Discord's 1..{DESCRIPTION_LIMIT} "
        f"char limit (the bot will fail to start on sync): {violations}"
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_command_names_fit_discord_limit(locale):
    data = _commands_yaml(locale)
    tree_copy = {"commands": data.get("commands", {}), "groups": data.get("groups", {})}
    violations = []
    for path, value in _walk(tree_copy):
        leaf = path.rsplit(".", 1)[-1]
        is_command_name = (
            path.startswith("commands.") and path.count(".") == 2
            and leaf in ("name", "subgroup")
        )
        is_group_name = path.startswith("groups.") and path.count(".") == 1
        if is_command_name or is_group_name:
            namespace = path.split(".")[1]
            allow_spaces = namespace in CONTEXT_MENU_NAMESPACES
            text = str(value)
            if not (1 <= len(text) <= NAME_LIMIT) or (" " in text and not allow_spaces):
                violations.append((path, text))
    assert not violations, (
        f"commands.{locale}.yml has command names breaking Discord's "
        f"1..{NAME_LIMIT}-chars/no-spaces rule: {violations}"
    )
