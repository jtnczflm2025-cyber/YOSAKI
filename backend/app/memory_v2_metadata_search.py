"""Explicitly rebuilt metadata terms; reads revalidate every field and RAW source."""
from . import memory_v2_metadata as metadata, memory_v2_sources as sources
from .memory_v2_contract import CandidateSeed, SourceRef


def init_db(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mv3_metadata_terms(
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
      field_name TEXT NOT NULL, field_token TEXT NOT NULL, term TEXT NOT NULL,
      PRIMARY KEY(character_id,entry_id,field_name,term))''')


def rebuild(db,entry_id,character_id):
    detail=metadata.read_draft(db,entry_id,character_id)
    db.execute('DELETE FROM mv3_metadata_terms WHERE entry_id=? AND character_id=?',(entry_id,character_id))
    for name,field in detail['fields'].items():
        if field['lifecycleStatus']!='available': continue
        value=field['value']
        if name in ('title','synopsis'): words=sources.terms(value)
        elif name=='keywords': words=set().union(*(sources.terms(x['text']) for x in value)) if value else set()
        elif name in ('categories','tags'): words={x['id'] for x in value}
        else: continue
        db.executemany('INSERT INTO mv3_metadata_terms VALUES(?,?,?,?,?)',
            [(character_id,entry_id,name,field['token'],word) for word in sorted(words)])


def search(db,character_id,query,*,vocabulary_ids=(),purpose='management',limit=20):
    if purpose not in ('management','candidate') or not 1<=limit<=50:
        raise ValueError('invalid_search')
    words=sources.query_terms(query)|set(vocabulary_ids)
    if not words: return []
    placeholders=','.join('?' for _ in words)
    rows=db.execute(f'''SELECT * FROM mv3_metadata_terms WHERE character_id=? AND term IN ({placeholders})
        ORDER BY entry_id,field_name,term LIMIT 1000''',(character_id,*sorted(words))).fetchall()
    hits={}
    for row in rows:
        try: detail=metadata.read_draft(db,row['entry_id'],character_id)
        except ValueError: continue
        if purpose=='candidate' and not detail['qualifications']['candidateRecall']: continue
        field=detail['fields'][row['field_name']]
        if field['lifecycleStatus']!='available' or field.get('token')!=row['field_token']: continue
        hit=hits.setdefault(row['entry_id'],{'entryId':row['entry_id'],'detail':detail,'matches':[]})
        hit['matches'].append({'field':row['field_name'],'token':row['field_token'],'term':row['term']})
    return sorted(hits.values(),key=lambda x:(-len(x['matches']),x['entryId']))[:limit]


class MetadataGenerator:
    name='chronicle_metadata'
    def generate(self,db,request,generation):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_metadata_terms'").fetchone(): return []
        return [CandidateSeed(tuple(SourceRef(r['source_kind'],r['source_id']) for r in hit['detail']['sourceRefs']),
            self.name,tuple(sorted({m['term'] for m in hit['matches']})),'metadata',
            hit['entryId'],hit['detail']['versionId']) for hit in search(db,request.character_id,request.topic,purpose='candidate')]
