"""
Mocks para objetos Discord.

Uso:
    from tests.mocks.discord import MockGuild, MockMember, MockChannel

Estes mocks simulam o comportamento dos objetos Discord reais
e fornecem helpers para assertions em testes.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict
from unittest.mock import AsyncMock, MagicMock
import discord


@dataclass
class MockRole:
    """Mock de discord.Role."""
    id: int
    name: str
    position: int = 1
    managed: bool = False
    guild: "MockGuild" = None

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"


@dataclass
class MockChannel:
    """Mock de discord.TextChannel."""
    id: int
    name: str
    guild: "MockGuild" = None

    def __post_init__(self):
        self._sent_messages: List["MockMessage"] = []
        self._send = AsyncMock(side_effect=self._handle_send)
        self._fetch_message = AsyncMock(side_effect=self._handle_fetch_message)

    async def _handle_send(self, content=None, *, embed=None, view=None, delete_after=None, **kwargs):
        """Captura chamadas de send."""
        message = MockMessage(
            id=1000 + len(self._sent_messages),
            content=content or "",
            author=None,
            channel=self,
            guild=self.guild,
            embeds=[embed] if embed else []
        )
        self._sent_messages.append(message)
        return message

    async def send(self, content=None, *, embed=None, view=None, delete_after=None, **kwargs):
        """Envia mensagem no canal."""
        return await self._send(content=content, embed=embed, view=view, delete_after=delete_after, **kwargs)

    async def _handle_fetch_message(self, message_id: int):
        """Busca mensagem pelo ID."""
        for msg in self._sent_messages:
            if msg.id == message_id:
                return msg
        return None

    async def fetch_message(self, message_id: int):
        return await self._fetch_message(message_id)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    # Assertion helpers
    def assert_message_sent(self, **expected):
        """Verifica que send foi chamado com os argumentos esperados."""
        assert len(self._sent_messages) > 0, "Nenhuma mensagem foi enviada"
        if expected:
            self._send.assert_called_with(**expected)

    def assert_no_message_sent(self):
        """Verifica que send NAO foi chamado."""
        assert len(self._sent_messages) == 0, f"Mensagens foram enviadas: {len(self._sent_messages)}"

    def get_last_embed(self) -> Optional[discord.Embed]:
        """Retorna o ultimo embed enviado."""
        if self._sent_messages and self._sent_messages[-1].embeds:
            return self._sent_messages[-1].embeds[0]
        return None

    def get_last_message(self) -> Optional["MockMessage"]:
        """Retorna a ultima mensagem enviada."""
        return self._sent_messages[-1] if self._sent_messages else None

    def get_all_messages(self) -> List["MockMessage"]:
        """Retorna todas as mensagens enviadas."""
        return self._sent_messages.copy()

    def reset(self):
        """Limpa mensagens enviadas."""
        self._sent_messages.clear()
        self._send.reset_mock()


@dataclass
class MockMember:
    """Mock de discord.Member."""
    id: int
    name: str
    guild: "MockGuild" = None
    roles: List[MockRole] = field(default_factory=list)
    bot: bool = False
    display_name: str = None

    def __post_init__(self):
        if self.display_name is None:
            self.display_name = self.name
        self._avatar_url = f"https://cdn.discordapp.com/avatars/{self.id}/fake.png"
        self._add_roles = AsyncMock()
        self._remove_roles = AsyncMock()

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def display_avatar(self):
        mock = MagicMock()
        mock.url = self._avatar_url
        return mock

    @property
    def top_role(self) -> MockRole:
        if not self.roles:
            return MockRole(id=0, name="@everyone", position=0)
        return max(self.roles, key=lambda r: r.position)

    async def add_roles(self, *roles, reason=None):
        await self._add_roles(*roles, reason=reason)
        self.roles.extend(roles)

    async def remove_roles(self, *roles, reason=None):
        self._remove_roles(*roles, reason=reason)
        for role in roles:
            if role in self.roles:
                self.roles.remove(role)

    def assert_roles_added(self, *expected_roles):
        """Verifica que roles foram adicionadas."""
        self._add_roles.assert_called()
        added = self._add_roles.call_args[0]
        for role in expected_roles:
            assert any(r.name == role or r.id == role for r in added), \
                f"Role '{role}' nao foi adicionada"

    def assert_no_roles_added(self):
        """Verifica que nenhuma role foi adicionada."""
        self._add_roles.assert_not_called()


@dataclass
class MockGuild:
    """Mock de discord.Guild."""
    id: int
    name: str
    text_channels: List[MockChannel] = field(default_factory=list)
    roles: List[MockRole] = field(default_factory=list)
    members: List[MockMember] = field(default_factory=list)
    member_count: int = 100
    me: MockMember = None

    def __post_init__(self):
        self._icon_url = f"https://cdn.discordapp.com/icons/{self.id}/fake.png"
        self._channel_map: Dict[int, MockChannel] = {}

        # Conecta canais ao guild
        for channel in self.text_channels:
            channel.guild = self
            self._channel_map[channel.id] = channel

        # Conecta roles ao guild
        for role in self.roles:
            role.guild = self

    @property
    def icon(self):
        mock = MagicMock()
        mock.url = self._icon_url
        return mock

    def get_channel(self, channel_id: int) -> Optional[MockChannel]:
        return self._channel_map.get(channel_id)

    def get_role(self, role_id: int) -> Optional[MockRole]:
        return next((r for r in self.roles if r.id == role_id), None)

    def get_member(self, member_id: int) -> Optional[MockMember]:
        return next((m for m in self.members if m.id == member_id), None)

    def add_channel(self, channel: MockChannel):
        """Adiciona um canal ao guild."""
        channel.guild = self
        self.text_channels.append(channel)
        self._channel_map[channel.id] = channel


@dataclass
class MockMessage:
    """Mock de discord.Message."""
    id: int
    content: str
    author: MockMember
    channel: MockChannel
    guild: MockGuild = None
    embeds: List[Any] = field(default_factory=list)

    def __post_init__(self):
        self._delete = AsyncMock()
        self._edit = AsyncMock()
        self._reply = AsyncMock()
        if self.guild is None and self.channel:
            self.guild = self.channel.guild

    async def delete(self, *, delay=None):
        await self._delete(delay=delay)

    async def edit(self, **kwargs):
        if 'embed' in kwargs:
            self.embeds = [kwargs['embed']]
        await self._edit(**kwargs)

    async def reply(self, content=None, **kwargs):
        return await self._reply(content=content, **kwargs)

    def assert_deleted(self):
        """Verifica que mensagem foi deletada."""
        self._delete.assert_called_once()

    def assert_not_deleted(self):
        """Verifica que mensagem NAO foi deletada."""
        self._delete.assert_not_called()

    def assert_edited(self):
        """Verifica que mensagem foi editada."""
        self._edit.assert_called()


class MockInteraction:
    """Mock de discord.Interaction."""

    def __init__(
        self,
        user: MockMember,
        guild: MockGuild,
        channel: MockChannel = None,
        locale: str = "en-US"
    ):
        self.user = user
        self.guild = guild
        self.guild_id = guild.id
        self.channel = channel or (guild.text_channels[0] if guild.text_channels else None)
        self.locale = discord.Locale(locale)
        self.message = None

        # Response tracking
        self._responses: List[dict] = []
        self._current_modal = None

        self.response = self._create_response()
        self.followup = self._create_followup()

    def _create_response(self):
        response = MagicMock()

        async def send_message(content=None, *, embed=None, view=None, ephemeral=False, delete_after=None, **kwargs):
            self._responses.append({
                "type": "send_message",
                "content": content,
                "embed": embed,
                "view": view,
                "ephemeral": ephemeral,
                "delete_after": delete_after
            })

        async def edit_message(*, content=None, embed=None, view=None, **kwargs):
            self._responses.append({
                "type": "edit_message",
                "content": content,
                "embed": embed,
                "view": view
            })

        async def send_modal(modal):
            self._current_modal = modal
            self._responses.append({
                "type": "send_modal",
                "modal": modal
            })

        async def defer(*, ephemeral=False, thinking=False):
            self._responses.append({"type": "defer", "ephemeral": ephemeral})

        response.send_message = AsyncMock(side_effect=send_message)
        response.edit_message = AsyncMock(side_effect=edit_message)
        response.send_modal = AsyncMock(side_effect=send_modal)
        response.defer = AsyncMock(side_effect=defer)
        response.is_done = MagicMock(return_value=False)

        return response

    def _create_followup(self):
        followup = MagicMock()

        async def edit_message(message_id, *, embed=None, view=None, **kwargs):
            self._responses.append({
                "type": "followup_edit",
                "message_id": message_id,
                "embed": embed,
                "view": view
            })

        async def send(content=None, *, embed=None, view=None, ephemeral=False, **kwargs):
            self._responses.append({
                "type": "followup_send",
                "content": content,
                "embed": embed,
                "view": view,
                "ephemeral": ephemeral
            })

        followup.edit_message = AsyncMock(side_effect=edit_message)
        followup.send = AsyncMock(side_effect=send)

        return followup

    # Helpers para testes
    def get_last_response(self) -> Optional[dict]:
        return self._responses[-1] if self._responses else None

    def get_last_embed(self) -> Optional[discord.Embed]:
        for resp in reversed(self._responses):
            if resp.get("embed"):
                return resp["embed"]
        return None

    def get_pending_modal(self):
        return self._current_modal

    def was_ephemeral_error_sent(self) -> bool:
        """Verifica se uma mensagem de erro efemera foi enviada."""
        for resp in self._responses:
            if resp.get("type") == "send_message" and resp.get("ephemeral"):
                return True
        return False

    def assert_response_sent(self, response_type: str = None, ephemeral: bool = None):
        """Verifica que uma resposta foi enviada."""
        assert len(self._responses) > 0, "Nenhuma resposta foi enviada"
        if response_type:
            assert any(r.get("type") == response_type for r in self._responses), \
                f"Resposta do tipo '{response_type}' nao encontrada"
        if ephemeral is not None:
            assert any(r.get("ephemeral") == ephemeral for r in self._responses), \
                f"Resposta ephemeral={ephemeral} nao encontrada"


# ============================================================================
# FACTORY FUNCTIONS - Criacao simplificada
# ============================================================================

def create_guild(
    id: int = 123456789,
    name: str = "Test Server",
    channels: List[str] = None,
    roles: List[str] = None
) -> MockGuild:
    """
    Cria um MockGuild com configuracao rapida.

    Uso:
        guild = create_guild(channels=["general", "welcome"], roles=["Admin", "Member"])
    """
    channel_list = []
    if channels:
        for i, channel_name in enumerate(channels):
            channel_list.append(MockChannel(id=100 + i, name=channel_name))
    else:
        channel_list = [
            MockChannel(id=100, name="general"),
            MockChannel(id=101, name="welcome"),
            MockChannel(id=102, name="announcements"),
        ]

    role_list = []
    if roles:
        for i, role_name in enumerate(roles):
            role_list.append(MockRole(id=200 + i, name=role_name, position=len(roles) - i))
    else:
        role_list = [
            MockRole(id=200, name="Admin", position=10),
            MockRole(id=201, name="Moderator", position=5),
            MockRole(id=202, name="Member", position=1),
        ]

    guild = MockGuild(
        id=id,
        name=name,
        text_channels=channel_list,
        roles=role_list
    )

    # Bot member
    guild.me = MockMember(
        id=999,
        name="Keiko",
        guild=guild,
        roles=[MockRole(id=999, name="Bot", position=5)]
    )

    return guild


def create_member(
    guild: MockGuild,
    id: int = 111,
    name: str = "TestUser",
    roles: List[str] = None,
    bot: bool = False
) -> MockMember:
    """
    Cria um MockMember vinculado ao guild.

    Uso:
        member = create_member(guild, roles=["Member"])
    """
    role_list = []
    if roles:
        for role_name in roles:
            role = next((r for r in guild.roles if r.name == role_name), None)
            if role:
                role_list.append(role)

    member = MockMember(
        id=id,
        name=name,
        guild=guild,
        roles=role_list,
        bot=bot
    )

    guild.members.append(member)
    return member


def create_message(
    channel: MockChannel,
    author: MockMember,
    content: str = "Test message"
) -> MockMessage:
    """
    Cria uma MockMessage.

    Uso:
        msg = create_message(channel, author, "https://spam.com")
    """
    return MockMessage(
        id=500,
        content=content,
        author=author,
        channel=channel
    )
