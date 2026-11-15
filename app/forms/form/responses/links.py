"""Link parsing shared by the link transform and the link validator."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse


@dataclass(frozen=True)
class ParsedLink:
    """A link split into its host, path and query, scheme and www. dropped."""

    host: str
    path: str
    query: dict[str, str] = field(default_factory=dict)


def parse_link(text: str) -> ParsedLink:
    """Normalize a link or domain: scheme optional, lowercase host, no www."""
    text = str(text).strip()
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()

    if host.startswith("www."):
        host = host[len("www.") :]
    path = parsed.path or ""
    if path.endswith("/"):
        path = path[:-1]
    return ParsedLink(host=host, path=path, query=dict(parse_qsl(parsed.query)))


def link_host(value: str) -> str:
    """The website of a link or domain: no scheme, no www., no path."""
    text = str(value or "").strip()
    return parse_link(text).host if text else ""


def normalize_link(value: str) -> str:
    """The stored form of a link: host, path and query, nothing else."""
    if not value:
        return value
    parsed = parse_link(value)
    normalized = parsed.host + parsed.path
    if parsed.query:
        normalized += f"?{urlencode(parsed.query)}"
    return normalized or value
