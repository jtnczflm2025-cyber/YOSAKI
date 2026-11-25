import os
from pathlib import Path

NOTICE_VERSION = "2026-11-08-v1"
PROVIDER = "elevenlabs"


def data_dir() -> Path:
    configured = os.getenv("YOSAKI_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[3] / "data"


def enabled() -> bool:
    return os.getenv("YOSAKI_VOICE_ENABLED", "0") == "1"


def expression_enabled() -> bool:
    return os.getenv("YOSAKI_V3_EXPRESSION_ENABLED", "0") == "1"


def automatic_expression_enabled() -> bool:
    return expression_enabled() and os.getenv("YOSAKI_VOICE_AUTO_EXPRESSION_ENABLED", "0") == "1"


def v4_output_enabled() -> bool:
    return os.getenv("YOSAKI_V4_OUTPUT_ENABLED", "0") == "1"


def v4_real_provider_enabled() -> bool:
    return os.getenv("YOSAKI_V4_REAL_PROVIDER_ENABLED", "0") == "1"


def configured() -> bool:
    return bool(os.getenv("ELEVENLABS_API_KEY", "").strip())


def defaults() -> dict[str, str]:
    return {
        "voice_id": os.getenv("YOSAKI_VOICE_ID", "").strip(),
        "model_realtime": os.getenv("YOSAKI_VOICE_MODEL_REALTIME", "eleven_v4_turbo"),
        "model_standard": os.getenv("YOSAKI_VOICE_MODEL_STANDARD", "eleven_v4"),
        "model_stt": os.getenv("YOSAKI_STT_MODEL", "scribe_v2_realtime"),
    }
