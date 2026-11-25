"""Owner's entry-level date correction; original RAW/receipt times stay intact."""
import json
import uuid
from datetime import date, datetime
from . import memory_v2_metadata as metadata, memory_v2_derivation as m3


def init_db(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mv3_chronicle_dates(
        set_id TEXT PRIMARY KEY REFERENCES mv3_entry_field_sets(id) ON DELETE CASCADE,
        entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
        source_token TEXT NOT NULL, owner_id INTEGER NOT NULL REFERENCES users(id),
        occurred_date TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS ix_chronicle_dates_entry ON mv3_chronicle_dates(entry_id,set_id)')


def _calendar_day(item):
    # Preserve the receipt and the field-set evidence. Only the date projection
    # uses the source's calendar day; the clock has no known UTC offset.
    if item['precision'] != 'local_datetime_unverified' or item.get('occurredAt'):
        return item
    try:
        stamp = datetime.fromisoformat(item['rawTime'])
    except (TypeError, ValueError):
        return item
    if stamp.tzinfo is not None or stamp.date().isoformat() != item.get('occurredDate'):
        return item
    return dict(item, precision='day', sourcePrecision=item['precision'],
                timezoneBasis='source_local_day_unverified_zone')


def corrected(db, entry, version, original):
    calendar = [_calendar_day(item) for item in original]
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_chronicle_dates'").fetchone():
        return calendar
    row = db.execute('''SELECT d.* FROM mv3_chronicle_dates d JOIN mv3_entry_field_sets s ON s.id=d.set_id
        WHERE d.entry_id=? AND s.version<=? ORDER BY s.version DESC LIMIT 1''',
        (entry['id'], version['version'])).fetchone()
    if row is None or row['source_token'] != version['source_token']:
        return calendar
    return [dict(item, occurredAt=None, occurredDate=row['occurred_date'],
        precision='day' if row['occurred_date'] else 'unknown',
        timezoneBasis='owner_entry_date', correctionVersion=row['set_id']) for item in original]


def save(db, cid, owner_id, eid, expected_version, occurred_date, reason):
    if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
    metadata._owner(db, owner_id)
    detail = metadata.read_draft(db, eid, cid)
    if detail['versionId'] != expected_version: raise ValueError('version_conflict')
    if occurred_date is not None:
        if not isinstance(occurred_date, str) or date.fromisoformat(occurred_date).isoformat() != occurred_date:
            raise ValueError('date_invalid')
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise ValueError('date_reason_required')
    prior = db.execute('SELECT * FROM mv3_entry_field_sets WHERE id=?', (expected_version,)).fetchone()
    sid = str(uuid.uuid4())
    db.execute('INSERT INTO mv3_entry_field_sets VALUES(?,?,?,?,?,?,?)',
        (sid,eid,prior['version']+1,prior['sources_json'],prior['source_token'],prior['time_evidence_json'],m3._now()))
    db.execute('INSERT INTO mv3_entry_field_links SELECT ?,name,field_id FROM mv3_entry_field_links WHERE set_id=?', (sid,expected_version))
    db.execute('INSERT INTO mv3_chronicle_dates VALUES(?,?,?,?,?,?,?)',
        (sid,eid,prior['source_token'],owner_id,occurred_date,reason.strip(),m3._now()))
    changed = db.execute('UPDATE mv3_entries SET draft_version_id=? WHERE id=? AND draft_version_id=?',
                        (sid,eid,expected_version)).rowcount
    if changed != 1: raise ValueError('version_conflict')
    # Prior publication is not silently transferred to a changed date/range.
    return metadata.read_draft(db,eid,cid)
