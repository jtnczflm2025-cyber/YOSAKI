"""Small provider boundary. Network methods are never called during app startup."""
from typing import Protocol


class VoiceProvider(Protocol):
    async def models(self) -> list[dict]: ...
    async def voice(self, voice_id: str) -> dict: ...
