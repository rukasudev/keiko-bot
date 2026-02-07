"""
Generators para criacao de dados de teste no banco.

Uso:
    from tests.generators import moderations, notifications

    await moderations.welcome_messages(db, guild_id="123")
    await notifications.twitch(db, guild_id="123", streamers=[...])
"""

from tests.generators import moderations
from tests.generators import notifications

__all__ = ["moderations", "notifications"]
