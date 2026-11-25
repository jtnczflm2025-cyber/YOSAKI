"""Freeze a single isolated model request with the production context builder.

This module never reads memory, summary, history, tools, or credentials. The
caller supplies the same build_messages/payload functions used by normal chat.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrozenRequest:
    owner_id: int
    character_id: int
    conversation_id: int
    model_profile_id: int
    provider: str
    model: str
    prompt: str
    messages: list[dict]
    prompt_blocks: list[dict]
    model_profile_fields: dict
    body: dict
    context_sha256: str
    persona_characters: int

    def public(self) -> dict:
        return {"owner_id": self.owner_id, "character_id": self.character_id,
            "conversation_id": self.conversation_id,
            "model_profile_id": self.model_profile_id, "provider": self.provider,
            "model": self.model, "prompt": self.prompt, "messages": self.messages,
            "enabled_prompt_blocks": self.prompt_blocks,
            "model_profile_fields": self.model_profile_fields,
            "request_body": self.body,
            "context_sha256": self.context_sha256,
            "persona_characters": self.persona_characters,
            "excluded": ["tools", "conversation_history", "memory_position_prompt_blocks", "memory_rows",
                         "conversation_summary", "desire_advisory", "monologue"]}


def freeze(db, *, owner_id: int, character_id: int, conversation_id: int,
           model_profile_id: int, prompt: str, max_output_tokens: int,
           build_messages: Callable, plan_payload: Callable) -> FrozenRequest:
    if not prompt or len(prompt) > 500 or not (1 <= max_output_tokens <= 256):
        raise ValueError("frozen_prompt_or_token_limit_invalid")
    character = db.execute("SELECT * FROM characters WHERE id=? AND enabled=1",
                           (character_id,)).fetchone()
    conversation = db.execute("SELECT * FROM conversations WHERE id=? AND character_id=?",
                              (conversation_id, character_id)).fetchone()
    profile = db.execute("SELECT * FROM model_profiles WHERE id=? AND enabled=1",
                         (model_profile_id,)).fetchone()
    if character is None or conversation is None or profile is None or \
            profile["protocol"] != "chatgpt_plan" or \
            profile["model_name"] != "gpt-6.1-sol" or \
            profile["base_url"].rstrip("/") != "https://api.openai.com/v1":
        raise ValueError("frozen_identity_or_model_invalid")
    profile_fields = {key: profile[key] for key in ("id", "provider_name",
        "base_url", "model_name", "protocol", "temperature", "top_p",
        "max_tokens", "reasoning_effort", "supports_streaming", "enabled")}
    all_blocks = db.execute("SELECT * FROM prompt_blocks WHERE character_id=? AND enabled=1 "
                        "ORDER BY order_index,id", (character_id,)).fetchall()
    # A prompt block at the memory injection position is a persistent memory
    # hint. This particular run excludes it along with memory rows/summary.
    blocks = [row for row in all_blocks if row["injection_position"] != "memory"]
    details = [{"id": row["id"], "name": row["name"],
                "injection_position": row["injection_position"],
                "order_index": row["order_index"], "content": row["content"],
                "included": row["injection_position"] != "memory"}
               for row in all_blocks]
    settings = {"window_mode": "legacy", "history_limit": 0,
                "memory_enabled": False, "reasoning_prompt": "",
                "monologue_enabled": False}
    messages = build_messages(character, blocks, [], prompt, (), "",
                              settings=settings, include_provider_state=True,
                              desire_advisory="")
    if any(item["role"] not in ("system", "user") for item in messages) or \
            messages[-1] != {"role": "user", "content": prompt}:
        raise ValueError("frozen_context_unexpected_source")
    persona_chars = sum(len(item["content"]) for item in messages[:-1])
    if persona_chars > 2000:
        raise ValueError(f"required_persona_exceeds_2000:{persona_chars}")
    body = plan_payload(profile, messages, [])
    body["max_output_tokens"] = max_output_tokens
    if body.get("tools") or body.get("model") != "gpt-6.1-sol" or \
            body.get("store") is not False or body.get("stream") is not True:
        raise ValueError("frozen_payload_unexpected")
    envelope = {"owner_id": owner_id, "character_id": character_id,
        "conversation_id": conversation_id, "model_profile_id": model_profile_id,
        "provider": "chatgpt_plan", "messages": messages,
        "enabled_prompt_blocks": details, "model_profile_fields": profile_fields,
        "request_body": body,
        "excluded": ["tools", "conversation_history", "memory_position_prompt_blocks", "memory_rows",
                     "conversation_summary", "desire_advisory", "monologue"]}
    return FrozenRequest(owner_id, character_id, conversation_id, model_profile_id,
        "chatgpt_plan", body["model"], prompt, messages, details,
        profile_fields, body,
        digest(envelope), persona_chars)
