"""The stored configuration of a feature, in the shape its YAML declares.

A YAML-driven cog stores exactly the keys its form names, so its document *is*
that shape and the default provider is a plain read. A feature that owns its
persistence is the exception the reports kept tripping on: birthdays store the
channel as `channel_id`, fold three settings into a nested `default_message`,
and keep the birthdays themselves in the `reminders` database. A reader that
walks the raw document finds none of those keys and concludes, wrongly, that
nobody uses the settings.

The translation already exists — it is what the manager renders — so this
registry points at it instead of describing the storage a second time. One
mapping, two consumers: a report can never disagree with the screen.

Providers are named as `module:function` and imported on use, so a report can
depend on a feature service without the service importing the report back.
"""
from importlib import import_module
from typing import Any, Callable, Dict, Final, List

from app.constants import Commands as constants
from app.data import cogs as cogs_data

PROVIDERS: Final[Dict[str, str]] = {
    constants.REMINDERS_BIRTHDAY_KEY: (
        "app.services.reminders_birthdays:birthday_config_states"
    ),
}


def feature_config_states(feature: str) -> List[Dict[str, Any]]:
    """One entry per guild that configured the feature, keyed as the form is."""
    provider = PROVIDERS.get(feature)
    if not provider:
        return cogs_data.find_all_cogs(feature)
    return _resolve(provider)()


def _resolve(path: str) -> Callable[[], List[Dict[str, Any]]]:
    module_name, function_name = path.split(":")
    return getattr(import_module(module_name), function_name)
