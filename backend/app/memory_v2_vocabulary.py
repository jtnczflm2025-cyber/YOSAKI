"""Character-scoped category/tag vocabulary. Revisions retain merge evidence."""
import json
import uuid
from . import memory_v2_metadata_contract as c, memory_v2_derivation as m3


def init_db(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS mv3_vocabulary(
      id TEXT PRIMARY KEY, character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('category','tag','keyword')), current_version INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS mv3_vocabulary_versions(
      vocabulary_id TEXT NOT NULL REFERENCES mv3_vocabulary(id) ON DELETE CASCADE,
      version INTEGER NOT NULL, dimension TEXT, language TEXT NOT NULL, name TEXT NOT NULL,
      definition TEXT NOT NULL, aliases_json TEXT NOT NULL, status TEXT NOT NULL,
      merged_into TEXT REFERENCES mv3_vocabulary(id), semantic_token TEXT NOT NULL,
      alias_token TEXT NOT NULL, display_token TEXT NOT NULL, normalization_version TEXT NOT NULL,
      provenance TEXT NOT NULL, evidence_json TEXT NOT NULL,
      editor_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
      PRIMARY KEY(vocabulary_id,version));
    ''')


def get(db, character_id, vocabulary_id):
    row = db.execute('''SELECT v.*,r.character_id,r.kind FROM mv3_vocabulary r
        JOIN mv3_vocabulary_versions v ON v.vocabulary_id=r.id AND v.version=r.current_version
        JOIN characters ch ON ch.id=r.character_id AND ch.enabled=1
        WHERE r.id=? AND r.character_id=?''', (vocabulary_id, character_id)).fetchone()
    if row is None: raise ValueError('vocabulary_unavailable')
    return dict(row)


def write(db, character_id, kind, name, language, editor_id, *, vocabulary_id=None,
          expected_version=None, dimension=None, definition='', aliases=(), status='active',
          merged_into=None, evidence=(), seed_id=None, seed_provenance='user_corrected'):
    from .memory_v2_metadata import _owner
    from .memory_v2_entries import _begin
    _begin(db); _owner(db, editor_id)
    if seed_provenance!='user_corrected' and (seed_provenance!='system_suggested' or not seed_id or vocabulary_id):
        raise ValueError('vocabulary_seed_invalid')
    if not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1', (character_id,)).fetchone():
        raise ValueError('role_unavailable')
    if (kind not in ('category','tag','keyword') or not isinstance(name,str) or not name.strip() or len(name)>80
            or not isinstance(language,str) or not language or len(language)>20
            or status not in ('active','deprecated') or len(aliases)>32
            or any(not isinstance(a,str) or not a.strip() or len(a)>80 for a in aliases)):
        raise ValueError('vocabulary_invalid')
    if kind == 'category' and dimension not in c.CATEGORIES:
        raise ValueError('category_dimension_invalid')
    previous = get(db, character_id, vocabulary_id) if vocabulary_id else None
    if previous and (previous['version'] != expected_version or previous['kind'] != kind
                     or previous['dimension'] != dimension or previous['language'] != language):
        raise ValueError('vocabulary_version_conflict')
    if merged_into:
        target = get(db, character_id, merged_into)
        if target['kind'] != kind or target['dimension'] != dimension or target['status'] != 'active':
            raise ValueError('merge_target_invalid')
        if merged_into == vocabulary_id: raise ValueError('merge_cycle')
        if not evidence: raise ValueError('merge_evidence_required')
        status = 'deprecated'
    aliases = sorted(set(aliases))
    normalized = {c.normalized(name), *(c.normalized(a) for a in aliases)}
    for row in db.execute('SELECT id FROM mv3_vocabulary WHERE character_id=? AND kind=?', (character_id,kind)):
        other = get(db, character_id, row['id'])
        if other['vocabulary_id'] == vocabulary_id or other['language'] != language or other['status'] != 'active': continue
        if status == 'active' and normalized & {c.normalized(other['name']), *(c.normalized(a) for a in json.loads(other['aliases_json']))}:
            raise ValueError('vocabulary_alias_collision')
    vid = vocabulary_id or seed_id or str(uuid.uuid4()); version = previous['version']+1 if previous else 1
    # Renaming display text does not change semantic identity or confirmed aliases.
    semantic = c.token([kind, dimension, definition, status, merged_into])
    alias_token = c.token([language, aliases, 'nfkc-casefold-v1'])
    db.execute('''INSERT INTO mv3_vocabulary_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (vid,version,dimension,language,name,definition,json.dumps(aliases,ensure_ascii=False),status,
         merged_into,semantic,alias_token,c.token([name]),'nfkc-casefold-v1','user_corrected',
         json.dumps(evidence,ensure_ascii=False),editor_id,m3._now())) if previous else None
    if not previous:
        db.execute('INSERT INTO mv3_vocabulary VALUES(?,?,?,?)', (vid,character_id,kind,version))
        db.execute('INSERT INTO mv3_vocabulary_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (vid,version,dimension,language,name,definition,json.dumps(aliases,ensure_ascii=False),status,
             merged_into,semantic,alias_token,c.token([name]),'nfkc-casefold-v1',seed_provenance,
             json.dumps(evidence,ensure_ascii=False),editor_id,m3._now()))
    else:
        db.execute('UPDATE mv3_vocabulary SET current_version=? WHERE id=?', (version,vid))
    return get(db, character_id, vid)


def seed(db, character_id, editor_id):
    result=[]
    for dimension, names in c.CATEGORIES.items():
        for position,name in enumerate(names):
            vid=str(uuid.uuid5(uuid.NAMESPACE_URL, f'yosaki/category/{character_id}/{dimension}/{position}'))
            if not db.execute('SELECT 1 FROM mv3_vocabulary WHERE id=?', (vid,)).fetchone():
                write(db,character_id,'category',name,'zh',editor_id,dimension=dimension,definition=name,seed_id=vid)
            result.append(get(db,character_id,vid))
    return result


def match(db, character_id, kind, text, language):
    key=c.normalized(text)
    for row in db.execute('SELECT id FROM mv3_vocabulary WHERE character_id=? AND kind=?', (character_id,kind)):
        item=get(db,character_id,row['id'])
        if item['language']==language and item['status']=='active' and key in {
            c.normalized(item['name']), *(c.normalized(a) for a in json.loads(item['aliases_json']))}:
            return item
    return None
