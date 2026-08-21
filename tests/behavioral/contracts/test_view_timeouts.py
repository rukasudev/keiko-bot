"""Timeout contract for the interaction views.

There is deliberately NO custom on_timeout behavior in Keiko (buttons just
stop responding and the ephemeral message ages out) — this suite pins that
fact and the configured timeout values, so an accidental `timeout=None`
(making a non-persistent view immortal) or a rogue on_timeout override
fails loudly instead of shipping silently.
"""
import discord
import pytest

from app.components.modals import ConfirmationModal, CustomModal
from app.components.select_views import FileUploadModal, MultiSelectView
from app.constants import ViewConstants
from app.views.confirm_action import ConfirmActionView
from app.views.form import Form
from app.views.options import OptionsView

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

LONG = ViewConstants.LONG_TIMEOUT_SECONDS    # interactive configuration surfaces
SHORT = ViewConstants.SHORT_TIMEOUT_SECONDS  # modals and confirmation prompts


def test_form_and_option_views_use_the_long_timeout():
    form = Form("block_links", "pt-br")
    options = OptionsView(options=["A"], callback=None, locale="pt-br")
    multi = MultiSelectView(config={"selects": []}, callback=None, locale="pt-br")
    assert form.timeout == LONG
    assert options.timeout == LONG
    assert multi.timeout == LONG


def test_modals_and_confirmations_use_the_short_timeout():
    modal = CustomModal(
        {"key": "x", "title": {"pt-br": "T", "en-us": "T"},
         "label": {"pt-br": "L", "en-us": "L"}},
        callback=None, locale="pt-br", cogs={},
    )
    ConfirmationModal(action="Pausar", locale="pt-br", callback=None)  # constructs
    upload = FileUploadModal(callback=None, locale="pt-br", title="T")
    confirm_action = ConfirmActionView(on_confirm=None, locale="pt-br")
    assert modal.timeout == SHORT
    assert upload.timeout == SHORT
    assert confirm_action.timeout == SHORT


def test_only_the_session_views_define_timeout_behavior():
    """Form and Manager own a session, so they close its story when the view
    expires. Every other view still has nothing to do on timeout — the buttons
    simply stop responding. A new override here needs behavioral coverage and
    this pin updated."""
    import app.views.confirm_action as confirm_module
    import app.views.form as form_module
    import app.views.manager as manager_module
    import app.views.summary_card as card_module

    assert "on_timeout" in form_module.Form.__dict__
    assert "on_timeout" in manager_module.Manager.__dict__

    allowed = {form_module.Form, manager_module.Manager}
    for module in (form_module, manager_module, card_module, confirm_module):
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) \
                    and issubclass(obj, (discord.ui.View, discord.ui.LayoutView)) \
                    and obj.__module__ == module.__name__ \
                    and obj not in allowed:
                assert "on_timeout" not in obj.__dict__, (
                    f"{module.__name__}.{name} now overrides on_timeout: add a "
                    f"behavioral scenario for it and update this contract"
                )


async def test_a_timeout_never_says_anything_to_the_user():
    """The user closed Discord or walked away. Timing out is bookkeeping, not
    a conversation — it must not try to send or edit anything."""
    from unittest.mock import AsyncMock, MagicMock

    form = Form("block_links", "pt-br")
    form.session.guild_id = "1"
    form.session.user_id = "9"
    form.view = MagicMock()
    form.view.send = AsyncMock()

    await form.on_timeout()

    assert not form.view.send.await_args_list
