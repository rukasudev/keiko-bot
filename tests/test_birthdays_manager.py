from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestBirthdaysManagerEdit:
    @pytest.mark.asyncio
    async def test_edit_save_dispatches_composition_item_to_save_form_birthday_item(self):
        from app.services.reminders_birthdays import edit_birthday_save

        manager_view = MagicMock()
        manager_view.edited_form_view.composition_index = 1

        item = {"user": {"value": "222"}, "date": {"value": "06-20"}}
        data = {
            "reminders_birthday": {
                "values": [{"user": {"value": "111"}}, item],
            },
        }

        interaction = MagicMock()
        interaction.guild_id = "guild-1"
        interaction.locale = "pt-BR"
        interaction.locale = "pt-BR"
        interaction.locale = "pt-BR"

        with patch("app.services.reminders_birthdays.save_form_birthday_item") as save_item:
            await edit_birthday_save(interaction, manager_view, data)

        save_item.assert_called_once_with("guild-1", item)

    @pytest.mark.asyncio
    async def test_edit_save_dispatches_config_changes_to_setup_birthdays(self):
        from app.services.reminders_birthdays import edit_birthday_save

        manager_view = MagicMock()
        manager_view.edited_form_view.composition_index = None

        data = {
            "channel": {"style": "channel", "values": "999"},
            "mention_everyone": {"style": "boolean", "values": "true"},
        }

        interaction = MagicMock()
        interaction.guild_id = "guild-1"
        interaction.locale = "pt-BR"

        with patch("app.services.reminders_birthdays.setup_birthdays") as setup:
            with patch(
                "app.services.reminders_birthdays.birthdays_data.find_birthday_config",
                return_value={"channel_id": "111", "mention_everyone": False},
            ):
                await edit_birthday_save(interaction, manager_view, data)

        setup.assert_called_once_with(
            "guild-1",
            "999",
            True,
            "pt-br",
            None,
            None,
            {"mode": "default", "title": None, "content": None},
        )


class TestBirthdaysManagerComposition:
    @pytest.mark.asyncio
    async def test_add_item_saves_birthday_item_instead_of_generic_cog(self):
        from app.constants import Commands as constants
        from app.views.manager import Manager

        form_item = {
            "user": {"value": "222", "title": "Member", "style": "user"},
            "date": {"value": "05-15", "title": "Birthday", "style": "mm_dd"},
        }
        saved_item = {
            "guild_id": "guild-1",
            "user_id": "222",
            "date": "05-15",
            "reminder_id": "reminder-1",
        }
        summary_item = {
            "user": {"value": "222", "title": "Member", "style": "user"},
            "date": {"value": "05-15", "title": "Birthday", "style": "mm_dd"},
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

        with patch("app.services.reminders_birthdays.save_form_birthday_item", return_value=saved_item) as save_item:
            with patch("app.services.reminders_birthdays.to_summary_composition", return_value=summary_item):
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

        with patch("app.services.reminders_birthdays.remove_birthday") as remove_item:
            with patch("app.views.manager.update_cog_by_guild") as update_cog:
                with patch("app.views.manager.insert_cog_event"):
                    with patch("app.views.manager.parse_command_event_description", return_value="ok"):
                        with patch("app.views.manager.ml", return_value="ok"):
                            await manager.remove_item_callback(interaction, item_removed, {})

        remove_item.assert_called_once_with("guild-1", "222")
        update_cog.assert_not_called()
