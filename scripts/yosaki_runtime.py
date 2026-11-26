"""Explicit local runtime entry points. Preflight runs before importing main."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_NAME = "yosaki.sqlite3"
PREVIEW_DIR = Path(tempfile.gettempdir()).resolve() / "yosaki-preview"
REQUIRED_DEVELOPMENT_TABLES = frozenset(
    """
    activity_cooldowns_v4 behavior_model_calls behavior_resource_samples
    drive_regulation_v4 experience_reviews_v4 feedback_activities_v4
    feedback_collection_v4 feedback_controls_v4 goal_feedback_v4
    interest_evidence_v4 learning_audit learning_batch_runs learning_controls
    learning_evidence learning_goal_notes learning_memories
    learning_preference_history learning_preference_links
    learning_preference_proposals learning_preferences learning_strategies
    learning_strategy_uses learning_subject_suggestions mv2_jobs mv2_meta
    mv2_segment_members mv2_segments mv2_sources mv2_terms
    psychological_appraisal_runs psychological_conflict_decisions
    psychological_controls psychological_cutovers psychological_edges
    psychological_events psychological_snapshots second_order_desires
    second_order_history voice_profiles voice_privacy_consents
    chat_attempts chat_attempt_workers chat_usage_receipts chat_message_state
    chat_branches chat_branch_members chat_path_exclusions chat_memory_scope
    chat_attachments chat_message_attachments chat_quotes chat_translations
    chat_summaries chat_context_policy chat_model_capabilities
    """.split()
)
REQUIRED_DEVELOPMENT_COLUMNS = {
    'chat_attempts': {'root_id','snapshot_sha256','reliability'},
    'chat_context_policy': {'summary_model_id'},
    "messages": {"origin_user_message_id", "origin_user_content_sha256", "origin_character_id"},
    "awakening_sessions": {
        "feedback_review_limit", "feedback_review_tokens", "feedback_review_calls_used",
        "feedback_review_output_used", "psychology_appraisal_limit", "psychology_appraisal_tokens",
    },
    "activity_operations": {"expected_enjoyment", "strategy_id"},
}
VOICE_ACCEPTANCE_OBJECTS = frozenset({
    "voice_audio_cache", "voice_message_audio",
    "voice_audio_cache_expiry", "voice_message_audio_cache",
    "sqlite_autoindex_voice_audio_cache_1",
    "sqlite_autoindex_voice_message_audio_1",
})
DAILY_VOICE_FLAGS = {
    "YOSAKI_ALLOWED_ORIGINS": "http://127.0.0.1:5173",
    "YOSAKI_BACKGROUND_TASKS_ENABLED": "0",
    "YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED": "1",
    "YOSAKI_CHATGPT_PLAN_ENABLED": "1",
    "YOSAKI_SCRIBE_DAILY_ENABLED": "1",
    "YOSAKI_VOICE_AUTO_SPEAK_ENABLED": "1",
    "YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED": "1",
    "YOSAKI_VOICE_CONTINUOUS_ISOLATED": "0",
    "YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED": "1",
    "YOSAKI_SCRIBE_DRAFT_ISOLATED": "0",
    "YOSAKI_SCRIBE_REAL_PROVIDER_ISOLATED": "0",
    "YOSAKI_V4_DAILY_OUTPUT_ENABLED": "0",
    "YOSAKI_V4_OUTPUT_ENABLED": "0",
    "YOSAKI_V4_REAL_PROVIDER_ENABLED": "0",
    "YOSAKI_V4_DYNAMIC_CHAT_ISOLATED": "0",
    "YOSAKI_V4_DYNAMIC_CANDIDATE_ISOLATED": "0",
    "YOSAKI_V4_DYNAMIC_REAL_EXECUTE": "0",
}


def _daily_voice_local_dependencies(data_dir: Path) -> None:
    """Check local prerequisites without importing the app or contacting providers."""
    from cryptography.fernet import Fernet, InvalidToken

    if not os.getenv("ELEVENLABS_API_KEY", "").strip():
        raise ValueError("日常语音缺少 ElevenLabs Key；未启动")
    key = os.getenv("YOSAKI_SECRET_KEY")
    if not key:
        key_file = data_dir / ".secret_key"
        if not key_file.is_file():
            raise ValueError("日常语音缺少本地凭据加密密钥；未启动")
        key = key_file.read_bytes()
    else:
        key = key.encode()
    credential = data_dir / ".chatgpt_plan"
    if not credential.is_file():
        raise ValueError("日常语音缺少 ChatGPT Plan 本地授权；未启动")
    try:
        account = (json.loads(Fernet(key).decrypt(credential.read_bytes()))
                   .get("account") or {})
    except (ValueError, InvalidToken, TypeError, OSError) as exc:
        raise ValueError("日常语音无法读取 ChatGPT Plan 本地授权；未启动") from exc
    if not (account.get("access_token") and account.get("refresh_token") and
            "chatgpt.tokens.use.direct" in account.get("scopes", [])):
        raise ValueError("日常语音 ChatGPT Plan 授权不完整；未启动")


@dataclass(frozen=True)
class Runtime:
    mode: str
    data_dir: Path
    database: Path
    port: int


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def configuration(
    mode: str, *, project_root: Path = PROJECT_ROOT,
    preview_dir: Path = PREVIEW_DIR, test_dir: Path | None = None,
    port: int | None = None, isolated_development_fixture: bool = False,
) -> Runtime:
    project_root = project_root.resolve()
    development_dir = (project_root / "data").resolve()
    preview_dir = preview_dir.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if mode == "development":
        data_dir, default_port = development_dir, 8000
        if _within(data_dir, temp_root) and not isolated_development_fixture:
            raise ValueError("Development 数据目录不能位于系统临时目录")
    elif mode == "preview":
        data_dir, default_port = preview_dir, 8001
        if _within(data_dir, development_dir) or data_dir == development_dir:
            raise ValueError("Preview 不能使用 Development 数据目录")
    elif mode == "test":
        if test_dir is None:
            raise ValueError("Test 必须指定新建的独立临时目录")
        data_dir, default_port = test_dir.resolve(), 8002
        if not _within(data_dir, temp_root) or data_dir == temp_root or _within(data_dir, preview_dir):
            raise ValueError("Test 必须使用独立的系统临时目录")
        if _within(data_dir, development_dir):
            raise ValueError("Test 不能使用 Development 数据目录")
    else:
        raise ValueError(f"未知运行模式：{mode}")
    database = (data_dir / DATABASE_NAME).resolve()
    if mode != "development" and database == (development_dir / DATABASE_NAME).resolve():
        raise ValueError("隔离模式不能连接 Development 数据库")
    return Runtime(mode, data_dir, database, port or default_port)


def _database_path_from_connection(database: Path) -> Path:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("PRAGMA database_list").fetchone()
        if row is None or not row[2]:
            raise ValueError("无法确认 SQLite 数据库路径")
        return Path(row[2]).resolve()
    finally:
        connection.close()


def _voice_schema_matches(connection: sqlite3.Connection) -> bool:
    """Compare the read-only target with the current Voice V2 DDL in memory."""
    # This preflight runs from a script path, before run() adds the project to sys.path.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app.voice import storage as voice_storage  # Does not import main.

    reference = sqlite3.connect(":memory:")
    try:
        voice_storage.init_db(reference)
        def objects(db: sqlite3.Connection) -> dict[str, tuple[str, str, str | None]]:
            return {
                row[1]: (row[0], row[2], row[3])
                for row in db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE name IN (" + ",".join("?" for _ in VOICE_ACCEPTANCE_OBJECTS) + ")",
                    tuple(sorted(VOICE_ACCEPTANCE_OBJECTS)),
                )
            }
        expected = objects(reference)
        return set(expected) == VOICE_ACCEPTANCE_OBJECTS and objects(connection) == expected
    finally:
        reference.close()


def _auto_budget_schema_matches(connection: sqlite3.Connection) -> bool:
    """The opt-in auto path may start only after its separate migration."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app.voice import auto_budget
    reference = sqlite3.connect(":memory:")
    try:
        auto_budget.init_db(reference)
        expected = reference.execute("SELECT sql FROM sqlite_master WHERE name='voice_auto_session_budget'").fetchone()[0]
        actual = connection.execute("SELECT sql FROM sqlite_master WHERE name='voice_auto_session_budget'").fetchone()
        return actual is not None and actual[0] == expected
    finally:
        reference.close()


def _auto_daily_schema_matches(connection: sqlite3.Connection) -> bool:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app.voice import auto_budget, budget_schema
    reference = sqlite3.connect(":memory:")
    try:
        auto_budget.init_daily_db(reference)
        for name in ("voice_auto_owner_day_budget", "voice_auto_owner_settings",
                     "sqlite_autoindex_voice_auto_owner_day_budget_1"):
            expected = reference.execute(
                "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            actual = connection.execute(
                "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            if actual is None or tuple(actual) != tuple(expected) and tuple(actual) != (
                    expected[0], expected[1], budget_schema.expanded_ddl(name, expected[2])):
                return False
        return True
    finally:
        reference.close()


def _scribe_daily_schema_matches(connection: sqlite3.Connection) -> bool:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app.voice import scribe_budget, budget_schema
    reference = sqlite3.connect(":memory:")
    try:
        scribe_budget.init_db(reference)
        for name in ("voice_scribe_owner_settings", "voice_scribe_owner_day_budget",
                     "voice_scribe_attempts", "sqlite_autoindex_voice_scribe_owner_day_budget_1",
                     "sqlite_autoindex_voice_scribe_attempts_1"):
            expected = reference.execute(
                "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            actual = connection.execute(
                "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,)
            ).fetchone()
            if actual is None or tuple(actual) != tuple(expected) and tuple(actual) != (
                    expected[0], expected[1], budget_schema.expanded_ddl(name, expected[2])):
                return False
        return True
    finally:
        reference.close()


def _continuous_batch_ready(connection: sqlite3.Connection) -> bool:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app.voice import continuous_batch
    batch_id = os.getenv("YOSAKI_VOICE_CONTINUOUS_BATCH_ID", "")
    if not continuous_batch.BATCH_ID.fullmatch(batch_id):
        return False
    reference = sqlite3.connect(":memory:")
    try:
        continuous_batch.init_db(reference)
        expected = reference.execute("""SELECT sql FROM sqlite_master
            WHERE name='voice_continuous_batch_receipts'""").fetchone()[0]
        actual = connection.execute("""SELECT sql FROM sqlite_master
            WHERE name='voice_continuous_batch_receipts'""").fetchone()
        return bool(actual and actual[0] == expected and connection.execute(
            "SELECT 1 FROM voice_continuous_batch_receipts WHERE batch_id=?",
            (batch_id,)).fetchone())
    finally:
        reference.close()


def _continuous_daily_ready(connection: sqlite3.Connection) -> bool:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app.voice import budget_schema, continuous_daily
    reference = sqlite3.connect(":memory:")
    reference_v2 = sqlite3.connect(":memory:")
    try:
        continuous_daily.init_db(reference)
        continuous_daily.init_db_v2(reference_v2)
        expected = {row[0]: row[1] for row in reference.execute(
            "SELECT name,sql FROM sqlite_master WHERE name IN "
            "('voice_continuous_daily_calls','voice_continuous_daily_active_owner',"
            "'voice_continuous_owner_settings')")}
        expected_v2 = {row[0]: row[1] for row in reference_v2.execute(
            "SELECT name,sql FROM sqlite_master WHERE name IN "
            "('voice_continuous_daily_calls','voice_continuous_daily_active_owner',"
            "'voice_continuous_owner_settings')")}
        actual = {row[0]: row[1] for row in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE name IN "
            "('voice_continuous_daily_calls','voice_continuous_daily_active_owner',"
            "'voice_continuous_owner_settings')")}
        return actual in (expected, expected_v2, {name: budget_schema.expanded_ddl(name, sql)
                                                   for name, sql in expected_v2.items()})
    finally:
        reference.close()
        reference_v2.close()


def _ai_http_preflight(connection: sqlite3.Connection, runtime: Runtime, voice_acceptance: bool) -> None:
    if os.getenv('YOSAKI_VOICE_AI_HTTP_ENABLED','0') != '1':
        return
    if not voice_acceptance or os.getenv('YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED') != '1':
        raise ValueError('AI表达仅允许受保护日常Voice入口')
    if os.getenv('YOSAKI_VOICE_AI_HTTP_DATABASE') != str(runtime.database):
        raise ValueError('AI表达数据库绑定不符')
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0,str(PROJECT_ROOT))
    from backend.app.voice.ai_daily import TABLES, migrate, schema_present
    reference=sqlite3.connect(':memory:')
    try:
        reference.execute('BEGIN IMMEDIATE');migrate(reference)
        def objects(db):
            return db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master WHERE tbl_name IN ('+
                ','.join('?' for _ in TABLES)+') ORDER BY name',tuple(sorted(TABLES))).fetchall()
        if not schema_present(connection) or [tuple(r) for r in objects(connection)] != [tuple(r) for r in objects(reference)]:
            raise ValueError('AI表达6表及自动索引未完成受控迁移或结构不符')
    finally:reference.close()


def preflight(runtime: Runtime, *, inherited_data_dir: str | None = None,
              voice_acceptance: bool = False,
              daily_ttd_ws: bool = False,
              acceptance_frontend_port: int | None = None) -> None:
    if daily_ttd_ws and (runtime.mode != "development" or not voice_acceptance):
        raise ValueError("TTD WS 验收仅允许受保护日常 Voice 入口")
    if voice_acceptance and runtime.mode != "development":
        raise ValueError("Voice 验收入口仅允许 Development 模式")
    if inherited_data_dir:
        inherited = Path(inherited_data_dir).expanduser().resolve()
        if inherited != runtime.data_dir:
            raise ValueError(
                "当前终端的 YOSAKI_DATA_DIR 与所选模式不符；请使用无旧覆盖值的新终端"
            )
    if os.getenv("YOSAKI_VOICE_ENABLED", "0") == "1":
        raise ValueError("请清除继承的 Voice 环境覆盖值，使用显式验收入口")
    if runtime.mode == "development" and not runtime.database.is_file():
        raise ValueError("Development 数据库不存在，拒绝自动创建")
    if runtime.database.exists() and _database_path_from_connection(runtime.database) != runtime.database:
        raise ValueError("SQLite 实际数据库路径与运行模式不符")
    if any(os.getenv(flag) == '1' for flag in ('YOSAKI_CHRONICLE_WORKSPACE', 'YOSAKI_CHRONICLE_CONTEXT')):
        if not runtime.database.is_file():
            raise ValueError('Chronicle requires explicit migration before startup')
        sys.path.insert(0, str(PROJECT_ROOT))
        from backend.app.memory_v2_chronicle_gate import verify
        with closing(sqlite3.connect(f"file:{runtime.database.as_posix()}?mode=ro", uri=True)) as chronicle_db:
            chronicle_db.row_factory = sqlite3.Row
            chronicle_db.execute('PRAGMA query_only=ON')
            verify(chronicle_db)
    if runtime.mode != "development":
        return
    connection = sqlite3.connect(f"file:{runtime.database.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        _ai_http_preflight(connection,runtime,voice_acceptance)
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = REQUIRED_DEVELOPMENT_TABLES - tables
        columns_missing = {
            name: required - {
                row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')
            }
            for name, required in REQUIRED_DEVELOPMENT_COLUMNS.items()
        }
        if missing or any(columns_missing.values()):
            raise ValueError(
                "Development 数据库仍需当前代码的 Schema 初始化；R2-C 授权前拒绝启动"
            )
        if voice_acceptance and not _voice_schema_matches(connection):
            raise ValueError("Voice V2 Schema 与当前代码不一致；拒绝在导入 main 前自动迁移")
        if (voice_acceptance and os.getenv("YOSAKI_VOICE_AUTO_SPEAK_ENABLED", "0") == "1"
                and not _auto_budget_schema_matches(connection)):
            raise ValueError("自动朗读额度表尚未受控迁移；拒绝在导入 main 前启动")
        if (voice_acceptance and os.getenv("YOSAKI_VOICE_AUTO_SPEAK_ENABLED", "0") == "1"
                and os.getenv("YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED", "0") == "1"):
            from backend.app.voice import auto_budget
            try:
                auto_budget.daily_limits()
            except auto_budget.BudgetUnavailable as exc:
                raise ValueError(str(exc)) from exc
            if not _auto_daily_schema_matches(connection):
                raise ValueError("日常自动朗读额度表尚未受控迁移；拒绝在导入 main 前启动")
        if voice_acceptance and os.getenv("YOSAKI_VOICE_AUTO_EXPRESSION_ENABLED") == "1":
            from backend.app.voice import automatic_expression, config
            from scripts.migrate_voice_delivery import _reference
            expected = _reference()
            names = ("voice_auto_expression_decisions", "voice_expression_audio")
            if (not config.automatic_expression_enabled() or not automatic_expression.schema_present(connection)
                    or any(connection.execute(
                        "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
                        != expected[name] for name in names)):
                raise ValueError("自动语气 Schema 或展示开关未就绪；拒绝在导入 main 前启动")
        if voice_acceptance and os.getenv("YOSAKI_SCRIBE_DAILY_ENABLED", "0") == "1":
            from backend.app.voice import scribe_budget
            scribe_budget.maximum()
            if not _scribe_daily_schema_matches(connection):
                raise ValueError("日常 Scribe 额度表尚未受控迁移；拒绝在导入 main 前启动")
        if (voice_acceptance and os.getenv("YOSAKI_VOICE_CONTINUOUS_ISOLATED", "0") == "1"
                and not _continuous_batch_ready(connection)):
            raise ValueError("连续通话批次收据缺失或结构不符；拒绝在导入 main 前启动")
        if voice_acceptance and os.getenv("YOSAKI_VOICE_CONTINUOUS_DAILY_ENABLED", "0") == "1":
            if os.getenv("YOSAKI_VOICE_CONTINUOUS_ISOLATED", "0") == "1":
                raise ValueError("日常与验收连续模式不能同时启用")
            expected_flags = dict(DAILY_VOICE_FLAGS)
            if acceptance_frontend_port is not None:
                expected_flags["YOSAKI_ALLOWED_ORIGINS"] = (
                    f"http://127.0.0.1:{acceptance_frontend_port}")
            if daily_ttd_ws:
                expected_flags.update({"YOSAKI_V4_DAILY_OUTPUT_ENABLED": "1",
                                       "YOSAKI_V4_REAL_PROVIDER_ENABLED": "1"})
            for name, expected in expected_flags.items():
                if os.getenv(name) != expected:
                    raise ValueError(f"日常语音启动配置不完整：{name}")
            if not _continuous_daily_ready(connection):
                raise ValueError("日常连续通话表缺失或结构不符；拒绝在导入 main 前启动")
            from backend.app.voice import continuous_daily
            continuous_daily.maximum()
            _daily_voice_local_dependencies(runtime.data_dir)
    finally:
        connection.close()


def run(runtime: Runtime, *, inherited_data_dir: str | None = None,
        voice_acceptance: bool = False,
        daily_ttd_ws: bool = False,
        acceptance_frontend_port: int | None = None) -> None:
    preflight(runtime, inherited_data_dir=inherited_data_dir,
              voice_acceptance=voice_acceptance,
              daily_ttd_ws=daily_ttd_ws,
              acceptance_frontend_port=acceptance_frontend_port)
    if runtime.mode != "development":
        runtime.data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOSAKI_DATA_DIR"] = str(runtime.data_dir)
    os.environ["YOSAKI_VOICE_ENABLED"] = "1" if voice_acceptance else "0"
    if not voice_acceptance:
        os.environ["YOSAKI_VOICE_AUTO_SPEAK_ENABLED"] = "0"
    os.environ["YOSAKI_RUN_MODE"] = runtime.mode
    if runtime.mode == "development":
        os.environ["YOSAKI_BACKGROUND_TASKS_ENABLED"] = "0"
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from backend.app import main  # Import only after all path and schema checks.

    with closing(main.connect()) as connection:
        row = connection.execute("PRAGMA database_list").fetchone()
    actual = Path(row[2]).resolve() if row and row[2] else None
    if actual != runtime.database:
        raise RuntimeError("启动后 SQLite 路径与运行模式不符")
    print(
        f"Mode={runtime.mode} Database Path={actual} "
        f"Voice Enabled={'true' if voice_acceptance else 'false'} "
        f"Auto Speak Enabled={'true' if voice_acceptance and os.getenv('YOSAKI_VOICE_AUTO_SPEAK_ENABLED') == '1' else 'false'} "
        f"Daily Budget Enabled={'true' if voice_acceptance and os.getenv('YOSAKI_VOICE_AUTO_DAILY_BUDGET_ENABLED') == '1' else 'false'} "
        f"ChatGPT Plan Enabled={'true' if os.getenv('YOSAKI_CHATGPT_PLAN_ENABLED') == '1' else 'false'} "
        f"Scribe Daily Enabled={'true' if os.getenv('YOSAKI_SCRIBE_DAILY_ENABLED') == '1' else 'false'} "
        f"Display Gate Enabled={'true' if os.getenv('YOSAKI_CHAT_VOICE_DISPLAY_GATED_ISOLATED') == '1' else 'false'} "
        f"Daily TTD WS Enabled={'true' if daily_ttd_ws else 'false'} "
        f"Background Tasks Enabled={'false' if runtime.mode == 'development' else 'unchanged'}",
        flush=True,
    )
    import uvicorn

    uvicorn.run(main.app, host="127.0.0.1", port=runtime.port)


def main() -> int:
    parser = argparse.ArgumentParser(description="YOSAKI local runtime")
    parser.add_argument("mode", choices=("development", "preview", "test"))
    parser.add_argument("--voice-acceptance", action="store_true",
                        help="explicit, guarded Development Voice acceptance")
    parser.add_argument("--daily-voice", action="store_true",
                        help="use the single reviewed daily Voice configuration")
    parser.add_argument("--daily-ttd-ws", action="store_true",
                        help="explicit experimental daily TTD WS acceptance")
    parser.add_argument('--ai-expression',action='store_true',help='explicit protected ordinary-chat AI expression capability; user opt-in remains off')
    parser.add_argument("--port", type=int, help="isolated acceptance port")
    parser.add_argument("--acceptance-frontend-port", type=int,
                        help="exact loopback frontend origin for an isolated daily Voice acceptance port")
    parser.add_argument("--two-round-acceptance", action="store_true",
                        help="cap each call at two rounds without changing daily settings")
    parser.add_argument("--headset-acceptance-conversation", type=int,
                        help="bind a single headset acceptance call to an empty conversation")
    parser.add_argument("--single-button-acceptance-conversation", type=int,
                        help="bind one HTTP playback interrupt round to an empty conversation")
    args = parser.parse_args()
    if args.port is not None and (not args.voice_acceptance or not 1 <= args.port <= 65535):
        parser.error("--port 仅用于受控 Voice 验收且必须在 1..65535")
    if args.daily_ttd_ws and not args.daily_voice:
        parser.error("--daily-ttd-ws 仅用于受保护日常 Voice 入口")
    if args.ai_expression and not args.daily_voice:
        parser.error('--ai-expression仅用于受保护日常Voice入口')
    if args.acceptance_frontend_port is not None and (
            not args.daily_voice or args.port is None or args.port == 8000 or
            not 1 <= args.acceptance_frontend_port <= 65535):
        parser.error("独立前端来源仅用于非 8000 的受保护日常 Voice 验收入口")
    if args.two_round_acceptance and args.acceptance_frontend_port is None:
        parser.error("--two-round-acceptance 仅用于独立日常 Voice 验收入口")
    headset_conversation = args.headset_acceptance_conversation
    single_button = args.single_button_acceptance_conversation
    if single_button is not None and (
            single_button < 1 or args.acceptance_frontend_port is None
            or args.two_round_acceptance or args.daily_ttd_ws or headset_conversation is not None):
        parser.error("--single-button-acceptance-conversation requires isolated daily HTTP Voice")
    if headset_conversation is not None and (
            headset_conversation < 1 or not args.two_round_acceptance or not args.daily_ttd_ws):
        parser.error("--headset-acceptance-conversation requires guarded two-round WS acceptance")
    os.environ["YOSAKI_VOICE_HEADSET_ACCEPTANCE_CONVERSATION"] = str(headset_conversation or "")
    os.environ["YOSAKI_VOICE_SINGLE_BUTTON_CONVERSATION"] = str(single_button or "")
    os.environ["YOSAKI_VOICE_CONTINUOUS_TWO_ROUND_ACCEPTANCE"] = (
        "1" if args.two_round_acceptance else "0")
    if args.daily_voice:
        if args.mode != "development" or not args.voice_acceptance:
            parser.error("--daily-voice 仅用于受保护 Development Voice 入口")
        os.environ.update(DAILY_VOICE_FLAGS)
        if args.port in (None, 8000) and args.acceptance_frontend_port is None:
            os.environ.setdefault("YOSAKI_OWNER_BILLING", "metered")
            if os.environ["YOSAKI_OWNER_BILLING"] == "metered":
                os.environ["YOSAKI_CHRONICLE_PAID_ENABLED"] = "1"
        if args.acceptance_frontend_port is not None:
            os.environ["YOSAKI_ALLOWED_ORIGINS"] = (
                f"http://127.0.0.1:{args.acceptance_frontend_port}")
        if args.daily_ttd_ws:
            os.environ["YOSAKI_V4_DAILY_OUTPUT_ENABLED"] = "1"
            os.environ["YOSAKI_V4_REAL_PROVIDER_ENABLED"] = "1"
    inherited = os.environ.get("YOSAKI_DATA_DIR")
    test_dir = Path(tempfile.mkdtemp(prefix="yosaki-runtime-test-")) if args.mode == "test" and not inherited else None
    try:
        runtime = configuration(args.mode, test_dir=test_dir, port=args.port)
        os.environ['YOSAKI_VOICE_AI_HTTP_ENABLED']='1' if args.ai_expression else '0'
        os.environ['YOSAKI_VOICE_AI_HTTP_DATABASE']=str(runtime.database) if args.ai_expression else ''
        run(runtime, inherited_data_dir=inherited,
            voice_acceptance=args.voice_acceptance,
            daily_ttd_ws=args.daily_ttd_ws,
            acceptance_frontend_port=args.acceptance_frontend_port)
    except (ValueError, sqlite3.Error) as exc:
        print(f"启动已拒绝：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
