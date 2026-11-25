"""A3.4 bounded, synthetic full-entry Synopsis protocol. No provider client."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from . import memory_v2_derivation as m3
from . import memory_v2_metadata as metadata
from . import memory_v2_metadata_contract as contract
from . import memory_v2_synopsis_qualifiers as qualifiers
from . import memory_v2_synopsis_punctuation as punctuation
from . import memory_v2_synopsis_quotes as quotes
from . import memory_v2_responses_stream as streams

PROFILE = 'synopsis-full-entry-mock-v1'
REAL_PROFILE = 'synopsis-full-entry-responses-v1'
REAL_CHRONICLE_PROFILE = 'synopsis-full-entry-responses-chronicle-v1'
REAL_CHRONICLE_REVIEW_PROFILE = 'synopsis-full-entry-responses-chronicle-review-v2'
CHRONICLE_REVIEW_POLICY = 'synopsis-lexical-warnings-v1'
CHRONICLE_REVIEW_VALIDATOR = 'synopsis-structure-review-v2'
REAL_SPEAKER_PROFILE = 'synopsis-full-entry-responses-chronicle-speakers-v3'
SPEAKER_VALIDATOR = 'synopsis-explicit-speakers-v3'
REAL_QUOTE_PROFILE = 'synopsis-full-entry-responses-chronicle-quotes-v4'
QUOTE_VALIDATOR = 'synopsis-explicit-speakers-unique-quotes-v4'
REAL_STREAM_PROFILE = 'synopsis-full-entry-responses-chronicle-quotes-v4-stream-v1'
REAL_STREAM_8192_PROFILE = 'synopsis-full-entry-responses-chronicle-quotes-v4-stream-8192-v2'
STREAM_8192_CAPACITY_VERSION = 'synopsis-stream-output-8192-v1'
_STREAM_PROFILES = (REAL_STREAM_PROFILE, REAL_STREAM_8192_PROFILE)
_QUOTE_PROFILES = (REAL_QUOTE_PROFILE,*_STREAM_PROFILES)
_SPEAKER_PROFILES = (REAL_SPEAKER_PROFILE, *_QUOTE_PROFILES)
_CHRONICLE_PROFILES = (REAL_CHRONICLE_PROFILE, REAL_CHRONICLE_REVIEW_PROFILE, *_SPEAKER_PROFILES)
REAL_E1_PROFILE = 'synopsis-full-entry-responses-e1-v2'
REAL_E2_PROFILE = 'synopsis-full-entry-responses-e2-v3'
REAL_E2_SENTENCE_PROFILE = 'synopsis-full-entry-responses-e2-v4'
REAL_E2_CAPACITY_PROFILE = 'synopsis-full-entry-responses-e2-v5'
REAL_BATCH_HANDOFF_PROFILE = 'synopsis-full-entry-responses-batch-handoff-v1'
REAL_PUNCTUATION_HANDOFF_PROFILE = 'synopsis-full-entry-responses-batch-handoff-punctuation-v2'
REAL_QUALIFIER_HANDOFF_PROFILE = 'synopsis-full-entry-responses-batch-handoff-qualifier-v3'
VALIDATOR = 'synopsis-evidence-v1'
PUNCTUATION_VALIDATOR = 'synopsis-evidence-punctuation-v2'
QUALIFIER_VALIDATOR = 'synopsis-evidence-punctuation-qualifier-v3'
PUNCTUATION_ARTIFACT_CAP = 12_000
_BATCH_HANDOFF_PROFILES = (REAL_BATCH_HANDOFF_PROFILE, REAL_PUNCTUATION_HANDOFF_PROFILE,
                           REAL_QUALIFIER_HANDOFF_PROFILE)


def _batch_qualifier_policy(profile):
    return qualifiers.VERSION_V3 if profile == REAL_QUALIFIER_HANDOFF_PROFILE else 'legacy'


def _punctuation_profile(profile):
    return profile in (REAL_PUNCTUATION_HANDOFF_PROFILE, REAL_QUALIFIER_HANDOFF_PROFILE)


def _batch_validator(profile):
    return (QUOTE_VALIDATOR if profile in _QUOTE_PROFILES else SPEAKER_VALIDATOR if profile == REAL_SPEAKER_PROFILE else CHRONICLE_REVIEW_VALIDATOR if profile == REAL_CHRONICLE_REVIEW_PROFILE else
            QUALIFIER_VALIDATOR if profile == REAL_QUALIFIER_HANDOFF_PROFILE else
            PUNCTUATION_VALIDATOR if profile == REAL_PUNCTUATION_HANDOFF_PROFILE else VALIDATOR)
MAX_OUTPUT_CHARS = 3000  # Python Unicode code points, matching metadata._value.
MAX_INPUT_CHARS = 12000
MAX_BATCHES = 128
REAL_MAX_BATCHES = 2
REAL_MAX_SOURCES = 6
REAL_MAX_INPUT_TOKENS = 4096
REAL_MAX_OUTPUT_TOKENS = 2048  # Includes provider-billed reasoning tokens.
REAL_E1_MAX_OUTPUT_TOKENS = 6144  # Provider cap; not a field/JSON size increase.
REAL_E2_MAX_OUTPUT_TOKENS = 6144  # Every E2 batch and final generation.
REAL_MAX_REQUESTS = 3  # This bounded entry trial: two batches and one final.
REAL_MAX_MICROUSD = 200_000
REAL_E2_MAX_MICROUSD = 300_000
REAL_MAX_PART_CHARS = 1500
REAL_E2_CAPACITY_PART_CHARS = 2000
REAL_E2_CAPACITY_FINAL_CHARS = 4600
REAL_BILLING_VERSION = 'a34-cache-aware-v1'
REAL_DIAGNOSTIC_TEXT_CAP = 8192
REAL_DIAGNOSTIC_USAGE_CAP = 4096
UNSUPPORTED = ('安心', '心疼', '爱上', '释然', '明白了自己的心', '永远相爱')
NEGATIVE = ('没有', '没', '不', '未', 'ない', '無')
CONDITIONAL = ('如果', '若', '只有', '除非', 'なら', 'たら')
CORRECTIVE = ('其实', '更正', '改到', '訂正', '変更')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def confirmed_snapshot(db, entry_id, character_id):
    """A saved source list alone is never boundary confirmation."""
    detail = metadata.read_draft(db, entry_id, character_id)
    required = ('mv2_import_segments', 'mv2_import_boundary_confirmations',
                'mv2_import_receipts', 'mv2_import_files', 'mv2_import_batches')
    installed = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(required) <= installed:
        raise ValueError('boundary_confirmation_required')
    matches = db.execute('''SELECT s.*,b.plan_version,b.plan_hash,b.source_snapshot_hash,
        b.confirmation_origin,b.confirmed_by_user_id,b.character_id AS confirmation_character,
        f.character_id AS file_character,f.status AS file_status,f.total AS file_total,
        f.cursor AS file_cursor,f.file_sha256,f.parser_version,f.conversation_id,
        ib.status AS batch_status,c.archive_only
        FROM mv2_import_segments s
        JOIN mv2_import_boundary_confirmations b ON b.file_id=s.file_id AND b.batch_id=s.batch_id
        JOIN mv2_import_files f ON f.id=s.file_id AND f.batch_id=s.batch_id
        JOIN mv2_import_batches ib ON ib.id=s.batch_id
        JOIN conversations c ON c.id=f.conversation_id
        WHERE s.entry_id=?''', (entry_id,)).fetchall()
    if len(matches) != 1:
        raise ValueError('boundary_confirmation_required')
    row = matches[0]
    if (row['confirmation_character'] != character_id or row['file_character'] != character_id
            or row['file_status'] != 'raw_complete' or row['batch_status'] != 'raw_complete'
            or not row['confirmed_by_user_id'] or not row['plan_hash']
            or not row['source_snapshot_hash'] or row['plan_version'] is None
            or row['file_total'] < 1 or row['file_cursor'] != row['file_total']
            or row['archive_only'] != 1):
        raise ValueError('boundary_confirmation_invalid')
    receipts = db.execute('''SELECT r.*,m.conversation_id AS live_conversation,
        m.role AS live_role,m.generation_status AS live_generation_status
        FROM mv2_import_receipts r JOIN messages m ON m.id=r.message_id
        WHERE r.file_id=? ORDER BY r.ordinal''', (row['file_id'],)).fetchall()
    if (len(receipts) != row['file_total'] or
            [r['ordinal'] for r in receipts] != list(range(1, row['file_total']+1)) or
            any(r['character_id'] != character_id or
                r['file_sha256'] != row['file_sha256'] or
                r['parser_version'] != row['parser_version'] or
                r['live_conversation'] != row['conversation_id'] or
                r['live_role'] not in ('user', 'assistant') or
                r['live_generation_status'] != 'imported' for r in receipts)):
        raise ValueError('boundary_receipts_invalid')
    members = [r for r in receipts if row['start_ordinal'] <= r['ordinal'] <= row['end_ordinal']]
    refs = detail['sourceRefs']
    if (len(members) != row['end_ordinal']-row['start_ordinal']+1 or
            len(members) != len(refs) or
            any(ref['source_kind'] != 'message' or
                ref['source_id'] != str(receipt['message_id']) or
                ref['source_role'] != receipt['live_role'] or
                ref['content_hash'] != receipt['content_sha256']
                for ref, receipt in zip(refs, members))):
        raise ValueError('boundary_sources_changed')
    boundary = {'segmentId': row['id'], 'fileId': row['file_id'],
                'batchId': row['batch_id'], 'planVersion': row['plan_version'],
                'planHash': row['plan_hash'], 'sourceSnapshotHash': row['source_snapshot_hash'],
                'start': row['start_ordinal'], 'end': row['end_ordinal']}
    field = detail['fields']['synopsis']
    return {'entryId': entry_id,
            'sourceToken': detail['sourceToken'], 'sourceRefs': refs,
            'fieldToken': field.get('token'), 'fieldState': field['lifecycleStatus'],
            'boundary': boundary, 'profile': PROFILE, 'validator': VALIDATOR}


def input_sources(db, character_id, snapshot):
    sources = []
    for ref in snapshot['sourceRefs']:
        live = m3.live_source(db, character_id, ref['source_kind'], ref['source_id'])
        if (live is None or live.role != ref['source_role'] or
                live.content_hash != ref['content_hash'] or live.state_hash != ref['state_hash']):
            raise ValueError('synopsis_source_changed')
        sources.append({'kind': live.kind, 'id': live.source_id, 'speaker': live.role,
                        'text': live.text, 'sourceStatus': live.status,
                        'contentHash': live.content_hash, 'stateHash': live.state_hash})
    return sources


def plan_batches(sources, *, input_cap=4096):
    if type(input_cap) is not int or not 256 <= input_cap <= MAX_INPUT_CHARS:
        raise ValueError('synopsis_input_cap_invalid')
    if not sources or len(sources) > 500:
        raise ValueError('synopsis_source_count_invalid')
    batches, current = [], []
    for source in sources:
        if len(source['text']) > MAX_INPUT_CHARS:
            raise ValueError('synopsis_single_source_over_limit')
        candidate = current + [source]
        if len(_canonical(candidate)) > input_cap:
            if not current:
                raise ValueError('synopsis_single_source_over_limit')
            batches.append(current)
            current = [source]
            if len(_canonical(current)) > input_cap:
                raise ValueError('synopsis_single_source_over_limit')
        else:
            current = candidate
    if current:
        batches.append(current)
    if len(batches) > MAX_BATCHES:
        raise ValueError('synopsis_entry_over_limit')
    return batches


_QUOTED_END_PAIRS = {'「':'」', '『':'』', '“':'”', '‘':'’'}
_QUOTED_CONTINUATIONS = ('但', '而', '却', '仍', '并', '也', '只是', '不过', '然而')


def _multiple_substantive_sentences(segment):
    """Ignore a terminal mark inside a closed quote only for clear continuation."""
    quote_closers = []
    marks = '。！？!?'
    for index, char in enumerate(segment):
        if char in _QUOTED_END_PAIRS:
            quote_closers.append(_QUOTED_END_PAIRS[char])
        elif quote_closers and char == quote_closers[-1]:
            quote_closers.pop()
        if char not in marks:
            continue
        next_index = index + 1
        while next_index < len(segment) and segment[next_index] in marks:
            next_index += 1
        if not segment[next_index:].strip():
            continue
        if quote_closers:
            after_quote = next_index
            for closer in reversed(quote_closers):
                if after_quote < len(segment) and segment[after_quote] == closer:
                    after_quote += 1
                else:
                    break
            if after_quote > next_index:
                tail = segment[after_quote:].lstrip()
                if (not tail or tail[0] in '，,；;：:' or
                        tail.startswith(_QUOTED_CONTINUATIONS)):
                    continue
        return True
    return False


def validate_output(output, sources, *, qualifier_policy="legacy"):
    """Bounded structural checks; never certifies natural-language fidelity."""
    if qualifier_policy not in ("legacy", qualifiers.VERSION, qualifiers.VERSION_V3, CHRONICLE_REVIEW_POLICY):
        raise ValueError("synopsis_qualifier_policy_invalid")
    if not isinstance(output, dict) or set(output) != {'text', 'units', 'sourceCoverage'}:
        raise ValueError('synopsis_output_shape_invalid')
    text = output['text']
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_OUTPUT_CHARS:
        raise ValueError('synopsis_output_length_invalid')
    expected = [(s['kind'], s['id']) for s in sources]
    if len(set(expected)) != len(expected):
        raise ValueError('synopsis_duplicate_input')
    lookup = dict(zip(expected, sources))
    units = output['units']
    if not isinstance(units, list) or not units:
        raise ValueError('synopsis_units_invalid')
    cursor, cited = 0, set()
    for unit_index, unit in enumerate(units):
        if not isinstance(unit, dict) or set(unit) != {'start', 'end', 'evidence'}:
            raise ValueError('synopsis_units_invalid')
        start, end = unit['start'], unit['end']
        if (type(start) is not int or type(end) is not int or
                not cursor <= start < end <= len(text) or text[cursor:start].strip() or
                not text[start:end].strip()):
            raise ValueError('synopsis_unmapped_output')
        cursor = end
        evidence = unit['evidence']
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('synopsis_evidence_missing')
        original = []
        for ref in evidence:
            if not isinstance(ref, dict) or set(ref) != {'kind','id','start','end','quote','speaker'}:
                raise ValueError('synopsis_evidence_invalid')
            key = (ref['kind'], ref['id'])
            source = lookup.get(key)
            a, b = ref['start'], ref['end']
            if (source is None or ref['speaker'] != source['speaker'] or
                    type(a) is not int or type(b) is not int or
                    not 0 <= a < b <= len(source['text']) or
                    source['text'][a:b] != ref['quote']):
                raise ValueError('synopsis_evidence_invalid')
            cited.add(key)
            original.append(ref['quote'])
            if source['sourceStatus'] in ('unknown', 'failed') and re.search(
                    r'已完成|成功|办妥|完了した', text[start:end]):
                raise ValueError('synopsis_unverified_success')
        segment = text[start:end]
        # A single evidence unit cannot conceal several substantive sentences.
        # This is only a structural boundary check, not a fidelity decision.
        if _multiple_substantive_sentences(segment):
            raise ValueError('synopsis_sentence_unit_required')
        if qualifier_policy == "legacy":
            for marker_group in (NEGATIVE, CONDITIONAL, CORRECTIVE):
                if any(marker in quote for quote in original for marker in marker_group) and not any(
                        marker in segment for marker in marker_group):
                    raise ValueError('synopsis_qualifier_dropped')
        elif qualifier_policy != CHRONICLE_REVIEW_POLICY:
            qualifiers.validate_unit(output, unit_index, evidence, original,
                                     (NEGATIVE, CONDITIONAL, CORRECTIVE),
                                     policy=qualifier_policy)
        if any(phrase in segment and all(phrase not in s['text'] for s in sources)
               for phrase in UNSUPPORTED):
            raise ValueError('synopsis_unsupported_psychology')
    if text[cursor:].strip():
        raise ValueError('synopsis_unmapped_output')
    coverage = output['sourceCoverage']
    if (not isinstance(coverage, list) or len(coverage) != len(sources) or
            any(not isinstance(item, dict) or set(item) != {'kind','id','used'} or
                type(item['used']) is not bool or
                (item['kind'], item['id']) != key or item['used'] != (key in cited)
                for item, key in zip(coverage, expected))):
        raise ValueError('synopsis_coverage_invalid')
    if qualifier_policy in (qualifiers.VERSION, qualifiers.VERSION_V3):
        qualifiers.validate_global(output)
    return {'text': text, 'units': units, 'sourceCoverage': coverage}


def lexical_warnings(output, sources):
    """Diagnostic locations, not proof of contradiction or content approval."""
    notes=[]
    for index,unit in enumerate(output['units']):
        segment=output['text'][unit['start']:unit['end']]
        for label,markers in (('negation',NEGATIVE),('condition',CONDITIONAL),('correction',CORRECTIVE)):
            if any(m in ref['quote'] for ref in unit['evidence'] for m in markers) and not any(m in segment for m in markers):
                notes.append({'unit':index,'start':unit['start'],'end':unit['end'],'reason':'quoted_'+label+'_requires_review'})
    return notes


def checked_punctuation_batch(row, sources=None, *, qualifier_policy='legacy'):
    """Verify the durable raw-to-normalized binding before downstream use."""
    try:
        artifact = json.loads(row['warning_json'])
        output = json.loads(row['output_json'])
        if (not isinstance(artifact, dict) or set(artifact) !=
                {'kind', 'ruleVersion', 'rawOutput', 'rawHash',
                 'normalizedHash', 'adjustments'} or
                len(row['warning_json']) > PUNCTUATION_ARTIFACT_CAP or
                artifact['kind'] != 'synopsis_punctuation_normalization_v1' or
                artifact['ruleVersion'] != punctuation.VERSION or
                _hash(artifact['rawOutput']) != artifact['rawHash'] or
                _hash(output) != artifact['normalizedHash']):
            raise ValueError('synopsis_punctuation_binding_invalid')
        normalized, adjustments = punctuation.normalize_terminal_boundaries_v1(
            artifact['rawOutput'])
        if normalized != output or adjustments != artifact['adjustments']:
            raise ValueError('synopsis_punctuation_binding_invalid')
        return (validate_output(output, sources, qualifier_policy=qualifier_policy)
                if sources is not None else output)
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError('synopsis_punctuation_binding_invalid') from exc


def validate_integrated_final_output(output, sources, parts, *, max_parts=2,
                                     qualifier_policy="legacy"):
    """Additional bounded checks for a final-only rewrite of settled parts.

    The checks do not certify factual fidelity. In particular, a plausible
    sentence with an unrelated but valid citation still needs human review.
    Historical Synopsis profiles continue to use validate_output unchanged.
    """
    checked = validate_output(output, sources, qualifier_policy=qualifier_policy)
    if not isinstance(parts, list) or not 2 <= len(parts) <= max_parts:
        raise ValueError('synopsis_final_parts_invalid')
    allowed = set()
    for part in parts:
        if not isinstance(part, dict) or set(part) != {'text', 'units'} or not isinstance(part['units'], list):
            raise ValueError('synopsis_final_parts_invalid')
        for unit in part['units']:
            if not isinstance(unit, dict) or not isinstance(unit.get('evidence'), list):
                raise ValueError('synopsis_final_parts_invalid')
            for ref in unit['evidence']:
                allowed.add((ref['kind'], ref['id'], ref['start'], ref['end'],
                             ref['quote'], ref['speaker']))
    for unit in checked['units']:
        segment = checked['text'][unit['start']:unit['end']]
        speakers = set()
        originals = []
        for ref in unit['evidence']:
            identity = (ref['kind'], ref['id'], ref['start'], ref['end'],
                        ref['quote'], ref['speaker'])
            if identity not in allowed:
                raise ValueError('synopsis_final_evidence_not_in_parts')
            speakers.add(ref['speaker'])
            originals.append(ref['quote'])
        actor = re.match(r'^\s*(用户|助手)(?:[：:，,\s]|(?=在|说|建议|表示|提醒|提出|未|没|不))', segment)
        if actor and ('user' if actor.group(1) == '用户' else 'assistant') not in speakers:
            raise ValueError('synopsis_final_attribution_conflict')
        # Only a limited Chinese conditional-antecedent polarity check. It
        # catches a direct inversion, not every semantic contradiction.
        def antecedents(value):
            for match in re.finditer(r'(?:如果|若)([^，,；;。]{1,60})', value):
                clause = re.split(r'则|就|再', match.group(1), maxsplit=1)[0]
                yield any(marker in clause for marker in NEGATIVE)
        source_polarities = [p for original in originals for p in antecedents(original)]
        if source_polarities and any(p not in source_polarities for p in antecedents(segment)):
            raise ValueError('synopsis_final_condition_polarity_conflict')
    return checked


def coverage_for(sources, units):
    used = {(ref['kind'], ref['id']) for unit in units for ref in unit['evidence']}
    return [{'kind': s['kind'], 'id': s['id'], 'used': (s['kind'],s['id']) in used}
            for s in sources]


def validate_speaker_anchors(output):
    """Reject only explicit single-speaker reporting inversions, not all semantics."""
    for unit in output['units']:
        segment=output['text'][unit['start']:unit['end']]
        actor=re.match(r'^\s*(イキヤ|阿夜)(?:说|表示|问|回答|告诉|提醒|建议|询问|提到|笑称|肯定)',segment)
        speakers={e['speaker'] for e in unit['evidence']}
        if actor and len(speakers)==1:
            expected='user' if actor.group(1)=='イキヤ' else 'assistant'
            if expected not in speakers:
                raise ValueError('synopsis_explicit_speaker_conflict')


def boundary_binding(db, entry_id, character_id):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'mv2_import_segments','mv2_import_boundary_confirmations',
            'mv2_import_files','mv2_import_batches'} <= tables:
        return None
    rows = db.execute('''SELECT s.id,s.file_id,s.start_ordinal,s.end_ordinal,
        b.plan_version,b.plan_hash,b.source_snapshot_hash,b.confirmed_by_user_id,
        b.confirmation_origin,f.file_sha256,f.parser_version,f.status AS file_status,
        f.cursor,f.total,ib.status AS batch_status
        FROM mv2_import_segments s JOIN mv2_import_boundary_confirmations b
          ON b.file_id=s.file_id AND b.batch_id=s.batch_id AND b.character_id=?
        JOIN mv2_import_files f ON f.id=s.file_id
        JOIN mv2_import_batches ib ON ib.id=s.batch_id
        WHERE s.entry_id=?''', (character_id, entry_id)).fetchall()
    if len(rows) != 1:
        return None
    return contract.token(dict(rows[0]))


def guard_field(db, entry_id, character_id, *, except_id=None):
    from . import memory_v2_processing as processing
    if processing.schema.installed(db):
        scope = processing._scope(character_id, 'entry', entry_id, ['synopsis'])
        processing._block_overlaps(db, character_id, scope, ['synopsis'], except_id=except_id)
    detail = metadata.read_draft(db, entry_id, character_id)
    old = metadata._fields(db, detail['versionId']).get('synopsis')
    if old and (old['locked'] or old['provenance'] in ('manual','user_corrected') or
                json.loads(old['value_json']) is None or
                db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',
                           (old['id'],)).fetchone()):
        raise ValueError('synopsis_field_protected')
    return detail


def cancel_job(db, job_id, character_id, editor_id):
    from . import memory_v2_processing as processing
    processing._transaction(db); metadata._owner(db, editor_id)
    job, _ = _job(db, job_id, character_id)
    if job['status'] == 'running':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?', (job_id,))
        return 'running'
    return processing.cancel(db, job_id, character_id)


def recover_job(db, job_id, character_id, editor_id, *, now=None):
    """Only a settled Mock job may move to a resumable paused state."""
    from . import memory_v2_processing as processing
    from . import memory_v2_jobs as jobs
    processing._transaction(db); metadata._owner(db, editor_id)
    job, payload = _job(db, job_id, character_id)
    processing._block_overlaps(db, character_id, job['scope_key'], ['synopsis'], except_id=job_id)
    if (job['status'] != 'running' or job['lease_until'] is None or
            job['lease_until'] > (time.time() if now is None else now) or
            jobs.unsettled_kind(db, job)):
        raise ValueError('synopsis_recovery_not_proven')
    state = jobs.recompute_parent(db, job_id, character_id,
                                  safe_pause=not job['cancel_intent'])
    if state not in ('paused', 'cancelled'):
        raise ValueError('synopsis_recovery_not_proven')
    db.execute('UPDATE mv3_jobs SET lease_token=NULL,lease_until=NULL WHERE id=?', (job_id,))
    return state


def _read_set(db, entry_id, character_id, input_cap):
    snap = confirmed_snapshot(db, entry_id, character_id)
    snap['boundaryBinding'] = boundary_binding(db, entry_id, character_id)
    sources = input_sources(db, character_id, snap)
    batches = plan_batches(sources, input_cap=input_cap)
    snap['inputCap'] = input_cap
    snap['batchRefs'] = [[[s['kind'], s['id'], s['contentHash'], s['stateHash']]
                          for s in batch] for batch in batches]
    return snap, sources, batches


def create_job(db, entry_id, character_id, editor_id, *, input_cap=4096):
    from . import memory_v2_processing as processing
    processing._transaction(db)
    metadata._owner(db, editor_id)
    guard_field(db, entry_id, character_id)
    read_set, _, _ = _read_set(db, entry_id, character_id, input_cap)
    return processing.create(db, character_id, 'entry', entry_id, ['synopsis'], read_set,
        rule_version=PROFILE, input_version='confirmed-ordered-raw-v1',
        execution_kind='mock_synopsis_v1')


def _job(db, job_id, character_id):
    from . import memory_v2_processing as processing
    job = processing._owned(db, job_id, character_id)
    payload = json.loads(job['payload_json'])
    if (job['kind'] != 'A3_PROCESSING' or payload.get('targetKind') != 'entry' or
            payload.get('fields') != ['synopsis'] or payload.get('ruleVersion') != PROFILE or
            payload.get('inputVersion') != 'confirmed-ordered-raw-v1' or
            payload.get('executionKind') != 'mock_synopsis_v1' or
            payload.get('readSet', {}).get('profile') != PROFILE):
        raise ValueError('not_synopsis_job')
    return job, payload


def _same_inputs(db, payload, character_id):
    prior = payload['readSet']
    try:
        current, sources, batches = _read_set(db, payload['targetId'], character_id,
                                              prior['inputCap'])
    except ValueError:
        return False, None, None
    return current == prior, sources, batches


def _owned_lease(job, lease):
    return (job['status'] == 'running' and job['lease_token'] == lease and
            job['lease_until'] is not None and job['lease_until'] > time.time())


def _finalize(db, job, payload, output, sources, lease, editor_id, *, profile=PROFILE):
    """Only field write is savepointed; reliable settlement has already happened."""
    from . import memory_v2_jobs as jobs
    from . import memory_v2_processing as processing
    job, payload = (_job(db, job['id'], job['character_id']) if profile == PROFILE
                    else _real_job(db, job['id'], job['character_id']))
    if job['cancel_intent']:
        return {'status': jobs.recompute_parent(db, job['id'], job['character_id'])}
    same, _, _ = (_same_inputs(db, payload, job['character_id']) if profile == PROFILE
                  else _real_same_inputs(db, payload, job['character_id']))
    if not same:
        state = jobs.recompute_parent(db, job['id'], job['character_id'],
                                      error='synopsis_input_changed')
        return {'status': state, 'reason': 'synopsis_input_changed'}
    if not _owned_lease(job, lease):
        state = jobs.recompute_parent(db, job['id'], job['character_id'], safe_pause=True)
        return {'status': state, 'reason': 'synopsis_lease_changed'}
    try:
        guard_field(db, payload['targetId'], job['character_id'], except_id=job['id'])
    except ValueError as exc:
        state = jobs.recompute_parent(db, job['id'], job['character_id'], error=str(exc))
        return {'status': state, 'reason': str(exc)}
    db.execute('SAVEPOINT synopsis_business')
    try:
        detail = metadata.read_draft(db, payload['targetId'], job['character_id'])
        old = metadata._fields(db, detail['versionId']).get('synopsis')
        if (old and old['provenance'] == 'model' and old['rule_version'] == profile and
                json.loads(old['value_json']) == output['text'] and
                detail['fields']['synopsis']['lifecycleStatus'] != 'stale'):
            set_id = detail['versionId']
        else:
            evidence = [{'format': _batch_validator(profile), 'units': output['units'],
                         'sourceCoverage': output['sourceCoverage']}]
            if profile in (REAL_CHRONICLE_REVIEW_PROFILE, *_SPEAKER_PROFILES):
                evidence[0]['semanticStatus'] = 'needs_review'
                evidence[0]['warnings'] = lexical_warnings(output, sources)
            if profile == REAL_STREAM_8192_PROFILE:
                evidence[0]['capacityVersion'] = STREAM_8192_CAPACITY_VERSION
            if profile in _QUOTE_PROFILES:
                row = db.execute('SELECT warning_json FROM mv3_model_chunks WHERE job_id=? AND ordinal=0', (job['id'],)).fetchone()
                record = json.loads(row['warning_json']) if row and row['warning_json'] else None
                quotes.checked_artifact(output, record, sources)
                evidence[0]['quoteLocalization'] = record
            result = metadata.revise(db, payload['targetId'], job['character_id'],
                detail['versionId'], {'synopsis': output['text']}, editor_id,
                provenance='model', rule=profile,
                dependencies={'synopsisBoundary': boundary_binding(db, payload['targetId'],
                    job['character_id']), 'synopsisValidator': _batch_validator(profile), **({'synopsisQuoteLocator': quotes.VERSION} if profile in _QUOTE_PROFILES else {}), **({'synopsisCapacityVersion': STREAM_8192_CAPACITY_VERSION} if profile == REAL_STREAM_8192_PROFILE else {})}, evidence=evidence)
            set_id = result['versionId']
        db.execute('INSERT INTO mv3_job_field_results(job_id,set_id) VALUES(?,?)',
                   (job['id'], set_id))
        state = jobs.recompute_parent(db, job['id'], job['character_id'], output_ready=True)
        db.execute('RELEASE synopsis_business')
        return {'status': state, 'fieldSetId': set_id, 'needsReview': True}
    except (ValueError, TypeError, sqlite3.DatabaseError) as exc:
        db.execute('ROLLBACK TO synopsis_business'); db.execute('RELEASE synopsis_business')
        state = jobs.recompute_parent(db, job['id'], job['character_id'], error=str(exc))
        return {'status': state, 'reason': str(exc)}


def run_job(connect, job_id, character_id, editor_id, adapter):
    """Explicit Mock-only dispatcher; each result and its usage settle atomically."""
    from . import memory_v2_processing as processing
    from . import memory_v2_jobs as jobs
    from . import memory_v2_fidelity as fidelity
    from .memory_v2_model_adapter import MockSynopsisAdapter
    if type(adapter) is not MockSynopsisAdapter:
        raise ValueError('mock_synopsis_adapter_required')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE'); metadata._owner(db, editor_id)
        job, payload = _job(db, job_id, character_id)
        if job['status'] == 'completed':
            row = db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                             (job_id,)).fetchone()
            detail = metadata.read_draft(db, payload['targetId'], character_id)
            field = detail['fields']['synopsis']
            if (not row or row['set_id'] != detail['versionId'] or
                    field.get('token') is None or field['lifecycleStatus'] == 'stale' or
                    detail['sourceToken'] != payload['readSet']['sourceToken'] or
                    boundary_binding(db, payload['targetId'], character_id) !=
                    payload['readSet']['boundaryBinding']):
                raise ValueError('synopsis_result_superseded')
            return {'status':'completed', 'fieldSetId':row['set_id'], 'reused':True}
        if job['status'] != 'queued' or job['cancel_intent']:
            raise ValueError('synopsis_job_not_claimable')
        guard_field(db, payload['targetId'], character_id, except_id=job_id)
        same, sources, batches = _same_inputs(db, payload, character_id)
        if not same:
            raise ValueError('synopsis_input_changed')
        total = len(batches) + int(len(batches) > 1)
        existing = db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',
                              (job_id,)).fetchone()[0]
        if not existing:
            processing.prepare_chunks(db, job_id, character_id, [
                {'phase':'batch' if n < len(batches) else 'final',
                 'sourceToken':payload['readSet']['sourceToken'],
                 'refs':payload['readSet']['batchRefs'][n] if n < len(batches) else [],
                 'reservedTokens':payload['readSet']['inputCap'] + MAX_OUTPUT_CHARS,
                 'reservedCostUnits':payload['readSet']['inputCap'] + MAX_OUTPUT_CHARS}
                for n in range(total)])
        elif existing != total:
            raise ValueError('synopsis_chunk_plan_changed')
        lease = str(uuid.uuid4())
        db.execute("UPDATE mv3_jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=? AND status='queued'",
                   (lease, time.time()+60, job_id))
    for ordinal in range(total):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE'); metadata._owner(db, editor_id)
            job, payload = _job(db, job_id, character_id)
            row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=?',
                             (job_id, ordinal)).fetchone()
            if row['state'] == 'completed':
                if not row['output_json']:
                    raise ValueError('synopsis_completed_output_missing')
                continue
            if not _owned_lease(job, lease) or row['state'] != 'prepared':
                raise ValueError('synopsis_lease_or_chunk_changed')
            if job['cancel_intent']:
                return {'status': jobs.recompute_parent(db, job_id, character_id)}
            same, sources, batches = _same_inputs(db, payload, character_id)
            if not same:
                return {'status': jobs.recompute_parent(db, job_id, character_id,
                                      error='synopsis_input_changed'), 'reason':'synopsis_input_changed'}
            if ordinal < len(batches):
                phase = 'batch'
                actual = {'phase':phase, 'sources':batches[ordinal], 'ordinal':ordinal,
                          'inputVersion':payload['readSet']['sourceToken']}
            else:
                phase = 'final'
                stored_parts = [json.loads(r['output_json']) for r in db.execute(
                    "SELECT output_json FROM mv3_model_chunks WHERE job_id=? AND ordinal<? ORDER BY ordinal",
                    (job_id, ordinal))]
                parts = [{'text':part['text'],'units':part['units']} for part in stored_parts]
                actual = {'phase':phase, 'parts':parts,
                          'sourceManifest':[{'kind':s['kind'],'id':s['id']}
                              for s in sources], 'inputVersion':payload['readSet']['sourceToken']}
                if len(_canonical(actual)) > payload['readSet']['inputCap']:
                    return {'status': jobs.recompute_parent(db, job_id, character_id,
                         error='synopsis_final_input_over_limit'),
                         'reason':'synopsis_final_input_over_limit'}
                # The final input is frozen only after all child outputs exist.
                db.execute('UPDATE mv3_model_chunks SET input_hash=?,input_json=? WHERE job_id=? AND ordinal=? AND state=\'prepared\'',
                           (_hash(actual), _canonical({'inputHash':_hash(actual)}), job_id, ordinal))
            identity = processing.dispatch(db, job_id, character_id, ordinal)
            request = {'phase':phase, 'input':actual, 'requestId':identity['requestId'],
                       'inputHash':identity['inputHash'], 'ordinal':ordinal}
        try:
            output, used_input, used_output = adapter.generate(request)
        except Exception:
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                jobs.settle_dispatch(db, job_id, character_id, ordinal, identity['requestId'],
                                     identity['dispatchProof'], state='unknown')
            return {'status':'unknown'}
        if (type(used_input) is not int or type(used_output) is not int or
                min(used_input, used_output) < 0 or
                used_input + used_output > identity['reservedTokens']):
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                jobs.settle_dispatch(db, job_id, character_id, ordinal, identity['requestId'],
                                     identity['dispatchProof'], state='unknown')
            return {'status':'unknown'}
        try:
            validated = fidelity.validate_synopsis(output,
                batches[ordinal] if ordinal < len(batches) else sources)
            reason = None
        except ValueError as exc:
            validated = None; reason = str(exc)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = jobs.settle_dispatch(db, job_id, character_id, ordinal,
                identity['requestId'], identity['dispatchProof'],
                state='rejected' if reason else 'completed', used_input=used_input,
                used_output=used_output, used_cost=used_input+used_output,
                result_hash=_hash(['synopsis-result', validated if validated else reason]))
            if reason:
                return {'status':jobs.recompute_parent(db,job_id,character_id,error=reason),
                        'reason':reason}
            db.execute('UPDATE mv3_model_chunks SET output_json=? WHERE job_id=? AND ordinal=?',
                       (_canonical(validated), job_id, ordinal))
            if ordinal == total-1:
                return _finalize(db, job, payload, validated, sources, lease, editor_id)
            if state != 'running':
                return {'status':state}
    # A former sender may have settled the final response after its lease
    # expired. Resume may publish that saved result without sending again.
    with connect() as db:
        db.execute('BEGIN IMMEDIATE'); metadata._owner(db, editor_id)
        job, payload = _job(db, job_id, character_id)
        same, sources, _ = _same_inputs(db, payload, character_id)
        if not same:
            return {'status':jobs.recompute_parent(db,job_id,character_id,
                error='synopsis_input_changed'), 'reason':'synopsis_input_changed'}
        rows = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',
                          (job_id,)).fetchall()
        if len(rows) != total or any(r['state'] != 'completed' or not r['output_json'] for r in rows):
            raise ValueError('synopsis_incomplete')
        return _finalize(db, job, payload, json.loads(rows[-1]['output_json']),
                         sources, lease, editor_id)


def _real_read_set(db, entry_id, character_id, batch_cap, final_cap, output_cap,
                   trial_id, model, price_hash, owner_id, *, profile=REAL_PROFILE, workspace_model_id=None):
    snap, sources, batches = _read_set(db, entry_id, character_id, batch_cap)
    if len(sources) > (32 if profile in _SPEAKER_PROFILES else 20 if profile in _CHRONICLE_PROFILES else REAL_MAX_SOURCES) or len(batches) > (1 if profile in _CHRONICLE_PROFILES else REAL_MAX_BATCHES):
        raise ValueError('synopsis_real_scope_over_limit')
    snap['profile'] = profile
    if profile in (REAL_CHRONICLE_REVIEW_PROFILE, *_SPEAKER_PROFILES):
        snap['validator'] = _batch_validator(profile)
        snap['qualifierVersion'] = CHRONICLE_REVIEW_POLICY
    if _punctuation_profile(profile):
        snap['validator'] = _batch_validator(profile)
        snap['punctuationVersion'] = punctuation.VERSION
    if profile == REAL_QUALIFIER_HANDOFF_PROFILE:
        snap['qualifierVersion'] = qualifiers.VERSION_V3
    snap['realConfig'] = {'trialId': trial_id, 'model': model, 'priceHash': price_hash,
                           'billingVersion': REAL_BILLING_VERSION,
                          'batchInputCap': batch_cap, 'finalInputCap': final_cap,
                          'inputTokenCap': 8192 if profile in _CHRONICLE_PROFILES else REAL_MAX_INPUT_TOKENS,
                          'outputTokenCap': output_cap,
                          'partCharsCap': _real_part_cap(profile), 'ownerId': owner_id}
    if profile in (REAL_E1_PROFILE, REAL_E2_PROFILE, REAL_E2_SENTENCE_PROFILE,
                   REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES):
        snap['realConfig']['requestProfile'] = profile
    if profile in _SPEAKER_PROFILES:
        snap['realConfig']['waitPolicy'] = 'synopsis-read-180-v1'
        snap['realConfig']['speakerPolicy'] = 'user-ikiya-assistant-aya-v1'
    if profile in _QUOTE_PROFILES:
        snap['quoteLocatorVersion'] = quotes.VERSION
        snap['realConfig']['quoteArtifactCap'] = quotes.ARTIFACT_CAP
    if profile in _STREAM_PROFILES:
        snap['streamVersion']=streams.VERSION
        snap['realConfig']['streamPolicy']=streams.VERSION
        snap['realConfig']['streamLimits']=dict(streams.LIMITS)
    if profile == REAL_STREAM_8192_PROFILE:
        snap['capacityVersion'] = STREAM_8192_CAPACITY_VERSION
    if workspace_model_id is not None:
        if profile not in _STREAM_PROFILES or type(workspace_model_id) is not int or workspace_model_id<=0:
            raise ValueError('synopsis_workspace_binding_invalid')
        snap['realConfig'].update(workspaceModelId=workspace_model_id,workspaceBinding='chronicle-selected-account-v1')
    return snap, sources, batches


def _real_part_cap(profile):
    if profile in _CHRONICLE_PROFILES: return 24000
    return (REAL_E2_CAPACITY_PART_CHARS if profile in (
                 REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES)
            else REAL_MAX_PART_CHARS)


def _real_job(db, job_id, character_id):
    from . import memory_v2_processing as processing
    job = processing._owned(db, job_id, character_id)
    payload = json.loads(job['payload_json'])
    profile = payload.get('ruleVersion')
    if (job['kind'] != 'A3_PROCESSING' or payload.get('targetKind') != 'entry' or
            payload.get('fields') != ['synopsis'] or
             profile not in (*_CHRONICLE_PROFILES, REAL_PROFILE, REAL_E1_PROFILE, REAL_E2_PROFILE,
                             REAL_E2_SENTENCE_PROFILE, REAL_E2_CAPACITY_PROFILE,
                              *_BATCH_HANDOFF_PROFILES) or
            payload.get('inputVersion') != 'confirmed-ordered-raw-v1' or
            payload.get('executionKind') != 'responses_synopsis_v1' or
             payload.get('readSet', {}).get('profile') != profile or
             payload.get('readSet', {}).get('validator') != _batch_validator(profile) or
             payload.get('readSet', {}).get('punctuationVersion') != (
                 punctuation.VERSION if _punctuation_profile(profile) else None) or
             payload.get('readSet', {}).get('qualifierVersion') != (
                 qualifiers.VERSION_V3 if profile == REAL_QUALIFIER_HANDOFF_PROFILE
                 else CHRONICLE_REVIEW_POLICY if profile in (REAL_CHRONICLE_REVIEW_PROFILE, *_SPEAKER_PROFILES) else None)):
        raise ValueError('not_real_synopsis_job')
    config = payload['readSet'].get('realConfig', {})
    if ('workspaceModelId' in config or 'workspaceBinding' in config) and (
            profile not in _STREAM_PROFILES or type(config.get('workspaceModelId')) is not int or
            config['workspaceModelId']<=0 or config.get('workspaceBinding')!='chronicle-selected-account-v1'):
        raise ValueError('synopsis_workspace_binding_invalid')
    if (profile in (REAL_E1_PROFILE, REAL_E2_PROFILE, REAL_E2_SENTENCE_PROFILE,
                      REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES) and
            (config.get('requestProfile') != profile or
             config.get('outputTokenCap') != REAL_E1_MAX_OUTPUT_TOKENS)):
        raise ValueError('synopsis_evaluation_profile_invalid')
    if (config.get('partCharsCap') != _real_part_cap(profile) or
             (profile in (REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES) and
             config.get('finalInputCap') != REAL_E2_CAPACITY_FINAL_CHARS)):
        raise ValueError('synopsis_evaluation_profile_invalid')
    if profile in _CHRONICLE_PROFILES and (config.get('inputTokenCap') != 8192 or config.get('outputTokenCap') != (8192 if profile == REAL_STREAM_8192_PROFILE else 6144) or config.get('batchInputCap') != 12000):
        raise ValueError('synopsis_chronicle_config_invalid')
    if profile in _SPEAKER_PROFILES and (config.get('waitPolicy') != 'synopsis-read-180-v1' or config.get('speakerPolicy') != 'user-ikiya-assistant-aya-v1'):
        raise ValueError('synopsis_speaker_config_invalid')
    if (payload['readSet'].get('quoteLocatorVersion') != (quotes.VERSION if profile in _QUOTE_PROFILES else None) or
        (profile in _QUOTE_PROFILES and config.get('quoteArtifactCap') != quotes.ARTIFACT_CAP)):
        raise ValueError('synopsis_quote_version_invalid')
    if profile in _QUOTE_PROFILES:
        try:
            consistent = (json.loads(job['read_set_json']) == payload['readSet'] and
                job['input_hash'] == processing._hash(['a3-job-v1',character_id,payload]))
        except (ValueError,TypeError,KeyError):
            consistent = False
        if not consistent:
            raise ValueError('synopsis_quote_task_binding_invalid')
    if (payload['readSet'].get('streamVersion') != (streams.VERSION if profile in _STREAM_PROFILES else None) or
        config.get('streamPolicy') != (streams.VERSION if profile in _STREAM_PROFILES else None) or
        config.get('streamLimits') != (streams.LIMITS if profile in _STREAM_PROFILES else None)):
        raise ValueError('synopsis_stream_binding_invalid')
    if payload['readSet'].get('capacityVersion') != (STREAM_8192_CAPACITY_VERSION if profile == REAL_STREAM_8192_PROFILE else None):
        raise ValueError('synopsis_capacity_binding_invalid')
    if type(payload.get('readSet', {}).get('realConfig', {}).get('ownerId')) is not int:
        raise ValueError('synopsis_owner_binding_required')
    return job, payload


def _real_same_inputs(db, payload, character_id):
    prior = payload['readSet']
    config = prior['realConfig']
    try:
        current, sources, batches = _real_read_set(
            db, payload['targetId'], character_id, config['batchInputCap'],
            config['finalInputCap'], config['outputTokenCap'], config['trialId'],
            config['model'], config['priceHash'], config['ownerId'],
            profile=prior['profile'],workspace_model_id=config.get('workspaceModelId'))
    except ValueError:
        return False, None, None
    if 'replacement' in prior:
        from . import memory_v2_synopsis_replacement as replacement
        if not replacement.binding_current(db, character_id, payload):
            return False, None, None
        current['replacement'] = prior['replacement']
    return current == prior, sources, batches


def create_real_trial(db, entry_id, character_id, editor_id, *, model, price,
                      batch_input_cap=4096, final_input_cap=4096,
                      output_cap=REAL_MAX_OUTPUT_TOKENS,
                      max_microusd=REAL_MAX_MICROUSD, profile=REAL_PROFILE, workspace_model_id=None):
    """Create only an isolated trial plan. This function never contacts a provider."""
    from . import memory_v2_processing as processing
    from . import memory_v2_m36_trial as trials
    processing._transaction(db)
    metadata._owner(db, editor_id)
    if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mv3_trial_budgets'"
    ).fetchone():
        raise ValueError('synopsis_trial_schema_required')
    if (profile not in (*_CHRONICLE_PROFILES, REAL_PROFILE, REAL_E1_PROFILE, REAL_E2_PROFILE,
                         REAL_E2_SENTENCE_PROFILE, REAL_E2_CAPACITY_PROFILE,
                          *_BATCH_HANDOFF_PROFILES) or
            type(final_input_cap) is not int or
             (profile in (REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES) and
             final_input_cap != REAL_E2_CAPACITY_FINAL_CHARS) or
             (profile not in (REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES) and
             not 256 <= final_input_cap <= 4096) or
            type(output_cap) is not int or
            (profile == REAL_PROFILE and not 1 <= output_cap <= REAL_MAX_OUTPUT_TOKENS) or
            (profile == REAL_E1_PROFILE and output_cap != REAL_E1_MAX_OUTPUT_TOKENS) or
            (profile in (REAL_E2_PROFILE, REAL_E2_SENTENCE_PROFILE,
                          REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES) and
             output_cap != REAL_E2_MAX_OUTPUT_TOKENS) or
            type(max_microusd) is not int or not 0 < max_microusd <= (
                REAL_E2_MAX_MICROUSD if profile in (REAL_E2_PROFILE,
                                                   REAL_E2_SENTENCE_PROFILE,
                                                   REAL_E2_CAPACITY_PROFILE,
                                                   *_BATCH_HANDOFF_PROFILES)
                else REAL_MAX_MICROUSD)):
        raise ValueError('synopsis_real_limits_invalid')
    if profile == REAL_STREAM_8192_PROFILE and max_microusd > 147456:
        raise ValueError('synopsis_capacity_budget_limit')
    if profile in _CHRONICLE_PROFILES and (model != 'gpt-6.1-sol' or output_cap != (8192 if profile == REAL_STREAM_8192_PROFILE else 6144) or batch_input_cap != 12000):
        raise ValueError('synopsis_chronicle_config_invalid')
    guard_field(db, entry_id, character_id)
    _, sources, batches = _read_set(db, entry_id, character_id, batch_input_cap)
    if len(sources) > (32 if profile in _SPEAKER_PROFILES else 20 if profile in _CHRONICLE_PROFILES else REAL_MAX_SOURCES) or len(batches) > (1 if profile in _CHRONICLE_PROFILES else REAL_MAX_BATCHES):
        raise ValueError('synopsis_real_scope_over_limit')
    total = len(batches) + int(len(batches) > 1 and profile not in _BATCH_HANDOFF_PROFILES)
    if profile == REAL_E1_PROFILE and (len(batches) != 1 or model != 'gpt-6.1-sol'):
        raise ValueError('synopsis_e1_scope_invalid')
    if profile in (REAL_E2_PROFILE, REAL_E2_SENTENCE_PROFILE,
                     REAL_E2_CAPACITY_PROFILE, *_BATCH_HANDOFF_PROFILES) and (
            len(batches) != 2 or model != 'gpt-6.1-sol'):
        raise ValueError('synopsis_e2_scope_invalid')
    if total > REAL_MAX_REQUESTS:
        raise ValueError('synopsis_real_request_limit')
    trial_id = trials.create_trial(db, character_id, model, price,
                                   max_microusd=max_microusd, max_requests=total)
    read_set, _, _ = _real_read_set(db, entry_id, character_id, batch_input_cap,
                                   final_input_cap, output_cap, trial_id, model,
                                   _hash(price), editor_id, profile=profile,workspace_model_id=workspace_model_id)
    job_id = processing.create(db, character_id, 'entry', entry_id, ['synopsis'], read_set,
                               rule_version=profile,
                               input_version='confirmed-ordered-raw-v1',
                               execution_kind='responses_synopsis_v1')
    return {'jobId': job_id, 'trialId': trial_id, 'generationLimit': total,
            'countLimit': total, 'maxMicrousd': max_microusd}


def create_chronicle_trial(db, entry_id, character_id, editor_id, *, price, max_microusd=122880):
    return create_real_trial(db,entry_id,character_id,editor_id,model='gpt-6.1-sol',price=price,batch_input_cap=12000,final_input_cap=4096,output_cap=6144,max_microusd=max_microusd,profile=REAL_CHRONICLE_REVIEW_PROFILE)


def stream_capacity(output_tokens):
    if type(output_tokens) is not int or output_tokens not in (6144, 8192):
        raise ValueError('synopsis_stream_capacity_invalid')
    return (REAL_STREAM_PROFILE, 122880) if output_tokens == 6144 else (REAL_STREAM_8192_PROFILE, 147456)


def preview_chronicle_stream(db, entry_id, character_id, editor_id, output_tokens):
    metadata._owner(db, editor_id)
    profile, cap = stream_capacity(output_tokens)
    snap, sources, batches = _read_set(db, entry_id, character_id, 12000)
    if len(sources) > 32 or len(batches) != 1:
        raise ValueError('synopsis_real_scope_over_limit')
    return dict(profile=profile, validator=QUOTE_VALIDATOR, quoteLocatorVersion=quotes.VERSION,
        sourceToken=snap['sourceToken'], sourceCount=len(sources),
        sources=[dict(id=s['id'], speaker=s['speaker'], characters=len(s['text'])) for s in sources],
        inputCharacters=len(_canonical(_batch_actual(batches[0], 0, snap['sourceToken']))),
        inputTokens=8192, outputTokens=output_tokens, bodyCharacters=3000, model='gpt-6.1-sol',
        reasoning='medium', service='Standard', store=False, maxMicrousd=cap,
        countLimit=1, generationLimit=1, streaming=True, readSeconds=180,
        scopeHash=_hash([profile, snap, output_tokens]), readOnly=True)


def create_chronicle_stream_trial(db, entry_id, character_id, editor_id, *, price,
                                 output_tokens, max_microusd, expected_scope, workspace_model_id):
    preview = preview_chronicle_stream(db, entry_id, character_id, editor_id, output_tokens)
    if preview['scopeHash'] != expected_scope:
        raise ValueError('synopsis_material_changed')
    if max_microusd != preview['maxMicrousd']:
        raise ValueError('synopsis_capacity_budget_limit')
    return create_real_trial(db, entry_id, character_id, editor_id, model='gpt-6.1-sol',
        price=price, batch_input_cap=12000, final_input_cap=4096, output_cap=output_tokens,
        max_microusd=max_microusd, profile=preview['profile'],workspace_model_id=workspace_model_id)


def create_e1_trial(db, entry_id, character_id, editor_id, *, price,
                    batch_input_cap=2000, max_microusd=120_000):
    """Explicit single-batch paid evaluation profile; never upgrades old jobs."""
    return create_real_trial(db, entry_id, character_id, editor_id,
        model='gpt-6.1-sol', price=price, batch_input_cap=batch_input_cap,
        final_input_cap=4096, output_cap=REAL_E1_MAX_OUTPUT_TOKENS,
        max_microusd=max_microusd, profile=REAL_E1_PROFILE)


def create_e2_trial(db, entry_id, character_id, editor_id, *, price,
                    batch_input_cap=1200, max_microusd=300_000):
    """Explicit two-batch evaluation profile; never upgrades E1 or old jobs."""
    return create_real_trial(db, entry_id, character_id, editor_id,
        model='gpt-6.1-sol', price=price, batch_input_cap=batch_input_cap,
        final_input_cap=4096, output_cap=REAL_E2_MAX_OUTPUT_TOKENS,
        max_microusd=max_microusd, profile=REAL_E2_PROFILE)


def create_e2_sentence_trial(db, entry_id, character_id, editor_id, *, price,
                             batch_input_cap=1200, max_microusd=300_000):
    """New E2 sentence-unit profile; never relabels the historical v3 job."""
    return create_real_trial(db, entry_id, character_id, editor_id,
        model='gpt-6.1-sol', price=price, batch_input_cap=batch_input_cap,
        final_input_cap=4096, output_cap=REAL_E2_MAX_OUTPUT_TOKENS,
        max_microusd=max_microusd, profile=REAL_E2_SENTENCE_PROFILE)


def create_e2_capacity_trial(db, entry_id, character_id, editor_id, *, price,
                             batch_input_cap=1200, max_microusd=300_000):
    """New bounded evidence-capacity profile; historical v3/v4 jobs stay fixed."""
    return create_real_trial(db, entry_id, character_id, editor_id,
        model='gpt-6.1-sol', price=price, batch_input_cap=batch_input_cap,
        final_input_cap=REAL_E2_CAPACITY_FINAL_CHARS,
        output_cap=REAL_E2_MAX_OUTPUT_TOKENS,
        max_microusd=max_microusd, profile=REAL_E2_CAPACITY_PROFILE)


def create_batch_handoff_trial(db, entry_id, character_id, editor_id, *, price,
                               batch_input_cap=1200, max_microusd=300_000):
    """Two settled batches only; a separate A3 task owns final integration."""
    return create_real_trial(db, entry_id, character_id, editor_id,
        model='gpt-6.1-sol', price=price, batch_input_cap=batch_input_cap,
        final_input_cap=REAL_E2_CAPACITY_FINAL_CHARS,
        output_cap=REAL_E2_MAX_OUTPUT_TOKENS,
        max_microusd=max_microusd, profile=REAL_BATCH_HANDOFF_PROFILE)


def create_punctuation_batch_handoff_trial(db, entry_id, character_id, editor_id,
                                           *, price, batch_input_cap=1200,
                                           max_microusd=300_000):
    """Versioned batch handoff with bounded punctuation boundary normalization."""
    return create_real_trial(db, entry_id, character_id, editor_id,
        model='gpt-6.1-sol', price=price, batch_input_cap=batch_input_cap,
        final_input_cap=REAL_E2_CAPACITY_FINAL_CHARS,
        output_cap=REAL_E2_MAX_OUTPUT_TOKENS,
        max_microusd=max_microusd, profile=REAL_PUNCTUATION_HANDOFF_PROFILE)


def create_qualifier_batch_handoff_trial(db, entry_id, character_id, editor_id,
                                         *, price, batch_input_cap=1200,
                                         max_microusd=300_000):
    """New bounded qualifier policy; historical batch profiles stay unchanged."""
    return create_real_trial(db, entry_id, character_id, editor_id,
        model='gpt-6.1-sol', price=price, batch_input_cap=batch_input_cap,
        final_input_cap=REAL_E2_CAPACITY_FINAL_CHARS,
        output_cap=REAL_E2_MAX_OUTPUT_TOKENS,
        max_microusd=max_microusd, profile=REAL_QUALIFIER_HANDOFF_PROFILE)


def cancel_real_trial(db, job_id, character_id, editor_id):
    """Controlled cancellation; release unsent siblings only at a safe stop."""
    from . import memory_v2_processing as processing
    from . import memory_v2_jobs as jobs
    processing._transaction(db)
    metadata._owner(db, editor_id)
    job, payload = _real_job(db, job_id, character_id)
    config = payload['readSet']['realConfig']
    if config['ownerId'] != editor_id:
        raise ValueError('synopsis_owner_binding_mismatch')
    trial_id = config['trialId']
    trial = _trial_row(db, trial_id, character_id)
    if job['status'] in ('queued', 'paused'):
        state = processing.cancel(db, job_id, character_id)
        if trial['status'] in ('prepared', 'running'):
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',"
                       "error_code='synopsis_cancelled' WHERE id=?", (trial_id,))
            jobs.release_never_dispatched_trial_requests(
                db, job_id, character_id, trial_id, 'synopsis_cancelled')
        return state
    if job['status'] in ('running', 'unknown'):
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?', (job_id,))
        if trial['status'] == 'stopped':
            jobs.release_never_dispatched_trial_requests(
                db, job_id, character_id, trial_id, 'synopsis_cancelled')
        return job['status']
    return job['status']


def _batch_actual(batch, ordinal, source_token):
    return {'phase': 'batch', 'sources': batch, 'ordinal': ordinal,
            'inputVersion': source_token}


def _final_actual(parts, sources, source_token):
    return {'phase': 'final', 'parts': parts,
            'sourceManifest': [{'kind': s['kind'], 'id': s['id']} for s in sources],
            'inputVersion': source_token}


def _real_parts(db, job_id, count):
    rows = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=? '
                      'AND ordinal<? ORDER BY ordinal', (job_id, count)).fetchall()
    if len(rows) != count or any(r['state'] != 'completed' or not r['output_json'] for r in rows):
        raise ValueError('synopsis_completed_output_missing')
    return [{'text': part['text'], 'units': part['units']}
            for part in (json.loads(r['output_json']) for r in rows)]


def _trial_row(db, trial_id, character_id):
    from . import memory_v2_m36_trial as trials
    row = trials._trial(db, trial_id)
    if row['character_id'] != character_id:
        raise ValueError('trial_not_accessible')
    return row


def prepare_real_trial(connect, job_id, character_id, editor_id, adapter):
    """Count each actual batch once, then reserve all generations, including final."""
    from . import memory_v2_processing as processing
    from . import memory_v2_jobs as jobs
    from . import memory_v2_responses_adapter as responses
    if not isinstance(adapter, responses.ResponsesAdapter):
        raise ValueError('responses_adapter_required')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _real_job(db, job_id, character_id)
        config = payload['readSet']['realConfig']
        if config.get('waitPolicy') != getattr(adapter, 'wait_policy', None) or (config.get('waitPolicy') and
                tuple(getattr(adapter.client.timeout,k) for k in ('connect','read','write','pool'))!=(10,180,60,60)):
            raise ValueError('synopsis_wait_policy_mismatch')
        if config.get('streamPolicy') != getattr(adapter,'stream_policy',None):
            raise ValueError('synopsis_stream_adapter_mismatch')
        if config['ownerId'] != editor_id:
            raise ValueError('synopsis_owner_binding_mismatch')
        trial_id = config['trialId']
        trial = _trial_row(db, trial_id, character_id)
        if (job['status'] != 'queued' or trial['status'] != 'created' or
                adapter.model != trial['model'] or trial['model'] != config['model'] or
                _hash(json.loads(trial['price_json'])) != config['priceHash']):
            raise ValueError('synopsis_trial_not_preparable')
        guard_field(db, payload['targetId'], character_id, except_id=job_id)
        same, sources, batches = _real_same_inputs(db, payload, character_id)
        if not same:
            raise ValueError('synopsis_input_changed')
        total = len(batches) + int(len(batches) > 1 and
                                   payload['ruleVersion'] not in _BATCH_HANDOFF_PROFILES)
        if total != trial['max_requests'] or total > REAL_MAX_REQUESTS:
            raise ValueError('synopsis_real_request_limit')
        if len(batches) > 1:
            baseline = len(_canonical(_final_actual([], sources, payload['readSet']['sourceToken'])))
            if baseline + len(batches) * config['partCharsCap'] + len(batches) - 1 > config['finalInputCap']:
                raise ValueError('synopsis_final_worst_case_over_limit')
        descriptors = []
        requests = []
        for ordinal, batch in enumerate(batches):
            actual = _batch_actual(batch, ordinal, payload['readSet']['sourceToken'])
            if len(_canonical(actual)) > config['batchInputCap']:
                raise ValueError('synopsis_batch_input_over_limit')
            request = responses.synopsis_request_payload(
                trial['model'], 'batch', actual, config['outputTokenCap'],
                profile=payload['ruleVersion'])
            requests.append(request)
            descriptors.append({'phase': 'batch', 'requestHash': _hash(request),
                                'reservedTokens': config['inputTokenCap'] + config['outputTokenCap'],
                                'reservedCostUnits': 0})
        if len(batches) > 1 and payload['ruleVersion'] not in _BATCH_HANDOFF_PROFILES:
            descriptors.append({'phase': 'final', 'sourceToken': payload['readSet']['sourceToken'],
                                'reservedTokens': config['inputTokenCap'] + config['outputTokenCap'],
                                'reservedCostUnits': 0})
        processing.prepare_chunks(db, job_id, character_id, descriptors)
        for ordinal, request in enumerate(requests):
            db.execute('''UPDATE mv3_model_chunks SET trial_id=?,prompt_variant='A34_STRUCTURED',
                input_hash=?,input_json=?,provider_status='prepared'
                WHERE job_id=? AND ordinal=? AND state='prepared' ''',
                (trial_id, _hash(request), _canonical({'requestHash': _hash(request)}),
                 job_id, ordinal))
        if len(batches) > 1 and payload['ruleVersion'] not in _BATCH_HANDOFF_PROFILES:
            db.execute('''UPDATE mv3_model_chunks SET trial_id=?,prompt_variant='A34_STRUCTURED',
                provider_status='prepared' WHERE job_id=? AND ordinal=? AND state='prepared' ''',
                (trial_id, job_id, len(batches)))
        db.execute("UPDATE mv3_trial_budgets SET status='counting' WHERE id=? AND status='created'",
                   (trial_id,))
    try:
        for ordinal, request in enumerate(requests):
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT preflight_input_tokens FROM mv3_model_chunks '
                                 'WHERE job_id=? AND ordinal=?', (job_id, ordinal)).fetchone()
                if row is None or row[0] is not None or _trial_row(
                        db, trial_id, character_id)['status'] != 'counting':
                    raise ValueError('synopsis_count_already_attempted')
                db.execute('UPDATE mv3_model_chunks SET preflight_input_tokens=-1 '
                           'WHERE job_id=? AND ordinal=?', (job_id, ordinal))
            count = adapter.count_input(request)
            if count > config['inputTokenCap']:
                raise ValueError('synopsis_input_token_limit')
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('UPDATE mv3_model_chunks SET preflight_input_tokens=? '
                              'WHERE job_id=? AND ordinal=? AND preflight_input_tokens=-1',
                              (count, job_id, ordinal)).rowcount != 1:
                    raise ValueError('synopsis_count_persist_conflict')
    except Exception as exc:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? "
                       "WHERE id=? AND status='counting'", (str(exc)[:120], trial_id))
        raise
    prepare_error = None
    prepared_result = None
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job, payload = _real_job(db, job_id, character_id)
        trial = _trial_row(db, trial_id, character_id)
        same, _, current_batches = _real_same_inputs(db, payload, character_id)
        counts = db.execute('SELECT ordinal,preflight_input_tokens FROM mv3_model_chunks '
                            'WHERE job_id=? ORDER BY ordinal', (job_id,)).fetchall()
        if (not same or job['status'] != 'queued' or trial['status'] != 'counting' or
                job['cancel_intent'] or len(counts) != total or
                len(current_batches) != len(batches) or
                any(type(row['preflight_input_tokens']) is not int or
                    row['preflight_input_tokens'] < 0 for row in counts[:len(batches)])):
            prepare_error = 'synopsis_prepare_changed'
        else:
            price = json.loads(trial['price_json'])
            per_call = responses.synopsis_quote_microusd(
                config['inputTokenCap'], config['outputTokenCap'],
                price['inputUsdPerMillion'], price['outputUsdPerMillion'])
            total_reserved = per_call * total
            if total_reserved > trial['max_microusd']:
                prepare_error = 'trial_budget_exceeded'
            else:
                db.execute('UPDATE mv3_model_chunks SET reserved_usd_micros=? WHERE job_id=?',
                           (per_call, job_id))
                db.execute("UPDATE mv3_trial_budgets SET reserved_microusd=?,status='prepared' WHERE id=?",
                           (total_reserved, trial_id))
                prepared_result = {'trialId': trial_id, 'generationRequests': total,
                                   'countRequests': len(batches),
                                   'reservedMicrousd': total_reserved}
        if prepare_error:
            db.execute('UPDATE mv3_trial_budgets SET status=\'stopped\',error_code=? WHERE id=?',
                       (prepare_error, trial_id))
    if prepare_error:
        raise ValueError(prepare_error)
    return prepared_result


def _real_stop(db, trial_id, job_id, character_id, reason):
    from . import memory_v2_jobs as jobs
    db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
               (reason, trial_id))
    jobs.release_never_dispatched_trial_requests(
        db, job_id, character_id, trial_id, reason)
    return jobs.recompute_parent(db, job_id, character_id, error=reason)


def _real_unknown(connect, trial_id, job_id, character_id, ordinal, identity,
                  reason, result=None):
    from . import memory_v2_jobs as jobs
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = bool(db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',
                                 (character_id,)).fetchone())
        jobs.settle_dispatch(db, job_id, character_id, ordinal, identity['requestId'],
                             identity['dispatchProof'], state='unknown',
                             original_owner_id=(None if active else
                                                identity['dispatchProof'].owner_id))
        db.execute('''UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,
            provider_usage_json=? WHERE job_id=? AND ordinal=?''',
            (reason, result.get('responseId') if result else None,
             _canonical(result['usage']) if result and result.get('usage') else None,
             job_id, ordinal))
        db.execute("UPDATE mv3_jobs SET error_code=? WHERE id=?", (reason, job_id))
        db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                   (reason, trial_id))
        jobs.release_never_dispatched_trial_requests(
            db, job_id, character_id, trial_id, reason)
    return {'status': 'unknown', 'reason': reason}


def _real_settle_revoked(db, job_id, character_id, editor_id, ordinal, identity,
                         result, estimated):
    """Persist known usage without reading revoked sources or submitting an output."""
    from . import memory_v2_jobs as jobs
    metadata._owner(db, editor_id)
    proof = identity['dispatchProof']
    job, row = jobs._revoked_settlement_job(
        db, job_id, character_id, ordinal, identity['requestId'], proof, editor_id)
    trial_id = identity['trialId']
    trial = _trial_row(db, trial_id, character_id)
    if row['trial_id'] != trial_id or trial['status'] not in ('running', 'stopped'):
        raise ValueError('synopsis_trial_settlement_conflict')
    reason = 'synopsis_role_revoked'
    usage = result['usage']
    state = jobs.settle_dispatch(
        db, job_id, character_id, ordinal, identity['requestId'], proof,
        state='rejected', used_input=usage['input_tokens'],
        used_output=usage['output_tokens'], used_cost=0,
        result_hash=_hash(['synopsis-provider-result-v1', result['responseId'],
                           result['status'], usage, reason]),
        trial_id=trial_id, usd_estimate=estimated, original_owner_id=editor_id)
    db.execute('''UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,
        provider_usage_json=?,usage_estimated_usd_micros=?
        WHERE job_id=? AND ordinal=?''',
        (result['status'], result['responseId'], _canonical(usage), estimated,
         job_id, ordinal))
    db.execute('UPDATE mv3_jobs SET error_code=? WHERE id=?', (reason, job_id))
    db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
               (reason, trial_id))
    jobs.release_never_dispatched_trial_requests(
        db, job_id, character_id, trial_id, reason)
    return {'status': state, 'reason': reason, 'usageEstimatedMicrousd': estimated}


def _real_settle(connect, job_id, character_id, editor_id, ordinal, identity,
                 result, output, reason, estimated, lease, normalization=None):
    """The one A3.0 settlement entry also changes the real USD ledger."""
    from . import memory_v2_jobs as jobs
    job_id_request = identity['requestId']
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        if not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',
                          (character_id,)).fetchone():
            return _real_settle_revoked(db, job_id, character_id, editor_id,
                                        ordinal, identity, result, estimated)
        try:
            job, payload = _real_job(db, job_id, character_id)
        except ValueError:
            # A changed task version cannot submit an output, but the original
            # immutable dispatch proof still permits known usage settlement.
            from . import memory_v2_processing as processing
            job = processing._owned(db, job_id, character_id)
            payload = None
            reason = reason or 'synopsis_version_changed'
        if payload is not None and payload['readSet']['realConfig']['ownerId'] != editor_id:
            reason = reason or 'synopsis_owner_binding_mismatch'
        trial_id = identity['trialId']
        trial = _trial_row(db, trial_id, character_id)
        row = jobs.validate_dispatch_identity(db, job_id, character_id, ordinal,
                                              job_id_request, identity['dispatchProof'])
        if trial['status'] != 'running' or row['trial_id'] != trial_id:
            raise ValueError('synopsis_trial_settlement_conflict')
        if estimated > row['reserved_usd_micros']:
            reason = 'synopsis_usage_exceeds_reservation'
        if payload is not None:
            same, sources, batches = _real_same_inputs(db, payload, character_id)
            if not same:
                reason = reason or 'synopsis_input_changed'
        else:
            sources, batches = None, None
        if job['cancel_intent']:
            reason = reason or 'synopsis_cancelled'
        if not (job['lease_token'] == lease and job['lease_until'] is not None and
                job['lease_until'] > time.time()):
            reason = reason or 'synopsis_lease_changed'
        if reason is None:
            try:
                guard_field(db, payload['targetId'], character_id, except_id=job_id)
            except ValueError as exc:
                reason = str(exc)
        usage = result['usage']
        result_hash = _hash(['synopsis-provider-result-v1', result['responseId'],
                             result['status'], usage, output if reason is None else reason,
                             normalization] if normalization is not None else
                            ['synopsis-provider-result-v1', result['responseId'],
                             result['status'], usage, output if reason is None else reason])
        state = jobs.settle_dispatch(
            db, job_id, character_id, ordinal, job_id_request, identity['dispatchProof'],
            state='rejected' if reason else 'completed',
            used_input=usage['input_tokens'], used_output=usage['output_tokens'],
            used_cost=0, result_hash=result_hash, trial_id=trial_id,
            usd_estimate=estimated)
        db.execute('''UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,
            provider_usage_json=?,usage_estimated_usd_micros=?
            WHERE job_id=? AND ordinal=?''',
            (result['status'], result['responseId'], _canonical(usage), estimated,
             job_id, ordinal))
        if reason:
            if payload is None:
                db.execute("UPDATE mv3_trial_budgets SET status='stopped',"
                           "error_code=? WHERE id=?", (reason, trial_id))
                state = jobs.recompute_parent(
                    db, job_id, character_id, error=reason)
                return {'status': state, 'reason': reason,
                        'usageEstimatedMicrousd': estimated}
            return {'status': _real_stop(db, trial_id, job_id, character_id, reason),
                    'reason': reason, 'usageEstimatedMicrousd': estimated}
        # This write is intentionally in the same transaction as known settlement.
        # If it fails, that transaction rolls back and the caller settles usage
        # separately as a rejected, output-missing request.
        db.execute('UPDATE mv3_model_chunks SET output_json=?,warning_json=? '
                   'WHERE job_id=? AND ordinal=?',
                   (_canonical(output),
                    _canonical(normalization) if normalization is not None else None,
                    job_id, ordinal))
        total = len(batches) + int(len(batches) > 1 and
                                   payload['ruleVersion'] not in _BATCH_HANDOFF_PROFILES)
        if ordinal == total - 1:
            if payload['ruleVersion'] in _BATCH_HANDOFF_PROFILES:
                handoff = jobs.recompute_parent(db, job_id, character_id,
                                                output_ready=True)
                db.execute('UPDATE mv3_trial_budgets SET status=? WHERE id=?',
                           ('completed' if handoff == 'completed' else 'stopped',
                            trial_id))
                return {'status': handoff, 'batchHandoff': handoff == 'completed',
                        'usageEstimatedMicrousd': estimated}
            final = _finalize(db, job, payload, output, sources, lease, editor_id,
                              profile=payload['ruleVersion'])
            if final['status'] == 'completed':
                db.execute("UPDATE mv3_trial_budgets SET status='completed' WHERE id=?",
                           (trial_id,))
            else:
                db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                           (final.get('reason', final['status']), trial_id))
            final['usageEstimatedMicrousd'] = estimated
            return final
        if state != 'running':
            return {'status': state, 'usageEstimatedMicrousd': estimated}
        return {'status': 'running', 'usageEstimatedMicrousd': estimated}


def _real_settle_missing_output(connect, job_id, character_id, editor_id, ordinal,
                                identity, result, estimated):
    """Independent transaction after a proven output-write rollback."""
    from . import memory_v2_jobs as jobs
    trial_id = None
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        if not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',
                          (character_id,)).fetchone():
            return _real_settle_revoked(db, job_id, character_id, editor_id,
                                        ordinal, identity, result, estimated)
        job, payload = _real_job(db, job_id, character_id)
        trial_id = payload['readSet']['realConfig']['trialId']
        row = jobs.validate_dispatch_identity(db, job_id, character_id, ordinal,
                                              identity['requestId'], identity['dispatchProof'])
        if row['state'] in ('completed', 'rejected'):
            if row['state'] == 'completed' and row['output_json']:
                return {'status': job['status'], 'reused': True}
            return {'status': job['status'], 'reason': 'synopsis_output_missing'}
        if row['state'] not in ('possibly_dispatched', 'unknown'):
            raise ValueError('synopsis_settlement_state_invalid')
        reason = 'synopsis_output_persist_failed'
        jobs.settle_dispatch(db, job_id, character_id, ordinal, identity['requestId'],
                             identity['dispatchProof'], state='rejected',
                             used_input=result['usage']['input_tokens'],
                             used_output=result['usage']['output_tokens'], used_cost=0,
                             result_hash=_hash(['synopsis-provider-result-v1',
                                                result['responseId'], result['status'],
                                                result['usage'], reason]),
                             trial_id=trial_id, usd_estimate=estimated)
        db.execute('''UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,
            provider_usage_json=?,usage_estimated_usd_micros=?
            WHERE job_id=? AND ordinal=?''',
            (result['status'], result['responseId'], _canonical(result['usage']),
             estimated, job_id, ordinal))
        return {'status': _real_stop(db, trial_id, job_id, character_id, reason),
                'reason': reason, 'usageEstimatedMicrousd': estimated}


def _real_diagnostic(result, outcome, identity, job_id, ordinal, billing_mode):
    """Only public Provider output, bounded for an external isolated sink.

    This is not a field revision, M3 version, or a source of usable Synopsis.
    Never copy the HTTP envelope, request headers, hidden reasoning, or input.
    """
    reason = outcome.get('reason') or result.get('incompleteReason') or result.get('status')
    usage = result.get('usage')
    raw_usage = usage if isinstance(usage, dict) and len(_canonical(usage)) <= REAL_DIAGNOSTIC_USAGE_CAP else None
    def public_text(value):
        value = value if isinstance(value, str) else ''
        value = re.sub(r'(?i)\bBearer\s+\S+', '[redacted bearer]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]{16,}', '[redacted key]', value)
        return value[:REAL_DIAGNOSTIC_TEXT_CAP], len(value) > REAL_DIAGNOSTIC_TEXT_CAP
    text, text_cut = public_text(result.get('text'))
    refusal, refusal_cut = public_text(result.get('refusalText'))
    structure = None
    if text and not text_cut:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                structure = parsed
        except ValueError:
            pass
    if reason in ('synopsis_role_revoked', 'synopsis_input_changed',
                  'synopsis_cancelled', 'synopsis_lease_changed'):
        text, refusal, structure = '', '', None
    return {'version': 'a34-public-provider-diagnostic-v1',
            'jobId': job_id, 'ordinal': ordinal,
            'requestId': identity['requestId'],
            'dispatchAttemptId': identity['dispatchProof'].attempt_id,
            'providerStatus': result.get('status'),
            'providerReason': str(result.get('incompleteReason') or '')[:120],
            'outcomeStatus': outcome.get('status'), 'outcomeReason': str(reason)[:120],
            'responseId': result.get('responseId'),
            'rawUsage': raw_usage, 'usageUnavailable': raw_usage is None,
            'billingMode': billing_mode,
            'publicOutputText': text, 'publicOutputTruncated': text_cut,
            'publicRefusalText': refusal, 'publicRefusalTruncated': refusal_cut,
            'publicOutputStructure': structure}


def write_isolated_diagnostic(directory, record):
    """Exclusive, bounded external artifact; never part of a settlement commit."""
    if record.get('kind') == 'synopsis-stream-public-event-v1':
        return streams.write_public_event(directory, record)
    blob = _canonical(record)
    if len(blob.encode('utf-8')) > 24_000:
        raise ValueError('synopsis_diagnostic_over_limit')
    destination = Path(directory)
    if not destination.is_dir():
        raise ValueError('synopsis_diagnostic_directory_required')
    name = ('provider_' + str(uuid.UUID(record['jobId'])) + '_' +
            str(record['ordinal']) + '_' +
            str(uuid.UUID(record['dispatchAttemptId'])) + '.json')
    path = destination / name
    try:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(blob)
    except FileExistsError:
        if path.read_text(encoding='utf-8') != blob:
            raise ValueError('synopsis_diagnostic_conflict') from None
    return path


def _real_generation_result(connect, job_id, character_id, editor_id, ordinal,
                            identity, result, batch_sources, all_sources, lease):
    from . import memory_v2_responses_adapter as responses
    from . import memory_v2_fidelity as fidelity
    usage = result.get('usage')
    if (not result.get('responseId') or type(usage) is not dict or
            type(usage.get('input_tokens')) is not int or
            type(usage.get('output_tokens')) is not int or
            min(usage['input_tokens'], usage['output_tokens']) < 0 or
            len(_canonical(usage)) > REAL_DIAGNOSTIC_USAGE_CAP):
        return _real_unknown(connect, identity['trialId'], job_id, character_id,
                             ordinal, identity, 'provider_usage_or_result_unknown', result)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        trial = _trial_row(db, identity['trialId'], character_id)
        price = json.loads(trial['price_json'])
    estimate = responses.synopsis_usage_estimate(
        usage, price['inputUsdPerMillion'], price['outputUsdPerMillion'])
    estimated = estimate['microusd']
    result['_billingMode'] = estimate['billingMode']
    reason = 'provider_stream_integrity_invalid' if result.get('_streamIntegrityError') else None
    if result.get('modelId') != trial['model']:
        reason = 'provider_model_mismatch'
    elif result.get('serviceTier') != 'default':
        reason = 'provider_service_tier_mismatch'
    elif result.get('status') != 'completed':
        reason = 'provider_' + str(result.get('status'))
    elif usage['input_tokens'] > identity.get('inputTokenCap', REAL_MAX_INPUT_TOKENS) or (
            usage['output_tokens'] > identity['outputTokenCap']):
        reason = 'provider_usage_over_limit'
    output = None
    normalization = None
    if reason is None:
        try:
            raw_output = json.loads(result['text'])
            if identity.get('quoteLocatorVersion') is not None:
                if identity['quoteLocatorVersion'] != quotes.VERSION or batch_sources is None:
                    raise ValueError('synopsis_quote_version_invalid')
                normalized, adjustments = quotes.resolve(raw_output, batch_sources)
                output = validate_output(normalized, batch_sources, qualifier_policy=CHRONICLE_REVIEW_POLICY)
                validate_speaker_anchors(output)
                normalization = quotes.artifact(raw_output, output, adjustments)
            elif identity.get('punctuationVersion') == punctuation.VERSION:
                if batch_sources is None:
                    raise ValueError('synopsis_punctuation_batch_only')
                normalized, adjustments = punctuation.normalize_terminal_boundaries_v1(
                    raw_output)
                output = validate_output(normalized, batch_sources,
                    qualifier_policy=identity.get('qualifierVersion', 'legacy'))
                normalization = {
                    'kind': 'synopsis_punctuation_normalization_v1',
                    'ruleVersion': punctuation.VERSION,
                    'rawOutput': raw_output, 'rawHash': _hash(raw_output),
                    'normalizedHash': _hash(output), 'adjustments': adjustments}
                if len(_canonical(normalization)) > PUNCTUATION_ARTIFACT_CAP:
                    raise ValueError('synopsis_punctuation_artifact_over_limit')
            elif identity.get('qualifierVersion') == CHRONICLE_REVIEW_POLICY:
                output = validate_output(raw_output, batch_sources or all_sources, qualifier_policy=CHRONICLE_REVIEW_POLICY)
                if identity.get('speakerPolicy'):
                    validate_speaker_anchors(output)
            else:
                output = fidelity.validate_synopsis(raw_output,
                                                    batch_sources or all_sources)
            if batch_sources is not None and len(_canonical(
                    {'text': output['text'], 'units': output['units']})) > identity.get(
                        'partCharsCap', REAL_MAX_PART_CHARS):
                raise ValueError('synopsis_batch_output_over_limit')
        except (ValueError, TypeError, KeyError) as exc:
            reason = 'synopsis_output_invalid:' + str(exc)[:80]
    try:
        return _real_settle(connect, job_id, character_id, editor_id, ordinal,
                            identity, result, output, reason, estimated, lease,
                            normalization if reason is None else None)
    except sqlite3.DatabaseError:
        # If the commit outcome is uncertain, probe the durable row first.
        try:
            return _real_settle_missing_output(connect, job_id, character_id, editor_id,
                                               ordinal, identity, result, estimated)
        except (sqlite3.DatabaseError, ValueError):
            return {'status': 'unknown', 'reason': 'synopsis_storage_unconfirmed',
                    'usageObservedNotPersisted': True}


def run_real_trial(connect, job_id, character_id, editor_id, adapter,
                   *, diagnostic_sink=None):
    """Explicit bounded Responses path; adapter injection alone never upgrades Mock."""
    from . import memory_v2_processing as processing
    from . import memory_v2_jobs as jobs
    from . import memory_v2_responses_adapter as responses
    if not isinstance(adapter, responses.ResponsesAdapter):
        raise ValueError('responses_adapter_required')
    if adapter.network_enabled and not callable(diagnostic_sink):
        raise ValueError('synopsis_diagnostic_sink_required')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _real_job(db, job_id, character_id)
        config = payload['readSet']['realConfig']
        if config.get('waitPolicy') != getattr(adapter, 'wait_policy', None) or (config.get('waitPolicy') and
                tuple(getattr(adapter.client.timeout,k) for k in ('connect','read','write','pool'))!=(10,180,60,60)):
            raise ValueError('synopsis_wait_policy_mismatch')
        if config.get('streamPolicy') != getattr(adapter,'stream_policy',None):
            raise ValueError('synopsis_stream_adapter_mismatch')
        if config['ownerId'] != editor_id:
            raise ValueError('synopsis_owner_binding_mismatch')
        trial_id = config['trialId']
        trial = _trial_row(db, trial_id, character_id)
        if adapter.model != trial['model']:
            raise ValueError('trial_model_mismatch')
        if job['status'] == 'completed':
            if payload['ruleVersion'] in _BATCH_HANDOFF_PROFILES:
                rows = db.execute('SELECT state,output_json FROM mv3_model_chunks '
                                  'WHERE job_id=? ORDER BY ordinal', (job_id,)).fetchall()
                if (trial['status'] != 'completed' or len(rows) != 2 or
                        any(r['state'] != 'completed' or not r['output_json'] for r in rows)):
                    raise ValueError('synopsis_batch_handoff_invalid')
                jobs.require_dispatch_settled(db, job)
                return {'status': 'completed', 'batchHandoff': True, 'reused': True}
            row = db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                             (job_id,)).fetchone()
            detail = metadata.read_draft(db, payload['targetId'], character_id)
            field = detail['fields']['synopsis']
            if (not row or row['set_id'] != detail['versionId'] or
                    field.get('token') is None or field['lifecycleStatus'] == 'stale' or
                    detail['sourceToken'] != payload['readSet']['sourceToken'] or
                    boundary_binding(db, payload['targetId'], character_id) !=
                    payload['readSet']['boundaryBinding']):
                raise ValueError('synopsis_result_superseded')
            return {'status': 'completed', 'fieldSetId': row[0], 'reused': True}
        same, sources, batches = _real_same_inputs(db, payload, character_id)
        if not same:
            raise ValueError('synopsis_input_changed')
        total = len(batches) + int(len(batches) > 1 and
                                   payload['ruleVersion'] not in _BATCH_HANDOFF_PROFILES)
        rows = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=? '
                          'ORDER BY ordinal', (job_id,)).fetchall()
        if len(rows) != total:
            raise ValueError('synopsis_chunk_plan_changed')
        if job['status'] == 'queued' and trial['status'] == 'prepared':
            guard_field(db, payload['targetId'], character_id, except_id=job_id)
            lease = str(uuid.uuid4())
            db.execute("UPDATE mv3_jobs SET status='running',attempts=attempts+1,"
                       "lease_token=?,lease_until=? WHERE id=? AND status='queued'",
                       (lease, time.time()+(300 if payload['ruleVersion'] in _SPEAKER_PROFILES else 120), job_id))
        elif (job['status'] == 'running' and job['lease_until'] is not None and
              job['lease_until'] <= time.time() and
              all(r['state'] == 'completed' and r['output_json'] for r in rows)):
            # Only persisted full results may be assembled after a crash.
            lease = str(uuid.uuid4())
            db.execute('UPDATE mv3_jobs SET attempts=attempts+1,lease_token=?,lease_until=? '
                       'WHERE id=? AND status=\'running\'', (lease, time.time()+(300 if payload['ruleVersion'] in _SPEAKER_PROFILES else 120), job_id))
        else:
            raise ValueError('synopsis_trial_not_claimable')
    for ordinal in range(total):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            metadata._owner(db, editor_id)
            job, payload = _real_job(db, job_id, character_id)
            trial = _trial_row(db, trial_id, character_id)
            row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=?',
                             (job_id, ordinal)).fetchone()
            if row['state'] == 'completed':
                if not row['output_json']:
                    return {'status': _real_stop(db, trial_id, job_id, character_id,
                                                'synopsis_completed_output_missing')}
                continue
            if row['state'] != 'prepared' or not _owned_lease(job, lease):
                raise ValueError('synopsis_trial_not_dispatchable')
            if job['cancel_intent']:
                return {'status': _real_stop(db, trial_id, job_id, character_id,
                                             'synopsis_cancelled')}
            same, sources, batches = _real_same_inputs(db, payload, character_id)
            if not same:
                return {'status': _real_stop(db, trial_id, job_id, character_id,
                                             'synopsis_input_changed')}
            if ordinal < len(batches):
                actual = _batch_actual(batches[ordinal], ordinal,
                                       payload['readSet']['sourceToken'])
                phase = 'batch'
                cap = config['batchInputCap']
            else:
                actual = _final_actual(_real_parts(db, job_id, len(batches)), sources,
                                       payload['readSet']['sourceToken'])
                phase = 'final'
                cap = config['finalInputCap']
            if len(_canonical(actual)) > cap:
                return {'status': _real_stop(db, trial_id, job_id, character_id,
                                             'synopsis_actual_input_over_limit')}
            request = responses.synopsis_request_payload(trial['model'], phase, actual,
                config['outputTokenCap'], profile=payload['ruleVersion'])
            request_hash = _hash(request)
            if phase == 'final':
                if row['preflight_input_tokens'] is not None:
                    raise ValueError('synopsis_final_count_already_attempted')
                db.execute('UPDATE mv3_model_chunks SET preflight_input_tokens=-1 '
                           'WHERE job_id=? AND ordinal=?', (job_id, ordinal))
            elif row['preflight_input_tokens'] is None or row['preflight_input_tokens'] < 0:
                raise ValueError('synopsis_batch_not_counted')
            elif row['input_hash'] != request_hash:
                raise ValueError('synopsis_count_generation_mismatch')
        if phase == 'final':
            try:
                count = adapter.count_input(request)
                if count > config['inputTokenCap']:
                    raise ValueError('synopsis_input_token_limit')
            except Exception as exc:
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    return {'status': _real_stop(db, trial_id, job_id, character_id,
                                                 'synopsis_final_count_failed'),
                            'reason': str(exc)[:120]}
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                job, payload = _real_job(db, job_id, character_id)
                same, _, _ = _real_same_inputs(db, payload, character_id)
                if not same or job['cancel_intent'] or not _owned_lease(job, lease):
                    return {'status': _real_stop(db, trial_id, job_id, character_id,
                                                 'synopsis_final_count_changed')}
                changed = db.execute('''UPDATE mv3_model_chunks SET preflight_input_tokens=?,
                    input_hash=?,input_json=? WHERE job_id=? AND ordinal=?
                    AND state='prepared' AND preflight_input_tokens=-1''',
                    (count, request_hash, _canonical({'requestHash': request_hash}),
                     job_id, ordinal)).rowcount
                if changed != 1:
                    raise ValueError('synopsis_final_count_persist_conflict')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            metadata._owner(db, editor_id)
            job, payload = _real_job(db, job_id, character_id)
            trial = _trial_row(db, trial_id, character_id)
            row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=?',
                             (job_id, ordinal)).fetchone()
            same, _, _ = _real_same_inputs(db, payload, character_id)
            if (not same or job['cancel_intent'] or not _owned_lease(job, lease) or
                    trial['status'] not in ('prepared', 'running') or
                    trial['dispatched_requests'] >= trial['max_requests'] or
                    trial['reserved_microusd'] < row['reserved_usd_micros'] or
                    row['state'] != 'prepared' or row['trial_id'] != trial_id or
                    row['input_hash'] != request_hash or
                    row['preflight_input_tokens'] is None or
                    row['preflight_input_tokens'] < 0):
                raise ValueError('synopsis_trial_not_dispatchable')
            identity = processing.dispatch(db, job_id, character_id, ordinal,
                                           original_owner_id=editor_id)
            identity['trialId'] = trial_id
            identity['outputTokenCap'] = config['outputTokenCap']
            identity['inputTokenCap'] = config['inputTokenCap']
            identity['partCharsCap'] = config['partCharsCap']
            if payload['ruleVersion'] in _SPEAKER_PROFILES:
                identity['speakerPolicy'] = config['speakerPolicy']
            if payload['ruleVersion'] in _QUOTE_PROFILES:
                identity['quoteLocatorVersion'] = payload['readSet']['quoteLocatorVersion']
            if _punctuation_profile(payload['ruleVersion']):
                identity['punctuationVersion'] = payload['readSet']['punctuationVersion']
            if payload['ruleVersion'] in (REAL_QUALIFIER_HANDOFF_PROFILE, REAL_CHRONICLE_REVIEW_PROFILE, *_SPEAKER_PROFILES):
                identity['qualifierVersion'] = payload['readSet']['qualifierVersion']
            db.execute("UPDATE mv3_model_chunks SET provider_status='possibly_dispatched' "
                       "WHERE job_id=? AND ordinal=?", (job_id, ordinal))
            db.execute("UPDATE mv3_trial_budgets SET status='running',"
                       "dispatched_requests=dispatched_requests+1 WHERE id=?", (trial_id,))
            db.execute('UPDATE mv3_jobs SET lease_until=? WHERE id=?',
                       (time.time()+(300 if payload['ruleVersion'] in _SPEAKER_PROFILES else 120), job_id))
        try:
            if payload['ruleVersion'] in _STREAM_PROFILES:
                adapter.begin_stream_dispatch()
            result = adapter.generate(request)
        except Exception:
            transport = getattr(adapter, 'last_transport_diagnostic', None)
            observed = transport.get('publicResponse') if isinstance(transport, dict) else None
            try:
                outcome = _real_unknown(connect, trial_id, job_id, character_id, ordinal,
                                        identity, 'provider_result_unknown', observed)
            except (sqlite3.DatabaseError, ValueError):
                return {'status': 'unknown', 'reason': 'provider_result_and_storage_unknown'}
            if diagnostic_sink is not None:
                try:
                    record = _real_diagnostic(observed or {}, outcome, identity,
                                              job_id, ordinal, 'not_settled')
                    if isinstance(transport, dict):
                        record['transport'] = {k: v for k, v in transport.items()
                                               if k != 'publicResponse'}
                    diagnostic_sink(record)
                    outcome['diagnosticStored'] = True
                except Exception:
                    outcome['diagnosticStored'] = False
            return outcome
        outcome = _real_generation_result(
            connect, job_id, character_id, editor_id, ordinal, identity, result,
            batches[ordinal] if ordinal < len(batches) else None, sources, lease)
        if diagnostic_sink is not None and (outcome['status'] not in ('running', 'completed') or
                                            result.get('status') != 'completed'):
            try:
                record = _real_diagnostic(
                    result, outcome, identity, job_id, ordinal,
                    result.get('_billingMode', 'not_settled'))
                transport = getattr(adapter, 'last_transport_diagnostic', None)
                if isinstance(transport, dict):
                    record['transport'] = {k: v for k, v in transport.items()
                                           if k != 'publicResponse'}
                diagnostic_sink(record)
                outcome['diagnosticStored'] = True
            except Exception:
                # Diagnostics are outside the settlement transaction. A failed
                # sink cannot undo reliably confirmed usage or permit a retry.
                outcome['diagnosticStored'] = False
        if outcome['status'] != 'running':
            return outcome
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job, payload = _real_job(db, job_id, character_id)
        if payload['ruleVersion'] in _BATCH_HANDOFF_PROFILES:
            trial = _trial_row(db, trial_id, character_id)
            rows = db.execute('SELECT state,output_json FROM mv3_model_chunks '
                              'WHERE job_id=? ORDER BY ordinal', (job_id,)).fetchall()
            if (job['status'] != 'completed' or trial['status'] != 'completed' or
                    len(rows) != 2 or
                    any(r['state'] != 'completed' or not r['output_json'] for r in rows)):
                raise ValueError('synopsis_batch_handoff_invalid')
            jobs.require_dispatch_settled(db, job)
            return {'status': 'completed', 'batchHandoff': True}
        same, sources, batches = _real_same_inputs(db, payload, character_id)
        if not same:
            return {'status': _real_stop(db, trial_id, job_id, character_id,
                                         'synopsis_input_changed')}
        rows = db.execute('SELECT state,output_json FROM mv3_model_chunks WHERE job_id=? '
                          'ORDER BY ordinal', (job_id,)).fetchall()
        if len(rows) != total or any(r['state'] != 'completed' or not r['output_json']
                                     for r in rows):
            raise ValueError('synopsis_incomplete')
        result = _finalize(db, job, payload, json.loads(rows[-1]['output_json']),
                           sources, lease, editor_id, profile=payload['ruleVersion'])
        db.execute("UPDATE mv3_trial_budgets SET status=? WHERE id=?",
                   ('completed' if result['status'] == 'completed' else 'stopped', trial_id))
        return result
