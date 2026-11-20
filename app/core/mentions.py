"""Who a notice pings: the mention it opens with and all Discord may ping in it."""

import re
from dataclasses import dataclass
from typing import Any

import discord


@dataclass(frozen=True)
class Mention:
    """A notice's mention as an admin chose it: nobody, @everyone, @here or roles."""

    kind: str = "none"
    roles: tuple[str, ...] = ()

    @property
    def prefix(self) -> str:
        """The mention written before the notice, empty when it pings nobody."""
        if self.kind in ("everyone", "here"):
            return f"@{self.kind}"
        return " ".join(f"<@&{role}>" for role in self.roles)

    @property
    def allowed(self) -> discord.AllowedMentions:
        """What Discord may ping in the notice: exactly what the mention names."""
        if self.kind in ("everyone", "here"):
            return discord.AllowedMentions(
                everyone=True, users=False, roles=False, replied_user=False
            )
        if not self.roles:
            return discord.AllowedMentions.none()
        return discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[discord.Object(id=int(role)) for role in self.roles],
            replied_user=False,
        )

    def before(self, message: str | None) -> str | None:
        """The notice's text, a typed @everyone or @here written as plain text, with the
        mention in front; None when both are empty."""
        typed = re.sub(r"@(everyone|here)", "@\u200b\\1", message or "")
        return " ".join(part for part in (self.prefix, typed) if part) or None


def mention_of(kind: Any, roles: Any = None) -> Mention:
    """The mention a saved choice names; an unknown or empty one pings nobody."""
    if kind in ("everyone", "here"):
        return Mention(str(kind))
    if kind != "roles":
        return Mention()

    listed = roles if isinstance(roles, (list, tuple)) else [roles]
    ids = tuple(str(role) for role in listed if str(role or "").isdigit())
    return Mention("roles", ids) if ids else Mention()
