"""Exercise the actual app/Chronicle on a NEW isolated database, never daily data.

No outbound network. An optional explicitly supplied reference stays local.
The supplied Synopsis is labelled a reference, not a new model generation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--reference')
    parser.add_argument('--translations')
    parser.add_argument('--no-reference-synopsis',action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=65104)
    args = parser.parse_args()
    data = Path(args.data_dir).resolve()
    if data == ROOT/'data' or (data/'yosaki.sqlite3').exists():
        raise SystemExit('requires_new_isolated_data_directory')
    data.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        if any(t in key.upper() for t in ('OPENAI','ELEVENLABS','API_KEY','YOSAKI_','TOKEN','SECRET')):
            os.environ.pop(key, None)
    os.environ.update(YOSAKI_DATA_DIR=str(data), YOSAKI_CHRONICLE_WORKSPACE='1', YOSAKI_CHRONICLE_CONTEXT='1',
                      YOSAKI_ALLOWED_ORIGINS=f'http://127.0.0.1:{args.port}')
    def guard(event, values):
        if event == 'socket.connect' and isinstance(values[1], tuple) and values[1][0] not in ('127.0.0.1','::1','localhost'):
            raise RuntimeError('external_network_blocked')
    sys.addaudithook(guard)
    sys.path.insert(0, str(ROOT))
    from backend.app import main as app
    from backend.app import memory_v2_derivation_schema as schema, memory_v2_import_schema as imports
    from backend.app import memory_v2_metadata as meta, memory_v2_import as importer
    from backend.app import memory_v2_index as indexer, memory_v2_dialogue_refinement as finite
    from backend.app import memory_v2_chronicle_curation as curation, memory_v2_chronicle_vectors as vectors
    from backend.app import memory_v2_processing_schema as a3, memory_v2_jobs as jobs
    from backend.app import memory_v2_metadata_jobs, memory_v2_tag_schema, memory_v2_lexical_schema
    from backend.app import memory_v2_temporal_digest_schema, memory_v2_time_projection as p2
    with app.connect() as db:
        before = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        schema.init_db(db); imports.init_db(db); meta.init_db(db); jobs.init_db(db); memory_v2_metadata_jobs.init_db(db); p2.init_db(db)
        curation.init_db(db); vectors.init_db(db)
        from backend.app import memory_v2_chronicle_dates as dates, memory_v2_m36_trial as trials
        dates.init_db(db); trials.init_db(db); db.commit()
        a3.migrate(db); memory_v2_tag_schema.migrate(db); memory_v2_lexical_schema.migrate(db); memory_v2_temporal_digest_schema.migrate(db)
        after = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(1,'closeout-local',?,?)",
                   (app.password_hash('Closeout-Isolated-Only-2026!'), app.now()))
        cid=db.execute('SELECT id FROM characters ORDER BY id LIMIT 1').fetchone()[0]
    reference_hash=None
    if args.reference:
        original=Path(args.reference).read_bytes();reference_hash=hashlib.sha256(original).hexdigest()
        document=original.decode('utf-8-sig')
        raw=document.split('【未经处理的原对话记录】',1)[1].split('【处理后的记忆条目】',1)[0]
        pattern=r'\[(用户|阿夜)\]\s*_(.*?)_\s*\n(.*?)(?=\n---\s*\n\[|\Z)'
        messages=[{'id':str(i+1),'role':'user' if role=='用户' else 'assistant','created_at':stamp,
                   'content':body.strip()} for i,(role,stamp,body) in enumerate(re.findall(pattern,raw,re.S))]
        reference=document.split('【处理后的记忆条目】',1)[1]
        synopsis=reference.split('梗概——',1)[1].split('对话记录——',1)[0].strip()
        title='2026-09-20 · 原始精修样例（本地核对）'
    else:
        messages=[{'id':'one','role':'user','created_at':'2026-11-16T10:00:00+08:00','content':'我考虑先修好收音机。没有测试之前，不确定它是否可用。'},
                  {'id':'two','role':'assistant','created_at':'2026-11-16T10:01:00+08:00','content':'……俺は待つ。\n……お前が決める。 （予備の部品は二つ）'}]
        synopsis='イキヤ考虑修理收音机，并明确测试前不能确定是否可用；阿夜表示等待她决定。'
        title='2026-11-16 · 修理收音机（合成）'
    body=json.dumps({'format':'yosaki-conversation','version':2,'conversation':{'id':'closeout-local-reference'},
                     'messages':messages},ensure_ascii=False).encode()
    annotations=json.loads(Path(args.translations).read_text(encoding='utf-8')) if args.translations else {}
    with app.connect() as db:
        bid=importer.prepare(db,cid,[('local-reference.json',body)])['batchId']
        importer.confirm(db,bid,cid)
        result=importer.step(db,bid,cid,limit=100)
        if result['status']!='raw_complete': raise ValueError('reference_incomplete')
        j=indexer.start_job(db,cid,'rebuild')
        for _ in range(100):
            if indexer.step_job(db,j['job_id'])['status']=='completed':break
        else:raise ValueError('index_incomplete')
        db.commit()
        f=db.execute('SELECT * FROM mv2_import_files WHERE batch_id=?',(bid,)).fetchone()
        _,records,fingerprint=importer.confirmed_file_snapshot(db,bid,cid,f['id'],f['file_sha256'],f['parser_version'],general=True)
        bound=importer.confirm_dialogue_drafts(db,bid,cid,f['id'],[dict(start=1,end=len(messages),title=title)],
            expected_sha=f['file_sha256'],expected_parser=f['parser_version'],expected_snapshot=fingerprint,editor_id=1)
        eid=bound['items'][0]['entryId'];detail=meta.read_draft(db,eid,cid)
        translated={r['messageId']:[{'start':a,'end':b,'quote':q} for a,b,q in annotations.get(str(i+1),[])]
                    for i,r in enumerate(records)}
        v=finite.create(db,cid,1,eid,detail['versionId'],translations=translated)
        if not args.no_reference_synopsis:
            meta.revise(db,eid,cid,detail['versionId'],{'synopsis':synopsis},1)
        assert db.execute('SELECT COUNT(*) FROM mv3_reviews').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM mv3_entries WHERE status="active"').fetchone()[0]==0
        saved=[dict(r) for r in db.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id',(f['conversation_id'],))]
        assert [(r['role'],r['content']) for r in saved]==[(r['role'],r['content']) for r in messages]
        report=dict(entryId=eid,characterId=cid,messages=len(messages),sourceFileSha256=reference_hash,
            refinedVersionId=v['id'],refinedBody=v['body'],mapping=json.loads(v['mapping_json']),
            synopsis=None if args.no_reference_synopsis else synopsis,synopsisOrigin='not_generated' if args.no_reference_synopsis else 'provided_reference_not_new_generation',raw=messages,
            reviews=0,published=0,providerRequests=0,addedTables=sorted(set(after)-set(before)))
    (data/'reference_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k in ('entryId','characterId','messages','sourceFileSha256','reviews','published','providerRequests','addedTables')},ensure_ascii=False),flush=True)
    if args.serve:
        import uvicorn
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        dist=ROOT/'frontend/dist'
        app.app.mount('/assets',StaticFiles(directory=dist/'assets'),name='closeout-assets')
        @app.app.get('/chronicle')
        @app.app.get('/hub')
        @app.app.get('/')
        def frontend(): return FileResponse(dist/'index.html')
        uvicorn.run(app.app,host='127.0.0.1',port=args.port,reload=False,workers=1)


if __name__=='__main__':main()
