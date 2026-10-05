from app.constants import Commands
from app.integrations import http_client

STREAMELEMENTS_API_URL = "https://api.streamelements.com/kappa/v2"


class StreamElementsClient:
    def get_channel_info(channel_name: str) -> dict:
        url = f"{STREAMELEMENTS_API_URL}/channels/{channel_name}"
        response = http_client.get(
            "stream_elements", url, timeout=Commands.STREAM_ELEMENTS_TIMEOUT_SECONDS
        )
        return response.json()

    def send_chat_command(self, channel_id: str, command: str) -> dict:
        url = f"{STREAMELEMENTS_API_URL}/bot/{channel_id}/say"
        headers = {
            "Content-Type": "application/json",
        }
        data = {"message": command}
        response = http_client.post(
            "stream_elements",
            url,
            headers=headers,
            json=data,
            timeout=Commands.STREAM_ELEMENTS_TIMEOUT_SECONDS,
        )
        return response.json()

    def get_chat_commands(channel_id: str) -> dict:
        url = f"{STREAMELEMENTS_API_URL}/bot/commands/{channel_id}/public"
        response = http_client.get(
            "stream_elements", url, timeout=Commands.STREAM_ELEMENTS_TIMEOUT_SECONDS
        )
        return response.json()
