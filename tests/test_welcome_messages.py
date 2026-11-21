"""A member who joins is welcomed in the channel the server chose: the saved
message, its placeholders filled once, under the banner of the chosen design; and
nothing at all when the welcome is not set up or its channel is gone."""

import logging
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import AsyncMock

import pytest

from app.commands.welcome import service as welcome_messages
from app.commands.welcome.service import parse_welcome_messages, send_welcome_message
from app.constants import Commands
from app.core import images
from tests.generators import moderations
from tests.mocks import create_guild, create_member

REAL_CREATE_BANNER = welcome_messages.create_banner
REAL_UPLOAD_ASSET = images.upload_asset
BANNER = f"attachment://{Commands.WELCOME_BANNER_FILENAME}"


async def set_up_welcome(deps, guild, **saved):
    """Save the server's welcome, in the shape the card saves it."""
    saved.setdefault("channel_id", str(guild.text_channels[0].id))
    await moderations.welcome_messages(deps.mongo_client, guild_id=str(guild.id), **saved)


def sent(guild):
    return [message for channel in guild.text_channels for message in channel.get_all_messages()]


class TestWelcomeMessagesSend:
    """What reaches the channel when a member joins."""

    async def test_a_new_member_is_welcomed_in_the_chosen_channel(self, deps, guild):
        """The saved message, `{user}` filled with the member's mention, under the
        banner drawn for the server and attached to the message."""
        await set_up_welcome(deps, guild, title="Welcome!", messages=["Hello {user}!"])
        new_member = create_member(guild, id=999, name="NewUser")

        await send_welcome_message(new_member)

        [welcome] = sent(guild)
        assert welcome.channel is guild.text_channels[0]
        assert welcome.embeds[0].description == "Hello <@!999>!"
        assert welcome.embeds[0].image.url == BANNER
        assert [file.filename for file in welcome.attachments] == [
            Commands.WELCOME_BANNER_FILENAME
        ]

    async def test_nothing_is_sent_when_the_welcome_is_not_set_up(self, guild):
        await send_welcome_message(create_member(guild, id=999, name="NewUser"))

        assert sent(guild) == []

    @pytest.mark.parametrize(
        "saved",
        [
            {"channel_id": "999999"},
            {"channel_id": None},
            {"messages": []},
        ],
        ids=["channel-gone", "no-channel", "no-message"],
    )
    async def test_nothing_is_sent_when_the_channel_or_the_message_is_missing(
        self, deps, guild, saved
    ):
        """A deleted channel, or a welcome saved without a channel or a message, sends
        nothing and raises nothing."""
        await set_up_welcome(deps, guild, **saved)

        await send_welcome_message(create_member(guild, id=999, name="NewUser"))

        assert sent(guild) == []


class TestWelcomeMessagesDesign:
    """The banner each design puts on the welcome."""

    @pytest.mark.parametrize("design", [None, "server_blur", "custom_blur"])
    async def test_a_drawn_design_attaches_its_banner(self, deps, guild, design):
        """The server's blur (also when no design was saved) and the custom blur are
        drawn and attached to the welcome."""
        await set_up_welcome(
            deps, guild, design=design, custom_image="https://example.com/custom.png"
        )

        await send_welcome_message(create_member(guild, id=999, name="NewUser"))

        [welcome] = sent(guild)
        assert welcome.embeds[0].image.url == BANNER
        assert [file.filename for file in welcome.attachments] == [
            Commands.WELCOME_BANNER_FILENAME
        ]

    async def test_the_custom_image_design_shows_the_image_as_saved(self, deps, guild):
        """`custom_only` shows the admin's image itself: nothing is drawn or attached."""
        image = "https://example.com/custom.gif"
        await set_up_welcome(deps, guild, design="custom_only", custom_image=image)

        await send_welcome_message(create_member(guild, id=999, name="NewUser"))

        [welcome] = sent(guild)
        assert welcome.embeds[0].image.url == image
        assert welcome.attachments == []


class TestGenerateDesignPreviews:
    """Testes para geracao de previews de design."""

    async def test_generates_preview_for_each_design(
        self, mock_banner, guild, bot, monkeypatch
    ):
        """
        Verifica que previews sao gerados para cada design.

        Input: Lista de 3 designs
        Output: Dict com preview URL para cada design
        """
        from app.commands.welcome.service import generate_design_previews

        # Arrange
        monkeypatch.setattr(welcome_messages, "create_banner", REAL_CREATE_BANNER)
        monkeypatch.setattr(images, "upload_asset", REAL_UPLOAD_ASSET)
        member = create_member(guild, id=999, name="TestUser")
        designs = ["server_blur", "custom_blur", "custom_only"]
        uploaded = "https://cdn.discordapp.com/attachments/1/2/custom_only_preview.gif"
        bot.get_channel.return_value.send = AsyncMock(
            return_value=SimpleNamespace(attachments=[SimpleNamespace(url=uploaded)])
        )

        # Act
        previews = await generate_design_previews(member, designs, "Welcome")

        # Assert
        assert "server_blur" in previews
        assert "custom_blur" in previews
        assert "custom_only" in previews
        assert urlparse(previews["custom_only"]).hostname == "cdn.discordapp.com"

    async def test_handles_unknown_design_key(
        self, mock_banner, guild
    ):
        """
        Verifica que design desconhecido e ignorado sem erro.

        Input: Design com key desconhecido
        Output: Preview nao incluido, sem excecao
        """
        from app.commands.welcome.service import generate_design_previews

        # Arrange
        member = create_member(guild, id=999, name="TestUser")
        designs = ["unknown_design"]

        # Act
        previews = await generate_design_previews(member, designs, "Welcome")

        # Assert
        assert "unknown_design" not in previews

    async def test_a_design_whose_banner_fails_is_left_out_with_a_warning(
        self, guild, monkeypatch, caplog
    ):
        """A preview that cannot be drawn is left out of the gallery and logged as a
        warning; the others still come."""
        from app.commands.welcome.service import generate_design_previews

        monkeypatch.setattr(
            welcome_messages,
            "create_banner",
            AsyncMock(side_effect=Exception("Banner generation failed")),
        )
        member = create_member(guild, id=999, name="TestUser")

        with caplog.at_level("INFO"):
            previews = await generate_design_previews(member, ["server_blur"], "Welcome")

        assert "server_blur" not in previews
        assert any(
            record.levelno == logging.WARNING and "server_blur" in record.getMessage()
            for record in caplog.records
        )


def newcomer(guild_name="Test Server"):
    guild = create_guild(name=guild_name)
    return create_member(guild, id=999, name="Newcomer")


@pytest.mark.behavioral
@pytest.mark.regression
async def test_a_server_named_with_braces_is_welcomed_as_it_is_named(deps):
    """A server or member name with braces in it is printed as typed, not filled in.

    Reported in the v1 review, after wave 1 moved the notices to `fill_placeholders`: the
    birthday message and the welcome message still chained `.replace`, so a value filled in
    first was read as a template by the next one. A member named "{server}" was celebrated
    as the server's name, and a server named "{member_count} fans" welcomed people to "100
    fans". The welcome looked for `{user}` in any capitals but filled only the lowercase one,
    so "Hi {USER}!" mentioned nobody at all.

    Shared behaviour: `fill_placeholders` (`app/core/utils.py`), now behind every
    template an admin writes: the notices, the welcome and the birthday celebration.
    Exposed by: the wave 1 review of the notices' placeholders.

    Guaranteed: every placeholder is filled in one pass, a value is never read as a
    template, and the welcome's mention is added on its own line whenever `{user}` is not
    written in lowercase, like a notice's link.
    """
    member = newcomer(guild_name="{member_count} fans")
    channel = member.guild.text_channels[0]
    await moderations.welcome_messages(
        deps.mongo_client,
        guild_id=str(member.guild.id),
        channel_id=str(channel.id),
        messages=["Welcome to {server}, {user}!"],
        footer="",
    )

    await send_welcome_message(member)

    assert channel.get_last_embed().description == "Welcome to {member_count} fans, <@!999>!"


@pytest.mark.behavioral
@pytest.mark.regression
def test_a_user_placeholder_in_capitals_is_text_and_the_mention_is_still_added():
    welcome = parse_welcome_messages("Hi {USER}!", newcomer())

    assert welcome == "Hi {USER}!\n<@!999>"


class TestParseWelcomeMessages:
    """A saved welcome filled for the member who joined."""

    @pytest.fixture
    def member(self):
        guild = create_guild(name="Test Server")
        for index in range(99):
            create_member(guild, id=1000 + index, name=f"Member {index}")
        return create_member(guild, id=123, name="Newcomer")

    @pytest.mark.parametrize(
        "template,welcome",
        [
            ("Hello {user}!", "Hello <@!123>!"),
            ("Welcome to {server}!", "Welcome to Test Server!\n<@!123>"),
            ("We are {member_count} members!", "We are 100 members!\n<@!123>"),
            (
                "{user} joined {server}! Now we have {member_count} members!",
                "<@!123> joined Test Server! Now we have 100 members!",
            ),
            ("Welcome to the server!", "Welcome to the server!\n<@!123>"),
        ],
        ids=["user", "server", "member-count", "all-three", "mention-added"],
    )
    def test_each_placeholder_is_filled_and_the_mention_always_comes(
        self, member, template, welcome
    ):
        assert parse_welcome_messages(template, member) == welcome

    def test_one_of_several_saved_messages_is_picked_at_random(self, member):
        messages = "Msg1 {user};Msg2 {user};Msg3 {user}"

        results = {parse_welcome_messages(messages, member) for _ in range(50)}

        assert len(results) > 1
        assert results <= {f"Msg{n} <@!123>" for n in (1, 2, 3)}

