from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ErrorContext:
    """Contexto estruturado para erros, facilitando debug e investigação."""

    flow: str
    guild_id: Optional[str] = None
    user_id: Optional[str] = None
    channel_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "flow": self.flow,
            "guild_id": self.guild_id,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
        }
        result.update(self.extra)
        return {k: v for k, v in result.items() if v is not None}

    @classmethod
    def from_member(cls, flow: str, member, **extra) -> "ErrorContext":
        """Cria contexto a partir de um discord.Member."""
        return cls(
            flow=flow,
            guild_id=str(member.guild.id),
            user_id=str(member.id),
            extra={
                "user_name": member.name,
                "user_avatar": str(member.display_avatar.url),
                **extra,
            },
        )

    @classmethod
    def from_message(cls, flow: str, message, **extra) -> "ErrorContext":
        """Cria contexto a partir de um discord.Message."""
        return cls(
            flow=flow,
            guild_id=str(message.guild.id),
            user_id=str(message.author.id),
            channel_id=str(message.channel.id),
            extra={
                "user_name": message.author.name,
                "message_preview": message.content[:200] if message.content else None,
                **extra,
            },
        )

    @classmethod
    def from_interaction(cls, flow: str, interaction, **extra) -> "ErrorContext":
        """Cria contexto a partir de um discord.Interaction."""
        return cls(
            flow=flow,
            guild_id=str(interaction.guild.id) if interaction.guild else None,
            user_id=str(interaction.user.id),
            channel_id=str(interaction.channel.id) if interaction.channel else None,
            extra={
                "user_name": interaction.user.name,
                "interaction_id": str(interaction.id),
                **extra,
            },
        )


class KeikoError(Exception):
    """Base exception com contexto enriquecido para debug."""

    def __init__(self, message: str, context: ErrorContext, original: Exception = None):
        self.message = message
        self.context = context
        self.original = original
        super().__init__(message)
