"""Event-driven Chronicle upkeep using existing durable A3 tasks.

No paid dispatch is inferred from a page read or from local maintenance consent.
Each pass is restartable; uncertain requests retain their existing scope blocks.
"""
from datetime import datetime, timezone
from . import memory_v2_processing as processing, memory_v2_metadata as metadata
from . import memory_v2_chronicle_channel as channel


def reconcile_period_plans(db, cid, owner_id, *, now):
    """Retire obsolete plans before dispatch and refresh current period needs."""
    from . import memory_v2_temporal_digest as temporal

    processing._transaction(db)
    channel.authorized(db, cid, owner_id)
    plans = db.execute('''SELECT j.id FROM mv3_jobs j
        JOIN mv3_trial_budgets b ON b.id=json_extract(j.read_set_json,'$.realConfig.trialId')
        WHERE j.character_id=? AND j.kind='A3_PROCESSING' AND j.status='queued'
          AND json_extract(j.payload_json,'$.executionKind')='responses_temporal_digest_v1'
          AND json_extract(j.read_set_json,'$.realConfig.ownerId')=?
          AND b.character_id=j.character_id AND b.status='created'
          AND b.dispatched_requests=0 AND b.reserved_microusd=0
          AND b.spent_estimate_microusd=0
          AND NOT EXISTS (SELECT 1 FROM mv3_model_chunks c WHERE c.job_id=j.id)
        ORDER BY j.id LIMIT 101''', (cid, owner_id)).fetchall()
    if len(plans) > 100:
        raise ValueError('period_maintenance_capacity')
    retired = []
    for row in plans:
        _, payload = temporal._job(db, row['id'], cid)
        if temporal._same_inputs(db, payload, cid)[0]:
            continue
        processing.cancel(db, row['id'], cid)
        db.execute("UPDATE mv3_trial_budgets SET status='stopped', error_code='period_input_changed' WHERE id=?",
                   (payload['readSet']['realConfig']['trialId'],))
        retired.append(row['id'])
    return temporal.maintenance_plan(db, cid, now=now), retired


def prepare_entry(connect, cid, owner_id, eid, *, missing_only=False):
    """Finish local draft preparation before an import opens its editor."""
    try:
        diagnostics=[]
        for name in ('category','tag','keyword'):
            try:
                with connect() as db:
                    processing._transaction(db)
                    channel.authorized(db,cid,owner_id)
                    published=db.execute('''SELECT 1 FROM mv3_entries WHERE id=?
                        AND character_id=? AND status='active' AND active_version_id=draft_version_id''',
                        (eid,cid)).fetchone()
                    if published:
                        diagnostics.append({'field':name,'status':'published_version_preserved'})
                        continue
                    from . import memory_v2_import_filing as filing
                    field_name={'category':'categories','tag':'tags','keyword':'keywords'}[name]
                    stored= db.execute('''SELECT f.rule_version FROM mv3_entries e
                        JOIN mv3_entry_field_links l ON l.set_id=e.draft_version_id AND l.name=?
                        JOIN mv3_entry_fields f ON f.id=l.field_id WHERE e.character_id=? AND e.id=?''',
                        (field_name,cid,eid)).fetchone()
                    if stored and stored[0]==filing.VERSION:
                        diagnostics.append({'field':name,'status':'import_suggestion_preserved'})
                        continue
                    if missing_only:
                        current=metadata.read_draft(db,eid,cid)
                        field={'category':'categories','tag':'tags','keyword':'keywords'}[name]
                        if current['fields'][field].get('fieldId'):
                            diagnostics.append({'field':name,'status':'existing_field_preserved'})
                            continue
                    if name=='keyword':
                        # Automatic upkeep initializes missing fields only.
                        # Never bulk-rewrite existing historical candidates.
                        current=metadata.read_draft(db,eid,cid)
                        if metadata._fields(db,current['versionId']).get('keywords') is not None:
                            diagnostics.append({'field':name,'status':'existing_keywords_preserved'})
                            continue
                        from . import memory_v2_keyword_rules as keyword_rules
                        jid=processing.create_keyword_job(db,eid,cid,owner_id,rule_version=keyword_rules.SELECTION_RULE_VERSION)
                    else:jid=getattr(processing,f'create_{name}_job')(db,eid,cid,owner_id)
                    job=processing._owned(db,jid,cid)
                    if job['status']=='paused':
                        getattr(processing,f'recover_{name}_job')(db,jid,cid,owner_id)
                result=getattr(processing,f'run_{name}_job')(connect,jid,cid,owner_id)
                diagnostics.append({'field':name,'status':result.get('status')})
            except ValueError as exc:
                diagnostics.append({'field':name,'status':'blocked','reason':str(exc)})
        # Independent fields may be unavailable; they never turn absence of
        # a vocabulary into a reason to discard the complete dialogue.
        with connect() as db:
            db.execute('BEGIN IMMEDIATE');channel.authorized(db,cid,owner_id)
            from .memory_v2_chronicle_workspace import draft_view
            from . import memory_v2_dialogue_refinement as finite
            view=draft_view(db,cid,eid)
            # Existing manual/locked/null values are authoritative decisions.
            stored=metadata._fields(db,view['entry']['versionId']).get('narrative')
            protected=stored and (stored['locked'] or stored['provenance'] in ('manual','user_corrected') or db.execute('SELECT 1 FROM mv3_entry_field_checks WHERE field_id=?',(stored['id'],)).fetchone())
            if view['narrative'] is None and not protected:
                finite.create(db,cid,owner_id,eid,view['entry']['versionId'])
            return {'entryId':eid,'status':'prepared','synopsis':view['entry']['fields']['synopsis']['lifecycleStatus'],'metadata':diagnostics}
    except ValueError as exc:
        return {'entryId':eid,'status':'blocked','reason':str(exc)}


def maintain(connect, cid, owner_id, *, limit=100, approved_period_job=None, period_adapter=None):
    if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('maintenance_limit')
    if (approved_period_job is None) != (period_adapter is None):
        raise ValueError('period_maintenance_approval_required')
    with connect() as db:
        channel.authorized(db,cid,owner_id)
        entries=db.execute("SELECT id FROM mv3_entries WHERE character_id=? AND contract_version=2 AND status NOT IN ('archived','superseded') ORDER BY id LIMIT ?",(cid,limit+1)).fetchall()
        if len(entries)>limit: raise ValueError('maintenance_capacity')
    results = [prepare_entry(connect, cid, owner_id, entry['id']) for entry in entries]
    from . import memory_v2_lexical as lexical
    try:
        lexical.rebuild(connect,cid,owner_id,reuse_if_current=True)
        index='current'
    except ValueError as exc:
        index=str(exc)
    retired = []
    try:
        with connect() as db:
            periods, retired = reconcile_period_plans(db, cid, owner_id, now=datetime.now(timezone.utc))
    except ValueError as exc:
        periods = {'status': 'blocked', 'reason': str(exc)}
    paid_result = None
    if approved_period_job is not None:
        from . import memory_v2_chronicle_digest as digest

        # Workspace startup and write hooks can hand off an explicitly approved
        # one-shot job. The coordinator still owns the current claim and budget.
        with connect() as db:
            channel.authorized(db, cid, owner_id)
            job, payload = digest.temporal._job(db, approved_period_job, cid)
            config = payload['readSet'].get('realConfig', {})
            digest.validate_config(db, cid, owner_id, config)
            if job['status'] != 'queued' or not isinstance(periods, list) or not any(
                period['nodeId'] == payload['readSet']['manifest']['nodeId']
                and period['status'] != 'available' for period in periods
            ):
                raise ValueError('period_maintenance_not_needed')
        paid_result = digest.run(connect, approved_period_job, cid, owner_id, period_adapter)
    return {'entries':results,'index':index,'periodMaintenance':periods,
            'retiredPeriodPlans':retired,
            'periodResult':paid_result,
            'providerRequests':0 if paid_result is None else None,'automaticApproval':False}
