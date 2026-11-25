"""One-shot expression planning over the existing Responses adapters.

No main.py import, history builder, model selection, OAuth, secret-file reads or
message writes. The caller injects its explicitly selected profile and token
resolver; all real dispatch stays disabled unless explicitly enabled elsewhere.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .. import chatgpt_plan, responses_adapter
from .annotation_planner import AnnotationError, fingerprint
from .realtime_answer_display import AnswerDisplayGate


class PlanningError(Exception):
    def __init__(self, code: str, *, outcome="unknown", usage=None):
        super().__init__(code)
        self.code, self.outcome, self.usage = code, outcome, usage


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


class ProfilePlanningAdapter:
    def __init__(self, profile_id: int, resolve_profile: Callable, token_loader: Callable,
                 *, enabled=False, transport=None, proxy_url=None):
        self.profile_id = profile_id
        self.resolve_profile, self.token_loader = resolve_profile, token_loader
        self.enabled, self.transport, self.proxy_url = enabled, transport, proxy_url
        # Only fake HTTP transport is injectable into the network-denied lab.
        self.mock_only = isinstance(transport, (httpx.MockTransport, httpx.ASGITransport))

    def _profile(self):
        profile = dict(self.resolve_profile(self.profile_id))
        if profile.get("id") != self.profile_id or not profile.get("enabled"):
            raise AnnotationError("selected_model_profile_unavailable", 409)
        if profile.get("protocol") not in ("chatgpt_plan", "openai_responses"):
            raise AnnotationError("selected_model_protocol_unsupported", 422)
        return profile

    @property
    def supports_output_cap(self):
        return self.enabled and self._profile()["protocol"] in ("chatgpt_plan", "openai_responses")

    def binding(self):
        profile = self._profile()
        public = {key: profile.get(key) for key in ("id", "enabled", "protocol", "base_url",
            "model_name", "temperature", "top_p", "max_tokens", "reasoning_effort", "revision")}
        return {"profile_id": self.profile_id, "profile_sha256": fingerprint(public),
                "provider": profile["protocol"], "model": profile["model_name"],
                "max_output_tokens": 512}

    def request(self, request: dict) -> tuple[str, dict, dict]:
        profile = self._profile()
        if not self.enabled:
            raise PlanningError("planning_dispatch_disabled", outcome="not_dispatched")
        if request.get("model_profile_id") != self.profile_id or \
                request.get("model_binding") != self.binding() or \
                request.get("model") != profile["model_name"]:
            raise PlanningError("selected_profile_changed", outcome="not_dispatched")
        if type(request.get("max_output_tokens")) is not int or request["max_output_tokens"] != 512 or \
                request.get("total_timeout_seconds") != 20 or request.get("retries") != 0:
            raise PlanningError("planning_request_limits_invalid", outcome="not_dispatched")
        messages = request.get("messages")
        if (not isinstance(messages, list) or [m.get("role") for m in messages] != ["system", "user"] or
                any(not isinstance(m.get("content"), str) for m in messages) or
                sum(len(m["content"]) for m in messages) > 6000):
            raise PlanningError("planning_context_invalid", outcome="not_dispatched")
        effective = {**profile, "max_tokens": 512}
        if profile["protocol"] == "chatgpt_plan":
            body = chatgpt_plan.payload(effective, messages, [])
            # Scoped operation cap; the saved model profile remains unchanged.
            body["max_output_tokens"] = 512
            url = chatgpt_plan.API_BASE + "/responses"
        else:
            body = responses_adapter.payload(effective, messages, [], True)
            url = responses_adapter.endpoint(profile["base_url"])
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise PlanningError("selected_endpoint_invalid", outcome="not_dispatched")
        if body.get("model") != profile["model_name"] or type(body.get("max_output_tokens")) is not int or \
                body["max_output_tokens"] != 512 or body.get("tools") or body.get("stream") is not True:
            raise PlanningError("final_model_request_cap_invalid", outcome="not_dispatched")
        return url, body, profile

    async def generate(self, request: dict):
        url, body, _profile = self.request(request)
        usage = None
        attempted = False
        started = time.monotonic()
        try:
            async with asyncio.timeout(20):
                token = await self.token_loader(self.profile_id)
                if not isinstance(token, str) or not token:
                    raise PlanningError("planning_credential_unavailable", outcome="not_dispatched")
                # Re-resolve the SAME selected profile after credential acquisition.
                current_url, current_body, _ = self.request(request)
                if url != current_url or body != current_body:
                    raise PlanningError("selected_profile_changed", outcome="not_dispatched")
                transport = self.transport or httpx.AsyncHTTPTransport(retries=0)
                async with httpx.AsyncClient(transport=transport, proxy=self.proxy_url,
                        timeout=httpx.Timeout(20, connect=10), follow_redirects=False, trust_env=False) as client:
                    attempted = True
                    async with client.stream("POST", url, json=body,
                            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}) as response:
                        if response.status_code != 200:
                            raise PlanningError("model_http_" + str(response.status_code),
                                outcome="failed" if response.status_code in (400, 401, 403, 429) else "unknown")
                        state, gate = responses_adapter.StreamState(), AnswerDisplayGate()
                        parts, size, terminal, blocked = [], 0, None, None
                        async for event in responses_adapter.iter_sse_events(response.aiter_lines()):
                            kind = event.get("type")
                            response_data = event.get("response") or {}
                            raw_usage = response_data.get("usage") if isinstance(response_data, dict) else None
                            if kind in responses_adapter.CANDIDATE_TERMINAL and isinstance(raw_usage, dict):
                                observed = {k: v for k, v in raw_usage.items() if k in
                                    ("input_tokens", "output_tokens", "total_tokens") and type(v) is int and v >= 0}
                                usage = observed or usage
                            classification = responses_adapter.classify_candidate_event(event)
                            gate.observe(event)
                            if classification == "unsupported":
                                raise PlanningError("model_event_unknown", usage=usage)
                            if classification == "metadata_answer_moderation":
                                continue
                            if classification in responses_adapter.METADATA_STOPS:
                                blocked = "model_control_unsupported"
                                continue
                            if classification == "notice":
                                continue
                            if kind in ("response.failed", "response.incomplete", "error"):
                                terminal = kind
                                continue
                            if kind == "response.completed":
                                if terminal is not None or response_data.get("status") != "completed":
                                    raise PlanningError("model_terminal_conflict", usage=usage)
                                terminal = kind
                            elif terminal is not None and kind in ("response.output_text.delta", "response.output_text.done"):
                                raise PlanningError("model_output_after_terminal", usage=usage)
                            item = event.get("item") or {}
                            if isinstance(item, dict) and item.get("type") in ("function_call", "reasoning"):
                                if item["type"] == "function_call":
                                    blocked = "planning_tools_forbidden"
                                continue
                            normalized = state.normalize(event)
                            delta = normalized["choices"][0]["delta"]
                            text = delta.get("content")
                            if text:
                                size += len(text)
                                if size > 32768:
                                    raise PlanningError("planning_output_too_large", usage=usage)
                                parts.append(text)
                        if time.monotonic() - started > 20:
                            raise TimeoutError
                        if terminal != "response.completed" or not state.completed:
                            raise PlanningError("model_incomplete" if terminal == "response.incomplete" else
                                "model_failed" if terminal else "model_completion_missing",
                                outcome="failed" if terminal else "unknown", usage=usage)
                        text = "".join(parts)
                        display = gate.finish(model_completed=True, public_text=text)
                        if blocked or display.action != "allow":
                            raise PlanningError(blocked or "planning_display_" + display.reason,
                                                outcome="restricted", usage=usage)
                        try:
                            result = json.loads(text, object_pairs_hook=_unique_object,
                                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                        except (ValueError, TypeError):
                            raise PlanningError("planning_json_invalid", outcome="invalid", usage=usage) from None
                        return result, usage
        except PlanningError:
            raise
        except asyncio.CancelledError:
            raise PlanningError("planning_cancelled", outcome="cancelled", usage=usage) from None
        except TimeoutError:
            raise PlanningError("planning_total_timeout", outcome="unknown" if attempted else "not_dispatched", usage=usage) from None
        except (httpx.HTTPError, responses_adapter.ResponseSSEDecodeError, ValueError, TypeError, KeyError):
            raise PlanningError("planning_transport_or_parse_unknown",
                                outcome="unknown" if attempted else "not_dispatched", usage=usage) from None
