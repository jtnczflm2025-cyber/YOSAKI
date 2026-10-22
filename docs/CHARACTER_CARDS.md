# YOSAKI Character Card JSON v1

Character cards provide a portable copy of role profile fields and prompt blocks.

## Included

- Character name, subtitle, mood, system prompt, greeting and enabled state.
- Prompt block names, text, injection positions, order and enabled state.

## Excluded intentionally

- Chats/messages, tool execution records, memories, summaries and per-device layout.
- Model profile/default model references, API keys, MCP endpoint credentials, account data and sessions.

Import always creates a new role; it never overwrites an existing role. The user must confirm the import in the UI. Export/import are authenticated. Files are capped at 256 KiB in the UI and are JSON only. The backend rejects unknown fields and unsupported versions; imported prompt blocks are validated against the supported injection-position enum.

The API format is `yosaki-character-card`, version `1`. Role JSON is portable text and is **not encrypted**; do not include secrets in role prompts, and store exported cards appropriately.
