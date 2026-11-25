"""A3.5 confirmed-entry NARRATIVE, using synthetic adapters only.

The M3 version is the draft authority. This task never writes a P1-A2 field.
"""
import hashlib
import json
import re
import sqlite3
import time
import uuid

from . import memory_v2_derivation as m3
from . import memory_v2_fidelity as fidelity
from . import memory_v2_jobs as jobs
from . import memory_v2_metadata as metadata
from . import memory_v2_model_adapter as model
from . import memory_v2_processing as processing
from . import memory_v2_synopsis as synopsis


RULE_VERSION = 'model-narrative-a35-v1'
VALIDATOR_VERSION = 'narrative-ordered-evidence-v1'
EXECUTION_KIND = 'mock_narrative_v1'
MAX_BATCHES = 64
MAX_SOURCES = 500
MAX_BODY_CHARS = 60000
MAX_MAPPING_BYTES = 256000
MAX_TOTAL_RESERVATION = 200000


class _OutputWriteFailed(Exception):
    """The output transaction must roll back before usage can be settled alone."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


class _ByteTokenizer:
    def encode(self, value):
        return value.encode('utf-8')


def _guard_field(db, entry_id, character_id, *, except_id=None):
    detail = metadata.read_draft(db, entry_id, character_id)
    if processing.schema.installed(db):
        scope = processing._scope(character_id, 'entry', entry_id, ['narrative'])
        processing._block_overlaps(db, character_id, scope, ['narrative'], except_id=except_id)
    field = metadata._fields(db, detail['versionId']).get('narrative')
    if field and (field['locked'] or field['provenance'] in ('manual', 'user_corrected')
                  or json.loads(field['value_json']) is None
                  or db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',
                                (field['id'],)).fetchone()):
        raise ValueError('narrative_field_protected')
    if field and json.loads(field['value_json']):
        raise ValueError('narrative_field_already_linked')
    return detail


def _read_set(db, entry_id, character_id, input_cap, output_cap, total_cap, target_chars,
              *, except_id=None):
    if (any(type(x) is not int for x in (input_cap, output_cap, total_cap, target_chars))
            or not 512 <= input_cap <= 12000 or not 128 <= output_cap <= 12000
            or not 1 <= target_chars <= 12000 or not 1 <= total_cap <= MAX_TOTAL_RESERVATION):
        raise ValueError('narrative_budget_invalid')
    confirmed = synopsis.confirmed_snapshot(db, entry_id, character_id)
    detail = _guard_field(db, entry_id, character_id, except_id=except_id)
    live = [m3.live_source(db, character_id, r['source_kind'], r['source_id'])
            for r in detail['sourceRefs']]
    if not live or len(live) > MAX_SOURCES or any(s is None or s.kind != 'message' for s in live):
        raise ValueError('narrative_source_count_invalid')
    batches = model.batches(live, _ByteTokenizer(), input_cap=input_cap,
                            target_chars=target_chars)
    if len(batches) > MAX_BATCHES or len(batches) * (input_cap + output_cap) > total_cap:
        raise ValueError('narrative_total_budget_exceeded')
    read_set = {
        'entryId': entry_id, 'sourceToken': detail['sourceToken'],
        'sourceRefs': detail['sourceRefs'],
        'fieldToken': detail['fields']['narrative'].get('token'),
        'boundary': confirmed['boundary'],
        'boundaryBinding': synopsis.boundary_binding(db, entry_id, character_id),
        'batchRefs': [[{'kind': s['sourceKind'], 'id': s['sourceId'],
                        'contentHash': s['contentHash'], 'stateHash': s['stateHash'],
                        'speaker': s['speaker']} for s in batch] for batch in batches],
        'inputCap': input_cap, 'outputCap': output_cap, 'totalCap': total_cap,
        'targetChars': target_chars, 'promptVersion': model.PROMPT_VERSION,
        'validatorVersion': VALIDATOR_VERSION, 'ruleVersion': RULE_VERSION,
    }
    return read_set, batches


def create_job(db, entry_id, character_id, editor_id, *, input_cap=4096,
               output_cap=2048, total_cap=12000, target_chars=6000):
    processing._transaction(db)
    metadata._owner(db, editor_id)
    read_set, _ = _read_set(db, entry_id, character_id, input_cap, output_cap,
                            total_cap, target_chars)
    return processing.create(db, character_id, 'entry', entry_id, ['narrative'],
                             read_set, rule_version=RULE_VERSION,
                             input_version='confirmed-ordered-raw-v1',
                             execution_kind=EXECUTION_KIND)


def _job(db, job_id, character_id):
    job = processing._owned(db, job_id, character_id)
    payload = json.loads(job['payload_json'])
    if (payload.get('executionKind') != EXECUTION_KIND or
            payload.get('ruleVersion') != RULE_VERSION or
            payload.get('fields') != ['narrative'] or
            payload.get('targetKind') != 'entry' or
            payload.get('inputVersion') != 'confirmed-ordered-raw-v1'):
        raise ValueError('not_narrative_job')
    return job, payload


def _same_inputs(db, payload, character_id, job_id):
    prior = payload['readSet']
    if (prior.get('promptVersion') != model.PROMPT_VERSION or
            prior.get('validatorVersion') != VALIDATOR_VERSION or
            prior.get('ruleVersion') != RULE_VERSION):
        return False, None
    try:
        current, batches = _read_set(db, payload['targetId'], character_id,
                                     prior['inputCap'], prior['outputCap'],
                                     prior['totalCap'], prior['targetChars'], except_id=job_id)
    except ValueError:
        return False, None
    return current == prior, batches


def _lease(job, token):
    return (job['status'] == 'running' and job['lease_token'] == token and
            job['lease_until'] is not None and job['lease_until'] > time.time())


def _items_and_mapping(db, job, payload, rows):
    chunks = [{'items': json.loads(r['output_json']),
               'warnings': json.loads(r['warning_json'] or '[]')} for r in rows]
    items, warnings = fidelity.join_checked(chunks)
    refs = payload['readSet']['sourceRefs']
    if [(i['sourceKind'], i['sourceId'], i['speaker']) for i in items] != [
            (r['source_kind'], r['source_id'], r['source_role']) for r in refs]:
        raise ValueError('narrative_source_coverage_invalid')
    body = '\n\n'.join(item['text'] for item in items)
    if not body or len(body) > MAX_BODY_CHARS:
        raise ValueError('narrative_body_over_limit')
    offset, mapping = 0, []
    for item in items:
        text = item['text']
        mapping.append({'sourceKind': item['sourceKind'], 'sourceId': item['sourceId'],
                        'sourceRole': item['speaker'], 'outputStart': offset,
                        'outputEnd': offset + len(text), 'rawStart': None,
                        'rawEnd': None, 'mappingStatus': 'requires_human_fidelity_review',
                        'warningCodes': warnings, 'entryId': payload['targetId'],
                        'sourceToken': payload['readSet']['sourceToken'],
                        'boundaryBinding': payload['readSet']['boundaryBinding'],
                        'validatorVersion': VALIDATOR_VERSION})
        offset += len(text) + 2
    if len(_canonical(mapping).encode('utf-8')) > MAX_MAPPING_BYTES:
        raise ValueError('narrative_mapping_over_limit')
    return body, mapping, warnings


def _validate_v3_attribution(inputs, items):
    """Small, declared syntactic guard; human review remains the fidelity gate."""
    for source, item in zip(inputs, items):
        text = item['text']
        if source['speaker'] == 'user' and re.match(r'^\s*她说[:：]\s*我', text):
            raise ValueError('user_first_person_not_converted')
        if source['speaker'] == 'assistant' and re.match(r'^\s*我说[:：]\s*她说[:：]\s*我说', text):
            raise ValueError('assistant_attribution_duplicated')


def _finish(db, job, payload, lease):
    job, payload = _job(db, job['id'], job['character_id'])
    if job['lease_token'] != lease:
        # A newer claimant owns the business result. The original sender may
        # settle its usage, but must not change the new claimant's parent state.
        return {'status': job['status']}
    same, _ = _same_inputs(db, payload, job['character_id'], job['id'])
    if not same:
        return {'status': jobs.recompute_parent(db, job['id'], job['character_id'],
                                                error='narrative_input_changed')}
    if job['cancel_intent']:
        return {'status': jobs.recompute_parent(db, job['id'], job['character_id'])}
    if not _lease(job, lease):
        return {'status': jobs.recompute_parent(db, job['id'], job['character_id'],
                                                safe_pause=True)}
    try:
        _guard_field(db, payload['targetId'], job['character_id'], except_id=job['id'])
    except ValueError as exc:
        return {'status': jobs.recompute_parent(db, job['id'], job['character_id'],
                                                error=str(exc))}
    rows = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',
                      (job['id'],)).fetchall()
    if not rows or any(r['state'] != 'completed' or not r['output_json'] for r in rows):
        raise ValueError('narrative_batches_incomplete')
    db.execute('SAVEPOINT a35_business_result')
    try:
        body, mapping, warnings = _items_and_mapping(db, job, payload, rows)
        refs = [{'kind': r['source_kind'], 'id': r['source_id']}
                for r in payload['readSet']['sourceRefs']]
        derivation = m3.create_derivation(db, job['character_id'], 'NARRATIVE', refs)
        version = m3.add_version(db, derivation['id'], refs, body, author_kind='model',
                                 rule_version=RULE_VERSION,
                                 prompt_version=model.PROMPT_VERSION,
                                 model_provider='local-mock', model_id='local-mock-a35',
                                 mapping=mapping)
        state = jobs.recompute_parent(db, job['id'], job['character_id'],
                                      output_ready=True, result_version_id=version['id'])
        db.execute('RELEASE a35_business_result')
        return {'status': state, 'versionId': version['id'], 'warnings': warnings}
    except (ValueError, sqlite3.DatabaseError) as exc:
        db.execute('ROLLBACK TO a35_business_result')
        db.execute('RELEASE a35_business_result')
        if isinstance(exc, sqlite3.DatabaseError):
            state = jobs.recompute_parent(db, job['id'], job['character_id'], safe_pause=True)
        else:
            state = jobs.recompute_parent(db, job['id'], job['character_id'], error=str(exc))
        return {'status': state, 'reason': type(exc).__name__}


def _settle_unpersisted_output(connect, job_id, character_id, ordinal, identity,
                               used_input, used_output, validated):
    """Run only after the failed output transaction has rolled back."""
    result_hash = _hash(['a35-output-not-persisted', _hash(validated)])
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = jobs.validate_dispatch_identity(
                db, job_id, character_id, ordinal,
                identity['requestId'], identity['dispatchProof'])
            if (row['state'] not in ('possibly_dispatched', 'unknown')
                    or row['output_json'] is not None):
                raise ValueError('narrative_settlement_conflict')
            jobs.settle_dispatch(
                db, job_id, character_id, ordinal, identity['requestId'],
                identity['dispatchProof'], state='rejected',
                used_input=used_input, used_output=used_output,
                used_cost=used_input + used_output,
                result_hash=result_hash)
            state = jobs.recompute_parent(db, job_id, character_id,
                                          error='narrative_output_persistence_failed')
            return {'status': state, 'reason': 'narrative_output_persistence_failed'}
    except sqlite3.DatabaseError as exc:
        state = _confirmed_settlement(connect, job_id, character_id, ordinal,
                                      identity, 'rejected', used_input,
                                      used_output, result_hash, None)
        if state is not None:
            return {'status': state, 'reason': 'narrative_output_persistence_failed'}
        # An unavailable store cannot certify a successful settlement. The
        # original dispatch remains blocked until reliable reconciliation.
        raise RuntimeError('narrative_observed_usage_not_persisted') from exc


def _confirmed_settlement(connect, job_id, character_id, ordinal, identity,
                          state, used_input, used_output, result_hash, output):
    """Read only: resolve an ambiguous commit without issuing a second charge."""
    try:
        with connect() as db:
            row = jobs.validate_dispatch_identity(
                db, job_id, character_id, ordinal,
                identity['requestId'], identity['dispatchProof'])
            expected = m3._hash([state, used_input, used_output,
                                 used_input + used_output, result_hash, None, None])
            if (row['state'] == state and row['used_input_tokens'] == used_input
                    and row['used_output_tokens'] == used_output
                    and row['used_cost_units'] == used_input + used_output
                    and row['settlement_hash'] == expected
                    and row['output_json'] == output):
                return jobs.owned(db, job_id, character_id)['status']
    except (ValueError, sqlite3.DatabaseError):
        return None
    return None


def run_job(connect, job_id, character_id, editor_id, adapter):
    if type(adapter) is not model.MockNarrativeAdapter:
        raise ValueError('mock_narrative_adapter_required')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _job(db, job_id, character_id)
        if job['status'] == 'completed':
            if not job['result_version_id'] or m3._validity(
                    db, job['result_version_id'], character_id, set(), draft=True):
                raise ValueError('narrative_result_superseded')
            return {'status': 'completed', 'versionId': job['result_version_id'],
                    'reused': True}
        if job['status'] != 'queued' or job['cancel_intent']:
            raise ValueError('narrative_job_not_claimable')
        same, batches = _same_inputs(db, payload, character_id, job_id)
        if not same:
            raise ValueError('narrative_input_changed')
        old = db.execute('SELECT COUNT(*) FROM mv3_model_chunks WHERE job_id=?',
                         (job_id,)).fetchone()[0]
        if not old:
            processing.prepare_chunks(db, job_id, character_id, [
                {'sourceToken': payload['readSet']['sourceToken'],
                 'boundaryBinding': payload['readSet']['boundaryBinding'],
                 'ordinal': i, 'refs': payload['readSet']['batchRefs'][i],
                 'promptVersion': model.PROMPT_VERSION,
                 'validatorVersion': VALIDATOR_VERSION,
                 'reservedTokens': payload['readSet']['inputCap'] + payload['readSet']['outputCap'],
                 'reservedCostUnits': payload['readSet']['inputCap'] + payload['readSet']['outputCap']}
                for i in range(len(batches))])
        elif old != len(batches):
            raise ValueError('narrative_chunk_plan_changed')
        lease = str(uuid.uuid4())
        changed = db.execute("UPDATE mv3_jobs SET status='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE id=? AND status='queued'",
                             (lease, time.time()+60, job_id)).rowcount
        if changed != 1:
            raise ValueError('narrative_claim_conflict')
    for ordinal, batch in enumerate(batches):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job, payload = _job(db, job_id, character_id)
            row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=?',
                             (job_id, ordinal)).fetchone()
            if row['state'] == 'completed' and row['output_json']:
                continue
            if job['cancel_intent']:
                return {'status': jobs.recompute_parent(db, job_id, character_id)}
            if not _lease(job, lease) or row['state'] != 'prepared':
                raise ValueError('narrative_lease_or_chunk_changed')
            same, current_batches = _same_inputs(db, payload, character_id, job_id)
            if not same:
                return {'status': jobs.recompute_parent(db, job_id, character_id,
                                                        error='narrative_input_changed')}
            actual = {'ordinal': ordinal, 'messages': current_batches[ordinal],
                      'promptVersion': model.PROMPT_VERSION,
                      'validatorVersion': VALIDATOR_VERSION,
                      'sourceToken': payload['readSet']['sourceToken'],
                      'boundaryBinding': payload['readSet']['boundaryBinding']}
            if model.count_tokens(model.NARRATIVE_PROMPT + json.dumps(actual['messages'],
                                       ensure_ascii=False), _ByteTokenizer()) > payload['readSet']['inputCap']:
                raise ValueError('narrative_input_over_limit')
            input_hash = _hash(actual)
            db.execute("UPDATE mv3_model_chunks SET input_hash=?,input_json=? WHERE job_id=? AND ordinal=? AND state='prepared'",
                       (input_hash, _canonical({'inputHash': input_hash}), job_id, ordinal))
            identity = processing.dispatch(db, job_id, character_id, ordinal)
            request = {'requestId': identity['requestId'], 'inputHash': input_hash,
                       'ordinal': ordinal, 'kind': 'NARRATIVE',
                       'instruction': model.NARRATIVE_PROMPT,
                       'messages': actual['messages'],
                       'maxOutputTokens': payload['readSet']['outputCap'],
                       'promptVersion': model.PROMPT_VERSION,
                       'validatorVersion': VALIDATOR_VERSION}
        try:
            response, used_input, used_output = adapter.generate(request)
        except Exception:
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                jobs.settle_dispatch(db, job_id, character_id, ordinal,
                                     identity['requestId'], identity['dispatchProof'],
                                     state='unknown')
            return {'status': 'unknown'}
        if (type(used_input) is not int or type(used_output) is not int or
                min(used_input, used_output) < 0 or
                used_input + used_output > identity['reservedTokens']):
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                jobs.settle_dispatch(db, job_id, character_id, ordinal,
                                     identity['requestId'], identity['dispatchProof'],
                                     state='unknown')
            return {'status': 'unknown'}
        try:
            if len(_canonical(response).encode('utf-8')) > payload['readSet']['outputCap']:
                raise ValueError('narrative_output_over_limit')
            items, warnings = fidelity.validate_chunk(request['messages'], response,
                                                       kind='NARRATIVE')
            _validate_v3_attribution(request['messages'], items)
            validated = _canonical(items)
            reason = None
        except (ValueError, TypeError) as exc:
            validated, warnings, reason = None, [], str(exc)
        settlement_attempted = False
        try:
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                job, payload = _job(db, job_id, character_id)
                same, _ = _same_inputs(db, payload, character_id, job_id)
                try:
                    _guard_field(db, payload['targetId'], character_id, except_id=job_id)
                except ValueError:
                    same = False
                stale = not same or bool(job['cancel_intent'])
                settlement_attempted = True
                state = jobs.settle_dispatch(
                    db, job_id, character_id, ordinal, identity['requestId'],
                    identity['dispatchProof'], state='rejected' if reason or stale else 'completed',
                    used_input=used_input, used_output=used_output,
                    used_cost=used_input+used_output,
                    result_hash=_hash(['a35-result', reason or validated, stale]))
                if reason or stale:
                    error = reason or 'narrative_input_changed_or_cancelled'
                    return {'status': jobs.recompute_parent(db, job_id, character_id,
                                                            error=error), 'reason': error}
                try:
                    db.execute('UPDATE mv3_model_chunks SET output_json=?,warning_json=? '
                               'WHERE job_id=? AND ordinal=?',
                               (validated, _canonical(warnings), job_id, ordinal))
                except sqlite3.DatabaseError as exc:
                    raise _OutputWriteFailed() from exc
                if ordinal != len(batches)-1:
                    job, payload = _job(db, job_id, character_id)
                    if not _lease(job, lease):
                        return {'status': jobs.recompute_parent(db, job_id, character_id,
                                                                safe_pause=True)}
                    if state != 'running':
                        return {'status': state}
        except _OutputWriteFailed:
            return _settle_unpersisted_output(
                connect, job_id, character_id, ordinal, identity,
                used_input, used_output, validated)
        except sqlite3.DatabaseError as exc:
            if not settlement_attempted:
                raise RuntimeError('narrative_observed_usage_not_persisted') from exc
            expected_state = 'rejected' if reason or stale else 'completed'
            result_hash = _hash(['a35-result', reason or validated, stale])
            confirmed = _confirmed_settlement(
                connect, job_id, character_id, ordinal, identity, expected_state,
                used_input, used_output, result_hash,
                None if expected_state == 'rejected' else validated)
            if confirmed is None:
                raise RuntimeError('narrative_observed_usage_not_persisted') from exc
            if expected_state == 'rejected':
                return {'status': confirmed,
                        'reason': reason or 'narrative_input_changed_or_cancelled'}
            if ordinal != len(batches)-1:
                # A prior commit may have succeeded despite a lost response.
                # Never automatically issue the following request from this path.
                return {'status': confirmed}
        if ordinal == len(batches)-1:
            # The final business version is a separate short transaction. A
            # crash here leaves definite usage and output available for retry.
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                job, payload = _job(db, job_id, character_id)
                return _finish(db, job, payload, lease)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job, payload = _job(db, job_id, character_id)
        return _finish(db, job, payload, lease)


def cancel_job(db, job_id, character_id, editor_id):
    processing._transaction(db)
    metadata._owner(db, editor_id)
    job, _ = _job(db, job_id, character_id)
    if job['status'] in ('running', 'unknown'):
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?', (job_id,))
        return job['status']
    return processing.cancel(db, job_id, character_id)


def recover_job(db, job_id, character_id, editor_id, *, now=None):
    processing._transaction(db)
    metadata._owner(db, editor_id)
    job, _ = _job(db, job_id, character_id)
    if (job['status'] != 'running' or job['lease_until'] is None or
            job['lease_until'] > (time.time() if now is None else now) or
            jobs.unsettled_kind(db, job)):
        raise ValueError('narrative_recovery_not_proven')
    state = jobs.recompute_parent(db, job_id, character_id,
                                  safe_pause=not job['cancel_intent'])
    if state not in ('paused', 'cancelled'):
        raise ValueError('narrative_recovery_not_proven')
    db.execute('UPDATE mv3_jobs SET lease_token=NULL,lease_until=NULL WHERE id=?', (job_id,))
    return state


def version_invalid_reason(db, version, character_id):
    """Called by M3 before every draft preview, review, activation and read."""
    if version['rule_version'] != RULE_VERSION or version['prompt_version'] != model.PROMPT_VERSION:
        return 'narrative_rule_changed'
    try:
        mapping = json.loads(version['mapping_json'] or '[]')
        refs = m3._refs(db, version['id'])
        if not isinstance(mapping, list) or len(mapping) != len(refs) or not mapping:
            return 'narrative_binding_invalid'
        first = mapping[0]
        required = ('entryId', 'sourceToken', 'boundaryBinding', 'validatorVersion')
        if not isinstance(first, dict) or any(
                not isinstance(first.get(k), str) or not first[k] for k in required):
            return 'narrative_binding_invalid'
        if first['validatorVersion'] != VALIDATOR_VERSION:
            return 'narrative_validator_changed'
        entry = metadata._entry(db, first['entryId'], character_id)
        current = metadata._set(db, entry)
        live_refs = metadata._validate_sources(db, entry, current)
        if (current['source_token'] != first['sourceToken'] or
                synopsis.boundary_binding(db, first['entryId'], character_id) !=
                first['boundaryBinding'] or
                [(r['source_kind'], r['source_id']) for r in live_refs] !=
                [(r['source_kind'], r['source_id']) for r in refs]):
            return 'narrative_boundary_changed'
        cursor = 0
        for i, (item, ref) in enumerate(zip(mapping, refs)):
            if (not isinstance(item, dict) or any(item.get(k) != first[k] for k in required)
                    or item.get('sourceKind') != ref['source_kind']
                    or item.get('sourceId') != ref['source_id']
                    or item.get('sourceRole') != ref['source_role']
                    or item.get('mappingStatus') != 'requires_human_fidelity_review'
                    or item.get('rawStart', 'invalid') is not None
                    or item.get('rawEnd', 'invalid') is not None
                    or type(item.get('outputStart')) is not int
                    or type(item.get('outputEnd')) is not int
                    or item['outputStart'] != cursor
                    or not cursor < item['outputEnd'] <= len(version['body'])):
                return 'narrative_binding_invalid'
            cursor = item['outputEnd']
            if i < len(refs)-1:
                if version['body'][cursor:cursor+2] != '\n\n':
                    return 'narrative_mapping_invalid'
                cursor += 2
        if cursor != len(version['body']):
            return 'narrative_mapping_invalid'
    except (ValueError, KeyError, TypeError, sqlite3.DatabaseError):
        return 'narrative_binding_invalid'
    return None
