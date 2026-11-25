"""TTD WebSocket connector; construction is offline and dispatch is caller gated."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from websockets.asyncio.client import connect


TTD_ORIGIN = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"


@dataclass(frozen=True, repr=False)
class RealTTDConnector:
    api_key: str
    proxy_url: str | None

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("api_key_missing")
        if self.proxy_url is not None and not self.proxy_url.startswith("http://127.0.0.1:"):
            raise ValueError("unsupported_proxy")

    @staticmethod
    def url() -> str:
        return TTD_ORIGIN + "?" + urlencode({
            "model_id": "eleven_v4_turbo",
            "output_format": "pcm_24000",
            "language_code": "ja",
        })

    def __call__(self):
        # No request occurs until the returned context manager is entered.
        return connect(self.url(), additional_headers={"xi-api-key": self.api_key},
                       open_timeout=5, close_timeout=1, ping_interval=None,
                       max_size=12 * 1024 * 1024, proxy=self.proxy_url)
