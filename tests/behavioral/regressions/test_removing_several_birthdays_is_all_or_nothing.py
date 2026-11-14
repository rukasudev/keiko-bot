"""Regression: removing several birthdays removes every one chosen or none.

What broke: the birthday feature's `commit_remove_item` deleted each chosen
member as it went, skipping one that was already gone, so when another admin
removed one of the chosen members after the panel was drawn, the others were
deleted anyway and the panel never heard the list had changed. Every other
list feature refuses the whole removal (`ListChanged`) in that case.

Shared behavior affected: the all-or-nothing contract of a multi-item removal
(`RemovalConfirmed` in app/settings/form/manager.py, the `ListChanged` refusal
the adapter turns into the stale notice), here implemented by
`BirthdayFeature.commit_remove_item` (app/settings/features/reminders_birthday.py)
on its own collection.

Exposed by: the review of reminders_birthday (beta.2 corrections, K2): the
removal the manager hands over names two members and one went meanwhile.

Must remain guaranteed: when any chosen member is no longer saved, no birthday
is deleted and the commit refuses with `ListChanged` (the stale notice); when
every chosen member is saved, all of them go.
"""

from datetime import datetime, timezone

import pytest

from app.data import wait
from app.services.reminders_birthdays import find_birthdays_by_guild, remove_birthday
from tests.generators.birthdays import saved_birthday
from app.settings.features import feature_for
from app.settings.features.feature import CommitContext, ListChanged
from tests.behavioral.golden.paths.common import GUILD_ID

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


async def _saved():
    return sorted(item["user_id"] for item in await find_birthdays_by_guild(GUILD_ID))


def _removal(*user_ids):
    items = [{"user": {"value": user_id}} for user_id in user_ids]
    return {"indexes": list(range(len(items))), "items": items}


async def _remove(*user_ids):
    context = CommitContext(GUILD_ID, "555", "pt-br", {}, NOW, "slash", "s1")
    return await feature_for("reminders_birthday").commit(
        "remove_item", _removal(*user_ids), context
    )


async def test_a_removal_whose_member_went_meanwhile_removes_nobody(deps):
    saved_birthday(GUILD_ID, "555", "05-12")
    saved_birthday(GUILD_ID, "777", "06-12")
    await remove_birthday(GUILD_ID, "777")

    with pytest.raises(ListChanged):
        await _remove("555", "777")

    assert await _saved() == ["555"], "the member still saved was deleted anyway"


async def test_a_removal_of_members_all_saved_removes_them_all(deps):
    saved_birthday(GUILD_ID, "555", "05-12")
    saved_birthday(GUILD_ID, "777", "06-12")

    await _remove("555", "777")

    assert await _saved() == []

