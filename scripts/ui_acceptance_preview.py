"""Serve the real built UI/API on new synthetic data. No daily data or providers.

Run after VITE_YOSAKI_CHRONICLE_A0=1 npm run build.
Login: ui-preview / Yosaki-UI-Preview-2026! (disposable test account only).
"""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    for key in list(os.environ):
        if any(t in key.upper() for t in ('YOSAKI_', 'OPENAI', 'ELEVENLABS', 'API_KEY', 'TOKEN', 'SECRET')):
            os.environ.pop(key, None)
    from scripts.yosaki_runtime import configuration, preflight, run
    directory = Path(tempfile.mkdtemp(prefix='yosaki-ui-acceptance-'))
    runtime = configuration('test', test_dir=directory, port=65117)
    preflight(runtime)
    os.environ.update(YOSAKI_DATA_DIR=str(directory), YOSAKI_BACKGROUND_TASKS_ENABLED='0',
                      YOSAKI_ALLOWED_ORIGINS='http://127.0.0.1:65117', YOSAKI_VOICE_ENABLED='0')

    def block_external(event, args):
        if event == 'socket.connect' and isinstance(args[1], tuple) and args[1][0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('UI acceptance blocks external network')
    sys.addaudithook(block_external)
    from backend.app import main as app
    from backend.app import memory_v2_derivation_schema as schema, memory_v2_import_schema as imports
    from backend.app import memory_v2_metadata as meta, memory_v2_index as indexer
    from backend.app import memory_v2_dialogue_refinement as finite, memory_v2_chronicle_workspace as workspace
    from backend.app import memory_v2_chronicle_curation as curation, memory_v2_chronicle_vectors as vectors
    from backend.app import memory_v2_processing_schema as a3, memory_v2_jobs as jobs
    from backend.app import memory_v2_metadata_jobs, memory_v2_tag_schema, memory_v2_lexical_schema
    from backend.app import memory_v2_temporal_digest_schema, memory_v2_time_projection as p2
    from backend.app import memory_v2_chronicle_dates as dates, memory_v2_m36_trial as trials
    with app.connect() as db:
        for module in (schema, imports, meta, jobs, memory_v2_metadata_jobs, p2, curation, vectors, dates, trials):
            module.init_db(db)
        db.commit()
        for module in (a3, memory_v2_tag_schema, memory_v2_lexical_schema, memory_v2_temporal_digest_schema):
            module.migrate(db)
        stamp = app.now()
        db.execute('INSERT INTO users(id,username,password_hash,created_at) VALUES(1,?,?,?)',
                   ('ui-preview', app.password_hash('Yosaki-UI-Preview-2026!'), stamp))
        cid = db.execute('SELECT id FROM characters ORDER BY id LIMIT 1').fetchone()[0]
        conv = db.execute('INSERT INTO conversations(character_id,title,created_at,updated_at) VALUES(?,?,?,?)',
                          (cid, '雨后的散步 · 中日文阅读样本', stamp, stamp)).lastrowid
        user = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user',?,?)",
                          (conv, '今天想慢慢走一段路。你愿意陪我聊聊吗？\n\n这是独立测试库的合成阅读样本，不是真实对话。', stamp)).lastrowid
        text = ('当然。我们可以先把今天放慢一点，不急着替每件事情找一个结论。窗边的光还在，雨已经停了。\n\n'
                '「急がなくていい。今日は、少し遠回りをして帰ろう。お前が話したいことから、ゆっくり聞かせてくれ。」\n\n'
                '如果你愿意，我们就沿着河边走。想停下来时就停下来，没想好要说什么也没有关系。\n\n'
                '### 可以从这里开始\n\n- 今天让你停留片刻的一件小事。\n- 一个还没有答案的想法。\n- 或者，只是此刻的心情。\n\n'
                '> 「言葉にならない気持ちも、そのままでいい。」\n\n')
        long_text = text + '\n\n'.join(f'第 {i+1} 段。'+('静かな道を歩きながら、今日の出来事を少しずつ思い出す。答えを急がず、相手の言葉を最後まで聞く。'*6)+'\n\n'+('有些事情需要时间。我们保留完整的表达，也保留尚未确定的部分。'*8) for i in range(40))
        assistant = db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'assistant',?,?)", (conv, long_text, stamp)).lastrowid
        job = indexer.start_job(db, cid, 'rebuild')
        for _ in range(100):
            if indexer.step_job(db, job['job_id'])['status'] == 'completed': break
        refs = [{'kind': 'message', 'id': str(user)}, {'kind': 'message', 'id': str(assistant)}]
        draft = meta.create_draft(db, cid, refs, '雨后的散步 · 合成阅读样本', 1, 'ui-acceptance')
        v = finite.create(db, cid, 1, draft['entryId'], draft['versionId'])
        workspace.confirm_narrative(db, draft['entryId'], workspace.Confirm(characterId=cid, expectedVersion=draft['versionId'], versionId=v['id'], inputHash=v['input_hash'], outputHash=v['output_hash']), 1)
        draft = meta.read_draft(db, draft['entryId'], cid)
        draft = meta.revise(db, draft['entryId'], cid, draft['versionId'], {'synopsis': '合成样本：她想慢慢散步，阿夜表示愿意倾听。完整记录保留中文段落与日文引语。'}, 1)
        for name in ('title', 'synopsis'):
            draft = meta.review_field(db, draft['entryId'], cid, name, draft['fields'][name]['token'], 1)
        meta.publish(db, draft['entryId'], cid, draft['versionId'], 1)
    workspace.install(app.app, app.connect, app.current_session, allowed_origins=app.ALLOWED_ORIGINS)
    os.environ.update(YOSAKI_CHRONICLE_WORKSPACE='1', YOSAKI_CHRONICLE_CONTEXT='1')
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    dist = ROOT / 'frontend/dist'
    app.app.mount('/assets', StaticFiles(directory=dist/'assets'), name='ui-assets')
    @app.app.get('/{path:path}')
    def frontend(path: str):
        from fastapi import HTTPException
        if path.startswith('api/'):
            raise HTTPException(404)
        return FileResponse(dist/'index.html')
    print(f'Synthetic UI preview: http://127.0.0.1:65117 | data={directory}', flush=True)
    run(runtime, inherited_data_dir=str(directory))


if __name__ == '__main__':
    main()
