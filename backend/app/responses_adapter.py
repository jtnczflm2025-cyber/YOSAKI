"""OpenAI Responses wire format. Only public summaries are exposed to the UI."""
from __future__ import annotations

import json
from typing import Any, AsyncIterable, AsyncIterator


class ResponseSSEDecodeError(ValueError):
    """A bounded wire-format error; never include raw SSE data in its text."""

    def __init__(self, code: str, event_type: object = None) -> None:
        super().__init__(code)
        self.code = code
        self.event_type = event_type


async def iter_sse_events(lines: AsyncIterable[str]) -> AsyncIterator[dict[str, Any]]:
    """Decode Responses SSE frames, including lines split across transport chunks.

    The caller supplies complete lines (for example, httpx.aiter_lines()). A
    frame is dispatched only at its blank line. Its event label, when present,
    must match the JSON type; neither label nor payload is logged here.
    """
    label: str | None = None
    data: list[str] = []
    size = 0
    async for line in lines:
        if not isinstance(line, str) or len(line) > 1_048_576:
            raise ResponseSSEDecodeError("model_sse_line_invalid", label)
        if not line:
            if data:
                raw = "\n".join(data)
                if raw == "[DONE]":
                    return
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError) as exc:
                    raise ResponseSSEDecodeError("model_sse_json_invalid", label) from exc
                if not isinstance(value, dict):
                    raise ResponseSSEDecodeError("model_sse_event_invalid", label)
                if label is not None and label != value.get("type"):
                    raise ResponseSSEDecodeError("model_sse_type_mismatch", label)
                yield value
            label, data, size = None, [], 0
            continue
        if line.startswith(":"):
            continue
        field, sep, value = line.partition(":")
        if not sep:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            label = value
        elif field == "data":
            size += len(value)
            if size > 1_048_576:
                raise ResponseSSEDecodeError("model_sse_event_too_large", label)
            data.append(value)
    if data == ["[DONE]"]:
        return
    if data:
        raise ResponseSSEDecodeError("model_sse_unterminated_event", label)


# This candidate sends no tools and speaks only public output_text. A metadata
# envelope can carry answer-display or access controls, so inspect its known
# fields before treating an empty envelope as a notice. Unknown metadata fails
# closed; neither its payload nor a notification is public reply text.
CANDIDATE_NOTICES = frozenset({
    "response.queued", "response.created", "response.in_progress",
    "response.output_text.annotation.added",
    "response.reasoning_summary_part.added",
    "response.reasoning_summary_part.done",
    "response.reasoning_summary_text.delta",
    "response.reasoning_summary_text.done",
    "response.reasoning_text.delta", "response.reasoning_text.done",
})
CANDIDATE_ADAPTED = frozenset({
    "response.output_item.added", "response.output_item.done",
    "response.content_part.added", "response.content_part.done",
    "response.output_text.delta", "response.output_text.done",
    "response.function_call_arguments.delta",
    "response.function_call_arguments.done",
})
CANDIDATE_TERMINAL = frozenset({
    "response.completed", "response.failed", "response.incomplete", "error",
})
METADATA_STOPS = frozenset({
    "metadata_safety_buffering", "metadata_model_verification",
    "metadata_answer_moderation", "metadata_guardian",
    "metadata_headers", "metadata_unrecognized",
})


def candidate_metadata_boundary(event: dict[str, Any]) -> str | None:
    """Return a bounded stop category for metadata the Voice path cannot honor."""
    buffering = event.get("safety_buffering")
    if buffering is not None and buffering is not False:
        return "metadata_safety_buffering"
    if event.get("type") != "response.metadata":
        return None
    if event.get("headers") not in (None, {}):
        # Headers can report a different effective model or turn state.
        return "metadata_headers"
    if any(event.get(key) not in (None, {}) for key in
           ("response", "error", "item", "delta", "text")):
        return "metadata_unrecognized"
    metadata = event.get("metadata")
    if metadata is None or metadata == {}:
        return None
    if not isinstance(metadata, dict):
        return "metadata_unrecognized"
    if "openai_chatgpt_moderation_metadata" in metadata:
        return "metadata_answer_moderation"
    if "openai_verification_recommendation" in metadata:
        return "metadata_model_verification"
    if "guardian" in metadata:
        return "metadata_guardian"
    if metadata.get("type") == "safety_buffering":
        return "metadata_safety_buffering"
    return "metadata_unrecognized"


def classify_candidate_event(event: dict[str, Any]) -> str:
    kind = event.get("type")
    if not isinstance(kind, str):
        return "unsupported"
    boundary = candidate_metadata_boundary(event)
    if boundary is not None:
        return boundary
    if kind == "response.metadata":
        return "notice"
    if kind in CANDIDATE_NOTICES:
        return "notice"
    if kind in CANDIDATE_TERMINAL:
        return "terminal"
    if kind in CANDIDATE_ADAPTED:
        item = event.get("item") if kind.startswith("response.output_item.") else None
        part = event.get("part") if kind.startswith("response.content_part.") else None
        if item is not None and (not isinstance(item, dict) or
                                 item.get("type") not in {"message", "reasoning", "function_call"}):
            return "unsupported"
        if part is not None and (not isinstance(part, dict) or
                                 part.get("type") not in {"output_text", "reasoning_text"}):
            return "unsupported"
        return "adapt"
    return "unsupported"


def endpoint(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root if root.endswith("/responses") else f"{root}/responses"


def input_items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message.get("content") or ""})
            continue
        if role == "assistant":
            items.extend(message.get("provider_state") or [])
            if message.get("content"):
                items.append({"role": "assistant", "content": message["content"]})
            for call in message.get("tool_calls") or []:
                function = call["function"]
                items.append({"type": "function_call", "call_id": call["id"], "name": function["name"], "arguments": function["arguments"]})
            continue
        items.append({"role": role, "content": message.get("content") or ""})
    return items


def payload(profile: Any, messages: list[dict[str, Any]], tools: list[dict[str, Any]], stream: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"model": profile["model_name"], "input": input_items(messages), "max_output_tokens": profile["max_tokens"], "stream": stream, "store": False}
    if profile["reasoning_effort"]:
        body["reasoning"] = {"effort": profile["reasoning_effort"], "summary": "auto"}
        body["include"] = ["reasoning.encrypted_content"]
    if tools:
        body["tools"] = [{"type": "function", **tool["function"]} for tool in tools]
        body["tool_choice"] = "auto"
        body["parallel_tool_calls"] = False
    return body


def preview_text(response: dict[str, Any]) -> str:
    for item in response.get("output") or []:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text": return part.get("text") or ""
    return ""


class StreamState:
    def __init__(self) -> None:
        self.completed = False
        self.terminal = None
        self.arguments: dict[int, str] = {}
        self.reasoning_items: list[dict[str, Any]] = []
        self.summary_text = ""
        self.output_parts: dict[tuple[str,int], str] = {}

    def normalize(self, event: dict[str, Any]) -> dict[str, Any]:
        kind = event.get("type")
        delta: dict[str, Any] = {}
        usage = None
        finish_reason = None
        if kind == "response.output_text.delta":
            delta["content"] = event.get("delta") or ""
            key=(event.get("item_id") or "",event.get("content_index") or 0)
            self.output_parts[key]=self.output_parts.get(key,"")+delta["content"]
        elif kind == "response.output_text.done":
            key=(event.get("item_id") or "",event.get("content_index") or 0)
            current=self.output_parts.get(key,"")
            complete = event.get("text") or ""
            if complete.startswith(current) and len(complete) > len(current):
                delta["content"] = complete[len(current):]
                self.output_parts[key] = complete
        elif kind == "response.reasoning_summary_text.delta":
            delta["reasoning_content"] = event.get("delta") or ""
            self.summary_text += delta["reasoning_content"]
        elif kind == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                index = event["output_index"]
                self.arguments[index] = item.get("arguments") or ""
                delta["tool_calls"] = [{"index": index, "id": item.get("call_id") or "", "function": {"name": item.get("name") or "", "arguments": self.arguments[index]}}]
        elif kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
            index = event["output_index"]
            existing = self.arguments.get(index, "")
            new = event.get("delta") or "" if kind.endswith(".delta") else (event.get("arguments") or "")
            remainder = new if kind.endswith(".delta") else (new[len(existing):] if new.startswith(existing) else "")
            self.arguments[index] = existing + remainder
            if remainder: delta["tool_calls"] = [{"index": index, "function": {"arguments": remainder}}]
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "reasoning" and item.get("encrypted_content"):
                self.reasoning_items.append(item)
                complete = "\n".join(part.get("text", "") for part in item.get("summary") or [] if part.get("type") == "summary_text")
                if complete.startswith(self.summary_text) and len(complete) > len(self.summary_text):
                    delta["reasoning_content"] = complete[len(self.summary_text):]
                    self.summary_text = complete
            elif item.get("type") == "function_call":
                index = event["output_index"]
                existing = self.arguments.get(index, "")
                complete = item.get("arguments") or ""
                if complete.startswith(existing) and len(complete) > len(existing):
                    remainder = complete[len(existing):]
                    self.arguments[index] = complete
                    delta["tool_calls"] = [{"index": index, "function": {"arguments": remainder}}]
        elif kind == "response.completed":
            self.terminal = kind
            self.completed = True
            response = event.get("response") or {}
            usage = response.get("usage")
            finish_reason = "stop"
            if not self.reasoning_items:
                self.reasoning_items = [item for item in response.get("output") or [] if item.get("type") == "reasoning" and item.get("encrypted_content")]
        elif kind in ("response.failed", "response.incomplete"):
            self.terminal = kind
            usage = (event.get("response") or {}).get("usage")
            finish_reason = "error" if kind == "response.failed" else "length"
        return {"choices": [{"delta": delta, "finish_reason": finish_reason}], "usage": usage}
