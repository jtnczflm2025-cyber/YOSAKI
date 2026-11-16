"""Isolated chat-to-speech bridge tests. No main import, database, or network."""

import asyncio
import base64
import hashlib
import json
import unittest

from backend.app.voice.realtime_chat_bridge import (
    BridgeLimits, ChatSSEDecoder, ChatSpeechBridge, PublicDeltaBuffer,
    consume_chat_sse,
)


PCM = base64.b64encode(b"\x00\x00\x10\x27" * 16).decode()


def frame(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class FakeSupplier:
    def __init__(self, calls, mode="normal"):
        self.calls = calls
        self.mode = mode
        self.queue = asyncio.Queue()
        self.closed = False

    async def __aenter__(self):
        self.calls.append({"connection": True})
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def send(self, raw):
        request = json.loads(raw)
        self.calls.append(request)
        if "inputs" in request:
            if self.mode == "quota":
                await self.queue.put('{"error":{"code":"quota_exceeded"}}')
            elif self.mode == "disconnect":
                raise OSError("simulated transport loss")
            elif self.mode == "normal":
                await self.queue.put(json.dumps({"audio": PCM,
                                                 "is_final_audio_for_turn": True}))
        if request.get("close_socket"):
            await self.queue.put('{"is_final":true}')

    async def recv(self):
        return await self.queue.get()


class ChatBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.calls = []
        self.clear_reasons = []
        self.cancelled_chats = []
        self.supplier_mode = "normal"

    def bridge(self, limits=None):
        async def emit(event):
            self.events.append(event)

        return ChatSpeechBridge(
            chat_run_id="chat-run-1", character_id=1, conversation_id=5,
            voice_id="fake-voice", connector=lambda: FakeSupplier(
                self.calls, self.supplier_mode), emit=emit,
            clear_playback=self.clear_reasons.append,
            cancel_chat=self.cancelled_chats.append,
            limits=limits or BridgeLimits(wait_seconds=.1))

    async def begin(self, bridge):
        await bridge.accept(run_id="chat-run-1", sequence=1,
                            event="message_start", data={"conversation_id": 5})

    async def delta(self, bridge, seq, text):
        await bridge.accept(run_id="chat-run-1", sequence=seq,
                            event="text_delta", data={"text": text})

    async def complete(self, bridge, seq):
        await bridge.accept(run_id="chat-run-1", sequence=seq,
                            event="message_complete", data={"conversation_id": 5})
        if bridge.worker and not bridge.voice_stopped:
            await bridge.worker

    def sent_texts(self):
        return [item["inputs"][0]["text"] for item in self.calls if "inputs" in item]

    def test_sse_decoder_ignores_nonpublic_channels_even_in_large_batch(self):
        decoder = ChatSSEDecoder()
        batch = frame("message_start", {"conversation_id": 5})
        batch += frame("reasoning_delta", {"text": "PRIVATE REASON"}) * 400
        batch += frame("monologue_delta", {"text": "PRIVATE INNER"})
        batch += frame("tool_waiting_confirmation", {"arguments": "SECRET"})
        batch += frame("text_delta", {"text": "公開です。"})
        seen = decoder.feed(batch)
        self.assertEqual([kind for _, kind, _ in seen], ["message_start", "text_delta"])
        self.assertEqual([seq for seq, _, _ in seen], [1, 2])
        self.assertNotIn("PRIVATE", str(seen))
        self.assertEqual(decoder.feed(frame("message_complete", {"conversation_id": 5}))[0][0], 3)
        with self.assertRaisesRegex(ValueError, "chat_sse_frame_limit"):
            decoder.feed(frame("text_delta", {"text": "x" * 17_000}))

    async def test_first_audio_before_chat_completion_and_no_cumulative_replay(self):
        bridge = self.bridge()
        first_audio = asyncio.Event()
        original_emit = bridge.emit

        async def emit(event):
            await original_emit(event)
            if event["type"] == "audio":
                first_audio.set()  # fake player receives a PCM block

        bridge.emit = emit

        async def chat():
            yield frame("message_start", {"conversation_id": 5})
            yield frame("reasoning_delta", {"text": "PRIVATE THINKING"})
            yield frame("text_delta", {"text": "最初の文です。次の"})
            yield frame("monologue_delta", {"text": "PRIVATE MONOLOGUE"})
            yield frame("text_delta", {"text": "文です。"})
            await asyncio.wait_for(first_audio.wait(), .5)
            self.assertFalse(bridge.chat_complete)
            yield frame("message_complete", {"conversation_id": 5})

        await asyncio.wait_for(consume_chat_sse(chat(), bridge), 2)
        self.assertEqual(self.sent_texts(), ["最初の文です。", "次の文です。"])
        self.assertEqual(bridge.public_text, "最初の文です。次の文です。")
        self.assertEqual([span.spoken_text for _, span in bridge.sent], self.sent_texts())
        self.assertEqual([seq for seq, _ in bridge.sent], [1, 2])
        for _, span in bridge.sent:
            raw = bridge.public_text[span.source_start:span.source_end]
            self.assertEqual(hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                             span.source_sha256)
        self.assertEqual(self.events[-1]["type"], "voice_turn_completed")
        self.assertNotIn("PRIVATE", str(self.calls))
        self.assertEqual(sum("connection" in call for call in self.calls), 2)

    async def test_unexpected_voice_accept_exception_does_not_close_chat_iterator(self):
        bridge = self.bridge()
        original_accept = bridge.accept
        observed = []

        async def broken_voice_accept(**kwargs):
            if kwargs["event"] == "text_delta":
                raise RuntimeError("synthetic_voice_sidecar_failure")
            await original_accept(**kwargs)

        bridge.accept = broken_voice_accept

        async def chat():
            yield frame("message_start", {"conversation_id": 5})
            yield frame("text_delta", {"text": "公開です。"})
            yield frame("message_complete", {"conversation_id": 5})

        await consume_chat_sse(chat(), bridge,
            lambda event, _data: observed.append(event))
        self.assertTrue(bridge.voice_stopped)
        self.assertTrue(bridge.chat_complete)
        self.assertIn("message_complete", observed)
        self.assertEqual(self.cancelled_chats, [])

    async def test_slow_output_wait_cut_and_cross_chunk_punctuation(self):
        bridge = self.bridge(BridgeLimits(wait_seconds=.1, wait_min_chars=8))
        await self.begin(bridge)
        await self.delta(bridge, 2, "これはゆっくりした説明で、")
        await bridge.tick(now=bridge.buffer.last_delta_at + .2)
        await self.delta(bridge, 3, "続けます")
        await self.delta(bridge, 4, "。")
        await self.complete(bridge, 5)
        self.assertEqual(self.sent_texts(), ["これはゆっくりした説明で、", "続けます。"])
        self.assertEqual([span.reason for _, span in bridge.sent], ["wait_limit", "sentence"])

    async def test_split_markdown_tags_code_html_and_internal_events_never_speak(self):
        bridge = self.bridge()
        await self.begin(bridge)
        deltas = ["普通の[whis", "pers]説明です。\n```python\n",
                  "tool_secret = 1\n```\n", "後の[文字]です。\n",
                  "<inner>hidden", "</inner>\n\n安全です。"]
        for seq, part in enumerate(deltas, 2):
            await self.delta(bridge, seq, part)
        await bridge.accept(run_id="chat-run-1", sequence=8, event="reasoning_delta",
                            data={"text": "SECRET REASON"})
        await self.complete(bridge, 9)
        self.assertEqual(self.sent_texts(), ["普通の説明です。", "後の文字です。", "安全です。"])
        self.assertNotIn("secret", str(self.calls).lower())
        self.assertNotIn("hidden", str(self.calls).lower())
        self.assertTrue(any("HTML" in reason for reason in bridge.buffer.skipped))

    async def test_duplicate_late_and_rewrite_do_not_resend(self):
        bridge = self.bridge()
        await self.begin(bridge)
        await self.delta(bridge, 2, "確定です。続く")
        await self.delta(bridge, 2, "DUPLICATE")
        await bridge.accept(run_id="old-run", sequence=3,
                            event="text_delta", data={"text": "OLD"})
        await asyncio.sleep(.02)
        await bridge.accept(run_id="chat-run-1", sequence=3,
                            event="text_replace", data={"text": "改稿"})
        await bridge.accept(run_id="chat-run-1", sequence=4,
                            event="text_delta", data={"text": "LATE"})
        self.assertEqual(self.sent_texts(), ["確定です。"])
        self.assertTrue(bridge.voice_stopped)
        self.assertEqual(self.clear_reasons, ["source_rewrite_unsupported"])
        self.assertNotEqual(bridge.public_text, "DUPLICATE")

    async def test_generation_incomplete_and_voice_failure_preserve_chat(self):
        bridge = self.bridge()
        await self.begin(bridge)
        await self.delta(bridge, 2, "部分返信です。残り")
        await bridge.accept(run_id="chat-run-1", sequence=3,
                            event="message_complete",
                            data={"conversation_id": 5, "generation_status": "stopped"})
        self.assertLessEqual(len(self.sent_texts()), 1)
        self.assertNotIn("残り", str(self.sent_texts()))
        self.assertEqual(bridge.public_text, "部分返信です。残り")
        self.assertTrue(bridge.voice_stopped)
        self.assertEqual(self.cancelled_chats, [])

        self.events.clear(); self.calls.clear()
        self.supplier_mode = "quota"
        bridge = self.bridge()
        await self.begin(bridge)
        await self.delta(bridge, 2, "供应商失败。后续文字")
        await asyncio.wait_for(bridge.worker, 1)
        await self.delta(bridge, 3, "仍然保存。")
        self.assertTrue(bridge.voice_stopped)
        self.assertIn("后续文字仍然保存。", bridge.public_text)
        self.assertEqual(self.cancelled_chats, [])
        self.assertEqual(sum("connection" in call for call in self.calls), 1)

    async def test_voice_stop_generation_stop_scope_switch_and_late_audio(self):
        bridge = self.bridge()
        self.supplier_mode = "hold"
        await self.begin(bridge)
        await self.delta(bridge, 2, "第一句。后续")
        await asyncio.sleep(.02)
        await bridge.stop_voice()
        await self.delta(bridge, 3, "文字仍在。")
        await bridge._relay_event({"type": "audio", "request_id":
                                  f"{bridge.turn_id}:1", "pcm_base64": PCM})
        self.assertEqual(self.clear_reasons, ["user_stop_voice"])
        self.assertEqual(self.cancelled_chats, [])
        self.assertFalse(any(e["type"] == "audio" for e in self.events))
        self.assertIn("文字仍在。", bridge.public_text)
        await bridge.stop_generation()
        self.assertEqual(self.cancelled_chats, ["chat-run-1"])

        bridge2 = self.bridge()
        await self.begin(bridge2)
        await bridge2.switch_scope(character_id=2, conversation_id=5)
        self.assertEqual(self.clear_reasons[-1], "scope_changed")

    async def test_total_character_and_audio_budget_stop_before_extra_dispatch(self):
        bridge = self.bridge(BridgeLimits(max_segment_chars=24, max_total_chars=24))
        await self.begin(bridge)
        await self.delta(bridge, 2, "这是第一段内容超过十个字。这是第二段内容超过十个字。")
        await self.complete(bridge, 3)
        self.assertTrue(bridge.voice_stopped)
        self.assertLessEqual(len(self.sent_texts()), 1)

        self.events.clear(); self.calls.clear()
        bridge = self.bridge(BridgeLimits(max_audio_bytes=16))
        await self.begin(bridge)
        await self.delta(bridge, 2, "一句。下一句。")
        await self.complete(bridge, 3)
        self.assertTrue(bridge.voice_stopped)
        self.assertEqual(bridge.total_audio_bytes, 0)
        self.assertEqual(sum("connection" in call for call in self.calls), 1)

    async def test_incomplete_transport_does_not_flush_unsafe_tail(self):
        bridge = self.bridge()

        async def chat():
            yield frame("message_start", {"conversation_id": 5})
            yield frame("text_delta", {"text": "公開です。[whis"})

        await consume_chat_sse(chat(), bridge)
        self.assertEqual(self.sent_texts(), ["公開です。"])
        self.assertTrue(bridge.voice_stopped)
        self.assertEqual(self.clear_reasons, ["chat_stream_incomplete"])
        self.assertEqual(bridge.public_text, "公開です。[whis")

    async def test_voice_failure_does_not_cancel_chat_stream_or_save(self):
        self.supplier_mode = "quota"
        bridge = self.bridge()

        async def chat():
            yield frame("message_start", {"conversation_id": 5})
            yield frame("text_delta", {"text": "音声は失敗します。次の"})
            await asyncio.wait_for(bridge.worker, 1)
            self.assertTrue(bridge.voice_stopped)
            yield frame("text_delta", {"text": "文字も保存します。"})
            yield frame("message_complete", {"conversation_id": 5})

        await asyncio.wait_for(consume_chat_sse(chat(), bridge), 2)
        self.assertTrue(bridge.chat_complete)
        self.assertEqual(bridge.public_text, "音声は失敗します。次の文字も保存します。")
        self.assertEqual(self.cancelled_chats, [])
        self.assertEqual(sum("connection" in call for call in self.calls), 1)
        self.assertEqual(len(self.sent_texts()), 1)

    def test_unclosed_markdown_is_not_flushed_and_character_splits_hold_tags(self):
        body = "先に話す。[whispers]普通の[言葉]です。\n```python\nsecret=1\n```\n"
        buffer = PublicDeltaBuffer(BridgeLimits())
        spoken = []
        for character in body:
            spoken.extend(span.spoken_text for span in buffer.feed(character))
        spoken.extend(span.spoken_text for span in buffer.finish())
        self.assertEqual(spoken, ["先に話す。", "普通の言葉です。"])
        self.assertNotIn("secret", str(spoken))

        buffer = PublicDeltaBuffer(BridgeLimits())
        self.assertEqual(buffer.feed("本文の後に `未閉鎖"), [])
        self.assertEqual(buffer.finish(), [])
        self.assertIn("未闭合或无法确认的增量结构", buffer.skipped)

        buffer = PublicDeltaBuffer(BridgeLimits())
        self.assertEqual([span.spoken_text for span in buffer.feed(
            "The value is 3.14. Dr. Smith agrees. Next step.")],
            ["The value is 3.14.", "Dr. Smith agrees."])
        self.assertEqual([span.spoken_text for span in buffer.finish()], ["Next step."])

    async def test_optional_final_public_hash_detects_rewrite(self):
        bridge = self.bridge()
        await self.begin(bridge)
        await self.delta(bridge, 2, "確定した文章です。")
        await bridge.accept(run_id="chat-run-1", sequence=3,
                            event="message_complete", data={
                                "conversation_id": 5,
                                "public_text_sha256": hashlib.sha256(
                                    "別の最終文章".encode("utf-8")).hexdigest()})
        self.assertTrue(bridge.voice_stopped)
        self.assertEqual(self.clear_reasons, ["final_text_mismatch"])
        self.assertNotIn("voice_turn_completed", [e["type"] for e in self.events])


if __name__ == "__main__":
    unittest.main()
