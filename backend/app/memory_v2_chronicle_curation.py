"""Four independent Owner choices; no automatic importance-to-permanent promotion.

Explicit additive migration only. Revisions preserve the previous choices.
"""
from . import memory_v2_chronicle_channel as channel, memory_v2_derivation as m3
from . import memory_v2_metadata as metadata

FLAGS = ('important', 'foundational', 'permanent', 'fixed')


def init_db(db):
    db.execute('''CREATE TABLE IF NOT EXISTS mv3_chronicle_curation(
        entry_id TEXT NOT NULL REFERENCES mv3_entries(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL CHECK(revision>0),
        character_id INTEGER NOT NULL REFERENCES characters(id),
        owner_id INTEGER NOT NULL REFERENCES users(id),
        important INTEGER NOT NULL CHECK(important IN(0,1)),
        foundational INTEGER NOT NULL CHECK(foundational IN(0,1)),
        permanent INTEGER NOT NULL CHECK(permanent IN(0,1)),
        fixed INTEGER NOT NULL CHECK(fixed IN(0,1)),
        reason TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(entry_id,revision))''')
    db.execute('CREATE INDEX IF NOT EXISTS ix_chronicle_curation_role ON mv3_chronicle_curation(character_id,entry_id,revision)')


def read(db, character_id, owner_id, entry_id):
    channel.authorized(db, character_id, owner_id)
    metadata.read_draft(db, entry_id, character_id)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_chronicle_curation'").fetchone():
        return dict(revision=0, installed=False, reason='', **dict.fromkeys(FLAGS, False))
    row = db.execute('SELECT * FROM mv3_chronicle_curation WHERE character_id=? AND entry_id=? ORDER BY revision DESC LIMIT 1',
                     (character_id, entry_id)).fetchone()
    return (dict(revision=row['revision'], installed=True, reason=row['reason'], **{k: bool(row[k]) for k in FLAGS})
            if row else dict(revision=0, installed=True, reason='', **dict.fromkeys(FLAGS, False)))


def save(db, character_id, owner_id, entry_id, expected_revision, flags, reason):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    if set(flags) != set(FLAGS) or any(type(x) is not bool for x in flags.values()):
        raise ValueError('curation_choices_invalid')
    if not isinstance(reason, str) or len(reason) > 1000:
        raise ValueError('curation_reason_invalid')
    current = read(db, character_id, owner_id, entry_id)
    if not current['installed']:
        raise ValueError('curation_migration_required')
    if current['revision'] != expected_revision:
        raise ValueError('curation_version_changed')
    if all(current[k] == flags[k] for k in FLAGS) and current['reason'] == reason:
        return current
    db.execute('INSERT INTO mv3_chronicle_curation VALUES(?,?,?,?,?,?,?,?,?,?)',
        (entry_id, expected_revision+1, character_id, owner_id, *(int(flags[k]) for k in FLAGS), reason, m3._now()))
    return read(db, character_id, owner_id, entry_id)


def fixed_entries(db, character_id, owner_id):
    channel.authorized(db, character_id, owner_id)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mv3_chronicle_curation'").fetchone():
        return []
    return [r['entry_id'] for r in db.execute('''SELECT c.entry_id FROM mv3_chronicle_curation c
        WHERE c.character_id=? AND (c.permanent=1 OR c.fixed=1)
        AND c.revision=(SELECT MAX(x.revision) FROM mv3_chronicle_curation x WHERE x.entry_id=c.entry_id)
        ORDER BY c.entry_id LIMIT 51''', (character_id,))]
