"""OpenAI Responses wire format. Only public summaries are exposed to the UI."""
from __future__ import annotations

from typing import Any


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
            self.completed = True
            response = event.get("response") or {}
            usage = response.get("usage")
            finish_reason = "stop"
            if not self.reasoning_items:
                self.reasoning_items = [item for item in response.get("output") or [] if item.get("type") == "reasoning" and item.get("encrypted_content")]
        elif kind in ("response.failed", "response.incomplete"):
            finish_reason = "error" if kind == "response.failed" else "length"
        return {"choices": [{"delta": delta, "finish_reason": finish_reason}], "usage": usage}
