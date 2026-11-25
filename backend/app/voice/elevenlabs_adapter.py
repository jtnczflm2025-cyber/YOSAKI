"""V1 metadata, bounded diagnostics, and explicit V2 HTTP synthesis adapter."""
import asyncio
import json
import httpx


class ProviderError(Exception):
    def __init__(self, message: str, *, unknown: bool = False):
        super().__init__(message)
        self.unknown = unknown


class ElevenLabsAdapter:
    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None,
                 proxy_url: str | None = None, *, diagnostics=None):
        self.api_key = api_key
        self.transport = transport
        self.proxy_url = proxy_url
        self.synthesis_phase: str | None = None
        self.diagnostics = diagnostics

    async def _get(self, path: str) -> object:
        try:
            async with httpx.AsyncClient(base_url="https://api.elevenlabs.io", timeout=15,
                                         transport=self.transport, trust_env=False) as client:
                response = await client.get(path, headers={"xi-api-key": self.api_key})
            if response.status_code in (401,403,429):
                raise ProviderError(f"供应商拒绝请求（HTTP {response.status_code}）")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("供应商连接或响应无效") from exc

    async def models(self) -> list[dict]:
        result = await self._get("/v1/models")
        if not isinstance(result, list):
            raise ProviderError("供应商模型响应格式无效")
        return result

    async def voice(self, voice_id: str) -> dict:
        result = await self._get(f"/v1/voices/{voice_id}")
        if not isinstance(result, dict):
            raise ProviderError("供应商声音响应格式无效")
        return result

    async def synthesize_http(self, text: str, voice_id: str, language_code: str) -> bytes:
        """One bounded HTTP TTD request; no redirect, retry, or raw error logging."""
        body: dict = {"model_id": "eleven_v4", "inputs": [{"text": text, "voice_id": voice_id}]}
        if language_code in ("zh", "ja"):
            body["language_code"] = language_code
        elif language_code != "auto":
            raise ValueError("不支持的朗读语言提示")
        self.synthesis_phase = "waiting_response"
        def record(event, **details):
            if self.diagnostics is not None:
                self.diagnostics.event(event, **details)

        async def trace(event: str, _details: dict) -> None:
            if event.endswith(("connect_tcp.started", "start_tls.started")):
                self.synthesis_phase = "connecting"
                record("connecting")
            elif event.endswith(("send_request_headers.started", "receive_response_headers.started")):
                self.synthesis_phase = "waiting_response"
                record("waiting_response")

        chunks: list[bytes] = []
        size = 0
        try:
            async with httpx.AsyncClient(base_url="https://api.elevenlabs.io",
                                         transport=self.transport, proxy=self.proxy_url,
                                         trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(30)) as client:
                record("dispatch_attempt")
                async with client.stream("POST", "/v1/text-to-dialogue",
                                         params={"output_format": "mp3_44100_128"},
                                         headers={"xi-api-key": self.api_key}, json=body,
                                         extensions={"trace": trace}) as response:
                    record("response_headers", http_status=response.status_code)
                    if response.status_code != 200:
                        raise ProviderError(
                            f"供应商未返回可用音频（HTTP {response.status_code}）",
                            unknown=response.status_code == 408 or response.status_code >= 500)
                    self.synthesis_phase = "receiving_audio"
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 16 * 1024 * 1024:
                            raise ProviderError("供应商音频超过上限", unknown=True)
                        chunks.append(chunk)
                        if self.diagnostics is not None:
                            self.diagnostics.chunk(chunk)
                        record("first_audio" if len(chunks) == 1 else "receive_progress", audio_bytes=size)
                    record("receive_complete", audio_bytes=size)
                record("response_closed")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as exc:
            self.synthesis_phase = "connecting"
            raise ProviderError("供应商连接失败，请求结果未知", unknown=True) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("供应商请求超时，请求结果未知；实际扣费以账户记录为准", unknown=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError("供应商连接中断，请求结果未知；实际扣费以账户记录为准", unknown=True) from exc
        if not chunks:
            raise ProviderError("供应商返回空音频，请求结果未知；实际扣费以账户记录为准", unknown=True)
        return b"".join(chunks)


async def bounded_ttd_idle_probe(socket, *, duration_seconds: float,
                                 keepalive_seconds: float = 10.0) -> int:
    """Exercise an already-authenticated socket, with a strict local deadline.

    The caller owns the approved connection and must supply its own approved
    budget. This helper never opens a connection or sends synthesis text.
    """
    if not 0 < duration_seconds <= 55 or not 0 < keepalive_seconds <= 10:
        raise ValueError("probe duration exceeds V1 limits")
    sent = 0
    try:
        async with asyncio.timeout(duration_seconds + 1):
            deadline = asyncio.get_running_loop().time() + duration_seconds
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, keepalive_seconds))
                if deadline - asyncio.get_running_loop().time() <= 0:
                    break
                await socket.send(json.dumps({"keep_alive": True}))
                sent += 1
                pong = await socket.ping()
                await asyncio.wait_for(pong, timeout=min(5, max(0.1, deadline - asyncio.get_running_loop().time())))
    finally:
        await socket.close()
    return sent
