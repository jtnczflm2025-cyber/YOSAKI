"""Local reading drafts for one completed import file. No model or publication."""
from datetime import datetime
import hashlib
import re

from . import memory_v2_import as importer
from . import memory_v2_chronicle_channel as channel

RULE = 'local-dialogue-reading-v1'
TOPIC = re.compile(r'^(?:换个话题|说到这里|另外一件事|对了[，,]|话说[，,]|ところで|話は変わる)')


def partition(rows):
    """Prefer conversation breaks, then split long stretches at a new user turn."""
    result = []
    start = 0
    chars = 0
    previous = None

    def finish(stop):
        group = rows[start:stop]
        title = f"{len(result)+1:02d} · {group[0]['occurred_date'] or '日期未核实'} · 主题待整理"
        result.append(dict(start=group[0]['ordinal'], end=group[-1]['ordinal'], title=title))

    for index, row in enumerate(rows):
        # Compare wall-clock times only within the same recorded time basis.
        stamp = row['occurred_at'] or (row['original_time_text'] if row['time_precision']=='local_datetime_unverified' else None)
        now = datetime.fromisoformat(stamp) if stamp else None
        gap = (now - previous).total_seconds() if now and previous and (now.tzinfo is None)==(previous.tzinfo is None) else 0
        changed_day = index and row['occurred_date'] and rows[index-1]['occurred_date'] and row['occurred_date']!=rows[index-1]['occurred_date']
        size = index-start
        if size and (gap > 1800 or changed_day or size >= 48 or
                     (row['role']=='user' and (size >= 24 or chars >= 6000 or (size >= 4 and TOPIC.match(row['content'].lstrip()))))):
            finish(index)
            start = index
            chars = 0
        chars += len(row['content'])
        previous = now
    if rows:
        finish(len(rows))
    return result


def create(db, batch_id, file_id, cid, owner_id):
    if db.in_transaction:
        raise ValueError('reading_requires_own_transaction')
    channel.authorized(db,cid,owner_id)
    importer._batch(db,batch_id,cid)
    file=db.execute('SELECT * FROM mv2_import_files WHERE id=? AND batch_id=? AND character_id=?',
                    (file_id,batch_id,cid)).fetchone()
    if not file:
        raise ValueError('import_file_unavailable')
    file,records,snapshot=importer.confirmed_file_snapshot(db,batch_id,cid,file_id,file['file_sha256'],file['parser_version'],general=True)
    rows=db.execute('''SELECT r.ordinal,r.occurred_date,r.occurred_at,r.original_time_text,r.time_precision,m.role,m.content
        FROM mv2_import_receipts r JOIN messages m ON m.id=r.message_id WHERE r.file_id=? ORDER BY r.ordinal''',(file_id,)).fetchall()
    if len(rows)!=len(records) or any(hashlib.sha256(r['content'].encode()).hexdigest()!=s['contentHash'] for r,s in zip(rows,records)):
        raise ValueError('reading_source_changed')
    saved=[(r[0],r[1],r[2]) for r in db.execute('SELECT start_ordinal,end_ordinal,title FROM mv2_import_segments WHERE file_id=? ORDER BY ordinal',(file_id,))]
    original=importer._suggest([{'row':{'role':r['role']},'ordinal':r['ordinal'],'time':{'occurredAt':r['occurred_at']}} for r in rows])
    # Keep custom ranges and the original persisted partition on replay.
    parts=(partition(rows) if saved==original else [dict(start=a,end=b,title=t) for a,b,t in saved])
    db.rollback()  # Release our read snapshot; the writer revalidates under BEGIN IMMEDIATE.
    return importer._confirm_drafts(db,batch_id,cid,file_id,parts,expected_sha=file['file_sha256'],
        expected_parser=file['parser_version'],expected_snapshot=snapshot,h31_snapshot_hash=hashlib.sha256(RULE.encode()).hexdigest(),
        editor_id=owner_id,confirmation_origin=RULE,required_count=len(parts),expected_total=len(records),generic=True,
        prepare_reading=True)
