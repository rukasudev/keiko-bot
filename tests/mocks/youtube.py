"""
Mock da API do YouTube.

Uso:
    from tests.mocks.youtube import MockYouTubeAPI

Este mock simula as operacoes do YoutubeClient real.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from unittest.mock import MagicMock


@dataclass
class VideoInfo:
    """Dados de um video YouTube."""
    id: str = "dQw4w9WgXcQ"
    title: str = "Test Video"
    description: str = "Test video description"
    channel_id: str = "UC12345"
    channel_title: str = "Test Channel"
    published_at: str = None
    thumbnail_url: str = "https://i.ytimg.com/vi/{id}/maxresdefault.jpg"
    tags: List[str] = None

    def __post_init__(self):
        if self.published_at is None:
            self.published_at = datetime.now(timezone.utc).isoformat()
        if self.tags is None:
            self.tags = ["test", "video"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "snippet": {
                "title": self.title,
                "description": self.description,
                "channelId": self.channel_id,
                "channelTitle": self.channel_title,
                "publishedAt": self.published_at,
                "tags": self.tags,
                "thumbnails": {
                    "maxres": {"url": self.thumbnail_url.format(id=self.id)}
                }
            }
        }


@dataclass
class ChannelInfo:
    """Dados de um canal YouTube."""
    id: str = "UC12345"
    title: str = "Test Channel"
    description: str = "Test channel description"
    custom_url: str = "@testchannel"
    thumbnail_url: str = "https://yt3.ggpht.com/channel_avatar.jpg"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "snippet": {
                "title": self.title,
                "description": self.description,
                "customUrl": self.custom_url,
                "thumbnails": {
                    "default": {"url": self.thumbnail_url}
                }
            }
        }


class MockYouTubeAPI:
    """
    Mock completo da API do YouTube.

    Uso:
        youtube = MockYouTubeAPI()
        youtube.add_channel("UC12345", "TestChannel", "@testchannel")
        youtube.add_video("abc123", "UC12345", "New Video!")
    """

    def __init__(self):
        self._channels: Dict[str, ChannelInfo] = {}
        self._videos: Dict[str, VideoInfo] = {}
        self._subscriptions: List[dict] = []

        # Contadores para verificacao
        self.get_channel_info_calls: List[str] = []
        self.get_video_info_calls: List[str] = []
        self.subscribe_calls: List[str] = []
        self.unsubscribe_calls: List[str] = []

    def add_channel(
        self,
        channel_id: str,
        title: str,
        custom_url: str = None,
        description: str = None
    ) -> "MockYouTubeAPI":
        """Adiciona um canal."""
        self._channels[channel_id] = ChannelInfo(
            id=channel_id,
            title=title,
            description=description or f"Channel {title}",
            custom_url=custom_url or f"@{title.lower().replace(' ', '')}"
        )
        return self

    def add_video(
        self,
        video_id: str,
        channel_id: str,
        title: str,
        description: str = None,
        tags: List[str] = None
    ) -> "MockYouTubeAPI":
        """Adiciona um video."""
        channel = self._channels.get(channel_id)
        if not channel:
            raise ValueError(f"Channel {channel_id} not added. Call add_channel first.")

        self._videos[video_id] = VideoInfo(
            id=video_id,
            title=title,
            description=description or f"Video {title}",
            channel_id=channel_id,
            channel_title=channel.title,
            tags=tags
        )
        return self

    # Metodos que serao chamados pelo codigo de producao
    def get_channel_id_from_username(self, username: str) -> Optional[str]:
        """Retorna ID do canal pelo username."""
        username_lower = username.lower().replace("@", "")
        for channel in self._channels.values():
            custom_url = channel.custom_url.lower().replace("@", "")
            if custom_url == username_lower or channel.title.lower() == username_lower:
                return channel.id
        return None

    def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Retorna informacoes do canal."""
        self.get_channel_info_calls.append(channel_id)
        channel = self._channels.get(channel_id)
        return channel.to_dict() if channel else None

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Retorna informacoes do video."""
        self.get_video_info_calls.append(video_id)
        video = self._videos.get(video_id)
        return video.to_dict() if video else None

    def subscribe_to_new_video_event(self, channel_id: str) -> MagicMock:
        """Simula subscription PubSubHubbub."""
        self.subscribe_calls.append(channel_id)
        self._subscriptions.append({"channel_id": channel_id, "type": "new_video"})
        response = MagicMock()
        response.status_code = 202
        return response

    def unsubscribe_from_new_video_event(self, channel_id: str) -> MagicMock:
        """Simula unsubscribe PubSubHubbub."""
        self.unsubscribe_calls.append(channel_id)
        self._subscriptions = [s for s in self._subscriptions if s["channel_id"] != channel_id]
        response = MagicMock()
        response.status_code = 202
        return response

    # Assertions
    def assert_channel_checked(self, channel_id: str):
        """Verifica que get_channel_info foi chamado."""
        assert channel_id in self.get_channel_info_calls, \
            f"get_channel_info nao foi chamado para '{channel_id}'"

    def assert_video_checked(self, video_id: str):
        """Verifica que get_video_info foi chamado."""
        assert video_id in self.get_video_info_calls, \
            f"get_video_info nao foi chamado para '{video_id}'"

    def assert_subscribed(self, channel_id: str):
        """Verifica que uma subscription foi criada."""
        assert channel_id in self.subscribe_calls, \
            f"Nenhuma subscription criada para channel_id={channel_id}"

    def reset(self):
        """Reseta o estado do mock."""
        self._channels.clear()
        self._videos.clear()
        self._subscriptions.clear()
        self.get_channel_info_calls.clear()
        self.get_video_info_calls.clear()
        self.subscribe_calls.clear()
        self.unsubscribe_calls.clear()
