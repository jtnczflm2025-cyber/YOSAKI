"""Rebuildable embedding storage and cosine ranking, never hash-vector fallback.

An embedding model must be supplied by a trusted installed adapter. This module
does not claim that test vectors establish semantic quality or call a Provider.
"""
import json
import math
from . import memory_v2_chronicle_channel as channel, memory_v2_entries as entries
from . import memory_v2_derivation as m3


def init_db(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mv3_chronicle_vectors(
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
        model_version TEXT NOT NULL, input_hash TEXT NOT NULL, dimensions INTEGER NOT NULL,
        vector_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(character_id,entry_id,model_version))''')


def _input(db, cid, eid):
    detail = entries.read_entry(db, eid, cid)
    # Reuse the established vector qualification: confirmed title + Synopsis.
    if not detail.get('synopsis'):
        raise ValueError('vector_synopsis_required')
    channel._narrative(db, cid, detail)
    text = detail['title'] + '\n' + detail['synopsis']
    return text, m3._hash([detail['versionId'], text, detail['sourceRefs']])


def unit(vector):
    if (not isinstance(vector, list) or not 1 <= len(vector) <= 4096
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)):
        raise ValueError('embedding_invalid')
    norm = math.sqrt(sum(x*x for x in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('embedding_invalid')
    return [x/norm for x in vector]


def store(db, cid, owner_id, eid, model_version, expected_hash, vector):
    """Internal trusted adapter completion, not a public vector-writing API."""
    if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
    channel.authorized(db, cid, owner_id)
    if not isinstance(model_version, str) or not model_version or len(model_version) > 200:
        raise ValueError('embedding_model_required')
    _, fingerprint = _input(db, cid, eid)
    if fingerprint != expected_hash:
        raise ValueError('embedding_input_changed')
    value = unit(vector)
    # Keep the corpus stable for cached query vectors from the same snapshot.
    # A different input fingerprint still replaces the old corpus vector.
    db.execute('''INSERT INTO mv3_chronicle_vectors VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(character_id,entry_id,model_version) DO UPDATE SET input_hash=excluded.input_hash,
        dimensions=excluded.dimensions,vector_json=excluded.vector_json,created_at=excluded.created_at
        WHERE mv3_chronicle_vectors.input_hash<>excluded.input_hash
           OR mv3_chronicle_vectors.dimensions<>excluded.dimensions''',
        (cid, eid, model_version, fingerprint, len(value), json.dumps(value, allow_nan=False), m3._now()))


def _qualified_vectors(db, cid, owner_id, model_version, entry_ids=None):
    channel.authorized(db, cid, owner_id)
    rows = db.execute('SELECT * FROM mv3_chronicle_vectors WHERE character_id=? AND model_version=? LIMIT 1001',
                      (cid, model_version)).fetchall()
    if len(rows) > 1000:
        raise ValueError('embedding_capacity_exceeded')
    for row in rows:
        if entry_ids is not None and row['entry_id'] not in entry_ids:
            continue
        try:
            text, fingerprint = _input(db, cid, row['entry_id'])
            if row['input_hash'] != fingerprint:
                continue
            v = unit(json.loads(row['vector_json']))
            if len(v)!=row['dimensions']: continue
        except (ValueError, TypeError):
            continue
        yield row['entry_id'], text, fingerprint, v


def _ranked_candidates(db, cid, owner_id, query_vector, model_version):
    q = unit(query_vector)
    return sorted((-sum(a*b for a,b in zip(q,v)), eid)
                  for eid, _, _, v in _qualified_vectors(db,cid,owner_id,model_version) if len(v)==len(q))


def candidates(db, cid, owner_id, query_vector, model_version, *, min_cosine=0.6, limit=10):
    if type(min_cosine) not in (int, float) or not 0 <= min_cosine <= 1 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('embedding_query_invalid')
    ranked = _ranked_candidates(db, cid, owner_id, query_vector, model_version)
    return [eid for score, eid in ranked if -score >= min_cosine][:limit]


def hybrid(db, cid, owner_id, query, *, query_vector=None, model_version=None, now=None, limit=5, budget_tokens=1024):
    if query_vector is None or model_version is None:
        return channel.search(db, cid, owner_id, query, now=now, limit=limit, budget_tokens=budget_tokens)
    try:
        ranked = _ranked_candidates(db, cid, owner_id, query_vector, model_version)
    except ValueError as exc:
        if str(exc) != 'embedding_capacity_exceeded':
            raise
        result = channel.search(db, cid, owner_id, query, now=now, limit=limit, budget_tokens=budget_tokens)
        return {**result, 'vectorStatus': 'capacity_exceeded'}
    if not ranked:
        return channel.search(db, cid, owner_id, query, now=now, limit=limit, budget_tokens=budget_tokens)
    admitted = [eid for score, eid in ranked if -score >= 0.60]
    lexical_ids = channel.lexical_candidates(db, cid, owner_id, query, limit=limit,
                                             allow_partial_for=set(admitted[:20]))
    ids = admitted[:max(10, limit)]
    # Reciprocal rank fusion; source qualification is repeated by projection.
    scores = {}
    for ranked in (lexical_ids, ids):
        for i, eid in enumerate(dict.fromkeys(ranked)):
            scores[eid] = scores.get(eid, 0) + 1/(60+i+1)
    selected = sorted(scores, key=lambda eid: (-scores[eid], eid))[:limit]
    result = channel.project(db, cid, owner_id, selected, now=now, budget_tokens=budget_tokens,
                             max_blocks=limit)
    return {**result, 'mode': 'hybrid', 'vectorStatus': 'current', 'embeddingModel': model_version}
