"""Opt-in final-only Synopsis task over already settled, validated batches.

No route, scheduler, credential lookup or automatic Provider invocation is installed.
The source batch job and its ledger are read-only dependencies of a new A3 job.
"""

import json
import sqlite3
import time
import uuid

from . import memory_v2_jobs as jobs
from . import memory_v2_metadata as metadata
from . import memory_v2_m36_trial as trials
from . import memory_v2_processing as processing
from . import memory_v2_responses_adapter as responses
from . import memory_v2_synopsis as synopsis
from . import memory_v2_synopsis_qualifiers as qualifiers


PROFILE = 'synopsis-full-entry-responses-final-general-v1'
VALIDATOR = 'synopsis-integrated-final-evidence-v1'
PROFILE_V2 = 'synopsis-full-entry-responses-final-general-v2'
VALIDATOR_V2 = 'synopsis-integrated-final-evidence-v2'
PROFILE_V3 = 'synopsis-full-entry-responses-final-general-v3'
VALIDATOR_V3 = 'synopsis-integrated-final-evidence-v3'
_VALIDATORS = {PROFILE: VALIDATOR, PROFILE_V2: VALIDATOR_V2,
               PROFILE_V3: VALIDATOR_V3}


def _qualifier_version(profile):
    return (qualifiers.VERSION if profile == PROFILE_V2 else
            qualifiers.VERSION_V3 if profile == PROFILE_V3 else None)


def _qualifier_policy(payload):
    profile = payload.get('ruleVersion')
    if profile == PROFILE:
        return 'legacy'
    if profile == PROFILE_V2:
        return qualifiers.VERSION
    if profile == PROFILE_V3:
        return qualifiers.VERSION_V3
    raise ValueError('synopsis_final_profile_invalid')
MAX_PARTS = 8
MAX_FINAL_INPUT_CHARS = 4600
MAX_INPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS = 6144
MAX_MICROUSD = 120_000
MAX_PART_OUTPUT_JSON_CHARS = 12_000
MAX_FINAL_OUTPUT_JSON_CHARS = 16_000
_SOURCE_PROFILES = (synopsis.PROFILE, synopsis.REAL_PROFILE,
                    synopsis.REAL_E2_PROFILE, synopsis.REAL_E2_SENTENCE_PROFILE,
                    synopsis.REAL_E2_CAPACITY_PROFILE,
                    synopsis.REAL_BATCH_HANDOFF_PROFILE,
                    synopsis.REAL_PUNCTUATION_HANDOFF_PROFILE,
                    synopsis.REAL_QUALIFIER_HANDOFF_PROFILE)


def _source(db, source_job_id, entry_id, character_id):
    """Rebuild the full source dependency from current authority and durable rows."""
    source_job = processing._owned(db, source_job_id, character_id)
    payload = json.loads(source_job['payload_json'])
    profile = payload.get('ruleVersion')
    execution = payload.get('executionKind')
    if (source_job['kind'] != 'A3_PROCESSING' or
            payload.get('targetKind') != 'entry' or payload.get('targetId') != entry_id or
            payload.get('fields') != ['synopsis'] or profile not in _SOURCE_PROFILES or
            execution != ('mock_synopsis_v1' if profile == synopsis.PROFILE else
                          'responses_synopsis_v1') or
            payload.get('inputVersion') != 'confirmed-ordered-raw-v1' or
            source_job['status'] not in ('completed', 'failed') or
            (profile in synopsis._BATCH_HANDOFF_PROFILES and
             source_job['status'] != 'completed')):
        raise ValueError('synopsis_source_job_invalid')
    jobs.require_dispatch_settled(db, source_job)
    previous = payload['readSet']
    expected_validator = synopsis._batch_validator(profile)
    expected_qualifier = (qualifiers.VERSION_V3 if profile ==
                          synopsis.REAL_QUALIFIER_HANDOFF_PROFILE else None)
    if (previous.get('profile') != profile or
            previous.get('validator') != expected_validator or
            previous.get('punctuationVersion') != (
                synopsis.punctuation.VERSION
                if synopsis._punctuation_profile(profile) else None) or
            previous.get('qualifierVersion') != expected_qualifier):
        raise ValueError('synopsis_source_profile_invalid')
    snapshot, sources, batches = synopsis._read_set(
        db, entry_id, character_id, previous['inputCap'])
    for key in ('entryId', 'sourceToken', 'sourceRefs', 'boundary',
                'boundaryBinding', 'batchRefs'):
        if snapshot.get(key) != previous.get(key):
            raise ValueError('synopsis_source_changed')
    if not 2 <= len(batches) <= MAX_PARTS:
        raise ValueError('synopsis_final_batch_count_invalid')
    rows = [dict(row) for row in db.execute(
        'SELECT * FROM mv3_model_chunks WHERE job_id=? ORDER BY ordinal',
        (source_job_id,))]
    expected_rows = len(batches) + int(profile not in synopsis._BATCH_HANDOFF_PROFILES)
    if len(rows) != expected_rows or [r['ordinal'] for r in rows] != list(range(expected_rows)):
        raise ValueError('synopsis_source_chunk_plan_invalid')
    if profile in synopsis._BATCH_HANDOFF_PROFILES:
        config = previous.get('realConfig', {})
        trial = synopsis._trial_row(db, config.get('trialId'), character_id)
        if (trial['status'] != 'completed' or trial['max_requests'] != len(batches) or
                db.execute('SELECT 1 FROM mv3_job_field_results WHERE job_id=?',
                           (source_job_id,)).fetchone()):
            raise ValueError('synopsis_batch_handoff_invalid')
    parts, bindings = [], []
    for ordinal, (row, batch) in enumerate(zip(rows, batches)):
        if (row['state'] != 'completed' or not row['output_json'] or
                len(row['output_json']) > MAX_PART_OUTPUT_JSON_CHARS or
                not row['settlement_hash'] or not jobs._valid_request_identity(row['request_id']) or
                not jobs._valid_request_identity(row['dispatch_attempt_id']) or
                type(row['used_input_tokens']) is not int or row['used_input_tokens'] < 0 or
                type(row['used_output_tokens']) is not int or row['used_output_tokens'] < 0 or
                (profile != synopsis.PROFILE and
                 (row['trial_id'] != previous.get('realConfig', {}).get('trialId') or
                  row['provider_status'] != 'completed' or
                  not row['provider_response_id'] or not row['provider_usage_json'] or
                  type(row['usage_estimated_usd_micros']) is not int or
                  row['usage_estimated_usd_micros'] < 0))):
            raise ValueError('synopsis_batch_not_reliably_settled')
        if profile != synopsis.PROFILE:
            try:
                provider_usage = json.loads(row['provider_usage_json'])
                if (provider_usage['input_tokens'] != row['used_input_tokens'] or
                        provider_usage['output_tokens'] != row['used_output_tokens']):
                    raise ValueError('synopsis_batch_usage_binding_invalid')
            except (TypeError, KeyError, json.JSONDecodeError) as exc:
                raise ValueError('synopsis_batch_usage_binding_invalid') from exc
        try:
            output = (synopsis.checked_punctuation_batch(
                          row, batch,
                          qualifier_policy=synopsis._batch_qualifier_policy(profile))
                      if synopsis._punctuation_profile(profile)
                      else synopsis.validate_output(json.loads(row['output_json']), batch))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError('synopsis_batch_output_invalid') from exc
        parts.append({'text': output['text'], 'units': output['units']})
        binding = {'ordinal': ordinal, 'requestId': row['request_id'],
                         'attemptId': row['dispatch_attempt_id'],
                         'inputHash': row['input_hash'],
                         'outputHash': synopsis._hash(output),
                         'settlementHash': row['settlement_hash'],
                         'usageHash': (synopsis._hash(json.loads(row['provider_usage_json']))
                                       if row['provider_usage_json'] else None),
                         'providerResponseId': row['provider_response_id'],
                         'providerStatus': row['provider_status'],
                         'trialId': row['trial_id'],
                         'usedInput': row['used_input_tokens'],
                         'usedOutput': row['used_output_tokens'],
                         'usedMicrousd': row['usage_estimated_usd_micros']}
        if synopsis._punctuation_profile(profile):
            binding['normalizationHash'] = synopsis._hash(json.loads(row['warning_json']))
        bindings.append(binding)
    dependency = {'sourceJobId': source_job_id,
                  'sourceJobInputHash': source_job['input_hash'],
                  'sourceProfile': profile, 'batches': bindings}
    if synopsis._punctuation_profile(profile):
        dependency['sourceValidator'] = expected_validator
        dependency['punctuationVersion'] = synopsis.punctuation.VERSION
    if profile == synopsis.REAL_QUALIFIER_HANDOFF_PROFILE:
        dependency['qualifierVersion'] = qualifiers.VERSION_V3
    return snapshot, sources, parts, dependency


def _read_set(db, entry_id, character_id, source_job_id, trial_id, model,
              price_hash, owner_id, profile=PROFILE):
    if profile not in _VALIDATORS:
        raise ValueError('synopsis_final_profile_invalid')
    snapshot, sources, parts, binding = _source(db, source_job_id, entry_id, character_id)
    if profile == PROFILE_V3 and binding['sourceProfile'] != synopsis.REAL_QUALIFIER_HANDOFF_PROFILE:
        raise ValueError('synopsis_final_source_profile_invalid')
    snapshot['profile'] = profile
    snapshot['validator'] = _VALIDATORS[profile]
    if _qualifier_version(profile):
        snapshot['qualifierVersion'] = _qualifier_version(profile)
    snapshot['batchDependency'] = binding
    snapshot['realConfig'] = {'trialId': trial_id, 'model': model,
                              'priceHash': price_hash, 'ownerId': owner_id,
                              'requestProfile': profile,
                              'billingVersion': synopsis.REAL_BILLING_VERSION,
                              'finalInputCap': MAX_FINAL_INPUT_CHARS,
                              'inputTokenCap': MAX_INPUT_TOKENS,
                              'outputTokenCap': MAX_OUTPUT_TOKENS}
    return snapshot, sources, parts


def batch_binding_current(db, character_id, binding):
    """Read-time check without reading RAW or recursively reading the field set."""
    if not isinstance(binding, dict):
        return False
    punct = synopsis._punctuation_profile(binding.get('sourceProfile'))
    required = {'sourceJobId', 'sourceJobInputHash', 'sourceProfile', 'batches'}
    if punct:
        required |= {'sourceValidator', 'punctuationVersion'}
    if binding.get('sourceProfile') == synopsis.REAL_QUALIFIER_HANDOFF_PROFILE:
        required.add('qualifierVersion')
    if set(binding) != required:
        return False
    source_job = db.execute('SELECT * FROM mv3_jobs WHERE id=? AND character_id=?',
                            (binding['sourceJobId'], character_id)).fetchone()
    if (source_job is None or source_job['input_hash'] != binding['sourceJobInputHash'] or
            source_job['status'] not in ('completed', 'failed') or
            binding['sourceProfile'] not in _SOURCE_PROFILES or
            not isinstance(binding['batches'], list) or
            not 2 <= len(binding['batches']) <= MAX_PARTS):
        return False
    try:
        payload = json.loads(source_job['payload_json'])
        if payload.get('ruleVersion') != binding['sourceProfile']:
            return False
        if punct and (binding['sourceValidator'] != synopsis._batch_validator(binding['sourceProfile']) or
                      binding['punctuationVersion'] != synopsis.punctuation.VERSION or
                      payload.get('readSet', {}).get('validator') != binding['sourceValidator'] or
                      payload.get('readSet', {}).get('punctuationVersion') !=
                      binding['punctuationVersion']):
            return False
        if payload.get('readSet', {}).get('qualifierVersion') != (
                qualifiers.VERSION_V3 if binding['sourceProfile'] ==
                synopsis.REAL_QUALIFIER_HANDOFF_PROFILE else None):
            return False
        if binding['sourceProfile'] == synopsis.REAL_QUALIFIER_HANDOFF_PROFILE and (
                binding['qualifierVersion'] != qualifiers.VERSION_V3):
            return False
        for ordinal, saved in enumerate(binding['batches']):
            row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=?',
                             (binding['sourceJobId'], ordinal)).fetchone()
            if row is None or row['state'] != 'completed' or not row['output_json']:
                return False
            actual = {'ordinal': ordinal, 'requestId': row['request_id'],
                      'attemptId': row['dispatch_attempt_id'],
                      'inputHash': row['input_hash'],
                      'outputHash': synopsis._hash(json.loads(row['output_json'])),
                      'settlementHash': row['settlement_hash'],
                      'usageHash': (synopsis._hash(json.loads(row['provider_usage_json']))
                                    if row['provider_usage_json'] else None),
                      'providerResponseId': row['provider_response_id'],
                      'providerStatus': row['provider_status'],
                      'trialId': row['trial_id'],
                      'usedInput': row['used_input_tokens'],
                      'usedOutput': row['used_output_tokens'],
                      'usedMicrousd': row['usage_estimated_usd_micros']}
            if punct:
                synopsis.checked_punctuation_batch(row)
                actual['normalizationHash'] = synopsis._hash(json.loads(row['warning_json']))
            if actual != saved:
                return False
    except (ValueError, KeyError, TypeError):
        return False
    return True


def _job(db, job_id, character_id):
    job = processing._owned(db, job_id, character_id)
    payload = json.loads(job['payload_json'])
    read = payload.get('readSet', {})
    config = read.get('realConfig', {})
    profile = payload.get('ruleVersion')
    if (job['kind'] != 'A3_PROCESSING' or payload.get('targetKind') != 'entry' or
            payload.get('fields') != ['synopsis'] or profile not in _VALIDATORS or
            payload.get('inputVersion') != 'confirmed-ordered-raw-v1' or
            payload.get('executionKind') != 'responses_synopsis_v1' or
            read.get('profile') != profile or read.get('validator') != _VALIDATORS[profile] or
            read.get('qualifierVersion') != _qualifier_version(profile) or
            config.get('requestProfile') != profile or
            config.get('billingVersion') != synopsis.REAL_BILLING_VERSION or
            config.get('finalInputCap') != MAX_FINAL_INPUT_CHARS or
            config.get('inputTokenCap') != MAX_INPUT_TOKENS or
            config.get('outputTokenCap') != MAX_OUTPUT_TOKENS or
            type(config.get('ownerId')) is not int or
            not read.get('batchDependency', {}).get('sourceJobId')):
        raise ValueError('synopsis_final_job_invalid')
    return job, payload


def _current(db, payload, character_id):
    read = payload['readSet']
    config = read['realConfig']
    try:
        current, sources, parts = _read_set(
            db, payload['targetId'], character_id,
            read['batchDependency']['sourceJobId'], config['trialId'], config['model'],
            config['priceHash'], config['ownerId'], payload['ruleVersion'])
    except (ValueError, KeyError, TypeError):
        return False, None, None
    return current == read, sources, parts


def _request(payload, sources, parts):
    config = payload['readSet']['realConfig']
    actual = synopsis._final_actual(parts, sources, payload['readSet']['sourceToken'])
    if len(synopsis._canonical(actual)) > MAX_FINAL_INPUT_CHARS:
        raise ValueError('synopsis_final_input_over_limit')
    return responses.synopsis_request_payload(config['model'], 'final', actual,
                                               MAX_OUTPUT_TOKENS,
                                               profile=config['requestProfile'])


def _sender_lease(job, lease):
    # Dispatch intentionally makes the parent unknown until the request settles.
    return (job['lease_token'] == lease and job['lease_until'] is not None and
            job['lease_until'] > time.time())


def _create_task(db, entry_id, character_id, editor_id, source_job_id, *, model, price,
                 max_microusd, profile):
    """Create an independent A3 task and trial; no paid request or old-ledger write."""
    processing._transaction(db)
    metadata._owner(db, editor_id)
    if profile not in _VALIDATORS:
        raise ValueError('synopsis_final_profile_invalid')
    if not processing.schema.installed(db):
        raise ValueError('a3_migration_required')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='mv3_trial_budgets'").fetchone():
        raise ValueError('synopsis_trial_schema_required')
    synopsis.guard_field(db, entry_id, character_id)
    if type(max_microusd) is not int or not 0 < max_microusd <= MAX_MICROUSD:
        raise ValueError('synopsis_final_budget_invalid')
    _, sources, parts, dependency = _source(db, source_job_id, entry_id, character_id)
    with_owner = db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',
                            (source_job_id,)).fetchone()
    source_config = json.loads(with_owner['payload_json'])['readSet'].get('realConfig', {})
    if source_config and source_config.get('ownerId') != editor_id:
        raise ValueError('synopsis_source_owner_mismatch')
    # Validate the entire final wrapper before creating a budget row.
    scratch = {'readSet': {'realConfig': {'model': model, 'requestProfile': profile}, 'sourceToken':
               synopsis.confirmed_snapshot(db, entry_id, character_id)['sourceToken']}}
    _request(scratch, sources, parts)
    price_hash = synopsis._hash(price)
    for row in db.execute("SELECT id,payload_json FROM mv3_jobs WHERE character_id=? AND kind='A3_PROCESSING'",
                          (character_id,)):
        prior = json.loads(row['payload_json'])
        prior_read = prior.get('readSet', {})
        if (prior.get('ruleVersion') == profile and prior.get('targetId') == entry_id and
                prior_read.get('batchDependency') == dependency and
                prior_read.get('realConfig', {}).get('model') == model and
                prior_read.get('realConfig', {}).get('priceHash') == price_hash and
                prior_read.get('realConfig', {}).get('ownerId') == editor_id):
            prior_trial = synopsis._trial_row(
                db, prior_read['realConfig']['trialId'], character_id)
            if prior_trial['max_microusd'] != max_microusd:
                raise ValueError('synopsis_final_budget_conflict')
            return {'jobId': row['id'], 'trialId': prior_trial['id'], 'reused': True}
    trial_id = trials.create_trial(db, character_id, model, price,
                                   max_microusd=max_microusd, max_requests=1)
    read, _, _ = _read_set(db, entry_id, character_id, source_job_id,
                           trial_id, model, price_hash, editor_id, profile)
    job_id = processing.create(db, character_id, 'entry', entry_id, ['synopsis'], read,
                               rule_version=profile, input_version='confirmed-ordered-raw-v1',
                               execution_kind='responses_synopsis_v1')
    return {'jobId': job_id, 'trialId': trial_id, 'reused': False}


def create_task(db, entry_id, character_id, editor_id, source_job_id, *, model, price,
                max_microusd=MAX_MICROUSD):
    """Preserve the legacy final profile for existing internal callers."""
    return _create_task(db, entry_id, character_id, editor_id, source_job_id,
                        model=model, price=price, max_microusd=max_microusd,
                        profile=PROFILE)


def create_task_v2(db, entry_id, character_id, editor_id, source_job_id, *, model, price,
                   max_microusd=MAX_MICROUSD):
    """Trusted internal entry for the versioned qualifier policy."""
    return _create_task(db, entry_id, character_id, editor_id, source_job_id,
                        model=model, price=price, max_microusd=max_microusd,
                        profile=PROFILE_V2)


def create_task_v3(db, entry_id, character_id, editor_id, source_job_id, *, model, price,
                   max_microusd=MAX_MICROUSD):
    """Trusted internal final entry for settled v3 qualifier batches."""
    return _create_task(db, entry_id, character_id, editor_id, source_job_id,
                        model=model, price=price, max_microusd=max_microusd,
                        profile=PROFILE_V3)


def prepare_task(connect, job_id, character_id, editor_id, adapter):
    if not isinstance(adapter, responses.ResponsesAdapter):
        raise ValueError('responses_adapter_required')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _job(db, job_id, character_id)
        config = payload['readSet']['realConfig']
        trial = synopsis._trial_row(db, config['trialId'], character_id)
        if (config['ownerId'] != editor_id or job['status'] != 'queued' or
                trial['status'] != 'created' or adapter.model != config['model'] or
                trial['model'] != config['model'] or
                synopsis._hash(json.loads(trial['price_json'])) != config['priceHash']):
            raise ValueError('synopsis_final_not_preparable')
        same, sources, parts = _current(db, payload, character_id)
        if not same or job['cancel_intent']:
            raise ValueError('synopsis_final_input_changed')
        synopsis.guard_field(db, payload['targetId'], character_id, except_id=job_id)
        request = _request(payload, sources, parts)
        request_hash = synopsis._hash(request)
        processing.prepare_chunks(db, job_id, character_id, [{
            'phase': 'final', 'requestHash': request_hash,
            'reservedTokens': MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS,
            'reservedCostUnits': 0}])
        db.execute('''UPDATE mv3_model_chunks SET trial_id=?,prompt_variant='A34_STRUCTURED',
            input_hash=?,input_json=?,provider_status='prepared',preflight_input_tokens=-1
            WHERE job_id=? AND ordinal=0 AND state='prepared' ''',
            (config['trialId'], request_hash,
             synopsis._canonical({'requestHash': request_hash}), job_id))
        db.execute("UPDATE mv3_trial_budgets SET status='counting' WHERE id=? AND status='created'",
                   (config['trialId'],))
    try:
        count = adapter.count_input(request)
        if count > MAX_INPUT_TOKENS:
            raise ValueError('synopsis_input_token_limit')
    except Exception as exc:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? "
                       "WHERE id=? AND status='counting'",
                       (str(exc)[:120], config['trialId']))
        raise
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _job(db, job_id, character_id)
        trial = synopsis._trial_row(db, config['trialId'], character_id)
        same, sources, parts = _current(db, payload, character_id)
        if (not same or job['status'] != 'queued' or job['cancel_intent'] or
                trial['status'] != 'counting' or
                synopsis._hash(_request(payload, sources, parts)) != request_hash):
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code='input_changed' "
                       'WHERE id=?', (config['trialId'],))
            raise ValueError('synopsis_final_input_changed')
        price = json.loads(trial['price_json'])
        reserve = responses.synopsis_quote_microusd(
            MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS,
            price['inputUsdPerMillion'], price['outputUsdPerMillion'])
        if reserve > trial['max_microusd']:
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code='trial_budget_exceeded' "
                       'WHERE id=?', (config['trialId'],))
            raise ValueError('trial_budget_exceeded')
        db.execute('UPDATE mv3_model_chunks SET preflight_input_tokens=?,reserved_usd_micros=? '
                   'WHERE job_id=? AND ordinal=0 AND state=\'prepared\' AND preflight_input_tokens=-1',
                   (count, reserve, job_id))
        db.execute("UPDATE mv3_trial_budgets SET reserved_microusd=?,status='prepared' WHERE id=?",
                   (reserve, config['trialId']))
    return {'trialId': config['trialId'], 'countRequests': 1,
            'generationRequests': 1, 'reservedMicrousd': reserve}


def _finish(db, job, payload, output, sources, parts, lease, editor_id):
    """Field, result reference, and success commit in the settlement transaction."""
    if job['cancel_intent'] or not _sender_lease(job, lease):
        raise ValueError('synopsis_final_business_not_owned')
    same, _, _ = _current(db, payload, job['character_id'])
    if not same:
        raise ValueError('synopsis_final_input_changed')
    synopsis.guard_field(db, payload['targetId'], job['character_id'], except_id=job['id'])
    detail = metadata.read_draft(db, payload['targetId'], job['character_id'])
    if detail['fields']['synopsis'].get('token') != payload['readSet']['fieldToken']:
        raise ValueError('synopsis_final_field_changed')
    old = metadata._fields(db, detail['versionId']).get('synopsis')
    profile = payload['ruleVersion']
    validator = _VALIDATORS[profile]
    if (old and old['provenance'] == 'model' and old['rule_version'] == profile and
            json.loads(old['value_json']) == output['text'] and
            detail['fields']['synopsis']['lifecycleStatus'] != 'stale'):
        set_id = detail['versionId']
    else:
        evidence = [{'format': validator, 'units': output['units'],
                     'sourceCoverage': output['sourceCoverage'],
                     'batchDependency': payload['readSet']['batchDependency']}]
        result = metadata.revise(
            db, payload['targetId'], job['character_id'], detail['versionId'],
            {'synopsis': output['text']}, editor_id, provenance='model', rule=profile,
            dependencies={'synopsisBoundary': synopsis.boundary_binding(
                db, payload['targetId'], job['character_id']),
                'synopsisValidator': validator,
                'synopsisFinalBatches': payload['readSet']['batchDependency']},
            evidence=evidence)
        set_id = result['versionId']
    db.execute('INSERT INTO mv3_job_field_results(job_id,set_id) VALUES(?,?)',
               (job['id'], set_id))
    state = jobs.recompute_parent(db, job['id'], job['character_id'], output_ready=True)
    return {'status': state, 'fieldSetId': set_id, 'needsReview': True}


def _settle_known(connect, job_id, character_id, editor_id, identity, result,
                  output, reason, estimate, lease):
    trial_id = identity['trialId']
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        if not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',
                          (character_id,)).fetchone():
            return synopsis._real_settle_revoked(db, job_id, character_id, editor_id,
                                                 0, identity, result, estimate)
        row = jobs.validate_dispatch_identity(db, job_id, character_id, 0,
                                              identity['requestId'], identity['dispatchProof'])
        trial = synopsis._trial_row(db, trial_id, character_id)
        if row['trial_id'] != trial_id or trial['status'] not in ('running', 'stopped'):
            raise ValueError('synopsis_final_settlement_conflict')
        try:
            job, payload = _job(db, job_id, character_id)
        except ValueError:
            # The original proof still authorizes known usage settlement, never
            # a business write from a task whose version/read set has changed.
            job = processing._owned(db, job_id, character_id)
            payload = None
            reason = reason or 'synopsis_final_version_changed'
        if payload is None:
            same, sources, parts = False, None, None
        else:
            same, sources, parts = _current(db, payload, character_id)
        if estimate > row['reserved_usd_micros']:
            reason = reason or 'synopsis_usage_exceeds_reservation'
        if not same:
            reason = reason or 'synopsis_final_input_changed'
        if job['cancel_intent']:
            reason = reason or 'synopsis_cancelled'
        if not _sender_lease(job, lease):
            reason = reason or 'synopsis_lease_changed'
        if reason is None:
            try:
                synopsis.guard_field(db, payload['targetId'], character_id, except_id=job_id)
            except ValueError as exc:
                reason = str(exc)
        usage = result['usage']
        state = jobs.settle_dispatch(
            db, job_id, character_id, 0, identity['requestId'], identity['dispatchProof'],
            state='rejected' if reason else 'completed',
            used_input=usage['input_tokens'], used_output=usage['output_tokens'],
            used_cost=0,
            result_hash=synopsis._hash(['synopsis-final-result-v1', result['responseId'],
                                        result['status'], usage, output if reason is None else reason]),
            trial_id=trial_id, usd_estimate=estimate)
        db.execute('''UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,
            provider_usage_json=?,usage_estimated_usd_micros=? WHERE job_id=? AND ordinal=0''',
            (result['status'], result['responseId'], synopsis._canonical(usage), estimate,
             job_id))
        if reason:
            jobs.recompute_parent(db, job_id, character_id, error=reason)
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                       (reason, trial_id))
            return {'status': 'failed', 'reason': reason,
                    'usageEstimatedMicrousd': estimate}
        db.execute('UPDATE mv3_model_chunks SET output_json=? WHERE job_id=? AND ordinal=0',
                   (synopsis._canonical(output), job_id))
        db.execute('SAVEPOINT synopsis_final_business')
        try:
            finished = _finish(db, job, payload, output, sources, parts, lease, editor_id)
            db.execute('RELEASE synopsis_final_business')
            db.execute("UPDATE mv3_trial_budgets SET status='completed' WHERE id=?", (trial_id,))
            finished['usageEstimatedMicrousd'] = estimate
            return finished
        except (ValueError, TypeError, sqlite3.DatabaseError) as exc:
            db.execute('ROLLBACK TO synopsis_final_business')
            db.execute('RELEASE synopsis_final_business')
            reason = str(exc)[:120]
            jobs.recompute_parent(db, job_id, character_id, error=reason)
            db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                       (reason, trial_id))
            return {'status': 'failed', 'reason': reason,
                    'usageEstimatedMicrousd': estimate}


def _settle_after_storage_failure(connect, job_id, character_id, editor_id,
                                  identity, result, estimate):
    """Only after a definite rollback; never send again or invent a result."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        if not db.execute('SELECT 1 FROM characters WHERE id=? AND enabled=1',
                          (character_id,)).fetchone():
            return synopsis._real_settle_revoked(db, job_id, character_id, editor_id,
                                                 0, identity, result, estimate)
        job, payload = _job(db, job_id, character_id)
        row = jobs.validate_dispatch_identity(db, job_id, character_id, 0,
                                              identity['requestId'], identity['dispatchProof'])
        if row['state'] in ('completed', 'rejected'):
            return {'status': job['status'], 'reused': True}
        if row['state'] not in ('possibly_dispatched', 'unknown'):
            raise ValueError('synopsis_final_settlement_state_invalid')
        reason = 'synopsis_final_output_persist_failed'
        usage = result['usage']
        jobs.settle_dispatch(db, job_id, character_id, 0, identity['requestId'],
                             identity['dispatchProof'], state='rejected',
                             used_input=usage['input_tokens'], used_output=usage['output_tokens'],
                             used_cost=0,
                             result_hash=synopsis._hash(['synopsis-final-result-v1',
                                                         result['responseId'], result['status'],
                                                         usage, reason]),
                             trial_id=identity['trialId'], usd_estimate=estimate)
        db.execute('''UPDATE mv3_model_chunks SET provider_status=?,provider_response_id=?,
            provider_usage_json=?,usage_estimated_usd_micros=? WHERE job_id=? AND ordinal=0''',
            (result['status'], result['responseId'], synopsis._canonical(usage), estimate,
             job_id))
        jobs.recompute_parent(db, job_id, character_id, error=reason)
        db.execute("UPDATE mv3_trial_budgets SET status='stopped',error_code=? WHERE id=?",
                   (reason, identity['trialId']))
        return {'status': 'failed', 'reason': reason,
                'usageEstimatedMicrousd': estimate}


def run_task(connect, job_id, character_id, editor_id, adapter, *, diagnostic_sink=None):
    if not isinstance(adapter, responses.ResponsesAdapter):
        raise ValueError('responses_adapter_required')
    if adapter.network_enabled and not callable(diagnostic_sink):
        raise ValueError('synopsis_diagnostic_sink_required')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _job(db, job_id, character_id)
        config = payload['readSet']['realConfig']
        if config['ownerId'] != editor_id or adapter.model != config['model']:
            raise ValueError('synopsis_final_owner_or_model_mismatch')
        trial_id = config['trialId']
        trial = synopsis._trial_row(db, trial_id, character_id)
        if job['status'] == 'completed':
            row = db.execute('SELECT set_id FROM mv3_job_field_results WHERE job_id=?',
                             (job_id,)).fetchone()
            detail = metadata.read_draft(db, payload['targetId'], character_id)
            if (not row or row['set_id'] != detail['versionId'] or
                    detail['fields']['synopsis']['lifecycleStatus'] == 'stale'):
                raise ValueError('synopsis_final_result_superseded')
            return {'status': 'completed', 'fieldSetId': row['set_id'], 'reused': True}
        same, sources, parts = _current(db, payload, character_id)
        if not same:
            raise ValueError('synopsis_final_input_changed')
        chunk = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',
                           (job_id,)).fetchone()
        if chunk is None or trial['status'] not in ('prepared', 'running'):
            raise ValueError('synopsis_final_not_prepared')
        if job['status'] == 'queued' and trial['status'] == 'prepared':
            synopsis.guard_field(db, payload['targetId'], character_id, except_id=job_id)
            lease = str(uuid.uuid4())
            db.execute("UPDATE mv3_jobs SET status='running',attempts=attempts+1,"
                       "lease_token=?,lease_until=? WHERE id=? AND status='queued'",
                       (lease, time.time()+120, job_id))
        elif (job['status'] == 'running' and job['lease_until'] is not None and
              job['lease_until'] <= time.time() and chunk['state'] == 'completed' and
              chunk['output_json']):
            lease = str(uuid.uuid4())
            db.execute('UPDATE mv3_jobs SET attempts=attempts+1,lease_token=?,lease_until=? '
                       "WHERE id=? AND status='running'", (lease, time.time()+120, job_id))
        else:
            raise ValueError('synopsis_final_not_claimable')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        metadata._owner(db, editor_id)
        job, payload = _job(db, job_id, character_id)
        same, sources, parts = _current(db, payload, character_id)
        if not same or job['cancel_intent'] or not synopsis._owned_lease(job, lease):
            raise ValueError('synopsis_final_input_changed')
        row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',
                         (job_id,)).fetchone()
        if row['state'] == 'completed' and row['output_json']:
            output = synopsis.validate_integrated_final_output(
                json.loads(row['output_json']), sources, parts, max_parts=MAX_PARTS,
                qualifier_policy=_qualifier_policy(payload))
            finished = _finish(db, job, payload, output, sources, parts, lease, editor_id)
            db.execute("UPDATE mv3_trial_budgets SET status='completed' WHERE id=?",
                       (trial_id,))
            return finished
        if row['state'] != 'prepared' or trial['status'] != 'prepared':
            raise ValueError('synopsis_final_not_dispatchable')
        request = _request(payload, sources, parts)
        request_hash = synopsis._hash(request)
        if (row['input_hash'] != request_hash or row['preflight_input_tokens'] is None or
                row['preflight_input_tokens'] < 0 or
                trial['reserved_microusd'] < row['reserved_usd_micros'] or
                trial['dispatched_requests'] >= trial['max_requests']):
            raise ValueError('synopsis_final_request_changed')
        identity = processing.dispatch(db, job_id, character_id, 0,
                                       original_owner_id=editor_id)
        identity.update({'trialId': trial_id, 'outputTokenCap': MAX_OUTPUT_TOKENS})
        db.execute("UPDATE mv3_model_chunks SET provider_status='possibly_dispatched' "
                   'WHERE job_id=? AND ordinal=0', (job_id,))
        db.execute("UPDATE mv3_trial_budgets SET status='running',"
                   'dispatched_requests=dispatched_requests+1 WHERE id=?', (trial_id,))
        db.execute('UPDATE mv3_jobs SET lease_until=? WHERE id=?',
                   (time.time()+120, job_id))
    try:
        result = adapter.generate(request)
    except Exception:
        try:
            return synopsis._real_unknown(connect, trial_id, job_id, character_id, 0,
                                          identity, 'provider_result_unknown')
        except (sqlite3.DatabaseError, ValueError):
            return {'status': 'unknown', 'reason': 'provider_result_and_storage_unknown'}
    usage = result.get('usage')
    if (not result.get('responseId') or type(usage) is not dict or
            type(usage.get('input_tokens')) is not int or
            type(usage.get('output_tokens')) is not int or
            min(usage['input_tokens'], usage['output_tokens']) < 0 or
            len(synopsis._canonical(usage)) > synopsis.REAL_DIAGNOSTIC_USAGE_CAP):
        outcome = synopsis._real_unknown(connect, trial_id, job_id, character_id, 0,
                                         identity, 'provider_usage_or_result_unknown', result)
    else:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trial = synopsis._trial_row(db, trial_id, character_id)
            price = json.loads(trial['price_json'])
        estimation = responses.synopsis_usage_estimate(
            usage, price['inputUsdPerMillion'], price['outputUsdPerMillion'])
        result['_billingMode'] = estimation['billingMode']
        reason = None
        if result.get('modelId') != trial['model']:
            reason = 'provider_model_mismatch'
        elif result.get('serviceTier') != 'default':
            reason = 'provider_service_tier_mismatch'
        elif result.get('status') != 'completed':
            reason = 'provider_' + str(result.get('status'))
        elif usage['input_tokens'] > MAX_INPUT_TOKENS or usage['output_tokens'] > MAX_OUTPUT_TOKENS:
            reason = 'provider_usage_over_limit'
        output = None
        if reason is None:
            try:
                if len(result['text']) > MAX_FINAL_OUTPUT_JSON_CHARS:
                    raise ValueError('synopsis_final_json_over_limit')
                output = synopsis.validate_integrated_final_output(
                    json.loads(result['text']), sources, parts, max_parts=MAX_PARTS,
                    qualifier_policy=_qualifier_policy(payload))
                if output['text'].strip() == ''.join(part['text'].strip() for part in parts):
                    raise ValueError('synopsis_final_concat_not_integrated')
            except (ValueError, TypeError, KeyError) as exc:
                reason = 'synopsis_output_invalid:' + str(exc)[:80]
        try:
            outcome = _settle_known(connect, job_id, character_id, editor_id, identity,
                                    result, output, reason, estimation['microusd'], lease)
        except sqlite3.DatabaseError:
            try:
                outcome = _settle_after_storage_failure(connect, job_id, character_id,
                                                         editor_id, identity, result,
                                                         estimation['microusd'])
            except (sqlite3.DatabaseError, ValueError):
                outcome = {'status': 'unknown', 'reason': 'synopsis_storage_unconfirmed',
                           'usageObservedNotPersisted': True}
    if diagnostic_sink is not None and (outcome['status'] != 'completed' or
                                        result.get('status') != 'completed'):
        try:
            diagnostic_sink(synopsis._real_diagnostic(
                result, outcome, identity, job_id, 0,
                result.get('_billingMode', 'not_settled')))
            outcome['diagnosticStored'] = True
        except Exception:
            outcome['diagnosticStored'] = False
    return outcome


def cancel_task(db, job_id, character_id, editor_id):
    processing._transaction(db)
    metadata._owner(db, editor_id)
    job, payload = _job(db, job_id, character_id)
    if payload['readSet']['realConfig']['ownerId'] != editor_id:
        raise ValueError('synopsis_final_owner_binding_mismatch')
    if job['status'] == 'running':
        db.execute('UPDATE mv3_jobs SET cancel_intent=1 WHERE id=?', (job_id,))
        return 'running'
    return processing.cancel(db, job_id, character_id)


def recover_task(db, job_id, character_id, editor_id, *, now=None):
    """Only a never-dispatched prepared request may return to the queued state."""
    processing._transaction(db)
    metadata._owner(db, editor_id)
    job, payload = _job(db, job_id, character_id)
    config = payload['readSet']['realConfig']
    trial = synopsis._trial_row(db, config['trialId'], character_id)
    row = db.execute('SELECT * FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',
                     (job_id,)).fetchone()
    jobs.require_settled_for_resume(db, job)
    same, _, _ = _current(db, payload, character_id)
    if (config['ownerId'] != editor_id or not same or job['cancel_intent'] or
            job['status'] != 'running' or job['lease_until'] is None or
            job['lease_until'] > (time.time() if now is None else now) or
            row is None or row['state'] != 'prepared' or
            row['dispatch_attempt_id'] is not None or
            trial['status'] != 'prepared' or trial['dispatched_requests'] != 0):
        raise ValueError('synopsis_final_recovery_not_proven')
    synopsis.guard_field(db, payload['targetId'], character_id, except_id=job_id)
    jobs.recompute_parent(db, job_id, character_id, safe_pause=True)
    db.execute('UPDATE mv3_jobs SET lease_token=NULL,lease_until=NULL WHERE id=?',
               (job_id,))
    return processing.resume(db, job_id, character_id, payload['readSet'])
