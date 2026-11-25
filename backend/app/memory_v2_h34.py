"""Isolated H5-B H3.4 title confirmation. Not installed by the formal backend."""

import json

from . import memory_v2_import as importer
from . import memory_v2_metadata as metadata
from . import memory_v2_metadata_contract as contract
from . import memory_v2_metadata_search as search
from .memory_v2_derivation import _now


def init_db(db):
    db.execute("""CREATE TABLE IF NOT EXISTS mv3_title_confirmation_batches(
        id TEXT PRIMARY KEY,
        character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL REFERENCES mv2_import_batches(id),
        file_id TEXT NOT NULL REFERENCES mv2_import_files(id),
        payload_hash TEXT NOT NULL,
        plan_hash TEXT NOT NULL,
        source_snapshot_hash TEXT NOT NULL,
        result_json TEXT NOT NULL,
        reviewer_id INTEGER NOT NULL REFERENCES users(id),
        reviewed_at TEXT NOT NULL)""")


def confirm_titles(db, character_id, batch_id, file_id, *, batch_key,
                   expected_file_sha, expected_parser, expected_source_snapshot,
                   expected_plan_hash, items, reviewer_id):
    """Confirm exactly the 13 H3.2 titles inside a caller-owned IMMEDIATE transaction.

    The trusted API supplies reviewer_id from its authenticated owner session.
    A caller must never take this identity from the JSON request.
    """
    if not db.in_transaction:
        raise ValueError('title_confirmation_transaction_required')
    metadata._owner(db, reviewer_id)
    if (not isinstance(batch_key, str) or not 1 <= len(batch_key) <= 120
            or not isinstance(items, list) or len(items) != 13):
        raise ValueError('title_confirmation_input_invalid')
    file, records, snapshot = importer.confirmed_file_snapshot(
        db, batch_id, character_id, file_id, expected_file_sha, expected_parser)
    if snapshot != expected_source_snapshot:
        raise ValueError('title_confirmation_source_changed')
    plan = db.execute("""SELECT * FROM mv2_import_boundary_confirmations
        WHERE file_id=? AND batch_id=? AND character_id=?""",
        (file_id, batch_id, character_id)).fetchone()
    if (plan is None or plan['source_snapshot_hash'] != snapshot
            or plan['plan_hash'] != expected_plan_hash):
        raise ValueError('title_confirmation_plan_changed')
    segments = db.execute("""SELECT * FROM mv2_import_segments
        WHERE file_id=? AND batch_id=? ORDER BY ordinal""", (file_id, batch_id)).fetchall()
    if (len(segments) != 13 or [r['ordinal'] for r in segments] != list(range(1, 14))
            or segments[0]['start_ordinal'] != 1 or segments[-1]['end_ordinal'] != 130
            or any(a['end_ordinal'] + 1 != b['start_ordinal']
                   for a, b in zip(segments, segments[1:]))):
        raise ValueError('title_confirmation_boundaries_changed')
    if any(not isinstance(item, dict) or set(item) != {
            'entryId', 'title', 'expectedVersion', 'expectedFieldId', 'expectedFieldToken'}
            for item in items):
        raise ValueError('title_confirmation_input_invalid')
    if any(item['entryId'] != segment['entry_id'] or not isinstance(item['title'], str)
           or not 1 <= len(item['title'].strip()) <= 200
           for item, segment in zip(items, segments)):
        raise ValueError('title_confirmation_entries_changed')
    request_hash = contract.token([character_id, batch_id, file_id, expected_file_sha.lower(),
        expected_parser, snapshot, expected_plan_hash,
        [(item['entryId'], item['title']) for item in items]])
    prior = db.execute('SELECT * FROM mv3_title_confirmation_batches WHERE id=?',
                       (batch_key,)).fetchone()
    if prior:
        if (prior['character_id'] != character_id or prior['batch_id'] != batch_id
                or prior['file_id'] != file_id or prior['payload_hash'] != request_hash
                or prior['plan_hash'] != expected_plan_hash
                or prior['source_snapshot_hash'] != snapshot
                or prior['reviewer_id'] != reviewer_id):
            raise ValueError('title_confirmation_idempotency_conflict')
        result = json.loads(prior['result_json'])
        for item in result:
            detail = metadata.read_draft(db, item['entryId'], character_id)
            title = detail['fields']['title']
            check = db.execute('SELECT * FROM mv3_entry_field_checks WHERE field_id=?',
                               (item['fieldId'],)).fetchone()
            if (detail['versionId'] != item['versionId'] or title['value'] != item['title']
                    or title['fieldId'] != item['fieldId'] or title['token'] != item['token']
                    or title['lifecycleStatus'] != 'available' or check is None
                    or check['binding'] != item['token'] or check['method'] != 'owner'
                    or check['reviewer_id'] != reviewer_id):
                raise ValueError('title_confirmation_result_changed')
        return {'batchKey': batch_key, 'reused': True, 'items': result}

    # Finish *all* identity and version checks before the first field revision.
    current = []
    for item, segment in zip(items, segments):
        detail = metadata.read_draft(db, item['entryId'], character_id)
        title = detail['fields']['title']
        refs = detail['sourceRefs']
        expected = records[segment['start_ordinal'] - 1:segment['end_ordinal']]
        field = db.execute('SELECT * FROM mv3_entry_fields WHERE id=?',
                           (title.get('fieldId'),)).fetchone()
        check = db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',
                           (title.get('fieldId'),)).fetchone()
        if (detail['versionId'] != item['expectedVersion']
                or title.get('fieldId') != item['expectedFieldId']
                or title.get('token') != item['expectedFieldToken']
                or title['value'] != segment['title']
                or title['lifecycleStatus'] != 'draft'
                or field is None or field['provenance'] != 'system_suggested'
                or field['locked'] or check is not None
                or [r['source_id'] for r in refs] != [r['messageId'] for r in expected]
                or any(r['content_hash'] != e['contentHash']
                       or r['state_hash'] != e['stateHash']
                       for r, e in zip(refs, expected))):
            raise ValueError('title_confirmation_baseline_changed')
        current.append(detail)
    if sum(item['title'] != segment['title'] for item, segment in zip(items, segments)) != 8:
        raise ValueError('title_confirmation_change_count_invalid')

    search.init_db(db)
    result = []
    for item, detail, segment in zip(items, current, segments):
        changed = item['title'] != segment['title']
        if changed:
            detail = metadata.revise(db, item['entryId'], character_id,
                item['expectedVersion'], {'title': item['title']}, reviewer_id,
                provenance='user_corrected', rule='h34-owner-title-v1')
        detail = metadata.review_field(db, item['entryId'], character_id,
            'title', detail['fields']['title']['token'], reviewer_id)
        title = detail['fields']['title']
        if (title['value'] != item['title'] or title['lifecycleStatus'] != 'available'
                or title['reviewStatus'] != 'human_checked'
                or detail['qualifications']['candidateRecall']
                or detail['qualifications']['formalContext']):
            raise ValueError('title_confirmation_publication_boundary')
        search.rebuild(db, item['entryId'], character_id)
        result.append({'entryId': item['entryId'], 'title': item['title'],
            'changed': changed, 'versionId': detail['versionId'],
            'fieldId': title['fieldId'], 'token': title['token']})
    db.execute("""INSERT INTO mv3_title_confirmation_batches
        (id,character_id,batch_id,file_id,payload_hash,plan_hash,source_snapshot_hash,
         result_json,reviewer_id,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (batch_key, character_id, batch_id, file_id, request_hash, expected_plan_hash,
         snapshot, json.dumps(result, ensure_ascii=False, separators=(',', ':')),
         reviewer_id, _now()))
    return {'batchKey': batch_key, 'reused': False, 'items': result}
