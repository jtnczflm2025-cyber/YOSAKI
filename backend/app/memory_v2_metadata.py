"""P1-A2 field revisions belonging to existing mv3_entries; isolated installation."""
import json
import uuid
from . import memory_v2_entries as legacy, memory_v2_derivation as m3
from . import memory_v2_metadata_contract as c


def init_db(db):
    from . import memory_v2_vocabulary as vocabulary
    columns = {r[1] for r in db.execute('PRAGMA table_info(mv3_entries)')}
    for name, sql in [('contract_version', 'INTEGER NOT NULL DEFAULT 1'),
                      ('draft_version_id', 'TEXT')]:
        if name not in columns:
            db.execute(f'ALTER TABLE mv3_entries ADD COLUMN {name} {sql}')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS mv3_entry_field_sets(
      id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
      version INTEGER NOT NULL, sources_json TEXT NOT NULL, source_token TEXT NOT NULL,
      time_evidence_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(entry_id,version));
    CREATE TABLE IF NOT EXISTS mv3_entry_fields(
      id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
      name TEXT NOT NULL, value_json TEXT NOT NULL, provenance TEXT NOT NULL,
      rule_version TEXT NOT NULL, evidence_json TEXT NOT NULL, dependencies_json TEXT NOT NULL,
      token TEXT NOT NULL, editor_id INTEGER REFERENCES users(id), locked INTEGER NOT NULL,
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mv3_entry_field_links(
      set_id TEXT NOT NULL REFERENCES mv3_entry_field_sets(id) ON DELETE CASCADE,
      name TEXT NOT NULL, field_id TEXT NOT NULL REFERENCES mv3_entry_fields(id),
      PRIMARY KEY(set_id,name));
    CREATE TABLE IF NOT EXISTS mv3_entry_field_checks(
      field_id TEXT PRIMARY KEY REFERENCES mv3_entry_fields(id) ON DELETE CASCADE,
      binding TEXT NOT NULL, method TEXT NOT NULL, reviewer_id INTEGER REFERENCES users(id),
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mv3_metadata_idempotency(
      character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
      request_key TEXT NOT NULL, input_hash TEXT NOT NULL,
      entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
      PRIMARY KEY(character_id,request_key));
    ''')
    vocabulary.init_db(db)


def _owner(db, editor_id):
    if not db.execute('SELECT 1 FROM users WHERE id=?', (editor_id,)).fetchone():
        raise ValueError('authenticated_owner_required')


def _entry(db, entry_id, character_id):
    row = legacy._entry(db, entry_id)
    if row is None or row['character_id'] != character_id:
        raise ValueError('entry_unavailable')
    if row['status'] in ('archived', 'superseded') or row['contract_version'] != 2:
        raise ValueError('entry_unavailable')
    if not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1', (character_id,)).fetchone():
        raise ValueError('role_unavailable')
    return row


def _times(db, refs, character_id):
    result = []
    has_receipts = db.execute("SELECT 1 FROM sqlite_master WHERE name='mv2_import_receipts'").fetchone()
    for ordinal, ref in enumerate(refs):
        source = m3.live_source(db, character_id, ref['source_kind'], ref['source_id'])
        receipt = db.execute('SELECT * FROM mv2_import_receipts WHERE message_id=?',
            (ref['source_id'],)).fetchone() if has_receipts and ref['source_kind'] == 'message' else None
        data = dict(receipt) if receipt else {}
        result.append(dict(kind=ref['source_kind'], id=ref['source_id'],
            sourceOrdinal=data.get('ordinal', ordinal),
            rawTime=data.get('original_time_text', source.occurred_at),
            occurredAt=source.occurred_at, occurredDate=source.occurred_date,
            precision=source.time_precision, timezoneBasis=data.get('timezone_basis', 'source')))
    return result


def _set(db, entry):
    return db.execute('SELECT * FROM mv3_entry_field_sets WHERE id=? AND entry_id=?',
        (entry['draft_version_id'], entry['id'])).fetchone()


def _validate_sources(db, entry, version):
    if version is None:
        raise ValueError('version_missing')
    refs = json.loads(version['sources_json'])
    actual = m3.source_inputs(db, entry['character_id'],
        [{'kind': r['source_kind'], 'id': r['source_id']} for r in refs])
    if actual != refs or c.token(actual) != version['source_token']:
        raise ValueError('source_changed')
    if _times(db, refs, entry['character_id']) != json.loads(version['time_evidence_json']):
        raise ValueError('time_evidence_changed')
    return refs


def _fields(db, set_id):
    return {r['name']: r for r in db.execute('''SELECT f.* FROM mv3_entry_field_links l
        JOIN mv3_entry_fields f ON f.id=l.field_id WHERE l.set_id=?''', (set_id,))}


def _value(name, value):
    if name not in c.FIELDS or name == 'time':
        raise ValueError('unsupported_field')
    if name in ('title', 'synopsis'):
        if not isinstance(value, str) or not value.strip() or len(value) > (200 if name == 'title' else 3000):
            raise ValueError('field_invalid')
    elif name in ('categories', 'tags', 'keywords'):
        if not isinstance(value, list) or len(value) > 32:
            raise ValueError('field_invalid')
    elif name == 'narrative' and not isinstance(value, str):
        raise ValueError('field_invalid')
    return value


def _write_field(db, entry_id, name, value, source_token, editor_id, *, provenance='manual',
                 rule='manual-v2', dependencies=None, evidence=None, locked=True):
    deps = dict(dependencies or {})
    deps['source'] = source_token
    if name in ('categories','tags','keywords'):
        deps['normalization'] = c.token([name,'nfkc-casefold-v1'])
    payload = [name, value, provenance, rule, evidence or [], deps]
    fid = str(uuid.uuid4())
    db.execute('''INSERT INTO mv3_entry_fields VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (fid, entry_id, name, json.dumps(value, ensure_ascii=False), provenance, rule,
         json.dumps(evidence or [], ensure_ascii=False), json.dumps(deps), c.token(payload),
         editor_id, int(locked), m3._now()))
    return fid


def create_draft(db, character_id, refs, title, editor_id, request_key, *, title_origin='manual'):
    legacy._begin(db); _owner(db, editor_id)
    if title_origin not in ('manual', 'h31_system_suggested'):
        raise ValueError('title_origin_invalid')
    inputs = m3.source_inputs(db, character_id, refs)
    _value('title', title)
    # Preserve the v2 manual idempotency contract; H3.1 suggestions use a
    # separate fingerprint so they cannot be mistaken for owner edits.
    fingerprint = c.token([inputs, title] if title_origin == 'manual'
                          else [inputs, title, title_origin])
    prior = db.execute('SELECT * FROM mv3_metadata_idempotency WHERE character_id=? AND request_key=?',
        (character_id, request_key)).fetchone()
    if prior:
        if prior['input_hash'] != fingerprint:
            raise ValueError('idempotency_conflict')
        return read_draft(db, prior['entry_id'], character_id)
    entry = legacy.create_entry(db, character_id)
    sid = str(uuid.uuid4()); source_token = c.token(inputs)
    db.execute('UPDATE mv3_entries SET contract_version=2,draft_version_id=? WHERE id=?', (sid, entry['id']))
    db.execute('INSERT INTO mv3_entry_field_sets VALUES(?,?,?,?,?,?,?)',
        (sid, entry['id'], 1, json.dumps(inputs), source_token,
         json.dumps(_times(db, inputs, character_id)), m3._now()))
    fid = (_write_field(db, entry['id'], 'title', title, source_token, editor_id)
           if title_origin == 'manual' else
           _write_field(db, entry['id'], 'title', title, source_token, None,
                        provenance='system_suggested', rule='h31-title-suggestion-v1',
                        locked=False))
    db.execute('INSERT INTO mv3_entry_field_links VALUES(?,?,?)', (sid, 'title', fid))
    db.execute('INSERT INTO mv3_metadata_idempotency VALUES(?,?,?,?)',
        (character_id, request_key, fingerprint, entry['id']))
    return read_draft(db, entry['id'], character_id)


def revise(db, entry_id, character_id, expected_version, changes, editor_id,
           *, provenance='manual', rule='manual-v2', dependencies=None, evidence=None):
    legacy._begin(db); _owner(db, editor_id)
    entry = _entry(db, entry_id, character_id); version = _set(db, entry)
    refs = _validate_sources(db, entry, version)
    if version['id'] != expected_version:
        raise ValueError('version_conflict')
    if not changes or set(changes) - set(c.FIELDS):
        raise ValueError('unsupported_field')
    fields = _fields(db, version['id'])
    dependencies = dict(dependencies or {})
    per_field_dependencies = {}
    for name, value in changes.items():
        field_dependencies = dict(dependencies)
        per_field_dependencies[name] = field_dependencies
        if value is not None:
            _value(name, value)
        if provenance not in ('manual', 'user_corrected') and name in fields and fields[name]['locked']:
            raise ValueError('manual_field_locked')
        if name == 'narrative' and value:
            legacy._narrative(db, character_id, value, refs)
        if name in ('categories', 'tags') and value:
            from . import memory_v2_vocabulary as vocabulary
            for item in value:
                if not isinstance(item, dict) or set(item) != {'id', 'evidence'}:
                    raise ValueError('vocabulary_binding_required')
                term = vocabulary.get(db, character_id, item['id'])
                if term['kind'] != ('category' if name == 'categories' else 'tag') or term['status'] != 'active':
                    raise ValueError('vocabulary_unavailable')
                _evidence(db, character_id, refs, item['evidence'])
                field_dependencies[f"vocabulary:{item['id']}:semantic_token"] = term['semantic_token']
                field_dependencies[f"vocabulary:{item['id']}:alias_token"] = term['alias_token']
        if name == 'keywords' and value:
            for item in value:
                if not isinstance(item,dict) or set(item)-{'vocabularyId'} != {'text','language','kind','weight','field','evidence'}:
                    raise ValueError('keyword_binding_required')
                if not isinstance(item['language'],str) or not item['language'] or len(item['language'])>20:
                    raise ValueError('keyword_language_invalid')
                if item['kind'] not in ('literal','semantic_expansion') or item['field'] not in ('raw',*c.FIELDS):
                    raise ValueError('keyword_kind_invalid')
                if not isinstance(item['text'],str) or not item['text'] or len(item['text'])>80:
                    raise ValueError('keyword_invalid')
                if type(item['weight']) not in (int,float) or not 0 <= item['weight'] <= 1:
                    raise ValueError('keyword_weight_invalid')
                _evidence(db, character_id, refs, item['evidence'])
                if item['field']!='raw':
                    used=fields.get(item['field'])
                    if not used: raise ValueError('keyword_input_missing')
                    field_dependencies[item['field']]=used['token']
                if item.get('vocabularyId'):
                    from . import memory_v2_vocabulary as vocabulary
                    term=vocabulary.get(db,character_id,item['vocabularyId'])
                    if term['kind']!='keyword' or term['status']!='active': raise ValueError('keyword_vocabulary_invalid')
                    for aspect in ('semantic_token','alias_token'):
                        field_dependencies[f"vocabulary:{item['vocabularyId']}:{aspect}"]=term[aspect]
    sid = str(uuid.uuid4())
    db.execute('INSERT INTO mv3_entry_field_sets VALUES(?,?,?,?,?,?,?)',
        (sid, entry_id, version['version'] + 1, version['sources_json'], version['source_token'],
         version['time_evidence_json'], m3._now()))
    for name, field in fields.items():
        if name not in changes:
            db.execute('INSERT INTO mv3_entry_field_links VALUES(?,?,?)', (sid, name, field['id']))
    for name, value in changes.items():
        # A manual null is an immutable suppression record, not a missing row.
        fid = _write_field(db, entry_id, name, value, version['source_token'], editor_id,
            provenance=provenance, rule=rule, dependencies=per_field_dependencies[name], evidence=evidence,
            locked=provenance in ('manual', 'user_corrected'))
        db.execute('INSERT INTO mv3_entry_field_links VALUES(?,?,?)', (sid, name, fid))
    db.execute('UPDATE mv3_entries SET draft_version_id=? WHERE id=? AND draft_version_id=?',
        (sid, entry_id, expected_version))
    return read_draft(db, entry_id, character_id)


def _evidence(db, character_id, refs, evidence):
    if not isinstance(evidence,list) or not evidence:
        raise ValueError('direct_evidence_required')
    allowed={(r['source_kind'],r['source_id']) for r in refs}
    for point in evidence:
        if not isinstance(point,dict) or set(point) != {'kind','id','start','end'}:
            raise ValueError('evidence_invalid')
        if (point['kind'],point['id']) not in allowed:
            raise ValueError('evidence_outside_sources')
        source=m3.live_source(db,character_id,point['kind'],point['id'])
        if type(point['start']) is not int or type(point['end']) is not int or not 0<=point['start']<point['end']<=len(source.text):
            raise ValueError('evidence_offset_invalid')


def read_draft(db, entry_id, character_id, *, offset_minutes=480):
    entry = _entry(db, entry_id, character_id); version = _set(db, entry)
    refs = _validate_sources(db, entry, version)
    live_times = _times(db, refs, character_id)
    from .memory_v2_chronicle_dates import corrected
    live_times = corrected(db, entry, version, live_times)
    times = c.time_tokens(live_times, offset_minutes)
    fields = _fields(db, version['id']); statuses = {}; output = {}; visiting = set()
    def valid(name):
        if name in statuses:
            return statuses[name]
        if name in visiting:
            return 'stale'
        visiting.add(name)
        field = fields[name]; deps = json.loads(field['dependencies_json'])
        value = json.loads(field['value_json'])
        expected = c.token([name, value, field['provenance'], field['rule_version'],
                           json.loads(field['evidence_json']), deps])
        state = 'draft'
        if expected != field['token'] or deps.get('source') != version['source_token']:
            state = 'stale'
        # A3.1 rule semantics live in a versioned local rule, independent of
        # the legacy seed's display-name-only vocabulary definition.
        if (name == 'categories' and field['provenance'] == 'rule' and
                field['rule_version'] in ('category-direct-evidence-v1',
                                          'category-direct-evidence-v2') and
                field['editor_id'] is not None and
                any(key.startswith('vocabulary:') for key in deps)):
            from . import memory_v2_category_rules as category_rules
            if field['rule_version'] != category_rules.RULE_VERSION:
                state = 'stale'
        if name == 'tags' and field['provenance'] == 'rule' and field['rule_version'].startswith('tag-direct-topic-'):
            from . import memory_v2_tag_rules as tag_rules
            if field['rule_version'] != tag_rules.RULE_VERSION:
                state = 'stale'
            from . import memory_v2_tags as tag_decisions
            if deps.get('tagMergeGuard')!=tag_decisions.MERGE_GUARD_VERSION:
                state = 'stale'
            elif isinstance(value,list):
                for item in value:
                    try:
                        if tag_decisions.merge_guard(db,character_id,entry_id,item['id']):
                            state = 'stale'
                    except (ValueError,KeyError,TypeError):
                        state = 'stale'
        if (name == 'keywords' and field['provenance'] == 'rule' and
                field['rule_version'].startswith('keyword-literal-raw-') and
                field['editor_id'] is not None):
            from . import memory_v2_keyword_rules as keyword_rules
            if field['rule_version'] != keyword_rules.RULE_VERSION:
                state = 'stale'
        if (name == 'keywords' and field['provenance'] == 'rule' and
                field['rule_version'].startswith('keyword-retrieval-phrases-')):
            from . import memory_v2_keyword_rules as keyword_rules
            if field['rule_version'] != keyword_rules.RETRIEVAL_RULE_VERSION:
                state = 'stale'
        if (name == 'keywords' and field['provenance'] == 'rule' and
                field['rule_version'].startswith('keyword-selection-suggestions-')):
            from . import memory_v2_keyword_rules as keyword_rules
            if field['rule_version'] != keyword_rules.SELECTION_RULE_VERSION:
                state = 'stale'
        if (name == 'synopsis' and field['provenance'] == 'model' and
                field['rule_version'].startswith('synopsis-full-entry-')):
            from . import memory_v2_synopsis as synopsis
            from . import memory_v2_synopsis_final as final
            if (field['rule_version'] not in (synopsis.PROFILE, synopsis.REAL_PROFILE, synopsis.REAL_CHRONICLE_PROFILE, synopsis.REAL_CHRONICLE_REVIEW_PROFILE, synopsis.REAL_SPEAKER_PROFILE, synopsis.REAL_QUOTE_PROFILE, synopsis.REAL_STREAM_PROFILE, synopsis.REAL_STREAM_8192_PROFILE,
                                              synopsis.REAL_E1_PROFILE,
                                              synopsis.REAL_E2_PROFILE,
                                              synopsis.REAL_E2_SENTENCE_PROFILE,
                                              synopsis.REAL_E2_CAPACITY_PROFILE,
                                              final.PROFILE, final.PROFILE_V2,
                                              final.PROFILE_V3) or
                    deps.get('synopsisValidator') != (
                        final.VALIDATOR if
                        field['rule_version'] == final.PROFILE
                        else final.VALIDATOR_V2 if
                        field['rule_version'] == final.PROFILE_V2
                        else final.VALIDATOR_V3 if
                        field['rule_version'] == final.PROFILE_V3
                        else synopsis._batch_validator(field['rule_version']))):
                state = 'stale'
        import_plan = name=='synopsis' and field['rule_version'].startswith('chronicle-import-plan-synopsis-')
        if import_plan or (name=='synopsis' and field['rule_version'] in ('synopsis-full-entry-responses-chronicle-quotes-v4','synopsis-full-entry-responses-chronicle-quotes-v4-stream-v1','synopsis-full-entry-responses-chronicle-quotes-v4-stream-8192-v2')):
            from . import memory_v2_synopsis_quotes as quotes
            from . import memory_v2_synopsis as synopsis
            try:
                evidence=json.loads(field['evidence_json'])[0]
                if import_plan and (field['rule_version']!='chronicle-import-plan-synopsis-v1' or field['provenance']!='model'):
                    raise ValueError('import_profile_changed')
                if field['rule_version']==synopsis.REAL_STREAM_8192_PROFILE and (deps.get('synopsisCapacityVersion')!=synopsis.STREAM_8192_CAPACITY_VERSION or evidence.get('capacityVersion')!=synopsis.STREAM_8192_CAPACITY_VERSION):
                    raise ValueError('capacity_version_changed')
                sources=[]
                for ref in refs:
                    live=m3.live_source(db,character_id,ref['source_kind'],ref['source_id'])
                    if live is None: raise ValueError('source_changed')
                    sources.append(dict(kind=live.kind,id=live.source_id,speaker=live.role,text=live.text,sourceStatus=live.status))
                checked={'text':value,'units':evidence['units'],'sourceCoverage':evidence['sourceCoverage']}
                if not import_plan and deps.get('synopsisQuoteLocator')!=quotes.VERSION:raise ValueError('quote_version_changed')
                quotes.checked_artifact(checked,evidence['quoteLocalization'],sources)
                synopsis.validate_output(checked,sources,qualifier_policy=synopsis.CHRONICLE_REVIEW_POLICY)
                synopsis.validate_speaker_anchors(checked)
            except (ValueError,TypeError,KeyError,IndexError):state='stale'
        for dependency, digest in deps.items():
            if dependency == 'source':
                continue
            if dependency in ('timeEvidenceToken', 'timeToken'):
                if times[dependency] != digest: state = 'stale'
            elif dependency=='normalization':
                if digest!=c.token([name,'nfkc-casefold-v1']): state='stale'
            elif dependency=='tagMergeGuard':
                from . import memory_v2_tags as tag_decisions
                if digest!=tag_decisions.MERGE_GUARD_VERSION: state='stale'
            elif dependency=='synopsisValidator':
                from . import memory_v2_synopsis as synopsis
                from . import memory_v2_synopsis_final as final
                expected = (final.VALIDATOR if
                            field['rule_version'] == final.PROFILE
                            else final.VALIDATOR_V2 if
                            field['rule_version'] == final.PROFILE_V2
                            else final.VALIDATOR_V3 if
                            field['rule_version'] == final.PROFILE_V3
                            else synopsis._batch_validator(field['rule_version']))
                if digest!=expected: state='stale'
            elif dependency=='synopsisCapacityVersion':
                from . import memory_v2_synopsis as synopsis
                if field['rule_version']!=synopsis.REAL_STREAM_8192_PROFILE or digest!=synopsis.STREAM_8192_CAPACITY_VERSION: state='stale'
            elif dependency=='synopsisQuoteLocator':
                from . import memory_v2_synopsis_quotes as quotes
                if digest!=quotes.VERSION: state='stale'
            elif dependency=='synopsisBoundary':
                from . import memory_v2_synopsis as synopsis
                if digest!=synopsis.boundary_binding(db,entry_id,character_id): state='stale'
            elif dependency=='synopsisFinalBatches':
                from . import memory_v2_synopsis_final as final
                if not final.batch_binding_current(db, character_id, digest): state='stale'
            elif dependency.startswith('vocabulary:'):
                from . import memory_v2_vocabulary as vocabulary
                try:
                    _,vid,aspect=dependency.split(':')
                    if aspect not in ('semantic_token','alias_token') or vocabulary.get(db,character_id,vid)[aspect]!=digest:
                        state='stale'
                except ValueError: state='stale'
            elif dependency not in fields or fields[dependency]['token'] != digest or valid(dependency) != 'available':
                state = 'stale'
        if name == 'narrative' and value:
            try: legacy._narrative(db, character_id, value, refs)
            except ValueError: state = 'stale'
        check = db.execute('SELECT * FROM mv3_entry_field_checks WHERE field_id=?', (field['id'],)).fetchone()
        if state != 'stale' and check and check['binding'] == field['token']:
            state = 'available'
        if value is None: state = 'disabled'
        visiting.remove(name); statuses[name] = state
        return state
    for name in c.FIELDS:
        if name == 'time': continue
        if name not in fields:
            output[name] = {'value': None, 'completionStatus': 'missing', 'lifecycleStatus': 'draft',
                            'validationStatus': 'pending', 'reviewStatus': 'not_applicable'}
            continue
        state = valid(name); field = fields[name]
        output[name] = {'value': None if state in ('stale', 'disabled') else json.loads(field['value_json']),
            'fieldId': field['id'], 'token': field['token'], 'lifecycleStatus': state,
            'completionStatus': 'complete' if state == 'available' else 'candidate',
            'validationStatus': 'failed' if state == 'stale' else 'passed' if state == 'available' else 'pending',
            'reviewStatus': ('human_checked' if db.execute("SELECT 1 FROM mv3_entry_field_checks WHERE field_id=? AND method='owner'",(field['id'],)).fetchone()
                             else 'not_applicable') if state == 'available' else 'needs_review',
            'locked': bool(field['locked'])}
    return {'entryId': entry_id, 'versionId': version['id'], 'contractVersion': 2,
        'sourceToken': version['source_token'], 'sourceRefs': refs, 'fields': output,
        **times, 'timeEvidence': live_times, 'authority': 'system_derived_text',
        'qualifications': c.qualifications(source_valid=True, field_statuses=statuses,
            entry_published=entry['status'] == 'active' and entry['active_version_id']==version['id'])}


def review_field(db, entry_id, character_id, name, expected_token, editor_id):
    legacy._begin(db); _owner(db, editor_id)
    detail = read_draft(db, entry_id, character_id)
    field = detail['fields'].get(name)
    if not field or field.get('token') != expected_token or field['lifecycleStatus'] in ('stale', 'disabled'):
        raise ValueError('review_snapshot_changed')
    db.execute('INSERT OR REPLACE INTO mv3_entry_field_checks VALUES(?,?,?,?,?)',
        (field['fieldId'], expected_token, 'owner', editor_id, m3._now()))
    return read_draft(db, entry_id, character_id)


def verify_rule(db, entry_id, character_id, name):
    """Recompute supported transformations; provenance strings alone confer no authority."""
    legacy._begin(db)
    detail=read_draft(db,entry_id,character_id)
    field=detail['fields'].get(name)
    if not field or field.get('lifecycleStatus')!='draft' or not field.get('fieldId'):
        raise ValueError('field_not_verifiable')
    row=db.execute('SELECT * FROM mv3_entry_fields WHERE id=?',(field['fieldId'],)).fetchone()
    if row['provenance']!='rule' or row['rule_version']!='metadata-extractive-v1':
        raise ValueError('rule_not_supported')
    sources=[m3.live_source(db,character_id,r['source_kind'],r['source_id']) for r in detail['sourceRefs']]
    value=field['value']
    if name=='synopsis':
        expected='\n'.join(('她说：' if s.role=='user' else '我说：')+s.text for s in sources)
        if expected!=value: raise ValueError('rule_output_mismatch')
    elif name=='keywords':
        for item in value:
            if item['kind']!='literal' or item['field']!='raw': raise ValueError('semantic_keyword_needs_review')
            for point in item['evidence']:
                source=m3.live_source(db,character_id,point['kind'],point['id'])
                if source.text[point['start']:point['end']]!=item['text']:
                    raise ValueError('rule_output_mismatch')
    elif name=='tags':
        from . import memory_v2_vocabulary as vocabulary
        for item in value:
            term=vocabulary.get(db,character_id,item['id'])
            spellings={term['name'],*json.loads(term['aliases_json'])}
            for point in item['evidence']:
                source=m3.live_source(db,character_id,point['kind'],point['id'])
                if source.text[point['start']:point['end']] not in spellings:
                    raise ValueError('rule_output_mismatch')
    else: raise ValueError('rule_not_supported')
    db.execute('INSERT OR REPLACE INTO mv3_entry_field_checks VALUES(?,?,?,?,?)',
        (field['fieldId'],field['token'],'verified_rule',None,m3._now()))
    return read_draft(db,entry_id,character_id)


def publish(db, entry_id, character_id, expected_version, editor_id):
    legacy._begin(db); _owner(db,editor_id)
    detail=read_draft(db,entry_id,character_id)
    if detail['versionId']!=expected_version: raise ValueError('version_conflict')
    fields=detail['fields']
    if fields['title']['lifecycleStatus']!='available':
        raise ValueError('required_fields_not_published')
    db.execute("UPDATE mv3_entries SET status='active',active_version_id=? WHERE id=?",(expected_version,entry_id))
    return read_draft(db,entry_id,character_id)


def read_active(db, entry_id, character_id):
    entry=_entry(db,entry_id,character_id)
    if entry['status']!='active' or entry['active_version_id']!=entry['draft_version_id']:
        raise ValueError('entry_not_current')
    detail=read_draft(db,entry_id,character_id)
    fields=detail['fields']
    def value(name,default=None):
        return fields[name]['value'] if fields[name]['lifecycleStatus']=='available' else default
    if not value('title'):
        raise ValueError('published_fields_stale')
    dates=[r['occurredAt'] if r['precision']=='instant' else r['occurredDate'] if r['precision']=='day' else None
           for r in detail['timeEvidence']]
    basis='occurred' if dates and all(dates) else 'unknown'
    dates=sorted(dates) if basis=='occurred' else []
    recorded=sorted(s.recorded_at for r in detail['sourceRefs']
        if (s:=m3.live_source(db,character_id,r['source_kind'],r['source_id'])).recorded_at)
    return {'entryId':entry_id,'versionId':detail['versionId'],'title':value('title'),
        'synopsis':value('synopsis'),'narrativeVersionId':value('narrative'),
        'keywords':[x['text'] for x in value('keywords',[])], 'tags':[],
        'categories':value('categories',[]),'tagRefs':value('tags',[]),
        'timeBasis':basis,'occurredAt':[dates[0],dates[-1]] if dates else [None,None],
        'recordedAt':[recorded[0],recorded[-1]] if recorded else [None,None],
        'sourceRefs':[{'kind':r['source_kind'],'id':r['source_id']} for r in detail['sourceRefs']],
        'qualifications':detail['qualifications'],'authority':'system_derived_text'}
