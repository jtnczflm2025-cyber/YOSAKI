"""Read-only startup contract. Never imports main or installs schema."""
import json
from pathlib import Path


def verify(db):
    expected = json.loads(Path(__file__).with_name('memory_v2_chronicle_schema_contract.json').read_text(encoding='utf-8'))
    missing = []
    for name, columns in expected['tables'].items():
        actual = {r[1]: list(r)[1:] for r in db.execute(f'PRAGMA table_info("{name}")')}
        if any(actual.get(column[1]) != column[1:] for column in columns):
            missing.append(name)
    for name, sql in expected['indexes']:
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if row is None or ' '.join(row[0].split()).casefold() != ' '.join(sql.split()).casefold():
            missing.append(name)
    for name, keys in expected.get('foreignKeys',{}).items():
        if [list(r) for r in db.execute(f'PRAGMA foreign_key_list("{name}")')]!=keys:
            missing.append(name+'.foreign_keys')
    for name, sql in expected.get('triggers',[]):
        row=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
        if row is None or ' '.join(row[0].split()).casefold()!=' '.join(sql.split()).casefold():
            missing.append(name)
    if 'archive_only' not in {r[1] for r in db.execute('PRAGMA table_info(conversations)')}:
        missing.append('conversations.archive_only')
    if missing:
        raise ValueError('chronicle_explicit_migration_required:' + ','.join(missing))
    from . import memory_v2_tag_schema, memory_v2_lexical_schema, memory_v2_temporal_digest_schema
    for schema in (memory_v2_tag_schema, memory_v2_lexical_schema):
        schema.require(db)
    if not memory_v2_temporal_digest_schema.installed(db):
        raise ValueError('chronicle_temporal_migration_required')
    return {'status': 'ready', 'readOnly': True}
