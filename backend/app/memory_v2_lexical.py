"""Isolated A3.3 Lexical search over rebuildable, source-checked metadata.

No route installs this module. The owner supplies a stable signing secret; this
module never reads a key file or a provider credential.
"""
import base64
import hashlib
import hmac
import json
import math
import time
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from . import memory_v2_keyword_rules as literal
from . import memory_v2_lexical_schema as schema
from . import memory_v2_metadata as metadata
from . import memory_v2_metadata_contract as contract

CONFIG_VERSION = 'metadata-lexical-v1'
TOKENIZER_VERSION = 'lexical-script-runs-v1'
CURSOR_VERSION = 'a33-lexical-cursor-v1'
FIELD_WEIGHTS = {'title': 4, 'tags': 3, 'keywords': 2, 'synopsis': 1}
MATCH_COEFFICIENTS = {'direct': 1000, 'alias': 750, 'expansion': 500}
MAX_ENTRIES = 10000
MAX_TERMS = 100000
MAX_AUTHORITY_BYTES = 40 * 1024 * 1024
MAX_VOCAB_ROWS = 10000
MAX_VOCAB_BYTES = 8 * 1024 * 1024
MAX_DECISION_ROWS = 50000
MAX_QUERY_ITEMS = 12
MAX_OPERATION_SECONDS = 90


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _budget(deadline):
    if time.monotonic() >= deadline:
        raise ValueError('lexical_resource_limit')


def _authorize(db, character_id, owner_id):
    if type(character_id) is not int or type(owner_id) is not int or not db.execute('''
        SELECT 1 FROM users u JOIN characters c ON c.id=? AND c.enabled=1
        WHERE u.id=?''', (character_id, owner_id)).fetchone():
        raise ValueError('role_unavailable')


def _weight(value):
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise ValueError('keyword_weight_invalid')
    try:
        weight = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('keyword_weight_invalid') from None
    if not weight.is_finite() or not Decimal(0) <= weight <= Decimal(1):
        raise ValueError('keyword_weight_invalid')
    return int((weight * 1000).quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))


def _vocabulary(db, character_id):
    vocabulary_bytes = db.execute('''SELECT COALESCE(SUM(
        length(CAST(v.name AS BLOB))+length(CAST(v.definition AS BLOB))+
        length(CAST(v.aliases_json AS BLOB))),0)
        FROM mv3_vocabulary r JOIN mv3_vocabulary_versions v
          ON v.vocabulary_id=r.id AND v.version=r.current_version
        WHERE r.character_id=?''', (character_id,)).fetchone()[0]
    if vocabulary_bytes > MAX_VOCAB_BYTES:
        raise ValueError('lexical_resource_limit')
    rows = [dict(row) for row in db.execute('''SELECT r.id,r.kind,r.character_id,v.version,
        v.dimension,v.language,v.name,v.definition,v.aliases_json,v.status,v.merged_into,
        v.semantic_token,v.alias_token,v.display_token,v.normalization_version
        FROM mv3_vocabulary r JOIN mv3_vocabulary_versions v
          ON v.vocabulary_id=r.id AND v.version=r.current_version
        WHERE r.character_id=? ORDER BY r.id LIMIT ?''',
        (character_id, MAX_VOCAB_ROWS + 1))]
    if len(rows) > MAX_VOCAB_ROWS:
        raise ValueError('lexical_resource_limit')
    seen = {}
    for row in rows:
        try:
            aliases = json.loads(row['aliases_json'])
        except (TypeError, ValueError):
            raise ValueError('lexical_vocabulary_invalid') from None
        if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
            raise ValueError('lexical_vocabulary_invalid')
        row['aliases'] = aliases
        if row['status'] != 'active':
            continue
        for spelling in (row['name'], *aliases):
            # Query language is not a selector in v1: an ambiguous spelling
            # across language labels cannot silently pick a vocabulary ID.
            key = (row['kind'], contract.normalized(spelling))
            if not key[-1] or (key in seen and seen[key] != row['id']):
                raise ValueError('lexical_alias_conflict')
            seen[key] = row['id']
    return {row['id']: row for row in rows}


def _authority(db, character_id, deadline=None):
    """Full role snapshot, including entries with no query hit or eligible field."""
    rows = db.execute('''SELECT id,status,active_version_id,draft_version_id,contract_version
        FROM mv3_entries WHERE character_id=? ORDER BY id LIMIT ?''',
        (character_id, MAX_ENTRIES + 1)).fetchall()
    if len(rows) > MAX_ENTRIES:
        raise ValueError('lexical_resource_limit')
    vocab = _vocabulary(db, character_id)
    decisions = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_entry_tag_decisions'").fetchone():
        decisions = [tuple(row) for row in db.execute('''SELECT entry_id,tag_id,revision,action,source_token,basis_field_id
            FROM mv3_entry_tag_decisions WHERE character_id=?
            ORDER BY entry_id,tag_id,revision LIMIT ?''',
            (character_id, MAX_DECISION_ROWS + 1))]
        if len(decisions) > MAX_DECISION_ROWS:
            raise ValueError('lexical_resource_limit')
    details = []
    fingerprint_rows = []
    fingerprint_bytes = 0
    for row in rows:
        if deadline is not None:
            _budget(deadline)
        head = tuple(row)
        if row['contract_version'] != 2 or row['status'] in ('archived', 'superseded'):
            fingerprint = [head, None]
            fingerprint_bytes += len(_json(fingerprint).encode('utf-8'))
            if fingerprint_bytes > MAX_AUTHORITY_BYTES:
                raise ValueError('lexical_resource_limit')
            fingerprint_rows.append(fingerprint)
            continue
        try:
            detail = metadata.read_draft(db, row['id'], character_id)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise ValueError('lexical_authority_invalid') from None
        field_checks = []
        for field in detail['fields'].values():
            if field.get('fieldId'):
                check = db.execute('SELECT binding,method,reviewer_id FROM mv3_entry_field_checks WHERE field_id=?',
                                   (field['fieldId'],)).fetchone()
                field_checks.append(tuple(check) if check else None)
        fingerprint = [head, detail['sourceToken'], detail['sourceRefs'],
                       detail['timeEvidenceToken'], detail['fields'], field_checks]
        fingerprint_bytes += len(_json(fingerprint).encode('utf-8'))
        if fingerprint_bytes > MAX_AUTHORITY_BYTES:
            raise ValueError('lexical_resource_limit')
        fingerprint_rows.append(fingerprint)
        details.append((dict(row), detail))
    vocabulary_rows = [[key, {k: v for k, v in value.items() if k != 'aliases'}, value['aliases']]
                       for key, value in sorted(vocab.items())]
    payload = ['a33-authority-v1', character_id, fingerprint_rows, vocabulary_rows, decisions]
    encoded = _json(payload).encode('utf-8')
    if len(encoded) > MAX_AUTHORITY_BYTES:
        raise ValueError('lexical_resource_limit')
    return hashlib.sha256(encoded).hexdigest(), details, vocab


def _field_values(detail, vocab, *, include_drafts=False):
    """One value remains one phrase boundary; never concatenate list members."""
    output = []
    for name in FIELD_WEIGHTS:
        field = detail['fields'][name]
        if field['lifecycleStatus'] != 'available' and not (
                include_drafts and field['lifecycleStatus'] == 'draft'):
            continue
        value = field['value']
        if value is None:
            continue
        if name in ('title', 'synopsis'):
            output.append({'field': name, 'fieldId': field['fieldId'], 'token': field['token'],
                           'ordinal': 0, 'text': value, 'aliases': (), 'kind': 'direct',
                           'weight': 1000, 'vocabularyId': '', 'evidence': []})
        elif name == 'tags':
            for ordinal, item in enumerate(value):
                term = vocab.get(item['id'])
                if not term or term['kind'] != 'tag' or term['status'] != 'active':
                    raise ValueError('lexical_vocabulary_invalid')
                output.append({'field': name, 'fieldId': field['fieldId'], 'token': field['token'],
                               'ordinal': ordinal, 'text': term['name'], 'aliases': tuple(term['aliases']),
                               'kind': 'direct', 'weight': 1000, 'vocabularyId': item['id'],
                               'evidence': item['evidence']})
        elif name == 'keywords':
            for ordinal, item in enumerate(value):
                weight = _weight(item['weight'])
                # The same effective view feeds the projection, matching and
                # candidate qualification. A stored but unsearchable Keyword
                # cannot be the auxiliary field that grants recall eligibility.
                if not weight or not contract.normalized(item['text']):
                    continue
                aliases = ()
                vocab_id = item.get('vocabularyId') or ''
                if vocab_id:
                    term = vocab.get(vocab_id)
                    if (not term or term['kind'] != 'keyword' or term['status'] != 'active' or
                            term['language'] != item['language'] or
                            contract.normalized(term['name']) != contract.normalized(item['text'])):
                        raise ValueError('lexical_vocabulary_invalid')
                    aliases = tuple(term['aliases'])
                kind = 'expansion' if item['kind'] == 'semantic_expansion' else 'direct'
                if kind == 'expansion' and field['reviewStatus'] != 'human_checked':
                    continue
                output.append({'field': name, 'fieldId': field['fieldId'], 'token': field['token'],
                               'ordinal': ordinal, 'text': item['text'], 'aliases': aliases,
                               'kind': kind, 'weight': weight, 'vocabularyId': vocab_id,
                               'evidence': item['evidence']})
    return output


def _tokens(value):
    return {item[3] for item in literal.spans(value)}


def _projection(details, vocab, deadline=None):
    projection = []
    seen = set()
    for _, detail in details:
        if deadline is not None:
            _budget(deadline)
        for value in _field_values(detail, vocab):
            direct = _tokens(value['text']) if value['field'] in ('title', 'synopsis') else {
                contract.normalized(value['text'])}
            terms = [(term, value['kind']) for term in sorted(direct)] + [
                (contract.normalized(alias), 'alias') for alias in value['aliases']]
            for term, kind in terms:
                key = (detail['entryId'], value['fieldId'], value['ordinal'], term, kind, value['vocabularyId'])
                if term and key not in seen:
                    seen.add(key)
                    projection.append((detail['entryId'], value, term, kind))
            if len(projection) > MAX_TERMS:
                raise ValueError('lexical_resource_limit')
    return projection


def _projection_rows(projection):
    return sorted((entry_id, value['field'], value['fieldId'], value['token'],
                   value['ordinal'], term, kind, value['vocabularyId'], _hash(value['evidence']))
                  for entry_id, value, term, kind in projection)


def rebuild(connect, character_id, owner_id, *, expected_authority=None, expected_projection=None, expected_state=None, reuse_if_current=False, publish_guard=None):
    """Read a consistent snapshot, then compare it inside a short publish txn."""
    deadline = time.monotonic() + MAX_OPERATION_SECONDS
    with connect() as db:
        db.execute('BEGIN')
        _authorize(db, character_id, owner_id)
        schema.require(db)
        authority, details, vocab = _authority(db, character_id, deadline)
        projection = _projection_rows(_projection(details, vocab, deadline))
        _budget(deadline)
        projection_hash = _hash(projection)
        if ((expected_authority is not None and authority != expected_authority) or
                (expected_projection is not None and projection_hash != expected_projection)):
            raise ValueError('maintenance_plan_stale')
        db.commit()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _authorize(db, character_id, owner_id)
        schema.require(db)
        if publish_guard is not None:
            publish_guard(db, character_id)
        current, _, _ = _authority(db, character_id, deadline)
        if current != authority:
            raise ValueError('lexical_rebuild_snapshot_changed')
        prior = db.execute('''SELECT generation,revision,authority_hash,projection_hash,config_version
            FROM mv3_lexical_state WHERE character_id=?''', (character_id,)).fetchone()
        prior_state = list(prior) if prior else None
        if reuse_if_current and prior and prior['authority_hash'] == authority and (
                prior['projection_hash'] == projection_hash and prior['config_version'] == CONFIG_VERSION):
            actual = [tuple(row) for row in db.execute('''SELECT entry_id,field_name,field_id,
                field_token,value_ordinal,normalized_term,match_kind,vocabulary_id,evidence_hash
                FROM mv3_lexical_terms WHERE character_id=? AND generation=?
                ORDER BY entry_id,field_name,field_id,value_ordinal,normalized_term,match_kind,vocabulary_id
                LIMIT ?''', (character_id, prior['generation'], MAX_TERMS + 1))]
            if len(actual) <= MAX_TERMS and _hash(actual) == projection_hash:
                return {'generation': prior['generation'], 'revision': prior['revision'],
                        'terms': len(projection), 'authorityHash': authority, 'reused': True}
        if expected_state is not None or expected_authority is not None:
            if prior_state != expected_state:
                raise ValueError('maintenance_plan_stale')
        generation = prior['generation'] + 1 if prior else 1
        revision = prior['revision'] + 1 if prior else 1
        db.executemany('''INSERT INTO mv3_lexical_terms(character_id,generation,entry_id,field_name,
            field_id,field_token,value_ordinal,normalized_term,match_kind,vocabulary_id,evidence_hash)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', [(character_id, generation, *row) for row in projection])
        _budget(deadline)
        db.execute('''INSERT INTO mv3_lexical_state VALUES(?,?,?,?,?,?)
            ON CONFLICT(character_id) DO UPDATE SET generation=excluded.generation,
            revision=excluded.revision,authority_hash=excluded.authority_hash,
            projection_hash=excluded.projection_hash,config_version=excluded.config_version''',
            (character_id, generation, revision, authority, projection_hash, CONFIG_VERSION))
        db.execute('DELETE FROM mv3_lexical_terms WHERE character_id=? AND generation<>?',
                   (character_id, generation))
        return {'generation': generation, 'revision': revision, 'terms': len(projection),
                'authorityHash': authority}


def _query(query_text, phrases, query_mode, purpose, draft_states):
    if purpose not in ('management', 'draft_management', 'candidate'):
        raise ValueError('lexical_purpose_invalid')
    if query_mode not in ('ANY', 'ALL') or not isinstance(query_text, str) or len(query_text) > 240:
        raise ValueError('lexical_query_invalid')
    if not isinstance(phrases, (tuple, list)) or len(phrases) > MAX_QUERY_ITEMS:
        raise ValueError('lexical_query_invalid')
    parsed = {}
    for _, _, _, normalized, _ in literal.spans(query_text):
        parsed[normalized] = False
    for phrase in phrases:
        if not isinstance(phrase, str) or not 2 <= len(phrase) <= 120:
            raise ValueError('lexical_query_invalid')
        normalized = contract.normalized(phrase)
        if not normalized:
            raise ValueError('lexical_query_invalid')
        parsed[normalized] = True
    if len(parsed) > MAX_QUERY_ITEMS:
        raise ValueError('lexical_resource_limit')
    if (not parsed and not (purpose == 'draft_management' and draft_states)):
        raise ValueError('lexical_query_empty')
    return tuple(sorted(parsed.items()))


def _filters(vocab, categories, tag_ids, tag_mode):
    if tag_mode not in ('ANY', 'ALL') or not isinstance(categories, dict) or not isinstance(tag_ids, (tuple, list)):
        raise ValueError('lexical_filter_invalid')
    cleaned_categories = {}
    for dimension, ids in categories.items():
        if dimension not in contract.CATEGORIES or not isinstance(ids, (tuple, list)) or not ids:
            raise ValueError('lexical_filter_invalid')
        if any(type(item) is not str for item in ids):
            raise ValueError('lexical_filter_invalid')
        unique = tuple(sorted(set(ids)))
        if len(unique) != len(ids) or any(not (
                (term := vocab.get(item)) and term['kind'] == 'category' and
                term['dimension'] == dimension and term['status'] == 'active') for item in unique):
            raise ValueError('lexical_filter_invalid')
        cleaned_categories[dimension] = unique
    if any(type(item) is not str for item in tag_ids):
        raise ValueError('lexical_filter_invalid')
    unique_tags = tuple(sorted(set(tag_ids)))
    if len(unique_tags) != len(tag_ids) or any(not (
            (term := vocab.get(item)) and term['kind'] == 'tag' and term['status'] == 'active')
            for item in unique_tags):
        raise ValueError('lexical_filter_invalid')
    return cleaned_categories, unique_tags


def _filter_detail(detail, categories, tag_ids, tag_mode, *, include_drafts):
    allowed = ('available', 'draft') if include_drafts else ('available',)
    category = detail['fields']['categories']
    category_ids = {item['id'] for item in category['value'] or []} if category['lifecycleStatus'] in allowed else set()
    for ids in categories.values():
        if not category_ids.intersection(ids):
            return False
    tags = detail['fields']['tags']
    present = {item['id'] for item in tags['value'] or []} if tags['lifecycleStatus'] in allowed else set()
    return not tag_ids or (bool(present.intersection(tag_ids)) if tag_mode == 'ANY'
                           else set(tag_ids).issubset(present))


def _match(value, normalized, phrase):
    def matched_range(text, *, script_run=False):
        if script_run:
            for start, end, _, term, _ in literal.spans(text):
                if term == normalized:
                    return [start, end]
            return None
        output, offsets = [], []
        for start, end, cluster in literal._clusters(text):
            for char in unicodedata.normalize('NFKC', cluster).casefold():
                output.append(char)
                offsets.append((start, end))
        joined = ''.join(output)
        left = len(joined) - len(joined.lstrip())
        right = len(joined.rstrip())
        mapped = joined[left:right]
        offsets = offsets[left:right]
        if mapped != contract.normalized(text):
            raise ValueError('lexical_normalization_mismatch')
        position = mapped.find(normalized) if phrase else (0 if mapped == normalized else -1)
        return [offsets[position][0], offsets[position + len(normalized) - 1][1]] if position >= 0 else None

    direct_range = matched_range(value['text'], script_run=value['field'] in ('title', 'synopsis')
                                 and not phrase)
    if direct_range is not None:
        return value['kind'], direct_range, ('vocabulary_name' if value['field'] == 'tags'
                                             else 'field_value')
    if value['kind'] != 'direct':
        return None
    for alias in value['aliases']:
        alias_range = matched_range(alias)
        if alias_range is not None:
            return 'alias', alias_range, 'confirmed_vocabulary_alias'
    return None


def _score(values, query_items):
    field_matches = {}
    global_best = {}
    for normalized, phrase in query_items:
        for value in values:
            match = _match(value, normalized, phrase)
            if not match:
                continue
            kind, matched_range, matched_source = match
            key = (normalized, value['field'])
            rank = MATCH_COEFFICIENTS[kind]
            previous = field_matches.get(key)
            contribution = FIELD_WEIGHTS[value['field']] * rank * value['weight'] // 1000
            if previous and (MATCH_COEFFICIENTS[previous['type']], previous['contribution']) >= (rank, contribution):
                continue
            field_matches[key] = {'field': value['field'], 'type': kind,
                                  'fieldToken': value['token'], 'fieldId': value['fieldId'],
                                  'valueOrdinal': value['ordinal'], 'vocabularyId': value['vocabularyId'],
                                  'evidence': value['evidence'], 'contribution': contribution,
                                  'matchRange': matched_range, 'matchSource': matched_source,
                                  'queryFingerprint': hashlib.sha256(normalized.encode()).hexdigest()}
            current = global_best.get(normalized)
            if current is None or rank > MATCH_COEFFICIENTS[current]:
                global_best[normalized] = kind
    matched = len(global_best)
    counts = tuple(sum(kind == expected for kind in global_best.values())
                   for expected in ('direct', 'alias', 'expansion'))
    score = sum(item['contribution'] for item in field_matches.values())
    return matched, counts, score, sorted(field_matches.values(), key=lambda item: (item['field'], item['valueOrdinal'], item['queryFingerprint']))


def _signing_key(secret):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError('lexical_signing_key_required')
    return hmac.new(secret, CURSOR_VERSION.encode(), hashlib.sha256).digest()


def _encode_cursor(payload, key):
    data = _json(payload).encode('utf-8')
    signature = hmac.new(key, data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(data + signature).rstrip(b'=').decode('ascii')


def _decode_cursor(cursor, key):
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
        raise ValueError('lexical_cursor_invalid')
    try:
        raw = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4))
        data, signature = raw[:-32], raw[-32:]
        if not data or not hmac.compare_digest(hmac.new(key, data, hashlib.sha256).digest(), signature):
            raise ValueError()
        payload = json.loads(data)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('lexical_cursor_invalid') from None
    if not isinstance(payload, dict) or payload.get('version') != CURSOR_VERSION:
        raise ValueError('lexical_cursor_invalid')
    return payload


def search(connect, character_id, owner_id, *, purpose, query_text='', phrases=(), query_mode='ALL',
           categories=None, tag_ids=(), tag_mode='ALL', draft_states=None, limit=20,
           cursor=None, signing_secret=None):
    """One consistent read transaction per page, with full role validation."""
    deadline = time.monotonic() + MAX_OPERATION_SECONDS
    with connect() as db:
        db.execute('BEGIN')
        _authorize(db, character_id, owner_id)
        schema.require(db)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('lexical_page_invalid')
        if draft_states is not None and (purpose != 'draft_management' or not isinstance(draft_states, dict) or
                any(name not in contract.FIELDS or state not in
                    ('missing', 'draft', 'available', 'stale', 'disabled') for name, state in draft_states.items())):
            raise ValueError('lexical_filter_invalid')
        query_items = _query(query_text, phrases, query_mode, purpose, draft_states)
        authority, details, vocab = _authority(db, character_id, deadline)
        category_filter, tag_filter = _filters(vocab, categories or {}, tag_ids, tag_mode)
        state = db.execute('SELECT * FROM mv3_lexical_state WHERE character_id=?', (character_id,)).fetchone()
        if not state:
            raise ValueError('lexical_index_missing')
        if state['config_version'] != CONFIG_VERSION or state['authority_hash'] != authority:
            raise ValueError('lexical_snapshot_changed')
        projected = _projection_rows(_projection(details, vocab, deadline))
        _budget(deadline)
        if _hash(projected) != state['projection_hash']:
            raise ValueError('lexical_index_invalid')
        actual_count = db.execute('''SELECT COUNT(*) FROM mv3_lexical_terms
            WHERE character_id=? AND generation=?''',
            (character_id, state['generation'])).fetchone()[0]
        _budget(deadline)
        if actual_count > MAX_TERMS:
            raise ValueError('lexical_resource_limit')
        actual = [tuple(row) for row in db.execute('''SELECT entry_id,field_name,field_id,
            field_token,value_ordinal,normalized_term,match_kind,vocabulary_id,evidence_hash
            FROM mv3_lexical_terms WHERE character_id=? AND generation=?
            ORDER BY entry_id,field_name,field_id,value_ordinal,normalized_term,match_kind,vocabulary_id
            LIMIT ?''',
            (character_id, state['generation'], MAX_TERMS + 1))]
        if len(actual) > MAX_TERMS:
            raise ValueError('lexical_resource_limit')
        _budget(deadline)
        if _hash(actual) != state['projection_hash']:
            raise ValueError('lexical_index_invalid')
        key = _signing_key(signing_secret)
        query_hash = _hash(['query-v1', TOKENIZER_VERSION, query_items, query_mode])
        filter_hash = _hash(['filter-v1', category_filter, tag_filter, tag_mode, draft_states])
        prior = _decode_cursor(cursor, key) if cursor is not None else None
        if prior and any((prior.get(label) != expected for label, expected in (
                ('characterId', character_id), ('purpose', purpose), ('queryHash', query_hash),
                ('filterHash', filter_hash), ('config', CONFIG_VERSION)))):
            raise ValueError('lexical_cursor_invalid')
        if prior and any((prior.get(label) != expected for label, expected in (
                ('generation', state['generation']), ('revision', state['revision']),
                ('authorityHash', authority)))):
            raise ValueError('lexical_snapshot_changed')
        if query_items and not any(phrase for _, phrase in query_items) and purpose != 'draft_management':
            terms = tuple(sorted({term for term, _ in query_items}))
            placeholders = ','.join('?' for _ in terms)
            allowed_ids = {row[0] for row in db.execute('''SELECT DISTINCT entry_id FROM mv3_lexical_terms
                WHERE character_id=? AND generation=? AND normalized_term IN (''' + placeholders + ')',
                (character_id, state['generation'], *terms))}
        else:
            allowed_ids = None
        hits = []
        for entry, detail in details:
            _budget(deadline)
            if allowed_ids is not None and entry['id'] not in allowed_ids:
                continue
            if purpose == 'candidate' and not (entry['status'] == 'active' and
                    entry['active_version_id'] == detail['versionId']):
                continue
            if draft_states and any(detail['fields'][name]['completionStatus'] != state and
                    detail['fields'][name]['lifecycleStatus'] != state for name, state in draft_states.items()):
                continue
            if not _filter_detail(detail, category_filter, tag_filter, tag_mode,
                                  include_drafts=purpose == 'draft_management'):
                continue
            values = _field_values(detail, vocab, include_drafts=purpose == 'draft_management')
            matched, counts, points, explanation = _score(values, query_items)
            if query_items and (matched == 0 if query_mode == 'ANY' else matched != len(query_items)):
                continue
            field_statuses = {name: field['lifecycleStatus']
                              for name, field in detail['fields'].items()}
            if not any(value['field'] == 'keywords' for value in values):
                field_statuses['keywords'] = 'missing'
            if purpose == 'candidate' and not contract.a3_qualifications(
                    source_valid=True, field_statuses=field_statuses,
                    entry_published=True, lexical_match=bool(matched))['lexicalCandidateRecall']:
                continue
            if purpose == 'management' and not any(field['lifecycleStatus'] == 'available'
                    for field in detail['fields'].values()):
                continue
            key_sort = (-counts[0], -counts[1], -counts[2], -points, entry['id'])
            hits.append((key_sort, {'entryId': entry['id'], 'scoreMilli': points,
                'matchCounts': {'direct': counts[0], 'alias': counts[1], 'expansion': counts[2]},
                'matches': explanation, 'fieldStates': {name: field['lifecycleStatus']
                    for name, field in detail['fields'].items()} if purpose == 'draft_management' else None,
                'purpose': purpose, 'formalContext': False, 'managementOnly': purpose != 'candidate'}))
        hits.sort(key=lambda item: item[0])
        _budget(deadline)
        if prior:
            last = prior.get('last')
            if not (isinstance(last, list) and len(last) == 5 and
                    all(type(value) is int for value in last[:4]) and isinstance(last[4], str)):
                raise ValueError('lexical_cursor_invalid')
            hits = [item for item in hits if item[0] > tuple(last)]
        total = len(hits) if prior is None else None
        page = hits[:limit]
        next_cursor = None
        if len(hits) > limit:
            next_cursor = _encode_cursor({'version': CURSOR_VERSION, 'characterId': character_id,
                'purpose': purpose, 'queryHash': query_hash, 'filterHash': filter_hash,
                'config': CONFIG_VERSION, 'generation': state['generation'],
                'revision': state['revision'], 'authorityHash': authority,
                'last': list(page[-1][0])}, key)
        _budget(deadline)
        return {'items': [item[1] for item in page], 'nextCursor': next_cursor,
                'total': total, 'generation': state['generation'], 'revision': state['revision'],
                'configVersion': CONFIG_VERSION, 'formalContext': False}
