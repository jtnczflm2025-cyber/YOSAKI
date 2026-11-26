"""Daily runtime billing policy; never a content, tool, or account grant."""
import os


def metered():
    return (os.environ.get("YOSAKI_RUN_MODE") == "development" and
            os.environ.get("YOSAKI_OWNER_BILLING") == "metered")
