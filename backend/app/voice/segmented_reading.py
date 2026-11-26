"""Isolated long-reading executor. Never installed by main or daily runtime.

Existing cache claims, MP3 validation and HTTP adapter are reused. A completed
fragment is exported independently of whole-job success. Unknowns are terminal.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import time
import uuid

from . import audio_cache
from .annotation_planner import fingerprint
from .elevenlabs_adapter import ProviderError
from .expression_fixture import _write_json
from .http_diagnostics import SegmentDiagnostics
from .segment_plan import build


class SegmentedReading:
    def __init__(self, root, connect, body, adapter_factory, authority, reserve, *, between=None,
                 annotations=(), candidate_tags=False, request_timeout_seconds=30,
                 diagnostics_factory=SegmentDiagnostics,short_policy=None):
        if type(request_timeout_seconds) not in (int,float) or not 0 < request_timeout_seconds <= 92:
            raise ValueError('isolated_request_deadline_invalid')
        self.request_timeout_seconds=request_timeout_seconds
        self.diagnostics_factory=diagnostics_factory
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.connect, self.adapter_factory = connect, adapter_factory
        self.authority, self.reserve, self.between = authority, reserve, between
        self.preview = build(body, annotations, candidate_tags=candidate_tags,short_policy=short_policy)
        self.lock = asyncio.Lock()
        self.task = None
        self.cancelled = False
        self.active = set()
        path = self.root / 'result.json'
        if path.exists():
            self.result = json.loads(path.read_text(encoding='utf-8'))
            if self.result['preview_sha256'] != self.preview['preview_sha256']:
                raise ValueError('frozen_text_changed')
            if self.result['status'] == 'generating':
                self.result['status'] = 'unknown'
                self.result['error_code'] = 'previous_process_outcome_unknown'
                self.save()
        else:
            _write_json(self.root / 'preview.json', self.preview)
            self.result = {'job_id': uuid.uuid4().hex, 'preview_sha256': self.preview['preview_sha256'],
                'status': 'not_started', 'error_code': None, 'http_attempts': 0, 'characters': 0,
                'actual_billing': 'unknown', 'segments': [dict(sequence=s['sequence'], status='not_executed',
                characters=s['characters'], text_sha256=s['text_sha256']) for s in self.preview['segments']]}
            self.save()

    def save(self):
        _write_json(self.root / 'result.json', self.result)

    async def start(self, preview_sha):
        async with self.lock:
            if preview_sha != self.preview['preview_sha256']:
                raise ValueError('preview_stale')
            if self.result['status'] != 'not_started':
                return self.result
            # Persistent exclusive marker before scheduling, also across processes.
            from .realtime_receipt import occupy_once
            occupy_once(self.root / 'execution.occupied', {'preview_sha256': preview_sha,
                'job_id': self.result['job_id'], 'status': 'occupied_outcome_unknown'})
            self.result['status'] = 'generating'
            self.save()
            self.task = asyncio.create_task(self.run())
            return self.result

    async def cancel(self):
        self.cancelled = True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            if self.result['status'] == 'generating':
                self.result.update(status='cancelled',error_code='cancelled_before_task_started')
                self.save()

    def audio(self, sequence):
        self.authority()
        item = self.result['segments'][sequence]
        if item['status'] != 'completed':
            raise ValueError('fragment_not_complete')
        path = self.root / item['audio_file']
        if time.time() - path.stat().st_mtime >= audio_cache.TTL_DAYS * 86400:
            raise ValueError('fragment_expired')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['audio_sha256']:
            raise ValueError('fragment_hash_changed')
        audio_cache.validate_mp3(data)
        return path

    async def run(self):
        self.result['started_monotonic'] = time.monotonic()
        try:
            async with asyncio.timeout(260):
                for segment, item in zip(self.preview['segments'], self.result['segments']):
                    if self.cancelled:
                        raise asyncio.CancelledError
                    binding = self.authority()
                    state, cache_id, attempt = audio_cache.claim(self.connect, 1, binding['voice_id'],
                        binding['revision'], self.preview['language_code'], segment['text'], self.active)
                    if state not in ('new', 'ready'):
                        raise ValueError('cache_unknown_or_busy')
                    diag = None
                    attempted = False
                    try:
                        if state == 'new':
                            diag = self.diagnostics_factory(self.root / 'diagnostics', self.result['job_id'],
                                                       segment['sequence'], attempt)
                            adapter = self.adapter_factory(diag)
                            # All permission/capacity checks precede the atomic per-segment debit.
                            self.authority()
                            self.reserve(segment['characters'], self.result['http_attempts'] == 0)
                            item.update(status='reserved_not_dispatched', budget_reserved=True)
                            self.save()
                            diag.event('meter_reserved')
                            if self.cancelled:
                                raise asyncio.CancelledError
                            item['status'] = 'attempted_result_unknown'
                            self.result['http_attempts'] += 1
                            self.result['characters'] += segment['characters']
                            self.save()
                            attempted = True
                            async with asyncio.timeout(self.request_timeout_seconds):
                                audio = await adapter.synthesize_http(segment['text'], binding['voice_id'],
                                                                      self.preview['language_code'])
                            if self.cancelled:
                                raise asyncio.CancelledError
                            self.authority()
                            adapter.synthesis_phase = 'integrity_check'
                            diag.event('integrity_check')
                            audio_cache.validate_mp3(audio)
                            diag.event('integrity_passed')
                            adapter.synthesis_phase = 'saving'
                            diag.event('saving')
                            audio_cache.publish(self.connect, cache_id, attempt, audio)
                            diag.event('saved', cache_id=cache_id)
                        else:
                            with self.connect() as db:
                                row = db.execute('SELECT relative_path FROM voice_audio_cache WHERE id=?',
                                                 (cache_id,)).fetchone()
                            audio = audio_cache.safe_path(row['relative_path']).read_bytes()
                            audio_cache.validate_mp3(audio)
                        # Each complete fragment becomes readable now, not only after the last.
                        path = self.root / ('segment-%02d.mp3' % (segment['sequence'] + 1))
                        if path.exists():
                            raise ValueError('fragment_export_exists')
                        with path.open('xb') as f:
                            f.write(audio)
                            f.flush()
                            import os
                            os.fsync(f.fileno())
                        item.update(status='completed', audio_file=path.name, audio_bytes=len(audio),
                            audio_sha256=hashlib.sha256(audio).hexdigest(), cache_id=cache_id,
                            reused_cache=state == 'ready')
                        self.save()
                    except BaseException as exc:
                        status = 'unknown'
                        if isinstance(exc, asyncio.CancelledError):
                            status = 'cancelled'
                        elif not attempted and state == 'new':
                            status = 'not_dispatched'
                        elif isinstance(exc, ProviderError) and not exc.unknown:
                            status = 'failed'
                        item.update(status=status,
                            failure_phase=getattr(locals().get('adapter'), 'synthesis_phase', None))
                        if state == 'new':
                            if attempted:
                                audio_cache.mark(self.connect, cache_id, attempt,
                                    unknown=not isinstance(exc, ProviderError) or exc.unknown)
                            else:
                                audio_cache.discard_unattempted(self.connect, cache_id, attempt)
                        if diag:
                            diag.event('stopped', status=item['status'], error_type=type(exc).__name__)
                        raise
                    finally:
                        self.active.discard(attempt)
                        if diag:
                            item['provider_dispatch_attempted'] = diag.record['dispatch_attempted']
                            diag.finish()
                        self.save()
                    if self.between and segment['sequence'] + 1 < self.preview['requests']:
                        await self.between()
                self.authority()
                self.result['status'] = 'completed'
        except asyncio.CancelledError:
            self.result.update(status='cancelled', error_code='user_cancelled_no_retry')
        except ProviderError as exc:
            self.result.update(status='unknown' if exc.unknown else 'failed',
                               error_code='provider_unknown' if exc.unknown else 'provider_rejected')
        except Exception as exc:
            self.result.update(status='failed' if type(exc).__name__ in ('BudgetExhausted','PermissionError')
                               else 'unknown', error_code='local_' +
                (type(exc).__name__ if type(exc).__name__ in ('TimeoutError', 'ValueError',
                 'DiagnosticFailure', 'BudgetExhausted', 'PermissionError', 'OSError') else 'unclassified'))
        finally:
            self.result['ended_monotonic'] = time.monotonic()
            self.save()
