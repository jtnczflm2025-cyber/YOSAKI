"""Voice settings ceilings belong to deployment configuration, not SQLite."""
import os
import re


BUDGET_TABLES = (
    "voice_auto_owner_settings", "voice_scribe_owner_settings",
    "voice_continuous_owner_settings", "voice_continuous_daily_calls",
)


def configured_limit(name, default, minimum, error, *, maximum=2_147_483_647):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise error(f"{name}_invalid") from exc
    if not minimum <= value <= maximum:
        raise error(f"{name}_invalid")
    return value


def expanded_ddl(name: str, sql: str) -> str:
    if name in BUDGET_TABLES:
        return re.sub(r"CHECK\((\w+) BETWEEN (\d+) AND \d+\)",
                      r"CHECK(typeof(\1)='integer' AND \1 >= \2)", sql)
    if name == "voice_expression_audio":
        return sql.replace("CHECK(mode IN ('thoughtful'))",
                           "CHECK(mode IN ('thoughtful','auto'))").replace(
                           "CHECK(mode='thoughtful')", "CHECK(mode IN ('thoughtful','auto'))")
    return sql
