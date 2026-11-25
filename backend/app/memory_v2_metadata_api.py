"""Explicitly installable P1-A2 management API. Never installed by main.py."""
from typing import Any, Literal
import re
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from . import memory_v2_metadata as metadata, memory_v2_vocabulary as vocabulary


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Ref(Strict):
    kind: str
    id: str


class Create(Strict):
    characterId: int
    sourceRefs: list[Ref]=Field(min_length=1,max_length=500)
    title: str=Field(min_length=1,max_length=200)
    requestKey: str=Field(min_length=1,max_length=100)


class Revision(Strict):
    characterId: int
    expectedVersion: str
    changes: dict[str,Any]


class Review(Strict):
    characterId: int
    field: str
    token: str


class Action(Strict):
    characterId: int
    expectedVersion: str


class Publish(Action):
    expectedNarrativeVersionId: str | None = None


class Work(Strict):
    characterId: int
    kind: str


class MaintenanceAction(Strict):
    characterId: int
    action: Literal['lexical_rebuild', 'cancel', 'recover_local']
    targetId: str|None=None
    planToken: str=Field(min_length=1,max_length=2048)
    requestKey: str=Field(min_length=1,max_length=100)


class Term(Strict):
    characterId: int
    kind: str
    name: str
    language: str
    vocabularyId: str|None=None
    expectedVersion: int|None=None
    dimension: str|None=None
    definition: str=''
    aliases: list[str]=Field(default_factory=list)
    status: str='active'
    mergedInto: str|None=None
    evidence: list=Field(default_factory=list)


def install(app, connect, authenticate, *, allowed_origins=(), maintenance_enabled=False, maintenance_secret=None):
    def owner(request:Request,response:Response):
        identity=authenticate(request)
        if not identity: raise HTTPException(401,'authentication_required')
        if request.method not in ('GET','HEAD') and request.headers.get('origin'):
            if request.headers['origin'].rstrip('/') not in {*allowed_origins,str(request.base_url).rstrip('/')}:
                raise HTTPException(403,'origin_rejected')
        response.headers['Cache-Control']='no-store'
        return identity
    router=APIRouter(prefix='/api/memory-v2/entry-drafts',dependencies=[Depends(owner)])
    def check_role(db, character_id, identity):
        if not db.execute('''SELECT 1 FROM characters c JOIN users u ON u.id=?
            WHERE c.id=? AND c.enabled=1''', (identity['id'], character_id)).fetchone():
            raise HTTPException(404, 'role_unavailable')

    def call(fn,*args,_character_id,_identity,**kwargs):
        try:
            with connect() as db:
                if not db.in_transaction: db.execute('BEGIN')
                check_role(db, _character_id, _identity)
                return fn(db,*args,**kwargs)
        except ValueError as exc: raise HTTPException(409,str(exc)) from None
    @router.post('',status_code=201)
    def create(p:Create,identity=Depends(owner)):
        return call(metadata.create_draft,p.characterId,[r.model_dump() for r in p.sourceRefs],p.title,identity['id'],p.requestKey,
                    _character_id=p.characterId,_identity=identity)
    @router.get('')
    def browse(characterId:int,offset:int=0,limit:int=20,identity=Depends(owner)):
        if offset<0 or not 1<=limit<=50: raise HTTPException(422,'invalid_page')
        with connect() as db:
            if not db.in_transaction: db.execute('BEGIN')
            check_role(db, characterId, identity)
            result=[]
            rows=db.execute('''SELECT id FROM mv3_entries WHERE character_id=? AND contract_version=2
                AND status NOT IN ('archived','superseded') ORDER BY created_at,id LIMIT ? OFFSET ?''',
                (characterId,limit+1,offset)).fetchall()
            for row in rows[:limit]:
                try: result.append(metadata.read_draft(db,row['id'],characterId))
                except ValueError: continue
            return {'items':result,'nextOffset':offset+limit if len(rows)>limit else None,'managementOnly':True}
    @router.get('/{entry_id}')
    def detail(entry_id:str,characterId:int,identity=Depends(owner)):
        return call(metadata.read_draft,entry_id,characterId,_character_id=characterId,_identity=identity)
    @router.post('/{entry_id}/revisions')
    def revise(entry_id:str,p:Revision,identity=Depends(owner)):
        return call(metadata.revise,entry_id,p.characterId,p.expectedVersion,p.changes,identity['id'],
                    _character_id=p.characterId,_identity=identity)
    @router.post('/{entry_id}/review')
    def review(entry_id:str,p:Review,identity=Depends(owner)):
        return call(metadata.review_field,entry_id,p.characterId,p.field,p.token,identity['id'],
                    _character_id=p.characterId,_identity=identity)
    @router.post('/{entry_id}/publish')
    def publish(entry_id:str,p:Publish,identity=Depends(owner)):
        def current(db):
            if 'expectedNarrativeVersionId' in p.model_fields_set:
                # Chronicle opts into the displayed text snapshot. Other P1
                # progressive-field clients retain their existing contract.
                from . import memory_v2_chronicle_workspace as workspace
                detail = metadata.read_draft(db, entry_id, p.characterId)
                candidate = workspace._check_narrative_snapshot(db, p.characterId, detail, p.expectedNarrativeVersionId)
                linked = detail['fields']['narrative']
                if candidate and (linked['value'] != candidate['id'] or linked['lifecycleStatus'] != 'available'):
                    raise ValueError('narrative_confirmation_required')
                synopsis = detail['fields']['synopsis']
                if synopsis['value'] and synopsis['lifecycleStatus'] != 'available':
                    raise ValueError('synopsis_confirmation_required')
            return metadata.publish(db,entry_id,p.characterId,p.expectedVersion,identity['id'])
        return call(current,_character_id=p.characterId,_identity=identity)
    @router.post('/{entry_id}/jobs')
    def enqueue(entry_id:str,p:Work,identity=Depends(owner)):
        from . import memory_v2_metadata_jobs
        return call(memory_v2_metadata_jobs.enqueue,entry_id,p.characterId,identity['id'],p.kind,
                    _character_id=p.characterId,_identity=identity)
    @router.post('/vocabulary/save')
    def save(p:Term,identity=Depends(owner)):
        return call(vocabulary.write,p.characterId,p.kind,p.name,p.language,identity['id'],
            vocabulary_id=p.vocabularyId,expected_version=p.expectedVersion,dimension=p.dimension,
            definition=p.definition,aliases=p.aliases,status=p.status,merged_into=p.mergedInto,evidence=p.evidence,
            _character_id=p.characterId,_identity=identity)
    app.include_router(router)
    if maintenance_enabled:
        from . import memory_v2_maintenance as maintenance
        if not isinstance(maintenance_secret, bytes) or len(maintenance_secret) < 32:
            raise ValueError('maintenance_signing_key_required')
        maintenance_router=APIRouter(prefix='/api/memory-v2/maintenance',
                                     dependencies=[Depends(owner)])
        def maintenance_call(fn,*args,**kwargs):
            try:
                return fn(*args,**kwargs)
            except ValueError as exc:
                code=str(exc)
                if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',code):
                    code='maintenance_rejected'
                raise HTTPException(404 if code=='role_unavailable' else 409,code) from None

        @maintenance_router.get('/status')
        def maintenance_status(characterId:int,identity=Depends(owner)):
            return maintenance_call(maintenance.status,connect,characterId,identity['id'])

        @maintenance_router.get('/plan')
        def maintenance_plan(characterId:int,identity=Depends(owner)):
            return maintenance_call(maintenance.plan,connect,characterId,identity['id'],
                                    signing_secret=maintenance_secret)

        @maintenance_router.post('/actions')
        def maintenance_action(p:MaintenanceAction,identity=Depends(owner)):
            return maintenance_call(maintenance.act,connect,p.characterId,identity['id'],
                                    action=p.action,target_id=p.targetId,plan_token=p.planToken,
                                    request_key=p.requestKey,signing_secret=maintenance_secret)

        app.include_router(maintenance_router)
    @app.middleware('http')
    async def no_store(request,call_next):
        response=await call_next(request)
        if request.url.path.startswith(('/api/memory-v2/entry-drafts', '/api/memory-v2/maintenance')):
            response.headers['Cache-Control']='no-store'
        return response
