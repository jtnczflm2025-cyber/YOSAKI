"""Chronicle's model-facing boundary. Never returns RAW/CLEANED, even on fallback.

The existing M2 RAW shadow interface is a management experiment, not this channel.
Reads do not migrate, dispatch, review or repair anything.
"""
from datetime import datetime, timezone
from heapq import nsmallest
import unicodedata

from . import memory_v2_derivation as m3, memory_v2_entries as entries
from . import memory_v2_time_projection as p2

RETENTION_POLICY = 'chronicle-curated-synopsis-v1'


def authorized(db, character_id, owner_id):
    if not db.in_transaction:
        db.execute('BEGIN')
    if not db.execute('''SELECT 1 FROM users u JOIN characters c ON c.id=?
            WHERE u.id=? AND c.enabled=1''', (character_id, owner_id)).fetchone():
        raise ValueError('chronicle_access_denied')


def _normalize(text):
    return unicodedata.normalize('NFKC', text).casefold().strip()


def _narrative(db, character_id, detail):
    vid = detail.get('narrativeVersionId')
    if not vid:
        raise ValueError('refined_dialogue_missing')
    version = m3.read_version(db, vid, character_id)
    owner = m3._row(db, 'mv3_derivations', version['derivation_id'])
    if owner['view_type'] != 'NARRATIVE' or version['review_status'] != 'human_checked':
        raise ValueError('refined_dialogue_not_confirmed')
    # A legacy quote wrapper is not first-person refinement. Keep it available
    # in manager history, but never rename it to a refined model-facing view.
    if version['rule_version'].startswith('attributed-quotation-'):
        raise ValueError('refined_dialogue_required')
    return version


def expand(db, character_id, owner_id, entry_id, *, offset=0, limit=4096, expected_version=None):
    authorized(db, character_id, owner_id)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8192:
        raise ValueError('invalid_page')
    detail = entries.read_entry(db, entry_id, character_id)
    version = _narrative(db, character_id, detail)
    if (offset and expected_version != version['id']) or (expected_version and expected_version != version['id']):
        raise ValueError('refined_dialogue_changed')
    text = version['body']
    return dict(entryId=entry_id, versionId=version['id'], view='NARRATIVE', title=detail['title'],
                text=text[offset:offset+limit], offset=offset, totalLength=len(text),
                nextOffset=offset+limit if offset+limit < len(text) else None)


def lexical_candidates(db, character_id, owner_id, query, *, limit=5, allow_partial_for=None):
    authorized(db, character_id, owner_id)
    if not isinstance(query, str) or len(query) > 1000 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('invalid_query')
    terms = list(dict.fromkeys(_normalize(query).split()))
    if not terms or not p2._table_exists(db, 'mv3_entries'):
        return []
    from . import memory_v2_lexical as lexical, memory_v2_metadata as metadata
    from .memory_v2_keyword_rules import _script
    vocab = lexical._vocabulary(db, character_id)
    query_items = [(term, False) for term in terms]
    cjk_phrases = [(term, True) for term in terms if len(term) >= 2 and _script(term) in ('han', 'kana')]
    rows = db.execute("SELECT id FROM mv3_entries WHERE character_id=? AND status='active' ORDER BY id",
                      (character_id,))
    def qualifying_hits():
        for row in rows:
            try:
                detail = entries.read_entry(db, row['id'], character_id)
                _narrative(db, character_id, detail)
                values = lexical._field_values(metadata.read_draft(db, row['id'], character_id), vocab)
                matched, counts, score, _ = lexical._score(values, query_items)
                prose = [value for value in values if value['field'] in ('title', 'synopsis')]
                if (allow_partial_for is not None and row['id'] not in allow_partial_for
                        and len(terms) > 1 and matched < len(terms)):
                    # Lexical-only hits in hybrid need evidence for the whole
                    # query, including terms matched as phrases in reviewed prose.
                    if not all(
                        lexical._score(values, [(term, False)])[0]
                        or ((term, True) in cjk_phrases and lexical._score(prose, [(term, True)])[0])
                        for term in terms
                    ):
                        continue
                if not matched and cjk_phrases:
                    # Chronicle's search box takes literal CJK phrases within
                    # approved prose. Tag and Keyword terms keep exact bounds.
                    matched, counts, score, _ = lexical._score(prose, cjk_phrases)
            except ValueError:
                continue
            if matched:
                yield tuple(-x for x in counts) + (-score,), detail['entryId']

    # The cursor streams one read snapshot, and nsmallest retains only the
    # requested winners. Large roles still scan every eligible row.
    return [entry_id for _, entry_id in nsmallest(limit, qualifying_hits())]


def search(db, character_id, owner_id, query, *, now=None, limit=5, budget_tokens=1024):
    selected = lexical_candidates(db, character_id, owner_id, query, limit=limit)
    if not p2._table_exists(db, 'mv3_entries'):
        return {'blocks': [], 'mode': 'lexical', 'state': 'not_installed', 'vectorStatus': 'not_configured'}
    return project(db, character_id, owner_id, selected, now=now,
                   budget_tokens=budget_tokens, max_blocks=limit)


def project(db, character_id, owner_id, selected, *, now=None, budget_tokens=1024, max_blocks=5):
    authorized(db, character_id, owner_id)
    view_time = now or datetime.now(timezone.utc)
    p2._checked_view(view_time, 480)
    if (not isinstance(selected,(list,tuple)) or len(selected)>50 or
            any(not isinstance(eid,str) for eid in selected) or
            type(budget_tokens) is not int or not 0<=budget_tokens<=4096 or
            type(max_blocks) is not int or not 1<=max_blocks<=20):
        raise ValueError('chronicle_projection_bounds')
    eligible, omitted = [], []
    for entry_id in dict.fromkeys(selected):
        try:
            _narrative(db, character_id, entries.read_entry(db, entry_id, character_id))
            eligible.append(entry_id)
        except ValueError:
            omitted.append({'entryId':entry_id,'reason':'current_content_not_eligible'})
            continue
    from . import memory_v2_chronicle_curation as curation
    retained = set(curation.fixed_entries(db, character_id, owner_id)).intersection(eligible)
    safe, retention_diagnostics, retained_cost = [], [], 0
    for entry_id in eligible:
        if entry_id not in retained:
            continue
        detail = entries.read_entry(db, entry_id, character_id)
        # This is an explicit Owner selection, never inferred from importance
        # or keyword scores. Use the whole confirmed Synopsis, not a period
        # digest that can compress away an agreement or include other members.
        text = detail.get('synopsis') or detail['title']
        cost = p2.estimate_tokens(text) + 12
        if retained_cost + cost > budget_tokens or len(safe) >= max_blocks:
            retention_diagnostics.append({'entryId': entry_id, 'reason': 'retained_budget_exhausted'})
            continue
        if not detail.get('synopsis'):
            retention_diagnostics.append({'entryId': entry_id, 'reason': 'confirmed_synopsis_missing_title_only'})
        safe.append(dict(projectionKind='entry_synopsis' if detail.get('synopsis') else 'title_index',
            entryIds=[entry_id], text=text, level=None, viewType=None,
            entryVersionId=detail['versionId'], retentionPolicy=RETENTION_POLICY,
            retainedBeyondTimeLayer=True))
        retained_cost += cost
    ordinary = [entry_id for entry_id in eligible if entry_id not in retained]
    result = (p2.context_projection(db, character_id, ordinary, now=view_time,
                 budget_tokens=budget_tokens-retained_cost, max_blocks=max_blocks-len(safe))
              if ordinary and len(safe) < max_blocks else {'blocks': []})
    for block in result['blocks']:
        if block['projectionKind'] == 'period_digest':
            version = m3._row(db, 'mv3_versions', block['digestVersionId'])
            if version is None or version['rule_version'].startswith('digest-extractive-'):
                # Extractive DIGEST contains verbatim RAW. Fall back using
                # individually qualified summaries, never its wrapped text.
                for entry_id in block['entryIds']:
                    detail = entries.read_entry(db, entry_id, character_id)
                    text = detail.get('synopsis') or detail['title']
                    safe.append(dict(projectionKind='entry_synopsis' if detail.get('synopsis') else 'title_index',
                        entryIds=[entry_id], text=text, level=block['level'], viewType=None))
                continue
        if block.get('viewType') not in (None, 'NARRATIVE', 'DIGEST'):
            raise ValueError('unsafe_memory_view')
        safe.append(block)
    # Recompute budget after fallback; no body truncation or half-record injection.
    blocks, used = [], 0
    for item in safe:
        cost = p2.estimate_tokens(item['text']) + 12
        if used + cost <= budget_tokens and len(blocks) < max_blocks:
            blocks.append({k: v for k, v in item.items() if k not in ('sourceRefs', 'eligibleForFormalRecall')})
            used += cost
    return {'blocks': blocks, 'estimatedTokens': used, 'mode': 'lexical',
            'vectorStatus': 'not_configured', 'noModelCalls': True,
            'retentionPolicy': RETENTION_POLICY, 'retentionDiagnostics': retention_diagnostics,
            'budget': {'limitTokens':budget_tokens,'limitBlocks':max_blocks,
                       'usedTokens':used,'usedBlocks':len(blocks),'estimated':True},
            'selectionDetails': [dict(entryId=eid,title=entries.read_entry(db,eid,character_id)['title'],
                 retained=eid in retained, included=any(eid in b['entryIds'] for b in blocks),
                 projection=next((b['projectionKind'] for b in blocks if eid in b['entryIds']),None),
                 level=next((b.get('level') for b in blocks if eid in b['entryIds']),None),
                 estimatedTokens=next((p2.estimate_tokens(b['text'])+12 for b in blocks if eid in b['entryIds']),0),
                 sharedBlock=next((len(b['entryIds'])>1 for b in blocks if eid in b['entryIds']),False),
                 reason=next((d['reason'] for d in retention_diagnostics if d['entryId']==eid),
                             None if any(eid in b['entryIds'] for b in blocks) else 'projection_budget_or_qualification'))
                for eid in eligible] + omitted}
