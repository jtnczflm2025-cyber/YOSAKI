"""Install the existing Chronicle manager plus small explicit editing actions.

No implicit migrations, Provider calls, automatic reviews or alternate authority.
"""
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from . import memory_v2_metadata as metadata, memory_v2_derivation as m3
from . import memory_v2_dialogue_refinement as finite
from . import memory_v2_chronicle_channel as channel
from . import memory_v2_chronicle_curation as curation


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int = Field(gt=0)
    expectedVersion: str


class Generation(Input):
    modelId: int = Field(gt=0)
    approveMaxMicrousd: int = Field(ge=1, le=147456)
    generationVersion: Literal['legacy', 'stream-v4'] = 'legacy'
    outputTokens: Literal[6144, 8192] = 6144
    materialHash: str | None = None


class CalendarAuthority(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    characterId:int=Field(gt=0)
    days:int=Field(ge=1,le=365)
    perTask:int=Field(gt=0,le=1000000)
    perDay:int=Field(gt=0,le=10**12)
    total:int=Field(gt=0,le=10**12)
    recall:bool
    futurePublished:bool


class IndexScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int = Field(gt=0)
    entryIds: list[UUID] = Field(min_length=1,max_length=50)


class IndexApproval(IndexScope):
    modelId: int = Field(gt=0)
    planHash: str
    approvalId: UUID
    approveMaxMicrousd: int = Field(ge=1,le=8200)


class PeriodGeneration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int = Field(gt=0)
    modelId: int = Field(gt=0)
    nodeId: str
    asOf: str
    approveMaxMicrousd: int = Field(ge=1,le=122880)
    queueForMaintenance: bool = False


class PeriodDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int = Field(gt=0)
    inputHash: str
    outputHash: str


class SemanticSearch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int = Field(gt=0)
    modelId: int = Field(gt=0)
    query: str = Field(min_length=1,max_length=1000)
    approveMaxMicrousd: int = Field(ge=164,le=164)


class SemanticSupplement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int = Field(gt=0)
    modelId: int = Field(gt=0)
    requestId: UUID
    query: str = Field(min_length=1, max_length=100)
    asOf: str = Field(max_length=40)
    fromDate: str | None = Field(default=None, max_length=10)
    toDate: str | None = Field(default=None, max_length=10)
    approveMaxMicrousd: int


class NarrativeInput(Input):
    # None means the user observed no existing refinement, not "ignore CAS".
    expectedNarrativeVersionId: str | None = None


class Edit(NarrativeInput):
    body: str = Field(min_length=1, max_length=200000)


class DateCorrection(Input):
    occurredDate: str | None
    reason: str = Field(min_length=1, max_length=1000)


class Confirm(Input):
    versionId: str
    inputHash: str
    outputHash: str


class Curation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int
    expectedRevision: int
    important: bool
    foundational: bool
    permanent: bool
    fixed: bool
    reason: str = Field(default='', max_length=1000)


class Boundary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    characterId: int
    sourceSnapshot: str
    segments: list[dict] = Field(min_length=1, max_length=500)


class Labels(Input):
    categories: list[str] = Field(max_length=32)
    tags: list[str] = Field(max_length=32)
    keywords: list[str] = Field(max_length=32)
    sourceOrdinal: int = Field(ge=0)


def refs_for(detail):
    return [{'kind': r['source_kind'], 'id': r['source_id']} for r in detail['sourceRefs']]


def _draft_narrative(db, character_id, detail):
    refs = refs_for(detail)
    # Lookup is read-only; viewing must never create a derivation.
    from .memory_v2_contract import SourceRef, experience_key
    key = experience_key(character_id, tuple(SourceRef(r['kind'], r['id']) for r in refs))
    candidate = db.execute('''SELECT v.* FROM mv3_versions v JOIN mv3_derivations d ON d.id=v.derivation_id
        WHERE d.character_id=? AND d.experience_key=? AND d.view_type='NARRATIVE'
        ORDER BY v.version DESC LIMIT 1''', (character_id, key)).fetchone()
    if candidate and m3._validity(db, candidate['id'], character_id, set(), draft=True) is None:
        return candidate
    return None


def _check_narrative_snapshot(db, character_id, detail, expected):
    candidate = _draft_narrative(db, character_id, detail)
    if (candidate['id'] if candidate else None) != expected:
        raise ValueError('narrative_snapshot_changed')
    return candidate


def draft_view(db, character_id, entry_id):
    detail = metadata.read_draft(db, entry_id, character_id)
    raw = []
    for ref in refs_for(detail):
        source = m3.live_source(db, character_id, ref['kind'], ref['id'])
        if source is None:
            raise ValueError('source_changed')
        raw.append(dict(**ref, speaker=source.role, text=source.text))
    candidate = _draft_narrative(db, character_id, detail)
    narrative = ({k: candidate[k] for k in ('id', 'body', 'input_hash', 'output_hash', 'status', 'review_status')}
                 if candidate else None)
    return {'entry': detail, 'raw': raw, 'narrative': narrative}


def confirm_narrative(db, entry_id, payload, owner_id):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    detail = metadata.read_draft(db, entry_id, payload.characterId)
    if detail['versionId'] != payload.expectedVersion:
        raise ValueError('version_conflict')
    version = m3._row(db, 'mv3_versions', payload.versionId)
    if version is None:
        raise ValueError('version_missing')
    ref_keys = [(r['source_kind'], r['source_id']) for r in m3._refs(db, payload.versionId)]
    if ref_keys != [(r['kind'], r['id']) for r in refs_for(detail)]:
        raise ValueError('narrative_entry_mismatch')
    owner = m3._row(db, 'mv3_derivations', version['derivation_id'])
    if owner['character_id'] != payload.characterId or owner['view_type'] != 'NARRATIVE':
        raise ValueError('narrative_entry_mismatch')
    if version['input_hash'] != payload.inputHash or version['output_hash'] != payload.outputHash:
        raise ValueError('review_snapshot_changed')
    _check_narrative_snapshot(db, payload.characterId, detail, payload.versionId)
    linked = detail['fields']['narrative']
    if (linked['value'] == payload.versionId and linked['lifecycleStatus'] == 'available'):
        m3.read_version(db, version['id'], payload.characterId)
        return draft_view(db, payload.characterId, entry_id)
    if version['status'] == 'needs_review':
        m3.review_version(db, version['id'], payload.characterId, owner_id, payload.outputHash, payload.inputHash)
        m3.activate_version(db, version['id'], payload.characterId)
    else:
        m3.read_version(db, version['id'], payload.characterId)
    # Explicitly requested field linkage is a NEW revision and new check;
    # never inherits a previous field's review or publishes the entry.
    updated = metadata.revise(db, entry_id, payload.characterId, detail['versionId'],
                              {'narrative': version['id']}, owner_id)
    metadata.review_field(db, entry_id, payload.characterId, 'narrative',
                          updated['fields']['narrative']['token'], owner_id)
    return draft_view(db, payload.characterId, entry_id)


class ReviewSelection(Input):
    previewToken: str = Field(min_length=64, max_length=64)


def review_card(db, cid, eid):
    entry = metadata.read_draft(db, eid, cid)
    fields = entry['fields']
    narrative = _draft_narrative(db, cid, entry)
    days = sorted({x['occurredDate'] for x in entry['timeEvidence'] if x.get('occurredDate')})
    reasons = []
    for name, label in [('title', '标题'), ('synopsis', '梗概')]:
        if not fields[name].get('value'):
            reasons.append(label + '缺失或已失效')
    if not narrative:
        reasons.append('完整精修缺失或已失效')
    if any(f['lifecycleStatus']=='stale' for f in fields.values()):
        reasons.append('存在已失效字段')
    published = entry['qualifications']['p2Projection']
    signature = metadata.c.token([cid, eid, entry['versionId'], entry['sourceToken'],
        entry['timeToken'], [(k, f.get('token'), f['lifecycleStatus']) for k,f in sorted(fields.items())],
        [narrative[k] for k in ('id','input_hash','output_hash','status')] if narrative else None, published])
    return dict(entryId=eid, version=entry['versionId'], token=signature,
        title=fields['title'].get('value'), synopsis=fields['synopsis'].get('value'),
        dates=days, dateBases=sorted({x['timezoneBasis'] for x in entry['timeEvidence']}),
        narrativeReady=bool(narrative), narrativeId=narrative['id'] if narrative else None, published=published, reasons=reasons,
        labels={k:fields[k].get('value') for k in ('categories','tags','keywords')})


def publish_selected(db, eid, payload, owner_id):
    cid = payload.characterId
    card = review_card(db, cid, eid)
    if card['version'] != payload.expectedVersion or card['token'] != payload.previewToken:
        raise ValueError('预览后来源、内容或确认状态已变化，请重新预览')
    if card['published'] or card['reasons']:
        return dict(status='skipped', reason='已发布' if card['published'] else '；'.join(card['reasons']))
    detail = metadata.read_draft(db, eid, cid)
    n = _draft_narrative(db, cid, detail)
    detail = confirm_narrative(db, eid, Confirm(characterId=cid, expectedVersion=card['version'],
        versionId=n['id'], inputHash=n['input_hash'], outputHash=n['output_hash']), owner_id)['entry']
    for name in ('title','synopsis','categories','tags','keywords'):
        field = detail['fields'][name]
        if field.get('value') and field['lifecycleStatus'] != 'available':
            detail = metadata.review_field(db,eid,cid,name,field['token'],owner_id)
    metadata.publish(db,eid,cid,detail['versionId'],owner_id)
    return dict(status='published', version=detail['versionId'])


def install(app, connect, authenticate, *, allowed_origins=(), provider_factory=None, embedding_factory=None,
            diagnostic_sink=None, semantic_factories=None, rate_status=None, calendar_sync=None):
    from . import memory_v2_metadata_api, memory_v2_calendar_api, memory_v2_import_api, memory_v2_time_api
    memory_v2_metadata_api.install(app, connect, authenticate, allowed_origins=allowed_origins)
    memory_v2_calendar_api.install(app, connect, authenticate)
    memory_v2_time_api.install(app, connect, authenticate)
    memory_v2_import_api.install(app, connect, authenticate, allowed_origins=allowed_origins)

    def identity(request: Request, response: Response):
        user = authenticate(request)
        if not user:
            raise HTTPException(401, 'authentication_required')
        if request.method != 'GET' and request.headers.get('origin'):
            if request.headers['origin'].rstrip('/') not in {*allowed_origins, str(request.base_url).rstrip('/')}:
                raise HTTPException(403, 'origin_rejected')
        response.headers['Cache-Control'] = 'no-store'
        return user

    router = APIRouter(prefix='/api/memory-v2/workspace')

    def paid_availability(installed, disabled_reason):
        # The same trusted rate snapshot used by the actual provider factory.
        # Legacy/mock installers without this hook retain their old contract.
        rates = rate_status() if rate_status else {'ratesCurrent': True}
        reason = disabled_reason if not installed else (
            None if rates['ratesCurrent'] else 'chronicle_daily_rates_verification_required')
        return {**rates, 'enabled': reason is None, 'reason': reason}

    @router.get('/calendar-summaries')
    def calendar_summaries(characterId:int,user=Depends(identity)):
        if calendar_sync is None:raise HTTPException(409,'calendar_sync_not_installed')
        try:return calendar_sync.view(characterId,user['id'])
        except ValueError as exc:raise HTTPException(403,str(exc)) from None

    @router.post('/calendar-summaries/consent')
    def calendar_consent(characterId:int,enabled:bool,user=Depends(identity)):
        if calendar_sync is None:raise HTTPException(409,'calendar_sync_not_installed')
        try:calendar_sync.toggle(characterId,user['id'],enabled)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        return {'enabled':enabled}

    @router.post('/calendar-summaries/authorize')
    def calendar_authorize(body:CalendarAuthority,user=Depends(identity)):
        if calendar_sync is None:raise HTTPException(409,'calendar_sync_not_installed')
        try:
            calendar_sync.configure(body.characterId,user['id'],days=body.days,per_task=body.perTask,
                per_day=body.perDay,total=body.total,recall=body.recall,future=body.futurePublished)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        return {'enabled':True}

    @router.post('/calendar-summaries/revoke')
    def calendar_revoke(characterId:int,user=Depends(identity)):
        if calendar_sync is None:raise HTTPException(409,'calendar_sync_not_installed')
        try:calendar_sync.toggle(characterId,user['id'],False,revoke=True)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        return {'enabled':False}

    @router.get('/generation-status')
    def generation_status(user=Depends(identity)):
        return {**paid_availability(provider_factory is not None, 'paid_generation_not_enabled'),'model':'gpt-6.1-sol',
                'maxMicrousd':122880,'inputTokens':8192,'outputTokens':6144,
                'automaticApproval':False,
                'streamCapacities':[{'outputTokens':6144,'maxMicrousd':122880},
                                    {'outputTokens':8192,'maxMicrousd':147456}]}

    @router.get('/entries/{entry_id}/synopsis-material')
    def synopsis_material(entry_id:str,characterId:int,outputTokens:int=6144,user=Depends(identity)):
        from . import memory_v2_synopsis as synopsis
        return call(lambda db:synopsis.preview_chronicle_stream(db,entry_id,characterId,user['id'],outputTokens),
                    characterId,user['id'])

    @router.get('/entries/{entry_id}/synopsis-status')
    def synopsis_status(entry_id:str,characterId:int,user=Depends(identity)):
        def read(db):
            metadata.read_draft(db,entry_id,characterId)
            row=db.execute("""SELECT j.status,j.cancel_intent,b.reserved_microusd,b.spent_estimate_microusd,
                b.dispatched_requests FROM mv3_jobs j JOIN mv3_trial_budgets b
                ON b.id=json_extract(j.read_set_json,'$.realConfig.trialId')
                WHERE j.character_id=? AND json_extract(j.payload_json,'$.targetId')=?
                AND json_extract(j.payload_json,'$.executionKind')='responses_synopsis_v1'
                AND json_extract(j.read_set_json,'$.realConfig.ownerId')=? ORDER BY j.rowid DESC LIMIT 1""",
                (characterId,entry_id,user['id'])).fetchone()
            return dict(row) if row else {'status':'not_started'}
        return call(read,characterId,user['id'])

    @router.post('/entries/{entry_id}/synopsis-plan')
    def plan_synopsis(entry_id: str,p: Generation,user=Depends(identity)):
        if provider_factory is None: raise HTTPException(409,'paid_generation_not_enabled')
        from . import memory_v2_synopsis as synopsis
        try:
            # Factory validates the selected server profile; never trusts model,
            # rates, owner or endpoint strings supplied by a caller.
            adapter,price=provider_factory(p.modelId)
            adapter.close()
            with connect() as db:
                channel.authorized(db,p.characterId,user['id'])
                if metadata.read_draft(db,entry_id,p.characterId)['versionId']!=p.expectedVersion:
                    raise ValueError('version_conflict')
                if p.generationVersion == 'stream-v4':
                    result=synopsis.create_chronicle_stream_trial(db,entry_id,p.characterId,user['id'],
                        price=price,output_tokens=p.outputTokens,max_microusd=p.approveMaxMicrousd,
                        expected_scope=p.materialHash,workspace_model_id=p.modelId)
                else:
                    if p.outputTokens!=6144 or p.approveMaxMicrousd>122880 or p.materialHash is not None:
                        raise ValueError('synopsis_legacy_config_invalid')
                    result=synopsis.create_chronicle_trial(db,entry_id,p.characterId,user['id'],
                        price=price,max_microusd=p.approveMaxMicrousd)
                # Model selection is persisted inside the trusted task payload,
                # never reselected silently by the executor.
                if p.generationVersion == 'legacy':
                    row=db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(result['jobId'],)).fetchone()
                    import json
                    payload=json.loads(row[0]);payload['workspaceModelId']=p.modelId
                    db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(payload),result['jobId']))
            return result
        except ValueError as exc: raise HTTPException(409,str(exc)) from None

    @router.post('/synopsis-jobs/{job_id}/execute')
    def execute_synopsis(job_id: str,characterId: int,user=Depends(identity)):
        if provider_factory is None: raise HTTPException(409,'paid_generation_not_enabled')
        from . import memory_v2_synopsis as synopsis
        try:
            with connect() as db:
                channel.authorized(db,characterId,user['id'])
                job,payload=synopsis._real_job(db,job_id,characterId)
                if payload['ruleVersion'] not in synopsis._CHRONICLE_PROFILES or payload['readSet']['realConfig']['ownerId']!=user['id']:
                    raise ValueError('generation_owner_mismatch')
                state=db.execute('SELECT status FROM mv3_trial_budgets WHERE id=?',(payload['readSet']['realConfig']['trialId'],)).fetchone()[0]
            selected_model=payload['readSet']['realConfig']['workspaceModelId'] if payload['ruleVersion'] in synopsis._STREAM_PROFILES else payload['workspaceModelId']
            adapter,price=provider_factory(selected_model)
            try:
                if payload['ruleVersion'] in synopsis._STREAM_PROFILES:
                    if not callable(diagnostic_sink):raise ValueError('synopsis_diagnostic_sink_required')
                    event_index = 0
                    def retain(event):
                        nonlocal event_index
                        event_index += 1
                        with connect() as db:
                            row=db.execute('SELECT dispatch_attempt_id FROM mv3_model_chunks WHERE job_id=? AND ordinal=0',(job_id,)).fetchone()
                        if not row or not row[0]:raise ValueError('stream_dispatch_binding_missing')
                        diagnostic_sink(dict(kind='synopsis-stream-public-event-v1',jobId=job_id,
                            ordinal=0,dispatchAttemptId=row[0],eventIndex=event_index,event=event))
                    adapter.configure_synopsis_stream(retain)
                if synopsis._hash(price)!=payload['readSet']['realConfig']['priceHash']:
                    raise ValueError('generation_price_changed')
                if state=='created': synopsis.prepare_real_trial(connect,job_id,characterId,user['id'],adapter)
                return synopsis.run_real_trial(connect,job_id,characterId,user['id'],adapter,diagnostic_sink=diagnostic_sink)
            finally: adapter.close()
        except ValueError as exc: raise HTTPException(409,str(exc)) from None

    @router.post('/synopsis-jobs/{job_id}/cancel')
    def cancel_synopsis(job_id:str,characterId:int,user=Depends(identity)):
        from . import memory_v2_synopsis as synopsis
        def cancel(db):
            _,payload=synopsis._real_job(db,job_id,characterId)
            if payload['ruleVersion'] not in synopsis._CHRONICLE_PROFILES or payload['readSet']['realConfig']['ownerId']!=user['id']:
                raise ValueError('generation_owner_mismatch')
            return synopsis.cancel_real_trial(db,job_id,characterId,user['id'])
        return call(cancel,characterId,user['id'])

    @router.post('/vector-index/preview')
    def index_preview(p:IndexScope,user=Depends(identity)):
        from . import memory_v2_chronicle_indexing as indexing
        return call(lambda db:indexing.plan(db,p.characterId,user['id'],[str(x) for x in p.entryIds]),p.characterId,user['id'])

    @router.post('/vector-index/approve')
    def index_approve(p:IndexApproval,user=Depends(identity)):
        if embedding_factory is None:raise HTTPException(409,'paid_embedding_not_enabled')
        from . import memory_v2_chronicle_indexing as indexing
        try:
            with connect() as db:channel.authorized(db,p.characterId,user['id'])
            adapter,price=embedding_factory(p.modelId)
            adapter.close()
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                return indexing.create(db,p.characterId,user['id'],[str(x) for x in p.entryIds],p.planHash,
                    str(p.approvalId),price,p.approveMaxMicrousd,model_id=p.modelId)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @router.post('/vector-index/jobs/{job_id}/execute')
    def index_execute(job_id:str,characterId:int,user=Depends(identity)):
        if embedding_factory is None:raise HTTPException(409,'paid_embedding_not_enabled')
        from . import memory_v2_chronicle_indexing as indexing
        try:
            with connect() as db:
                _,snap=indexing._load(db,job_id,characterId,user['id'])
            adapter,price=embedding_factory(snap['realConfig']['modelId'])
            try:
                if m3._hash(price)!=snap['priceHash']:raise ValueError('embedding_price_changed')
                return indexing.execute(connect,job_id,characterId,user['id'],adapter)
            finally:adapter.close()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @router.post('/vector-index/jobs/{job_id}/cancel')
    def index_cancel(job_id:str,characterId:int,user=Depends(identity)):
        from . import memory_v2_chronicle_indexing as indexing
        return call(lambda db:indexing.cancel(db,job_id,characterId,user['id']),characterId,user['id'])

    @router.post('/vector-index/approvals/{approval_id}/execute')
    def index_group_execute(approval_id:UUID,characterId:int,request:Request,user=Depends(identity)):
        if embedding_factory is None:raise HTTPException(409,'paid_embedding_not_enabled')
        from . import memory_v2_chronicle_indexing as indexing
        try:
            with connect() as db:
                channel.authorized(db,characterId,user['id'])
                group=db.execute("""SELECT id FROM mv3_jobs WHERE character_id=?
                    AND json_extract(read_set_json,'$.approvalId')=?
                    AND json_extract(read_set_json,'$.version')=?
                    AND json_extract(read_set_json,'$.realConfig.ownerId')=?
                    ORDER BY json_extract(read_set_json,'$.ordinal')""",
                    (characterId,str(approval_id),indexing.VERSION,user['id'])).fetchall()
                if not group:raise ValueError('embedding_approval_not_found')
            outcomes=[]
            for row in group:
                current_user=authenticate(request)
                if not current_user or current_user['id']!=user['id']:raise ValueError('chronicle_access_denied')
                with connect() as db:_,snap=indexing._load(db,row['id'],characterId,user['id'])
                adapter,price=embedding_factory(snap['realConfig']['modelId'])
                try:
                    if m3._hash(price)!=snap['priceHash']:raise ValueError('embedding_price_changed')
                    result=indexing.execute(connect,row['id'],characterId,user['id'],adapter)
                finally:adapter.close()
                outcomes.append(result)
                if result['status']!='completed':break
            return dict(status='completed' if len(outcomes)==len(group) and all(x['status']=='completed' for x in outcomes)
                        else 'stopped',results=outcomes,totalBatches=len(group))
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @router.post('/periods/plan')
    def period_plan(p: PeriodGeneration,user=Depends(identity)):
        if provider_factory is None: raise HTTPException(409,'paid_generation_not_enabled')
        from . import memory_v2_chronicle_digest as digest
        from datetime import datetime
        import json
        try:
            with connect() as db:
                channel.authorized(db,p.characterId,user['id'])
            adapter,price=provider_factory(p.modelId);adapter.close()
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                channel.authorized(db,p.characterId,user['id'])
                prior = pending_period(db, p.characterId, user['id'], p.nodeId) if p.queueForMaintenance else None
                if prior and (prior['status'] in ('running', 'unknown')
                              or (prior['status'] == 'queued' and prior['budget_status'] != 'stopped')
                              or (prior['result_version_id'] and not m3._validity(
                                  db, prior['result_version_id'], p.characterId, set(), draft=True))):
                    raise ValueError('period_approval_already_queued')
                if p.queueForMaintenance and p.approveMaxMicrousd < digest.responses.synopsis_quote_microusd(
                    8192, 6144, price['inputUsdPerMillion'], price['outputUsdPerMillion']
                ):
                    raise ValueError('period_budget_insufficient')
                plan=digest.create(db,p.characterId,p.nodeId,user['id'],now=datetime.fromisoformat(p.asOf.replace('Z','+00:00')),price=price,max_microusd=p.approveMaxMicrousd)
                row=db.execute('SELECT payload_json FROM mv3_jobs WHERE id=?',(plan['jobId'],)).fetchone()
                payload=json.loads(row[0]);payload['workspaceModelId']=p.modelId
                if p.queueForMaintenance:
                    payload['workspaceMaintenanceApproved']=True
                db.execute('UPDATE mv3_jobs SET payload_json=? WHERE id=?',(json.dumps(payload),plan['jobId']))
                return plan
        except ValueError as exc: raise HTTPException(409,str(exc)) from None

    @router.post('/period-jobs/{job_id}/execute')
    def period_execute(job_id: str,characterId:int,user=Depends(identity)):
        if provider_factory is None: raise HTTPException(409,'paid_generation_not_enabled')
        from . import memory_v2_chronicle_digest as digest
        try:
            with connect() as db:
                channel.authorized(db,characterId,user['id'])
                _,payload=digest.temporal._job(db,job_id,characterId)
                if payload['readSet']['realConfig']['ownerId']!=user['id']:raise ValueError('generation_owner_mismatch')
            adapter,price=provider_factory(payload['workspaceModelId'])
            try:
                if digest.temporal._hash(price)!=payload['readSet']['realConfig']['priceHash']:raise ValueError('generation_price_changed')
                return digest.run(connect,job_id,characterId,user['id'],adapter)
            finally:adapter.close()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    def period_version(db,vid,cid):
        reason=m3._validity(db,vid,cid,set(),draft=True)
        if reason:raise ValueError(reason)
        v=m3._row(db,'mv3_versions',vid);d=m3._row(db,'mv3_derivations',v['derivation_id'])
        if d['view_type']!='DIGEST':raise ValueError('not_period_digest')
        return {k:v[k] for k in ('id','body','input_hash','output_hash','status','review_status')}

    def pending_period(db, cid, owner, node_id=None, *, dispatchable=False):
        filters = ["j.character_id=?", "j.kind='A3_PROCESSING'",
                   "json_extract(j.payload_json,'$.executionKind')='responses_temporal_digest_v1'",
                   "json_extract(j.payload_json,'$.workspaceMaintenanceApproved')=1",
                   "json_extract(j.read_set_json,'$.realConfig.ownerId')=?"]
        args = [cid, owner]
        if node_id is not None:
            filters.append("json_extract(j.read_set_json,'$.manifest.nodeId')=?")
            args.append(node_id)
        if dispatchable:
            filters.extend(("j.status='queued'", "b.status='created'", "b.dispatched_requests=0",
                            "NOT EXISTS (SELECT 1 FROM mv3_model_chunks c WHERE c.job_id=j.id)"))
        query = '''SELECT j.id,j.status,j.result_version_id,j.payload_json,
                          b.status AS budget_status,b.error_code AS budget_error
                   FROM mv3_jobs j JOIN mv3_trial_budgets b
                     ON b.id=json_extract(j.read_set_json,'$.realConfig.trialId')
                    AND b.character_id=j.character_id WHERE '''
        return db.execute(query + ' AND '.join(filters) + ' ORDER BY j.rowid DESC LIMIT 1', args).fetchone()

    @router.get('/periods/task')
    def period_task(characterId: int, nodeId: str, user=Depends(identity)):
        with connect() as db:
            channel.authorized(db, characterId, user['id'])
            row = pending_period(db, characterId, user['id'], nodeId)
            if row is None:
                availability = paid_availability(provider_factory is not None, 'paid_generation_not_enabled')
                return {'status': 'approval_required' if availability['enabled'] else availability['reason']}
            status = row['status']
            version = row['result_version_id']
            if version and m3._validity(db, version, characterId, set(), draft=True):
                status, version = 'stale', None
            elif status == 'queued':
                if row['budget_status'] == 'counting':
                    status = 'counting'
                elif row['budget_status'] == 'stopped':
                    status = 'stopped'
                else:
                    availability = paid_availability(provider_factory is not None, 'paid_generation_not_enabled')
                    if not availability['enabled']:
                        status = availability['reason']
            return {'status': status, 'versionId': version, 'reason': row['budget_error']}

    @router.get('/period-versions/{version_id}')
    def period_read(version_id:str,characterId:int,user=Depends(identity)):
        return call(period_version,characterId,user['id'],version_id,characterId)

    @router.post('/period-versions/{version_id}/review')
    def period_review(version_id:str,p:PeriodDecision,user=Depends(identity)):
        def review(db):
            period_version(db,version_id,p.characterId)
            m3.review_version(db,version_id,p.characterId,user['id'],p.outputHash,p.inputHash)
            return period_version(db,version_id,p.characterId)
        return call(review,p.characterId,user['id'])

    @router.post('/period-versions/{version_id}/activate')
    def period_activate(version_id:str,p:PeriodDecision,user=Depends(identity)):
        def activate(db):
            v=period_version(db,version_id,p.characterId)
            if v['input_hash']!=p.inputHash or v['output_hash']!=p.outputHash:raise ValueError('version_conflict')
            m3.activate_version(db,version_id,p.characterId)
            return period_version(db,version_id,p.characterId)
        return call(activate,p.characterId,user['id'])

    @router.post('/semantic-search')
    def semantic_search(p: SemanticSearch,user=Depends(identity)):
        if embedding_factory is None: raise HTTPException(409,'paid_embedding_not_enabled')
        from . import memory_v2_chronicle_embedding as embedding, memory_v2_chronicle_indexing as indexing
        try:
            with connect() as db:
                channel.authorized(db,p.characterId,user['id'])
                if not next(embedding.vectors._qualified_vectors(db,p.characterId,user['id'],embedding.MODEL),None):
                    raise ValueError('embedding_index_unavailable')
            adapter,price=embedding_factory(p.modelId)
            try:
                with connect() as db:
                    channel.authorized(db,p.characterId,user['id'])
                    jid=indexing.create_query(db,p.characterId,user['id'],p.query,price,p.approveMaxMicrousd)
                result=indexing.execute(connect,jid,p.characterId,user['id'],adapter)
                if result['status']!='completed':return result
                with connect() as db:
                    q=indexing.cached_query(db,p.characterId,user['id'],p.query)
                    return embedding.vectors.hybrid(db,p.characterId,user['id'],p.query,query_vector=q,model_version=embedding.MODEL)
            finally: adapter.close()
        except ValueError as exc: raise HTTPException(409,str(exc)) from None

    @router.get('/semantic-supplement/status')
    def supplement_status(user=Depends(identity)):
        from .memory_v2_chronicle_semantic import BUDGET
        return {**paid_availability(semantic_factories is not None, 'semantic_supplement_disabled'), 'maxMicrousd': BUDGET}

    @router.post('/semantic-supplement')
    def supplement(p: SemanticSupplement, request: Request, user=Depends(identity)):
        if semantic_factories is None:
            raise HTTPException(409, 'semantic_supplement_disabled')
        from .memory_v2_chronicle_semantic import Search
        try:
            return Search(connect, p, user['id'], lambda: authenticate(request)).run(*semantic_factories)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/vocabulary')
    def catalog(characterId: int, user=Depends(identity)):
        from . import memory_v2_vocabulary as vocabulary
        with connect() as db:
            channel.authorized(db,characterId,user['id'])
            ids=db.execute('SELECT id FROM mv3_vocabulary WHERE character_id=? ORDER BY id LIMIT 1001',(characterId,)).fetchall()
            if len(ids)>1000: raise HTTPException(409,'vocabulary_capacity_exceeded')
            rows=[vocabulary.get(db,characterId,r['id']) for r in ids]
            return {'items':[{k:r[k] for k in ('vocabulary_id','kind','name','dimension')} for r in rows if r['status']=='active']}

    def boundary_file(db, batch_id, file_id, cid):
        from . import memory_v2_import as importer
        importer._batch(db, batch_id, cid)
        row = db.execute('SELECT * FROM mv2_import_files WHERE id=? AND batch_id=? AND character_id=?',
                         (file_id, batch_id, cid)).fetchone()
        if row is None:
            raise ValueError('import_file_unavailable')
        return row

    @router.get('/imports/{batch_id}/{file_id}/boundaries')
    def boundary_plan(batch_id: str, file_id: str, characterId: int, user=Depends(identity)):
        from . import memory_v2_import as importer
        with connect() as db:
            channel.authorized(db, characterId, user['id'])
            row = boundary_file(db, batch_id, file_id, characterId)
            _, records, snapshot = importer.confirmed_file_snapshot(db,batch_id,characterId,file_id,
                row['file_sha256'], row['parser_version'], general=True)
            segments = [dict(start=s['start_ordinal'], end=s['end_ordinal'], title=s['title'])
                for s in db.execute('SELECT * FROM mv2_import_segments WHERE file_id=? ORDER BY ordinal',(file_id,))]
            return {'sourceSnapshot':snapshot,'messageCount':len(records),'segments':segments}

    @router.post('/imports/{batch_id}/{file_id}/boundaries')
    def confirm_boundaries(batch_id: str, file_id: str, p: Boundary, user=Depends(identity)):
        from . import memory_v2_import as importer
        try:
            with connect() as db:
                # The importer starts its own write transaction and rechecks
                # the Owner, role, file identity and complete source snapshot.
                metadata._owner(db,user['id'])
                row=boundary_file(db,batch_id,file_id,p.characterId)
                result = importer.confirm_dialogue_drafts(db,batch_id,p.characterId,file_id,p.segments,
                    expected_sha=row['file_sha256'],expected_parser=row['parser_version'],
                    expected_snapshot=p.sourceSnapshot,editor_id=user['id'])
            if not result['reused']:
                from .memory_v2_chronicle_processing import prepare_entry
                # The editor must see the post-preparation version on its first GET.
                result['preparation'] = [prepare_entry(connect, p.characterId, user['id'], item['entryId'])
                                         for item in result['items']]
            return result
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    def call(fn, cid, owner_id, *args, **kwargs):
        try:
            with connect() as db:
                if not db.in_transaction:
                    db.execute('BEGIN IMMEDIATE' if fn != draft_view and fn != channel.search and fn != channel.expand else 'BEGIN')
                channel.authorized(db, cid, owner_id)
                return fn(db, *args, **kwargs)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/review-selection')
    def review_page(characterId:int, after:int=0, user=Depends(identity)):
        if after < 0:
            raise HTTPException(422, 'invalid_cursor')
        with connect() as db:
            db.execute('BEGIN')
            try:
                channel.authorized(db, characterId, user['id'])
            except ValueError as exc:
                raise HTTPException(403, str(exc)) from None
            rows=db.execute("""SELECT rowid AS cursor,id FROM mv3_entries WHERE character_id=?
                AND contract_version=2 AND status NOT IN ('archived','superseded') AND rowid>?
                ORDER BY rowid LIMIT 21""",(characterId,after)).fetchall()
            items=[]
            for row in rows[:20]:
                try:
                    items.append(review_card(db,characterId,row['id']))
                except ValueError:
                    items.append(dict(entryId=row['id'], title=None, reasons=['来源或版本不可用']))
            return dict(items=items,nextAfter=rows[19]['cursor'] if len(rows)>20 else None)

    @router.post('/review-selection/{entry_id}/publish')
    def selected_publish(entry_id:str,p:ReviewSelection,user=Depends(identity)):
        return call(publish_selected,p.characterId,user['id'],entry_id,p,user['id'])

    @router.get('/entries/{entry_id}')
    def read(entry_id: str, characterId: int, user=Depends(identity)):
        return call(draft_view, characterId, user['id'], characterId, entry_id)

    @router.post('/entries/{entry_id}/refine')
    def refine(entry_id: str, p: NarrativeInput, user=Depends(identity)):
        def create(db):
            detail = metadata.read_draft(db, entry_id, p.characterId)
            if 'expectedNarrativeVersionId' not in p.model_fields_set:
                candidate = _draft_narrative(db, p.characterId, detail)
                if candidate:
                    if candidate['author_kind'] != 'rule' or candidate['rule_version'] != finite.RULE:
                        raise ValueError('narrative_snapshot_changed')
                    same = finite.create(db, p.characterId, user['id'], entry_id, p.expectedVersion)
                    if same['id'] != candidate['id']:
                        raise ValueError('narrative_snapshot_changed')
                    return same
            _check_narrative_snapshot(db, p.characterId, detail, p.expectedNarrativeVersionId)
            return finite.create(db, p.characterId, user['id'], entry_id, p.expectedVersion)
        return call(create, p.characterId, user['id'])

    @router.post('/entries/{entry_id}/date')
    def correct_date(entry_id: str, p: DateCorrection, user=Depends(identity)):
        from . import memory_v2_chronicle_dates as dates
        return call(dates.save, p.characterId, user['id'], p.characterId, user['id'],
                    entry_id, p.expectedVersion, p.occurredDate, p.reason)

    @router.post('/entries/{entry_id}/prepare-local')
    def prepare_local(entry_id: str, p: Input, user=Depends(identity)):
        """One explicit action drives existing local jobs; never creates model work."""
        from . import memory_v2_processing as processing
        result = []
        with connect() as db:
            channel.authorized(db, p.characterId, user['id'])
            if metadata.read_draft(db, entry_id, p.characterId)['versionId'] != p.expectedVersion:
                raise HTTPException(409, 'version_conflict')
        for name in ('category', 'tag', 'keyword'):
            jid=None
            try:
                with connect() as db:
                    channel.authorized(db, p.characterId, user['id'])
                    if name=='keyword':
                        from . import memory_v2_keyword_rules as keyword_rules
                        jid=processing.create_keyword_job(db,entry_id,p.characterId,user['id'],rule_version=keyword_rules.SELECTION_RULE_VERSION)
                    else:jid = getattr(processing, f'create_{name}_job')(db, entry_id, p.characterId, user['id'])
                status = getattr(processing, f'run_{name}_job')(connect, jid, p.characterId, user['id'])
                result.append({'field': name, 'result': status})
                if status.get('status') not in ('completed', 'stale'):
                    break
            except ValueError as exc:
                # Optional vocabulary unavailable before any task was created:
                # do not invent a category or prevent independent RAW keywords.
                # A claimed task, source failure or unknown/hold still blocks.
                if jid is None and name in ('category','tag') and str(exc)=='vocabulary_unavailable':
                    result.append({'field':name,'status':'optional_unavailable','reason':'vocabulary_unavailable'})
                    continue
                result.append({'field': name, 'status': 'blocked', 'reason': str(exc)})
                break
        return {'results': result, 'providerRequests': 0, 'reviewed': False}

    @router.post('/maintain')
    def maintain_role(characterId: int, nodeId: str | None = None, user=Depends(identity)):
        try: return run_maintenance(characterId, user['id'], nodeId)
        except ValueError as exc: raise HTTPException(409,str(exc)) from None

    def run_maintenance(cid, owner, node_id=None):
        from .memory_v2_chronicle_processing import maintain
        from . import memory_v2_chronicle_digest as digest
        import json

        if provider_factory is None:
            return maintain(connect,cid,owner)
        with connect() as db:
            channel.authorized(db,cid,owner)
            pending = pending_period(db, cid, owner, node_id, dispatchable=True)
        if pending is None:
            return maintain(connect,cid,owner)
        payload = json.loads(pending['payload_json'])
        try:
            adapter, price = provider_factory(payload['workspaceModelId'])
        except ValueError as exc:
            result = maintain(connect,cid,owner)
            result['periodResult'] = {'status': 'blocked', 'reason': str(exc)}
            return result
        try:
            if digest.temporal._hash(price) != payload['readSet']['realConfig']['priceHash']:
                result = maintain(connect,cid,owner)
                result['periodResult'] = {'status': 'blocked', 'reason': 'generation_price_changed'}
                return result
            return maintain(connect,cid,owner,approved_period_job=pending['id'],period_adapter=adapter)
        finally:
            adapter.close()

    @router.post('/entries/{entry_id}/narrative')
    def edit(entry_id: str, p: Edit, user=Depends(identity)):
        def save(db):
            detail = metadata.read_draft(db, entry_id, p.characterId)
            if detail['versionId'] != p.expectedVersion:
                raise ValueError('version_conflict')
            _check_narrative_snapshot(db, p.characterId, detail, p.expectedNarrativeVersionId)
            refs = refs_for(detail)
            item = m3.create_derivation(db, p.characterId, 'NARRATIVE', refs)
            return m3.add_version(db, item['id'], refs, p.body, author_kind='user')
        return call(save, p.characterId, user['id'])

    @router.get('/entries/{entry_id}/keyword-suggestions')
    def keyword_suggestions(entry_id: str, characterId: int, user=Depends(identity)):
        def preview(db):
            from . import memory_v2_keyword_suggestions as suggestions
            detail=metadata.read_draft(db,entry_id,characterId)
            entry=metadata._entry(db,entry_id,characterId)
            metadata._validate_sources(db,entry,metadata._set(db,entry))
            sources=[dict(r,text=m3.live_source(db,characterId,r['kind'],r['id']).text) for r in refs_for(detail)]
            fields=metadata._fields(db,detail['versionId']); field=fields.get('keywords')
            return dict(suggestions.suggest(sources),versionId=detail['versionId'],sourceToken=detail['sourceToken'],
                current=detail['fields']['keywords'],savedRuleVersion=field['rule_version'] if field else None,
                savedProvenance=field['provenance'] if field else None)
        return call(preview,characterId,user['id'])

    @router.post('/entries/{entry_id}/labels')
    def labels(entry_id: str, p: Labels, user=Depends(identity)):
        def save(db):
            detail=metadata.read_draft(db,entry_id,p.characterId)
            if detail['versionId']!=p.expectedVersion: raise ValueError('version_conflict')
            refs=refs_for(detail)
            if p.sourceOrdinal>=len(refs): raise ValueError('source_outside_entry')
            ref=refs[p.sourceOrdinal]; source=m3.live_source(db,p.characterId,ref['kind'],ref['id'])
            evidence=[dict(ref,start=0,end=len(source.text))]
            changes={}
            for name in ('categories','tags'):
                selected=sorted(set(getattr(p,name)))
                current=detail['fields'][name]['value'] or []
                if sorted(x['id'] for x in current)!=selected:
                    changes[name]=[dict(id=vid,evidence=evidence) for vid in selected]
            from . import memory_v2_keyword_rules as keyword_rules
            submitted=keyword_rules.normalize_manual(p.keywords)
            current=detail['fields']['keywords']['value'] or []
            if sorted(set(x['text'] for x in current))!=sorted(submitted):
                words=[]
                for word in submitted:
                    if not word or len(word)>80: raise ValueError('keyword_invalid')
                    match=None
                    for r in refs:
                        text=m3.live_source(db,p.characterId,r['kind'],r['id']).text
                        start=text.find(word)
                        if start>=0:
                            match=dict(r,start=start,end=start+len(word));break
                    if match is None: raise ValueError('literal_keyword_not_in_source')
                    words.append(dict(text=word,language='und',kind='literal',weight=1,field='raw',evidence=[match]))
                changes['keywords']=words
            return metadata.revise(db,entry_id,p.characterId,p.expectedVersion,changes,user['id']) if changes else detail
        return call(save,p.characterId,user['id'])

    @router.post('/entries/{entry_id}/confirm-narrative')
    def confirm(entry_id: str, p: Confirm, user=Depends(identity)):
        return call(confirm_narrative, p.characterId, user['id'], entry_id, p, user['id'])

    @router.get('/recall')
    def recall(characterId: int, q: str, user=Depends(identity)):
        from .memory_v2_chronicle_embedding import cached_search
        return call(cached_search, characterId, user['id'], characterId, user['id'], q)

    @router.get('/entries/{entry_id}/curation')
    def choices(entry_id: str, characterId: int, user=Depends(identity)):
        return call(curation.read, characterId, user['id'], characterId, user['id'], entry_id)

    @router.post('/entries/{entry_id}/curation')
    def choose(entry_id: str, p: Curation, user=Depends(identity)):
        return call(curation.save, p.characterId, user['id'], p.characterId, user['id'], entry_id,
                    p.expectedRevision, {k: getattr(p, k) for k in curation.FLAGS}, p.reason)

    @router.get('/recall/{entry_id}')
    def expand(entry_id: str, characterId: int, offset: int = 0, expectedVersion: str | None = None,
               user=Depends(identity)):
        return call(channel.expand, characterId, user['id'], characterId, user['id'], entry_id,
                    offset=offset, expected_version=expectedVersion)
    def resume_local(owner_id):
        with connect() as db:
            metadata._owner(db,owner_id)
            roles=[r[0] for r in db.execute('SELECT id FROM characters WHERE enabled=1 ORDER BY id')]
        for cid in roles:
            try: run_maintenance(cid,owner_id)
            except (ValueError, RuntimeError): pass

    async def recover_on_start():
        # The same dispatch gate runs on restart. Only an approved plan with
        # no count claim, chunk or reservation can be picked up.
        from starlette.concurrency import run_in_threadpool
        with connect() as db:
            owners=[r[0] for r in db.execute('SELECT id FROM users')]
        if len(owners)==1: await run_in_threadpool(resume_local,owners[0])
    app.add_event_handler('startup',recover_on_start)
    if calendar_sync is not None:
        app.add_event_handler('startup',calendar_sync.start)
        app.add_event_handler('shutdown',calendar_sync.stop)

    @app.middleware('http')
    async def maintain_after_change(request, call_next):
        response=await call_next(request)
        if (request.method in ('POST','PUT','PATCH','DELETE') and response.status_code<300
                and (request.url.path.startswith('/api/memory-v2/entry-drafts/')
                     or request.url.path.startswith('/api/memory-v2/workspace/review-selection/')
                     or request.url.path.startswith('/api/memory-v2/workspace/entries/')
                     or request.url.path.startswith('/api/memory-v2/workspace/period-versions/')
                     or request.url.path.startswith('/api/conversations/')
                     or request.url.path=='/api/chat/stream')):
            user=getattr(request.state,'user',None)
            if user:
                from starlette.background import BackgroundTasks
                tasks=BackgroundTasks()
                if response.background: tasks.add_task(response.background)
                if calendar_sync is not None and request.url.path.startswith(('/api/memory-v2/entry-drafts/',
                        '/api/memory-v2/workspace/review-selection/','/api/memory-v2/workspace/entries/','/api/conversations/')):
                    tasks.add_task(calendar_sync.wake)
                tasks.add_task(resume_local,user['id']);response.background=tasks
        return response
    app.include_router(router)
