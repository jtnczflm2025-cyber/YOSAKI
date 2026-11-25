"""Gate automatic business workers; preserve legacy startup when unset."""
import os


def enabled() -> bool:
    value = os.environ.get("YOSAKI_BACKGROUND_TASKS_ENABLED", "1")
    if value not in ("0", "1"):
        raise ValueError("YOSAKI_BACKGROUND_TASKS_ENABLED must be 0 or 1")
    return value == "1"
