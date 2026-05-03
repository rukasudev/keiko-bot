from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestBirthdaysManagerEdit:
    @pytest.mark.asyncio
    async def test_member_edit_uses_month_form_and_preserves_customizations(self):
        from app.services.birthdays import _open_member_birthday_form

        captured = {}

        class FakeForm:
            def __init__(self, command_key, locale, steps=None, cogs=None):
                self.command_key = command_key
                self.locale = locale
                self.steps = steps
                self.cogs = cogs
                self.responses = []
                captured["form"] = self

            def filter_steps(self, steps):
                self.filtered_steps = steps

            def _set_after_callback(self, callback):
                self.after_callback = callback

            async def _callback(self, interaction):
                self.callback_interaction = interaction

        item = {
            "user_id": "222",
            "date": "05-15",
            "message": {"mode": "custom", "title": "Parabens", "content": "Feliz aniversario"},
            "image": {"mode": "custom", "url": "https://example.com/birthday.png"},
        }
        yaml_steps = [
            {
                "key": "reminders_birthday",
                "steps": [
                    {"key": "user", "action": "user_select"},
                    {"key": "month", "action": "month_select"},
                    {"key": "date", "action": "modal"},
                ],
            }
        ]
        interaction = MagicMock()
        interaction.guild_id = "guild-1"
        interaction.locale = "pt-br"
        view = MagicMock()
        view.get_response.return_value = "222"

        with patch("app.views.form.Form", FakeForm):
            with patch("app.services.birthdays.parse_form_yaml_to_dict", return_value=yaml_steps):
                with patch("app.services.birthdays.birthdays_data.find_birthday_item", return_value=item):
                    with patch("app.services.birthdays.upsert_birthday") as upsert:
                        with patch(
                            "app.services.birthdays.response_embed",
                            return_value=MagicMock(description="Atualizei para **{date}**."),
                        ):
                            await _open_member_birthday_form(interaction, view, "pt-br")

                            form = captured["form"]
                            assert form.steps == yaml_steps[0]["steps"]
                            assert form.filtered_steps == ["month", "date"]
                            assert form.cogs == {
                                "date": {"style": "birthday_date", "values": "05-15"}
                            }
                            assert form.callback_interaction is interaction

                            form.responses = [{"key": "date", "value": "06-20"}]
                            submit_interaction = MagicMock()
                            submit_interaction.guild_id = "guild-1"
                            submit_interaction.response.send_message = AsyncMock()

                            await form.after_callback(submit_interaction)

        upsert.assert_called_once_with(
            "guild-1",
            "222",
            "06-20",
            message=item["message"],
            image=item["image"],
        )
        submit_interaction.response.send_message.assert_awaited_once()
        embed = submit_interaction.response.send_message.await_args.kwargs["embed"]
        assert "20 de junho" in embed.description


class TestBirthdaysManagerComposition:
    @pytest.mark.asyncio
    async def test_add_item_saves_birthday_item_instead_of_generic_cog(self):
        from app.constants import Commands as constants
        from app.views.manager import Manager

        form_item = {
            "user": {"value": "222", "title": "Member", "style": "user"},
            "date": {"value": "05-15", "title": "Birthday", "style": "birthday_date"},
        }
        saved_item = {
            "guild_id": "guild-1",
            "user_id": "222",
            "date": "05-15",
            "reminder_id": "reminder-1",
        }
        summary_item = {
            "user": {"value": "222", "title": "Member", "style": "user"},
            "date": {"value": "05-15", "title": "Birthday", "style": "birthday_date"},
            "reminder_id": "reminder-1",
        }

        manager = Manager.__new__(Manager)
        manager.command_key = constants.REMINDERS_BIRTHDAY_KEY
        manager.cogs = {constants.REMINDERS_BIRTHDAY_KEY: {"values": []}}
        manager.locale = "en-us"
        manager.interaction = MagicMock()
        manager.lifecycle_callbacks = {}

        manager.form_view = MagicMock()
        manager.form_view.pre_finish_step = AsyncMock()
        manager.form_view._parse_responses_to_cog.return_value = {
            constants.REMINDERS_BIRTHDAY_KEY: {"values": [form_item]}
        }

        interaction = MagicMock()
        interaction.guild_id = "guild-1"
        interaction.guild.id = "guild-1"
        interaction.user.id = "admin-1"
        interaction.response.defer = AsyncMock()
        interaction.followup.edit_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.message.id = "message-1"
        interaction.message.embeds = [MagicMock()]

        async def add_item_callback(interaction, manager_view, response):
            saved = save_item(str(interaction.guild_id), response)
            if saved:
                manager_view.cogs[constants.REMINDERS_BIRTHDAY_KEY]["values"].append(summary_item)

        manager.lifecycle_callbacks = {"add_item": add_item_callback}

        with patch("app.services.birthdays.save_form_birthday_item", return_value=saved_item) as save_item:
            with patch("app.services.birthdays.to_summary_composition", return_value=summary_item):
                with patch("app.views.manager.update_cog_by_guild") as update_cog:
                    with patch("app.views.manager.insert_cog_event"):
                        with patch("app.views.manager.parse_command_event_description", return_value="ok"):
                            with patch("app.views.manager.ml", return_value="ok"):
                                await manager.add_item_callback(interaction)

        save_item.assert_called_once_with("guild-1", form_item)
        update_cog.assert_not_called()
        assert manager.cogs[constants.REMINDERS_BIRTHDAY_KEY]["values"] == [summary_item]

    @pytest.mark.asyncio
    async def test_remove_item_removes_birthday_item_instead_of_generic_cog(self):
        from app.constants import Commands as constants
        from app.views.manager import Manager

        manager = Manager.__new__(Manager)
        manager.command_key = constants.REMINDERS_BIRTHDAY_KEY
        manager.locale = "en-us"
        manager.interaction = MagicMock()
        manager.lifecycle_callbacks = {}

        interaction = MagicMock()
        interaction.guild_id = "guild-1"
        interaction.guild.id = "guild-1"
        interaction.user.id = "admin-1"
        interaction.response.defer = AsyncMock()
        interaction.followup.edit_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.message.id = "message-1"
        interaction.message.embeds = [MagicMock()]

        item_removed = {"user": {"value": "222"}}

        async def remove_item_callback(interaction, manager_view, item_removed, new_cogs):
            user = item_removed.get("user")
            remove_item(str(interaction.guild_id), user.get("value"))

        manager.lifecycle_callbacks = {"remove_item": remove_item_callback}

        with patch("app.services.birthdays.remove_birthday") as remove_item:
            with patch("app.views.manager.update_cog_by_guild") as update_cog:
                with patch("app.views.manager.insert_cog_event"):
                    with patch("app.views.manager.parse_command_event_description", return_value="ok"):
                        with patch("app.views.manager.ml", return_value="ok"):
                            await manager.remove_item_callback(interaction, item_removed, {})

        remove_item.assert_called_once_with("guild-1", "222")
        update_cog.assert_not_called()
