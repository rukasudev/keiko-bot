from datetime import datetime

from app.config import AppConfig
from app.integrations import check_integration_enabled, http_client
from app.types.integration import IntegrationBase


class NotionIntegration(IntegrationBase):
    def __init__(self, config: AppConfig):
        self.notion_token = config.NOTION_TOKEN
        self.database_id = config.NOTION_DATABASE_ID
        super().__init__(config.NOTION_ENABLED)

    @check_integration_enabled
    def create_report(
        self, title: str, description: str, command: str, author: str, attachment_url: str
    ) -> None:
        url = "https://api.notion.com/v1/pages"

        headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }

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

        response = http_client.post("notion", url, headers=headers, json=data)

        return response
