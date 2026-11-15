"""The log inspection offers one History button, however often the operator pages.

Broke as: every turn to the guild page added another History button, so paging back
and forth stacked them under the inspection, and the 25th turn would have overflowed
the view.

Shared behaviour: `LogInspectionView.callback` (`app/commands/admin/log_inspection.py`), the
Next button of the inspect action on every log message.
Exposed by: typing the log inspection service under mypy strict.

Guaranteed: the view holds the Next button and, from the guild page on, one History
button.
"""

import pytest

from app.ui.buttons import HistoryButton
from tests.mocks.discord import MockInteraction, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


async def test_paging_back_and_forth_keeps_one_history_button(deps, guild):
    from app.commands.admin.log_inspection import LogInspectionView

    view = LogInspectionView(None, None, "777")

    for _turn in range(4):
        await view.callback(MockInteraction(user=create_member(guild), guild=guild))

    history = [item for item in view.children if isinstance(item, HistoryButton)]
    assert len(history) == 1, f"{len(history)} History buttons after two round trips"
