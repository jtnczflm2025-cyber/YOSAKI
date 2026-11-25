"""Isolated public-chat-delta to V4 output bridge.

This module has no route, database import, model call, or provider constructor.
The caller must supply a trusted SSE stream, a connector, and an output sink.
The normal chat persistence and Memory injection paths are untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .readable_text import public_paragraphs
from .realtime_output import OutputLimits, OutputRelay


SENTENCE_END = frozenset("。！？!?.")
SENTENCE_CLOSERS = frozenset('”"\'』」）)】')
SOFT_BREAK = frozenset("，、；;,:： ")
MAX_FRAME_BYTES = 16_384


@dataclass(frozen=True)
class BridgeLimits:
    max_segment_chars: int = 400
    max_total_chars: int = 1_600
    max_segments: int = 8
    max_source_chars: int = 20_000
    max_audio_bytes: int = 8 * 1024 * 1024
    max_turn_seconds: float = 60.0
    max_provider_connections: int = 8
    wait_seconds: float = 0.8
    wait_min_chars: int = 24

    def __post_init__(self) -> None:
        if not (24 <= self.max_segment_chars <= 1_000 and
                self.max_segment_chars <= self.max_total_chars <= 8_000 and
                1 <= self.max_segments <= self.max_provider_connections <= 8 and
                self.max_source_chars >= self.max_total_chars and
                0 < self.max_audio_bytes <= 8 * 1024 * 1024 and
                0 < self.max_turn_seconds <= 120 and
                0.1 <= self.wait_seconds <= 5 and
                1 <= self.wait_min_chars <= self.max_segment_chars):
            raise ValueError("invalid_bridge_limits")


@dataclass(frozen=True)
class SpeechSpan:
    source_start: int
    source_end: int
    spoken_text: str
    reason: str
    source_sha256: str


class PublicDeltaBuffer:
    """Only release closed, Markdown-safe prefixes of the public text channel."""

    def __init__(self, limits: BridgeLimits) -> None:
        self.limits = limits
        self.source = ""
        self.cursor = 0
        self.last_delta_at = time.monotonic()
        self.skipped: set[str] = set()

    @staticmethod
    def _boundaries(raw: str, *, allow_terminal: bool) -> list[tuple[int, str]]:
        boundaries: list[tuple[int, str]] = []
        fence: tuple[str, int] | None = None
        ticks = 0
        brackets = 0
        link_target = 0
        html_paragraph = False
        i = 0
        while i < len(raw):
            at_line_start = i == 0 or raw[i - 1] == "\n"
            if at_line_start:
                k = i
                while k < len(raw) and k - i < 4 and raw[k] == " ":
                    k += 1
                if k - i <= 3 and k < len(raw) and raw[k] in "`~":
                    marker = raw[k]
                    end = k
                    while end < len(raw) and raw[end] == marker:
                        end += 1
                    count = end - k
                    if count >= 3 and (fence is None or
                                       fence[0] == marker and count >= fence[1]):
                        fence = None if fence is not None else (marker, count)
                        newline = raw.find("\n", end)
                        i = len(raw) if newline < 0 else newline
                        continue
                if raw.startswith("    ", i) or raw[i] == "\t":
                    newline = raw.find("\n", i)
                    i = len(raw) if newline < 0 else newline
                    continue
            char = raw[i]
            if char == "\n":
                if (i + 1 < len(raw) and raw[i + 1] == "\n" and
                        fence is None and not ticks and not brackets and not link_target):
                    boundaries.append((i + 2, "paragraph"))
                    html_paragraph = False
                    i += 2
                    continue
                i += 1
                continue
            if fence is not None:
                i += 1
                continue
            if char == "`":
                end = i
                while end < len(raw) and raw[end] == "`":
                    end += 1
                count = end - i
                ticks = 0 if ticks == count else count if ticks == 0 else ticks
                i = end
                continue
            if ticks:
                i += 1
                continue
            if char == "<":
                # An HTML/control-bearing paragraph is dropped in full at its
                # blank-line boundary. A partial tag never becomes speech.
                html_paragraph = True
            if char == "[":
                brackets += 1
            elif char == "]" and brackets:
                brackets -= 1
                if i + 1 < len(raw) and raw[i + 1] == "(":
                    link_target = 1
                    i += 2
                    continue
            elif link_target and char == "(":
                link_target += 1
            elif link_target and char == ")":
                link_target -= 1
            if not html_paragraph and not brackets and not link_target:
                if char in SENTENCE_END:
                    if char == ".":
                        previous = raw[:i].split()[-1].lower() if raw[:i].split() else ""
                        following = raw[i + 1] if i + 1 < len(raw) else ""
                        if ((previous[-1:].isdigit() and following.isdigit()) or
                                previous.startswith(("https://", "http://", "www.")) or
                                previous in {"dr", "mr", "ms", "mrs", "etc", "e.g", "i.e"}):
                            i += 1
                            continue
                    end = i + 1
                    while end < len(raw) and raw[end] in SENTENCE_CLOSERS:
                        end += 1
                    if end < len(raw) or allow_terminal:
                        boundaries.append((end, "sentence"))
                    i = end
                    continue
            i += 1
        return boundaries

    def _choices(self, raw: str, *, wait: bool, final: bool) -> list[tuple[int, str]]:
        # Use the same scanner for sentence/paragraph boundaries, then add
        # conservative length and silence cuts only outside open syntax.
        choices = self._boundaries(raw, allow_terminal=wait or final)
        if choices and choices[0][0] <= self.limits.max_segment_chars:
            return choices
        # A forced cut must not divide a tag, code delimiter, link destination,
        # or URL. Sentence/paragraph cuts above are syntax-aware; otherwise
        # hold the entire uncertain region rather than releasing a fragment.
        if any(marker in raw for marker in ("<", ">", "`", "[", "]",
                                             "http://", "https://", "www.")):
            return []
        safe = min(len(raw), self.limits.max_segment_chars)
        while safe > 0 and safe < len(raw) and (
            unicodedata.combining(raw[safe]) or raw[safe] in "\ufe0e\ufe0f\u200d" or
            raw[safe - 1] == "\u200d"
        ):
            safe -= 1
        if len(raw) >= self.limits.max_segment_chars and safe:
            near = [i + 1 for i, ch in enumerate(raw[:safe])
                    if i >= safe // 2 and ch in SOFT_BREAK]
            return [(max(near) if near else safe, "length_limit")]
        if choices:
            return choices
        if wait and len(raw) >= self.limits.wait_min_chars:
            soft = [i + 1 for i, ch in enumerate(raw)
                    if i + 1 >= self.limits.wait_min_chars and ch in SOFT_BREAK]
            if soft:
                return [(max(soft), "wait_limit")]
        if final and raw:
            return [(len(raw), "end_of_message")]
        return []

    def _drain(self, *, wait: bool = False, final: bool = False) -> list[SpeechSpan]:
        spans: list[SpeechSpan] = []
        while self.cursor < len(self.source):
            pending = self.source[self.cursor:]
            choices = self._choices(pending, wait=wait, final=final)
            if not choices:
                break
            end, reason = choices[0]
            if end <= 0:
                break
            part = pending[:end]
            start = self.cursor
            self.cursor += end
            if "<" in part:
                self.skipped.add("HTML 或控制标记")
                continue
            spoken = "\n\n".join(public_paragraphs(part, self.skipped)).strip()
            if not spoken:
                continue
            if len(spoken) > self.limits.max_segment_chars:
                raise ValueError("segment_character_budget")
            spans.append(SpeechSpan(start, self.cursor, spoken, reason,
                hashlib.sha256(part.encode("utf-8")).hexdigest()))
        return spans

    def feed(self, delta: str, *, now: float | None = None) -> list[SpeechSpan]:
        if not isinstance(delta, str) or not delta:
            return []
        if len(self.source) + len(delta) > self.limits.max_source_chars:
            raise ValueError("source_character_budget")
        self.source += delta
        self.last_delta_at = time.monotonic() if now is None else now
        return self._drain()

    def tick(self, *, now: float | None = None) -> list[SpeechSpan]:
        current = time.monotonic() if now is None else now
        return self._drain(wait=current - self.last_delta_at >= self.limits.wait_seconds)

    def finish(self) -> list[SpeechSpan]:
        spans = self._drain(final=True)
        if self.cursor < len(self.source):
            self.skipped.add("未闭合或无法确认的增量结构")
        return spans


class ChatSSEDecoder:
    """Frame existing SSE; ignore every channel except its public text_delta."""

    def __init__(self) -> None:
        self.buffer = ""
        self.sequence = 0

    def feed(self, chunk: str) -> list[tuple[int, str, dict[str, Any]]]:
        self.buffer += chunk
        result = []
        while "\n\n" in self.buffer:
            frame, self.buffer = self.buffer.split("\n\n", 1)
            if len(frame.encode("utf-8")) > MAX_FRAME_BYTES:
                raise ValueError("chat_sse_frame_limit")
            event = next((line[7:] for line in frame.splitlines()
                          if line.startswith("event: ")), "")
            data = next((line[6:] for line in frame.splitlines()
                         if line.startswith("data: ")), "")
            if event not in {"message_start", "text_delta", "message_complete",
                             "error", "tool_batch_waiting", "text_replace"}:
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ValueError("chat_sse_invalid_json") from exc
            if not isinstance(payload, dict):
                raise ValueError("chat_sse_invalid_payload")
            self.sequence += 1
            result.append((self.sequence, event, payload))
        if len(self.buffer.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ValueError("chat_sse_frame_limit")
        return result


async def _call(callback: Callable, value: Any) -> None:
    result = callback(value)
    if inspect.isawaitable(result):
        await result


class ChatSpeechBridge:
    """One ephemeral chat run, sequential speech segments, no automatic replay."""

    def __init__(self, *, chat_run_id: str, character_id: int,
                 conversation_id: int, voice_id: str,
                 connector: Callable[[], Any], emit: Callable[[dict], Awaitable[None]],
                 clear_playback: Callable[[str], Any],
                 cancel_chat: Callable[[str], Any] | None = None,
                 owner_id: int | None = None,
                 generation_id: str | None = None,
                 verify_saved: Callable[[dict[str, Any]], Any] | None = None,
                 reserve_segment: Callable[[int], Any] | None = None,
                 on_segment_pre_send: Callable[[int, SpeechSpan], Any] | None = None,
                 accept_audio: Callable[[int], Any] | None = None,
                 require_lifecycle: bool = False,
                 limits: BridgeLimits | None = None,
                 output_limits: OutputLimits | None = None) -> None:
        if not chat_run_id or not voice_id or character_id < 1 or conversation_id < 1:
            raise ValueError("invalid_chat_binding")
        self.chat_run_id = chat_run_id
        if require_lifecycle and (not owner_id or not generation_id or verify_saved is None):
            raise ValueError("lifecycle_binding_required")
        self.owner_id = owner_id
        self.generation_id = generation_id
        self.verify_saved = verify_saved
        self.reserve_segment = reserve_segment
        self.on_segment_pre_send = on_segment_pre_send
        self.accept_audio = accept_audio
        self.require_lifecycle = require_lifecycle
        self.character_id = character_id
        self.conversation_id = conversation_id
        self.turn_id = uuid.uuid4().hex
        self.limits = limits or BridgeLimits()
        self.buffer = PublicDeltaBuffer(self.limits)
        self.emit = emit
        self.clear_playback = clear_playback
        self.cancel_chat_callback = cancel_chat
        self.relay = OutputRelay(voice_id=voice_id, connector=connector,
                                 emit=self._relay_event,
                                 limits=output_limits or OutputLimits(
                                     total_seconds=min(45, self.limits.max_turn_seconds),
                                     reconnect_attempts=0,
                                     max_audio_bytes=self.limits.max_audio_bytes))
        self.queue: asyncio.Queue[tuple[int, SpeechSpan] | None] = asyncio.Queue(
            maxsize=self.limits.max_segments + 1)
        self.worker: asyncio.Task | None = None
        self.current: tuple[int, SpeechSpan] | None = None
        self.epoch = 0
        self.started = False
        self.chat_complete = False
        self.voice_stopped = False
        self.failure: str | None = None
        self.last_sequence = 0
        self.total_chars = 0
        self.total_audio_bytes = 0
        self.sent: list[tuple[int, SpeechSpan]] = []
        self.next_segment_sequence = 0
        self.started_at = time.monotonic()

    @property
    def public_text(self) -> str:
        return self.buffer.source

    def _enqueue(self, spans: list[SpeechSpan]) -> None:
        for span in spans:
            sequence = self.next_segment_sequence + 1
            if (sequence > self.limits.max_segments or
                    self.total_chars + len(span.spoken_text) > self.limits.max_total_chars or
                    sequence > self.limits.max_provider_connections):
                raise ValueError("speech_character_or_segment_budget")
            self.total_chars += len(span.spoken_text)
            self.queue.put_nowait((sequence, span))
            self.next_segment_sequence = sequence

    async def _relay_event(self, event: dict) -> None:
        current = self.current
        if self.voice_stopped or current is None or \
                event.get("request_id") != f"{self.turn_id}:{current[0]}":
            return
        if event["type"] == "audio":
            raw = event.get("pcm_base64", "")
            import base64
            try:
                size = len(base64.b64decode(raw, validate=True))
            except Exception:
                size = self.limits.max_audio_bytes + 1
            if self.total_audio_bytes + size > self.limits.max_audio_bytes:
                self.failure = "total_audio_budget"
                raise ValueError("audio_budget_exceeded")
            if self.accept_audio is not None:
                try:
                    self.accept_audio(size)
                except ValueError as exc:
                    self.failure = str(exc)[:80]
                    raise
            self.total_audio_bytes += size
        await self.emit({"turn_id": self.turn_id, "chat_run_id": self.chat_run_id,
                         "chat_generation_id": self.generation_id,
                         "segment_sequence": current[0], "source_start": current[1].source_start,
                         "source_end": current[1].source_end, **event})

    async def _run(self) -> None:
        try:
            while not self.voice_stopped:
                item = await self.queue.get()
                if item is None:
                    break
                if time.monotonic() - self.started_at >= self.limits.max_turn_seconds:
                    self.failure = "turn_time_budget"
                    break
                self.current = item
                sequence, span = item
                if self.reserve_segment is not None:
                    self.reserve_segment(len(span.spoken_text))
                if self.on_segment_pre_send is not None:
                    self.on_segment_pre_send(sequence, span)
                run = await self.relay.start(request_id=f"{self.turn_id}:{sequence}",
                                             text=span.spoken_text)
                self.sent.append(item)
                remaining = self.limits.max_turn_seconds - (time.monotonic() - self.started_at)
                try:
                    await asyncio.wait_for(run.task, timeout=max(0.001, remaining))
                except TimeoutError:
                    self.failure = "turn_time_budget"
                    await self.relay.stop()
                    break
                if run.final_type != "completed" or self.failure:
                    self.failure = self.failure or "voice_provider_failed"
                    break
                self.current = None
            if self.failure and not self.voice_stopped:
                await self.stop_voice(self.failure)
            elif not self.voice_stopped and self.chat_complete:
                await self.emit({"type": "voice_turn_completed", "turn_id": self.turn_id,
                                 "chat_run_id": self.chat_run_id,
                                 "segments_sent": len(self.sent),
                                 "total_characters": self.total_chars,
                                 "audio_bytes": self.total_audio_bytes})
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            self.failure = str(exc)[:80]
            if not self.voice_stopped:
                await self.stop_voice(self.failure)
        except Exception:
            self.failure = "bridge_dispatch_failed"
            if not self.voice_stopped:
                await self.stop_voice(self.failure)

    async def accept(self, *, run_id: str, sequence: int, event: str,
                     data: dict[str, Any]) -> None:
        if run_id != self.chat_run_id or self.chat_complete or sequence <= self.last_sequence:
            return
        if sequence != self.last_sequence + 1:
            await self.stop_voice("chat_event_gap")
            return
        self.last_sequence = sequence
        if event == "message_start":
            if self.started or data.get("conversation_id") != self.conversation_id:
                await self.stop_voice("chat_start_mismatch")
                return
            if self.require_lifecycle and (data.get("owner_id") != self.owner_id or
                    data.get("character_id") != self.character_id or
                    data.get("generation_id") != self.generation_id):
                await self.stop_voice("chat_identity_mismatch")
                return
            self.started = True
            self.worker = asyncio.create_task(self._run())
            return
        if not self.started:
            return
        if event == "text_delta":
            if not isinstance(data.get("text"), str):
                await self.stop_voice("invalid_public_delta")
                return
            try:
                spans = self.buffer.feed(data["text"])
                if not self.voice_stopped:
                    self._enqueue(spans)
            except (ValueError, asyncio.QueueFull) as exc:
                await self.stop_voice(str(exc))
        elif event == "message_complete":
            self.chat_complete = True
            if data.get("conversation_id") != self.conversation_id or \
                    data.get("generation_status", "completed") != "completed":
                await self.stop_voice("generation_incomplete")
                return
            if self.require_lifecycle and (data.get("generation_id") != self.generation_id or
                    not isinstance(data.get("message_id"), int) or
                    not isinstance(data.get("message_version_sha256"), str) or
                    not isinstance(data.get("public_text_sha256"), str)):
                await self.stop_voice("completion_receipt_missing")
                return
            final_hash = data.get("public_text_sha256")
            if final_hash is not None and final_hash != hashlib.sha256(
                    self.public_text.encode("utf-8")).hexdigest():
                await self.stop_voice("final_text_mismatch")
                return
            if self.verify_saved is not None:
                try:
                    verified = self.verify_saved(data)
                    if inspect.isawaitable(verified):
                        verified = await verified
                except Exception:
                    verified = False
                if verified is not True:
                    await self.stop_voice("saved_message_mismatch")
                    return
            if not self.voice_stopped:
                try:
                    self._enqueue(self.buffer.finish())
                    self.queue.put_nowait(None)
                except (ValueError, asyncio.QueueFull) as exc:
                    await self.stop_voice(str(exc))
        elif event in ("error", "tool_batch_waiting", "text_replace"):
            await self.stop_voice("generation_interrupted" if event != "text_replace"
                                  else "source_rewrite_unsupported")
        # reasoning_delta, monologue_delta, tool details, results and usage are
        # intentionally never passed into the text buffer.

    async def tick(self, *, now: float | None = None) -> None:
        if self.started and not self.chat_complete and not self.voice_stopped:
            try:
                self._enqueue(self.buffer.tick(now=now))
            except (ValueError, asyncio.QueueFull) as exc:
                await self.stop_voice(str(exc))

    async def stop_voice(self, reason: str = "user_stop_voice") -> None:
        if self.voice_stopped:
            return
        self.voice_stopped = True
        self.epoch += 1
        while not self.queue.empty():
            self.queue.get_nowait()
        await _call(self.clear_playback, reason)
        await self.relay.stop()
        if self.worker and self.worker is not asyncio.current_task():
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        await self.emit({"type": "voice_turn_stopped", "turn_id": self.turn_id,
                         "chat_run_id": self.chat_run_id, "reason": reason,
                         "public_text_characters": len(self.public_text),
                         "public_text_sha256": hashlib.sha256(
                             self.public_text.encode("utf-8")).hexdigest()})

    async def stop_generation(self) -> None:
        await self.stop_voice("user_stop_generation")
        if self.cancel_chat_callback:
            await _call(self.cancel_chat_callback, self.chat_run_id)

    async def switch_scope(self, *, character_id: int, conversation_id: int) -> None:
        if character_id != self.character_id or conversation_id != self.conversation_id:
            await self.stop_voice("scope_changed")


async def consume_chat_sse(chunks: Any, bridge: ChatSpeechBridge,
                           observe: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
    """Consume an authorized chat stream without altering its text persistence.

    A timer wakes on slow model output. An incomplete transport never flushes
    its uncertain tail or starts a new speech request.
    """
    decoder = ChatSSEDecoder()
    iterator = chunks.__aiter__()
    pending: asyncio.Task | None = None
    sidecar_failed = False
    try:
        while not bridge.chat_complete:
            if not bridge.voice_stopped and time.monotonic() - bridge.started_at >= bridge.limits.max_turn_seconds:
                await bridge.stop_voice("turn_time_budget")
            if pending is None:
                pending = asyncio.create_task(iterator.__anext__())
            done, _ = await asyncio.wait(
                {pending}, timeout=min(bridge.limits.wait_seconds,
                                       bridge.limits.max_turn_seconds -
                                       (time.monotonic() - bridge.started_at)))
            if not done and not bridge.voice_stopped:
                await bridge.tick()
            if not done:
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                break
            pending = None
            for sequence, event, data in decoder.feed(chunk):
                if sidecar_failed:
                    if event == "message_complete":
                        bridge.chat_complete = True
                else:
                    try:
                        await bridge.accept(run_id=bridge.chat_run_id, sequence=sequence,
                                            event=event, data=data)
                    except Exception as exc:
                        # Speech is a sidecar. Its failure must not close the
                        # model iterator before completion or usage arrives.
                        sidecar_failed = True
                        bridge.failure = type(exc).__name__
                        try:
                            await bridge.stop_voice("speech_sidecar_exception")
                        except Exception:
                            pass
                if observe is not None and event in ("message_start", "text_delta",
                                                      "message_complete", "error"):
                    await _call(lambda value: observe(event, value), data)
                if bridge.chat_complete:
                    break
        if not bridge.chat_complete and not bridge.voice_stopped:
            await bridge.stop_voice("chat_stream_incomplete")
        if bridge.worker and not bridge.voice_stopped:
            await bridge.worker
    except (ValueError, asyncio.CancelledError):
        await bridge.stop_voice("chat_stream_invalid_or_cancelled")
        raise
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, StopAsyncIteration):
                pass

