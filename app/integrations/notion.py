from datetime import datetime
from typing import Dict, Optional

import requests

from app.config import AppConfig
from app.integrations import check_integration_enabled, http_patch, http_post
from app.types.integration import IntegrationBase


class NotionIntegration(IntegrationBase):
    def __init__(self, config: AppConfig):
        self.notion_token = config.NOTION_TOKEN
        self.database_id = config.NOTION_DATABASE_ID
        super().__init__(config.NOTION_ENABLED)

    @check_integration_enabled
    def create_report(
        self,
        title: str,
        description: str,
        command: str,
        author: str,
        attachment_url: Optional[str],
    ) -> requests.Response:
        url = "https://api.notion.com/v1/pages"

        data = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "Titulo": {"title": [{"text": {"content": title}}]},
                "Descrição": {"rich_text": [{"text": {"content": description}}]},
                "Comando": {"rich_text": [{"text": {"content": command}}]},
                "Author": {"rich_text": [{"text": {"content": author}}]},
                "Anexo": {"url": attachment_url},
                "Data de criação": {"date": {"start": datetime.now().isoformat()}},
            },
        }

        response = http_post("notion", url, headers=self._headers(), json=data)

        return response

    @check_integration_enabled
    def update_status(self, page_id: str, name: str) -> requests.Response:
        """Set the `Status` of the report page `page_id` to its option `name`."""
        url = f"https://api.notion.com/v1/pages/{page_id}"
        data = {"properties": {"Status": {"status": {"name": name}}}}

        return http_patch("notion", url, headers=self._headers(), json=data)

    @staticmethod
    def error_message(response: requests.Response) -> str:
        """What Notion said when it refused a call: the `message` of its JSON error,
        else the start of the body it sent."""
        try:
            message = response.json().get("message")
        except ValueError:
            message = None
        return str(message or response.text[:200])

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
