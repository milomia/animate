"""Fetch a URL for ingestion, with the limits a server-side fetch needs.

Only http(s); a response is capped in size and time; redirects are followed by hand so every hop is
re-checked; and by default a URL that resolves to a private, loopback or link-local address is refused,
so the service cannot be pointed at internal systems. In an air-gapped deployment the documents ARE
internal: set INGEST_ALLOW_PRIVATE_URLS=1 there.

Known gap: the host is resolved once for the check and again by the HTTP client, so a hostile DNS server
could answer differently the second time (DNS rebinding). Pin the resolved address if that matters.
"""
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import httpx

from .models import IngestionError

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 3
ACCEPTED = {"text/html": "html", "application/xhtml+xml": "html", "application/pdf": "pdf",
            "text/plain": "text", "text/markdown": "markdown"}


def _allow_private() -> bool:
    return os.getenv("INGEST_ALLOW_PRIVATE_URLS", "").lower() in {"1", "true", "yes"}


def check_url(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise IngestionError("Only http:// and https:// URLs are supported.")
    if _allow_private():
        return
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise IngestionError(f"Could not resolve the host '{parts.hostname}'.") from exc
    if any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise IngestionError("That URL points to a private or local address, which is not allowed.")


def fetch_url(url: str):
    """(bytes, kind) where kind is "html", "pdf", "text" or "markdown"."""
    with httpx.Client(follow_redirects=False, timeout=TIMEOUT_SECONDS,
                      headers={"User-Agent": "aimate-ingest/1.0"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            try:
                with client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        raise IngestionError(f"The server answered {response.status_code} for that URL.")
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    kind = ACCEPTED.get(content_type)
                    if kind is None:
                        raise IngestionError(f"Unsupported content type '{content_type or 'unknown'}'.")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise IngestionError(f"The page is larger than {MAX_BYTES // (1024 * 1024)} MB.")
                    return bytes(data), kind
            except httpx.HTTPError as exc:
                raise IngestionError(f"Could not fetch the URL: {type(exc).__name__}: {exc}") from exc
        raise IngestionError("Too many redirects.")
