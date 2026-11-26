"""One live-source validator for M1 archive reads and M2 recall."""

from . import memory_v2_sources as adapters
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_validation = ContextVar('memory_read_validation', default=None)


@contextmanager
def validation_scope(db):
    current = _validation.get()
    if current is not None and current['db'] is db:
        yield
        return
    token = _validation.set({'db': db, 'stamp': None, 'sources': {}})
    try:
        yield
    finally:
        _validation.reset(token)


def validated_read(function):
    @wraps(function)
    def read(db, *args, **kwargs):
        with validation_scope(db):
            return function(db, *args, **kwargs)
    return read


def source_cache(db):
    current = _validation.get()
    if current is None or current['db'] is not db:
        return None
    stamp = (db.total_changes, db.execute('PRAGMA data_version').fetchone()[0])
    if current['stamp'] != stamp:
        current['sources'].clear()
        current['stamp'] = stamp
    return current['sources']


def valid_source(db, indexed, character_id, file_cache=None):
    if indexed is None or indexed["character_id"] != character_id:
        return None
    if "archive_file_id" not in indexed.keys() or "archive_ordinal" not in indexed.keys():
        return None  # A v1 index cannot establish the current order contract.
    live = adapters.source(db, indexed["source_kind"], indexed["source_id"], file_cache)
    if (live is None or live.character_id != character_id or live.content_hash != indexed["content_hash"]
            or live.state_hash != indexed["state_hash"] or live.status != indexed["source_status"]
            or live.archive_file_id != indexed["archive_file_id"]
            or live.archive_ordinal != indexed["archive_ordinal"]):
        return None
    return live
