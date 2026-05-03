from typing import List, Optional

from discord import app_commands

from app.services.utils import ml
from app.translator import locale_str


MONTH_KEYS: List[str] = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


def get_month_label(month: int, locale: str = None) -> Optional[str]:
    if not 1 <= month <= 12:
        return None
    key = MONTH_KEYS[month - 1]
    return ml(f"commands.commands.commons.months.{key}", locale=locale)


def get_month_choices(namespace: str = "commons") -> List[app_commands.Choice[int]]:
    return [
        app_commands.Choice(
            name=locale_str(key, type=f"months.{key}", namespace=namespace),
            value=index,
        )
        for index, key in enumerate(MONTH_KEYS, start=1)
    ]
