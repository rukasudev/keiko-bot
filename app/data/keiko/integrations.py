"""keiko.integrations: one document per integration, its switch and settings in
`options`."""

from app.data.collection import Document, Repository


class Integrations(Repository):
    """The integrations Keiko talks to."""

    async def find_integration(self, name: str) -> Document:
        """The options of an integration; empty when it has none saved."""
        document = await self._collection.find_one({"_id": name}) or {}
        return dict(document.get("options") or {})
