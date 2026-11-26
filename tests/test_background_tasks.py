"""Exercise real lifespan against an already initialized disposable database."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BackgroundGate(unittest.TestCase):
    def test_lifespan_gate_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="yosaki-background-gate-") as folder:
            env = dict(os.environ, YOSAKI_DATA_DIR=folder, YOSAKI_VOICE_ENABLED="0")
            env.pop("ELEVENLABS_API_KEY", None)
            result = subprocess.run(
                [sys.executable, "-c", PROBE], env=env,
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


PROBE = r'''
import asyncio, os, socket
from unittest.mock import patch
from contextlib import closing
from backend.app import main
from backend.app.background_tasks import enabled
def baseline():
    with closing(main.connect()) as db:
        return list(db.iterdump())
before = baseline()  # Schema initialization is complete before lifespan baseline.
async def exercise():
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    for value in ("0", "1", None):
        with patch.dict(os.environ):
            if value is None:
                os.environ.pop("YOSAKI_BACKGROUND_TASKS_ENABLED", None)
            else:
                os.environ["YOSAKI_BACKGROUND_TASKS_ENABLED"] = value
            created = []
            def capture(coro):
                coro.close()  # Enabled branch never runs real workers or dependencies.
                assert value != "0", "disabled startup created a task"
                future = asyncio.get_running_loop().create_future()
                future.set_result(None)
                created.append(future)
                return future
            with patch("asyncio.create_task", side_effect=capture):
                async with main.app.router.lifespan_context(main.app):
                    assert len(created) == (0 if value == "0" else 3)
                    assert baseline() == before
            assert all(f.done() for f in created)
            assert baseline() == before
    with patch.dict(os.environ, {"YOSAKI_BACKGROUND_TASKS_ENABLED": "invalid"}):
        try:
            enabled()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid switch accepted")
asyncio.run(exercise())
'''
