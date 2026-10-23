"""Encrypted local and private GitHub backups with a single-process scheduler."""
import asyncio
import base64
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

router=APIRouter(prefix="/api/system/backup-plan")
run_lock=threading.Lock()
GITHUB_API_BASE="https://api.github.com"


class BackupPlan(BaseModel):
    enabled:bool=False
    frequency:str="daily"
    hour:int=Field(default=3,ge=0,le=23)
    minute:int=Field(default=0,ge=0,le=59)
    weekday:int=Field(default=0,ge=0,le=6)
    keep:int=Field(default=7,ge=1,le=100)
    local_dir:str=""
    github_repo:str=""


def install(app,connect,now,build_encrypted_backup,validate_backup,data_dir):
    def current(db):
        row=db.execute("SELECT * FROM backup_plan WHERE id=1").fetchone()
        return dict(row) if row else BackupPlan().model_dump()

    def next_due(plan,reference):
        zone=timezone(timedelta(hours=8))
        local=reference.astimezone(zone)
        day=local.replace(hour=plan["hour"],minute=plan["minute"],second=0,microsecond=0)
        if plan["frequency"]=="weekly": day+=timedelta(days=(plan["weekday"]-day.weekday())%7)
        if day<=local: day+=timedelta(days=7 if plan["frequency"]=="weekly" else 1)
        return day.astimezone(timezone.utc).isoformat()

    def checked_path(raw):
        if not raw: return None
        path=Path(raw).expanduser().resolve()
        if path==data_dir.resolve() or data_dir.resolve() in path.parents: raise HTTPException(422,"同步目录不能位于运行数据目录内")
        return path

    def validate(plan):
        if plan.frequency not in ("daily","weekly"): raise HTTPException(422,"计划频率无效")
        checked_path(plan.local_dir)
        if plan.github_repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",plan.github_repo): raise HTTPException(422,"仓库须为 owner/repo")
        if plan.enabled and not (plan.local_dir or plan.github_repo): raise HTTPException(422,"请先配置备份目标")

    async def github_request(method,url,token,accept="application/vnd.github+json",**kwargs):
        async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
            return await client.request(method,url,headers={"Authorization":f"Bearer {token}","Accept":accept,"X-GitHub-Api-Version":"2022-11-28"},**kwargs)

    async def verify_repo(repo,token):
        response=await github_request("GET",f"{GITHUB_API_BASE}/repos/{repo}",token)
        if response.status_code!=200 or not response.json().get("private"): raise HTTPException(422,"GitHub 仓库不可访问或不是私有仓库")
        if repo.lower()=="jtnczflm2025-cyber/yosaki": raise HTTPException(422,"备份仓库必须与源码仓库分开")

    async def upload_github(url,token,blob,stamp):
        payload={"message":f"Encrypted YOSAKI backup {stamp}","content":base64.b64encode(blob).decode()}
        for attempt in range(3):
            try:
                response=await github_request("PUT",url,token,json=payload)
            except httpx.RequestError:
                if attempt==2: raise
            else:
                if response.status_code in (200,201): return
                if response.status_code not in (429,500,502,503,504) or attempt==2:
                    raise RuntimeError(f"GitHub HTTP {response.status_code}")
            await asyncio.sleep(0.2*(attempt+1))

    async def execute():
        if not run_lock.acquire(blocking=False): raise HTTPException(409,"备份任务正在运行")
        try:
            with connect() as db: plan=current(db)
            if not (plan["local_dir"] or plan["github_repo"]): raise HTTPException(422,"请先配置备份目标")
            phrase=os.environ.get("YOSAKI_BACKUP_PASSWORD","")
            if len(phrase)<12: raise HTTPException(422,"请通过 YOSAKI_BACKUP_PASSWORD 提供至少 12 位备份密码")
            blob=build_encrypted_backup(phrase)
            stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            name=f"yosaki-{stamp}-{uuid.uuid4().hex[:8]}.ybackup"
            result={"local":"disabled","github":"disabled"}
            local=checked_path(plan["local_dir"])
            if local:
                try:
                    local.mkdir(parents=True,exist_ok=True)
                    path=local/name
                    with path.open("xb") as file: file.write(blob)
                    result["local"]="success"
                except OSError:
                    result["local"]="failed"
                else:
                    try:
                        files=sorted(local.glob("yosaki-*.ybackup"),key=lambda p:p.name,reverse=True)
                        for old in files[plan["keep"]:]: old.unlink()
                    except OSError:
                        result["local"]="retention_failed"
            if plan["github_repo"]:
                token=os.environ.get("YOSAKI_BACKUP_GITHUB_TOKEN","")
                if not token: result["github"]="missing_token"
                else:
                    try:
                        await verify_repo(plan["github_repo"],token)
                        repo=plan["github_repo"]
                        url=f"{GITHUB_API_BASE}/repos/{repo}/contents/backups/{name}"
                        await upload_github(url,token,blob,stamp)
                        result["github"]="success"
                        try:
                            listing=await github_request("GET",f"{GITHUB_API_BASE}/repos/{repo}/contents/backups",token)
                            if listing.status_code!=200: raise RuntimeError("GitHub backup listing failed")
                            files=sorted((x for x in listing.json() if x.get("name","").endswith(".ybackup")),key=lambda x:x["name"],reverse=True)
                            for old in files[plan["keep"]:]:
                                deleted=await github_request("DELETE",old["url"],token,json={"message":"Prune encrypted backup","sha":old["sha"]})
                                if deleted.status_code not in (200,204): raise RuntimeError("GitHub backup prune failed")
                        except (httpx.RequestError,RuntimeError,ValueError,KeyError):
                            result["github"]="retention_failed"
                    except Exception: result["github"]="failed"
            with connect() as db:
                db.execute("INSERT INTO backup_runs(started_at,local_status,github_status) VALUES(?,?,?)",(now(),result["local"],result["github"]))
                db.execute("UPDATE backup_plan SET last_run_at=?,next_run_at=? WHERE id=1",(now(),next_due(plan,datetime.now(timezone.utc))))
            return result
        finally: run_lock.release()

    @router.get("")
    def get_plan():
        with connect() as db:
            plan=current(db)
            runs=[dict(x) for x in db.execute("SELECT * FROM backup_runs ORDER BY id DESC LIMIT 20")]
        return {"plan":plan,"runs":runs,"password_configured":bool(os.getenv("YOSAKI_BACKUP_PASSWORD")),"github_token_configured":bool(os.getenv("YOSAKI_BACKUP_GITHUB_TOKEN"))}

    @router.put("")
    def save_plan(plan:BackupPlan):
        validate(plan)
        due=next_due(plan.model_dump(),datetime.now(timezone.utc)) if plan.enabled else None
        with connect() as db:
            db.execute("INSERT INTO backup_plan(id,enabled,frequency,hour,minute,weekday,keep,local_dir,github_repo,next_run_at) VALUES(1,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,frequency=excluded.frequency,hour=excluded.hour,minute=excluded.minute,weekday=excluded.weekday,keep=excluded.keep,local_dir=excluded.local_dir,github_repo=excluded.github_repo,next_run_at=excluded.next_run_at",(plan.enabled,plan.frequency,plan.hour,plan.minute,plan.weekday,plan.keep,plan.local_dir,plan.github_repo,due))
        return get_plan()

    @router.post("/run")
    async def run_now(): return await execute()

    @router.get("/github")
    async def list_github():
        with connect() as db: repo=current(db)["github_repo"]
        token=os.getenv("YOSAKI_BACKUP_GITHUB_TOKEN","")
        if not repo or not token: raise HTTPException(422,"请配置 GitHub 仓库与 token")
        await verify_repo(repo,token)
        response=await github_request("GET",f"{GITHUB_API_BASE}/repos/{repo}/contents/backups",token)
        if response.status_code==404: return []
        if response.status_code!=200: raise HTTPException(502,"读取 GitHub 备份清单失败")
        return [{"name":x["name"],"size":x["size"]} for x in response.json() if re.fullmatch(r"yosaki-[0-9TZ-]+-[0-9a-f]{8}\.ybackup",x["name"])]

    @router.get("/github/{name}")
    async def download_github(name:str):
        if not re.fullmatch(r"yosaki-[0-9TZ-]+-[0-9a-f]{8}\.ybackup",name): raise HTTPException(422,"备份文件名无效")
        with connect() as db: repo=current(db)["github_repo"]
        token=os.getenv("YOSAKI_BACKUP_GITHUB_TOKEN","")
        if not repo or not token: raise HTTPException(422,"请配置 GitHub 仓库与 token")
        response=await github_request("GET",f"{GITHUB_API_BASE}/repos/{repo}/contents/backups/{name}",token,accept="application/vnd.github.raw+json")
        if response.status_code!=200: raise HTTPException(502,"下载 GitHub 备份失败")
        return Response(response.content,media_type="application/octet-stream",headers={"Content-Disposition":f'attachment; filename="{name}"',"Cache-Control":"no-store"})

    stop=threading.Event()
    async def scheduler():
        while not stop.is_set():
            with connect() as db: plan=current(db)
            if plan["enabled"] and plan.get("next_run_at") and datetime.fromisoformat(plan["next_run_at"])<=datetime.now(timezone.utc):
                try: await execute()
                except Exception:
                    with connect() as db: db.execute("UPDATE backup_plan SET next_run_at=? WHERE id=1",(next_due(plan,datetime.now(timezone.utc)),))
            await asyncio.sleep(30)

    @app.on_event("startup")
    async def start(): app.state.backup_task=asyncio.create_task(scheduler())
    @app.on_event("shutdown")
    async def shutdown():
        stop.set()
        if hasattr(app.state,"backup_task"): app.state.backup_task.cancel()
    app.include_router(router)
