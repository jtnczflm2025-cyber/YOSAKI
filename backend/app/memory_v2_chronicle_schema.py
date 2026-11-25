"""Explicit additive installation. Call only after backup and maintenance approval."""
def install(db):
    if db.in_transaction: raise ValueError('migration_requires_idle_connection')
    from . import memory_v2_derivation_schema, memory_v2_import_schema, memory_v2_metadata
    from . import memory_v2_jobs, memory_v2_metadata_jobs, memory_v2_time_projection
    from . import memory_v2_chronicle_curation, memory_v2_chronicle_vectors, memory_v2_chronicle_dates
    from . import memory_v2_m36_trial, memory_v2_processing_schema, memory_v2_tag_schema
    from . import memory_v2_lexical_schema, memory_v2_temporal_digest_schema
    # Initializers are additive and transactional. The existing migration
    # modules each own their explicit transaction and validation contract.
    try:
        db.execute('BEGIN IMMEDIATE')
        for module in (memory_v2_derivation_schema,memory_v2_import_schema,memory_v2_metadata,
                       memory_v2_jobs,memory_v2_metadata_jobs,memory_v2_time_projection,
                       memory_v2_chronicle_curation,memory_v2_chronicle_vectors,
                       memory_v2_chronicle_dates,memory_v2_m36_trial):
            module.init_db(db)
        db.commit()
        for module in (memory_v2_processing_schema,memory_v2_tag_schema,
                       memory_v2_lexical_schema,memory_v2_temporal_digest_schema):
            module.migrate(db)
    except Exception:
        db.rollback()
        raise
    # A partially installed schema never passes the startup gate. Previously
    # committed additive phases stay visible for diagnosis, not silent use.
    from .memory_v2_chronicle_gate import verify
    return verify(db)
