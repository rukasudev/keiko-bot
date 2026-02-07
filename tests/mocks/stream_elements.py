"""
Mock da API do StreamElements.

Uso:
    from tests.mocks.stream_elements import MockStreamElementsAPI
"""

from typing import Dict, Any, List, Optional
from unittest.mock import MagicMock


class MockStreamElementsAPI:
    """
    Mock completo da API do StreamElements.

    Uso:
        se = MockStreamElementsAPI()
        se.add_command("hello", "Hello {user}!", channel_id="123")
    """

    def __init__(self):
        self._channels: Dict[str, Dict[str, Any]] = {}
        self._commands: Dict[str, List[Dict[str, Any]]] = {}  # channel_id -> commands

        # Contadores para verificacao
        self.get_commands_calls: List[str] = []

    def add_channel(
        self,
        channel_id: str,
        display_name: str,
        username: str = None
    ) -> "MockStreamElementsAPI":
        """Adiciona um canal."""
        self._channels[channel_id] = {
            "_id": channel_id,
            "displayName": display_name,
            "username": username or display_name.lower()
        }
        if channel_id not in self._commands:
            self._commands[channel_id] = []
        return self

    def add_command(
        self,
        command: str,
        reply: str,
        channel_id: str,
        enabled: bool = True,
        cooldown: int = 5
    ) -> "MockStreamElementsAPI":
        """Adiciona um comando a um canal."""
        if channel_id not in self._commands:
            self._commands[channel_id] = []

        self._commands[channel_id].append({
            "command": command,
            "reply": reply,
            "enabled": enabled,
            "cooldown": {"user": cooldown, "global": cooldown}
        })
        return self

    # Metodos que serao chamados pelo codigo de producao
    def get_channel_info(self, channel_name: str) -> Optional[Dict[str, Any]]:
        """Retorna informacoes do canal."""
        for channel in self._channels.values():
            if channel["username"].lower() == channel_name.lower():
                return channel
        return None

    def get_chat_commands(self, channel_id: str) -> List[Dict[str, Any]]:
        """Retorna comandos do canal."""
        self.get_commands_calls.append(channel_id)
        return self._commands.get(channel_id, [])

    def send_chat_command(self, channel_id: str, command: str) -> Dict[str, Any]:
        """Simula envio de comando."""
        commands = self._commands.get(channel_id, [])
        for cmd in commands:
            if cmd["command"] == command and cmd["enabled"]:
                return {"success": True, "reply": cmd["reply"]}
        return {"success": False, "error": "Command not found"}

    # Assertions
    def assert_commands_fetched(self, channel_id: str):
        """Verifica que get_chat_commands foi chamado."""
        assert channel_id in self.get_commands_calls, \
            f"get_chat_commands nao foi chamado para '{channel_id}'"

    def reset(self):
        """Reseta o estado do mock."""
        self._channels.clear()
        self._commands.clear()
        self.get_commands_calls.clear()
