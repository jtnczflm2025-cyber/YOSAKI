"""Durable, explicitly requested local work. No model, publication or calendar worker."""
import json
import sqlite3
import threading
import logging

from fastapi import HTTPException

from . import memory_v2_import as raw
from . import memory_v2_import_reading as reading
from . import memory_v2_index as index
from .memory_v2_chronicle_channel import authorized

KIND = 'chronicle_local_archive_v1'
MODES = ('raw', 'index', 'organize')
ACTIVE = ('queued', 'running')


def task(db, batch, cid):
    return db.execute('SELECT * FROM mv3_jobs WHERE kind=? AND input_hash=? AND character_id=?',
                      (KIND, batch, cid)).fetchone()


def public(db, row):
    if not row:
        return None
    cfg = json.loads(row['payload_json'])
    current = index._job(db, cfg['index']) if cfg.get('index') else None
    return dict(id=row['id'], mode=cfg['mode'], status=row['status'], stage=cfg['stage'],
                version=row['attempts'], reason=row['error_code'],
                indexed=current['processed'] if current else 0)


def enqueue(db, batch, cid, owner, mode, version, fingerprint):
    raw._begin(db)
    authorized(db, cid, owner)
    source = raw._batch(db, batch, cid)
    if mode not in MODES or (mode == 'index' and source['status'] != 'raw_complete'):
        raise HTTPException(409, '请先完成原文归档')
    if db.execute("SELECT 1 FROM mv3_jobs WHERE id=? AND status IN ('running','queued')", (batch,)).fetchone():
        raise HTTPException(409, '本批已有单独授权的整理任务')
    row = task(db, batch, cid)
    if row:
        cfg = json.loads(row['payload_json'])
        if cfg['owner'] != owner:
            raise HTTPException(403, '不能操作其他账户的任务')
        if cfg['mode'] == mode:
            return
        if row['status'] != 'completed' or MODES.index(cfg['mode']) >= MODES.index(mode):
            raise HTTPException(409, '请先查看已有任务进度')
    else:
        cfg = dict(owner=owner, batch=batch, index=None)
    if source['status'] == 'preview' and (version != source['plan_version'] or
            not fingerprint or fingerprint != raw._staged_preview_fingerprint(db, batch)):
        raise HTTPException(409, '预览已变化，请重新查看后提交')
    if mode=='index' and not cfg.get('index'):
        legacy=db.execute("SELECT * FROM mv2_jobs WHERE character_id=? AND status IN ('paused','running')",(cid,)).fetchone()
        if legacy:
            owned=db.execute("SELECT 1 FROM mv3_jobs WHERE kind=? AND character_id=? AND json_extract(payload_json,'$.index')=?",
                             (KIND,cid,legacy['job_id'])).fetchone()
            if owned:
                raise HTTPException(409,'本角色另有后台索引任务，请先查看它的进度')
            if legacy['status']=='paused' and legacy['error_code']:
                raise HTTPException(409,'已有索引需要修正来源，请先检查其错误')
            cfg['index']=legacy['job_id']
    cfg.update(mode=mode, stage='archive', version=version, fingerprint=fingerprint)
    db.execute("""INSERT INTO mv3_jobs(id,character_id,kind,input_hash,payload_json,status,created_at)
        VALUES(?,?,?,?,?,'queued',?) ON CONFLICT(character_id,kind,input_hash) DO UPDATE SET
        payload_json=excluded.payload_json,status='queued',attempts=attempts+1,error_code=NULL""",
        ('local-import-'+batch,cid,KIND,batch,json.dumps(cfg),raw._now()))


def update(db, row, cfg, status, reason=None):
    db.execute('UPDATE mv3_jobs SET payload_json=?,status=?,error_code=? WHERE id=? AND kind=?',
               (json.dumps(cfg), status, reason, row['id'], KIND))


def require_unmanaged_index(db, job_id, cid):
    raw._begin(db)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_jobs'").fetchone() and db.execute(
        """SELECT 1 FROM mv3_jobs WHERE kind=? AND character_id=? AND status IN ('queued','running','paused','failed')
           AND json_extract(payload_json,'$.index')=?""",(KIND,cid,job_id)).fetchone():
        raise HTTPException(409, '请从 Chronicle 对应批次暂停或继续本地索引')


def change(db, batch, cid, owner, action, expected):
    raw._begin(db)
    authorized(db,cid,owner)
    row = task(db,batch,cid)
    if not row:
        raise HTTPException(404, '找不到本地任务')
    cfg = json.loads(row['payload_json'])
    if cfg['owner'] != owner:
        raise HTTPException(403, '任务归属已变化')
    allowed = {'restart':('cancelled',),'pause':ACTIVE,'resume':('paused','failed'),'cancel':(*ACTIVE,'paused','failed')}
    if row['attempts'] != expected or row['status'] not in allowed.get(action, ()):
        raise HTTPException(409, '任务状态已变化，请重新查看进度')
    status = {'restart':'queued','pause':'paused','resume':'queued','cancel':'cancelled'}[action]
    if action == 'cancel' and cfg.get('index'):
        db.execute("""UPDATE mv2_jobs SET status='failed',error_code='local_task_cancelled',updated_at=?
                      WHERE job_id=? AND status IN ('running','paused')""", (raw._now(),cfg['index']))
    if action == 'restart' and cfg.get('index'):
        previous=index._job(db,cfg['index'])
        if previous and previous['error_code']=='local_task_cancelled':
            cfg['index']=None
    if action in ('resume','restart'):
        source = raw._batch(db,batch,cid)
        if source['status']=='failed':
            db.execute("UPDATE mv2_import_batches SET status='paused',error_code=NULL WHERE id=?", (batch,))
        if cfg.get('index'):
            current=index._job(db,cfg['index'])
            if current and current['status']=='paused':
                index.set_status(db,cfg['index'],'running')
    update(db,row,cfg,status)
    db.execute('UPDATE mv3_jobs SET attempts=attempts+1 WHERE id=?',(row['id'],))


def advance(db, row):
    cfg = json.loads(row['payload_json'])
    cid, batch = row['character_id'], cfg['batch']
    authorized(db,cid,cfg['owner'])
    source=raw._batch(db,batch,cid)
    state=source['status']
    if state != 'raw_complete':
        cfg['stage']='archive'
        update(db,row,cfg,'running')
        if state=='preview':
            raw.confirm(db,batch,cid,cfg['version'],cfg['fingerprint'])
        elif state=='paused':
            raw.set_paused(db,batch,cid,False)
        elif state in ('confirmed','running'):
            raw.step(db,batch,cid)
        else:
            raise ValueError('archive_needs_attention')
        return
    if cfg['mode'] != 'raw':
        cfg['stage']='index'
        if not cfg.get('index'):
            meta=db.execute('SELECT state FROM mv2_meta WHERE character_id=?',(cid,)).fetchone()
            current=index.start_job(db,cid,'incremental' if meta and meta[0]=='ready' else 'rebuild')
            cfg['index']=current['job_id']
            update(db,row,cfg,'running')
            return
        current=index._job(db,cfg['index'])
        if not current or current['character_id']!=cid:
            raise ValueError('index_unavailable')
        if current['status']=='paused' and not current['error_code']:
            current=index.set_status(db,cfg['index'],'running')
        if current['status']=='running':
            current=index.step_job(db,cfg['index'])
        if current['status']!='completed':
            update(db,row,cfg,'running' if current['status']=='running' else 'failed',current['error_code'])
            return
    if cfg['mode']=='organize':
        file=db.execute("""SELECT f.id FROM mv2_import_files f WHERE f.batch_id=? AND NOT EXISTS
            (SELECT 1 FROM mv2_import_segments s WHERE s.file_id=f.id AND s.entry_id IS NOT NULL)
            ORDER BY f.rowid LIMIT 1""",(batch,)).fetchone()
        if file:
            cfg['stage']='drafts'
            update(db,row,cfg,'running')
            db.commit()
            reading.create(db,batch,file[0],cid,cfg['owner'])
            return
    cfg['stage']='done'
    update(db,row,cfg,'completed')


class Worker:
    """Single worker on the protected single-process server. Waits without polling when idle."""
    def __init__(self, connect):
        self.connect=connect
        self.signal=threading.Event()
        self.stopping=threading.Event()
        self.lock=threading.Lock()
        self.thread=None

    def start(self):
        self.stopping.clear()
        self.thread=threading.Thread(target=self.run,name='chronicle-local-archive',daemon=True)
        self.thread.start()
        self.signal.set()

    def stop(self):
        self.stopping.set()
        self.signal.set()
        if self.thread:
            self.thread.join()

    def tick(self):
        with self.lock, self.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_jobs'").fetchone():
                return False
            raw._begin(db)
            row=db.execute("""SELECT * FROM mv3_jobs WHERE kind=? AND status IN ('queued','running')
                              ORDER BY created_at,rowid LIMIT 1""",(KIND,)).fetchone()
            if not row:
                return False
            try:
                advance(db,row)
            except Exception as exc:
                db.rollback()
                reason=str(exc) if isinstance(exc,ValueError) and str(exc) in (
                    'job_already_open','archive_needs_attention','index_unavailable','reading_source_changed'
                ) else 'access_or_source_changed' if isinstance(exc,HTTPException) else 'local_step_failed'
                logging.getLogger(__name__).warning('local archive stage failed: %s / %s',reason,type(exc).__name__)
                db.execute("UPDATE mv3_jobs SET status='failed',error_code=? WHERE id=? AND status IN ('queued','running')",
                           (reason,row['id']))
            return True

    def run(self):
        while not self.stopping.is_set():
            self.signal.wait()
            self.signal.clear()
            try:
                while not self.stopping.is_set() and self.tick():
                    pass
            except sqlite3.Error:
                logging.getLogger(__name__).exception('Local archive database unavailable')
                if not self.stopping.wait(2):
                    self.signal.set()
