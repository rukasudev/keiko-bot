"""The admin's flows through each command, named once for every test that walks them.

One module per command folder of `app/commands/` (`block_links`, `birthdays`,
`roles`, `stream_elements`, `twitch`, `welcome`, `youtube`, `setup`) plus `common`
for what every form shares. A module holds the command's saved documents (`FORM`,
`ENABLED`, `PAUSED`), the seeds that put them in the mock Mongo, and one function per
intention that takes more than a click (open the card, pick the channel, type the
streamer). Tests and the golden drivers import these public names; a step added or
removed in the UI edits one function here.
"""

import importlib
from types import ModuleType


def module_of(form: str) -> ModuleType:
    """The flows module of the command whose form key is `form`: its folder's name."""
    from app.core.commands import form_file

    path = form_file(form)
    if path is None:
        raise KeyError(f"{form!r} has no form, so no flows module")
    return importlib.import_module(f"{__name__}.{path.parent.name}")
