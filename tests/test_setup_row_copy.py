"""Every command /setup offers writes its row's copy in its own copy.yml, in both
locales.

/setup, the greeting, the stats embed and the recent-activity line read these fixed
keys of each command through `command_text`, so the literal scan of
`tests/test_commands.py` cannot see one go missing; it would show as a raw key.
"""

import i18n
import pytest

from app.core import i18n as copy
from app.core.commands import copy_file, setup_features

pytestmark = pytest.mark.unit

LOCALES = ("en-us", "pt-br")
ROW_KEYS = (
    "command.setup.label",
    "command.setup.description",
    "command.stats.title",
    "command.stats.delivered.one",
    "command.stats.delivered.other",
    "command.slash.subgroup",
    "command.slash.name",
)


@pytest.mark.parametrize("command", setup_features(), ids=lambda command: command.key)
@pytest.mark.parametrize("locale", LOCALES)
def test_a_setup_row_writes_its_copy_in_its_own_folder(command, locale):
    path = copy_file(command.key)
    assert path is not None, f"write the copy.yml of {command.key}"
    own = dict(copy.written(path, ""))

    for key in ROW_KEYS:
        assert key in own, f"write {key} in {path.parent.name}/copy.yml"
        assert i18n.t(f"{command.key}.{key}", locale=locale) == own[key][locale]
