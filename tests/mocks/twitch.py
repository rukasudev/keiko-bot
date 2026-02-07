"""
Mock da API da Twitch.

Uso:
    from tests.mocks.twitch import MockTwitchAPI, TwitchFixtures

Este mock simula todas as operacoes da TwitchClient real,
permitindo configurar streamers e seus estados para testes.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from unittest.mock import MagicMock


@dataclass
class StreamInfo:
    """Dados de uma stream."""
    id: str = "stream-12345"
    user_name: str = "test_streamer"
    user_login: str = "test_streamer"
    game_name: str = "Just Chatting"
    title: str = "Test Stream!"
    viewer_count: int = 1000
    started_at: str = None
    thumbnail_url: str = "https://static-cdn.jtvnw.net/previews-ttv/live_user_{width}x{height}.jpg"

    def __post_init__(self):
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_name": self.user_name,
            "user_login": self.user_login,
            "game_name": self.game_name,
            "title": self.title,
            "viewer_count": self.viewer_count,
            "started_at": self.started_at,
            "thumbnail_url": self.thumbnail_url
        }


@dataclass
class UserInfo:
    """Dados de um usuario Twitch."""
    id: str = "12345"
    login: str = "test_streamer"
    display_name: str = "Test Streamer"
    profile_image_url: str = "https://static-cdn.jtvnw.net/user-default-pictures/avatar.jpg"
    description: str = "Test user description"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "login": self.login,
            "display_name": self.display_name,
            "profile_image_url": self.profile_image_url,
            "description": self.description
        }


class MockTwitchAPI:
    """
    Mock completo da API da Twitch.

    Uso:
        twitch = MockTwitchAPI()
        twitch.add_user("gaules", user_id="123")
        twitch.set_stream_online("gaules", game="CS2")

        # Nos testes
        with patch('app.bot.twitch', twitch):
            await handle_notification("gaules")
    """

    def __init__(self):
        self._users: Dict[str, UserInfo] = {}
        self._streams: Dict[str, Optional[StreamInfo]] = {}
        self._subscriptions: List[dict] = []
        self._subscription_counter: int = 0

        # Contadores para verificacao
        self.get_user_info_calls: List[str] = []
        self.get_stream_info_calls: List[str] = []
        self.subscribe_calls: List[dict] = []
        self.unsubscribe_calls: List[str] = []

    def add_user(
        self,
        login: str,
        user_id: str = None,
        display_name: str = None,
        profile_image_url: str = None,
        description: str = None
    ) -> "MockTwitchAPI":
        """Adiciona um usuario valido."""
        self._users[login.lower()] = UserInfo(
            id=user_id or str(abs(hash(login)) % 1000000),
            login=login.lower(),
            display_name=display_name or login.title(),
            profile_image_url=profile_image_url or f"https://static-cdn.jtvnw.net/user-default-pictures/{login}.jpg",
            description=description or f"Mock user {login}"
        )
        return self

    def set_stream_online(
        self,
        login: str,
        game: str = "Just Chatting",
        title: str = "Live!",
        viewers: int = 100,
        started_at: datetime = None
    ) -> "MockTwitchAPI":
        """Define que um streamer esta online."""
        user = self._users.get(login.lower())
        if not user:
            raise ValueError(f"User {login} not added. Call add_user first.")

        self._streams[login.lower()] = StreamInfo(
            id=str(abs(hash(f"{login}-stream")) % 1000000),
            user_name=user.display_name,
            user_login=user.login,
            game_name=game,
            title=title,
            viewer_count=viewers,
            started_at=(started_at or datetime.now(timezone.utc)).isoformat()
        )
        return self

    def set_stream_offline(self, login: str) -> "MockTwitchAPI":
        """Define que um streamer esta offline."""
        self._streams[login.lower()] = None
        return self

    # Metodos que serao chamados pelo codigo de producao
    def get_user_id_from_login(self, login: str) -> Optional[str]:
        """Retorna ID do usuario ou None se nao existe."""
        self.get_user_info_calls.append(login)
        user = self._users.get(login.lower())
        return user.id if user else None

    def get_user_info(self, login: str) -> Optional[Dict[str, Any]]:
        """Retorna informacoes do usuario."""
        self.get_user_info_calls.append(login)
        user = self._users.get(login.lower())
        return user.to_dict() if user else None

    def get_user_info_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Retorna informacoes do usuario por ID."""
        for user in self._users.values():
            if user.id == user_id:
                return user.to_dict()
        return None

    def get_stream_info(self, login: str) -> Optional[Dict[str, Any]]:
        """Retorna informacoes da stream ou None se offline."""
        self.get_stream_info_calls.append(login)
        stream = self._streams.get(login.lower())
        return stream.to_dict() if stream else None

    def subscribe_to_stream_online_event(self, user_id: str) -> MagicMock:
        """Simula criacao de subscription online."""
        self._subscription_counter += 1
        sub = {
            "id": f"sub-online-{user_id}-{self._subscription_counter}",
            "type": "stream.online",
            "user_id": user_id
        }
        self.subscribe_calls.append(sub)

        # Verifica se ja existe subscription para este user
        existing = [s for s in self._subscriptions if s["user_id"] == user_id and s["type"] == "stream.online"]

        response = MagicMock()
        if existing:
            response.status_code = 409  # Conflict - already subscribed
        else:
            self._subscriptions.append(sub)
            response.status_code = 202  # Accepted
        response.json.return_value = sub
        return response

    def subscribe_to_stream_offline_event(self, user_id: str) -> MagicMock:
        """Simula criacao de subscription offline."""
        self._subscription_counter += 1
        sub = {
            "id": f"sub-offline-{user_id}-{self._subscription_counter}",
            "type": "stream.offline",
            "user_id": user_id
        }
        self.subscribe_calls.append(sub)

        response = MagicMock()
        self._subscriptions.append(sub)
        response.status_code = 202
        response.json.return_value = sub
        return response

    def unsubscribe_from_stream_event(self, subscription_id: str) -> MagicMock:
        """Simula remocao de subscription."""
        self.unsubscribe_calls.append(subscription_id)
        self._subscriptions = [s for s in self._subscriptions if s["id"] != subscription_id]

        response = MagicMock()
        response.status_code = 204
        return response

    def get_subscriptions(self) -> Dict[str, Any]:
        """Retorna todas as subscriptions."""
        return {"data": self._subscriptions.copy()}

    def get_subscription_by_user_id(self, user_id: str) -> Dict[str, Any]:
        """Retorna subscriptions para um user_id especifico."""
        subs = [s for s in self._subscriptions if s.get("user_id") == user_id or
                s.get("condition", {}).get("broadcaster_user_id") == user_id]
        return {"data": subs}

    # Assertions
    def assert_user_checked(self, login: str):
        """Verifica que get_user_info foi chamado para este login."""
        assert login.lower() in [l.lower() for l in self.get_user_info_calls], \
            f"get_user_info nao foi chamado para '{login}'. Chamados: {self.get_user_info_calls}"

    def assert_stream_checked(self, login: str):
        """Verifica que get_stream_info foi chamado."""
        assert login.lower() in [l.lower() for l in self.get_stream_info_calls], \
            f"get_stream_info nao foi chamado para '{login}'. Chamados: {self.get_stream_info_calls}"

    def assert_subscribed(self, user_id: str, event_type: str = None):
        """Verifica que uma subscription foi criada."""
        matching = [s for s in self.subscribe_calls if s["user_id"] == user_id]
        if event_type:
            matching = [s for s in matching if s["type"] == event_type]
        assert len(matching) > 0, \
            f"Nenhuma subscription criada para user_id={user_id}, type={event_type}"

    def assert_unsubscribed(self, subscription_id: str = None):
        """Verifica que uma subscription foi removida."""
        if subscription_id:
            assert subscription_id in self.unsubscribe_calls, \
                f"Subscription {subscription_id} nao foi removida"
        else:
            assert len(self.unsubscribe_calls) > 0, "Nenhuma subscription foi removida"

    def reset(self):
        """Reseta o estado do mock."""
        self._users.clear()
        self._streams.clear()
        self._subscriptions.clear()
        self.get_user_info_calls.clear()
        self.get_stream_info_calls.clear()
        self.subscribe_calls.clear()
        self.unsubscribe_calls.clear()
