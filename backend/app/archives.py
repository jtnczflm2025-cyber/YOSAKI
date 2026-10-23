"""Versioned, bounded conversation archives. Imported tool metadata is inert."""
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field

router=APIRouter(prefix="/api/archives")
MAX_FILE=8*1024*1024
MAX_TOTAL=64*1024*1024
MAX_MESSAGES=20000
HONG_KONG=timezone(timedelta(hours=8))


class ExportOptions(BaseModel):
    conversation_ids:list[int]=Field(min_length=1,max_length=200)
    format:str="json"
    after:str|None=None
    before:str|None=None
    first_turn:int|None=None
    last_turn:int|None=None
    include_code:bool=True
    include_tools:bool=False
    include_reasoning:bool=False
    include_monologue:bool=False
    include_time:bool=True
    paragraph_mode:str="keep"
    user_name:str="用户"
    assistant_name:str="助手"
    replacements:list[tuple[str,str]]=Field(default_factory=list,max_length=20)


def check_options(o):
    if o.format not in ("json","markdown","txt"): raise HTTPException(422,"不支持的导出格式")
    if o.paragraph_mode not in ("keep","compact"): raise HTTPException(422,"段落格式无效")
    if any(len(x)>100 for x in (o.user_name,o.assistant_name)) or any(len(a)>100 or len(b)>100 for a,b in o.replacements): raise HTTPException(422,"替换规则过长")
    if o.first_turn is not None and o.first_turn<1 or o.last_turn is not None and o.last_turn<1: raise HTTPException(422,"轮次必须大于零")


def day_bound(value):
    try: return datetime.strptime(value,"%Y-%m-%d").replace(tzinfo=HONG_KONG)
    except ValueError as exc: raise HTTPException(422,"日期格式需为 YYYY-MM-DD") from exc


def filtered(rows,o):
    after=day_bound(o.after) if o.after else None
    before=day_bound(o.before) if o.before else None
    turns=[]
    for row in rows:
        if row["role"]=="user" or not turns: turns.append([])
        turns[-1].append(row)
    selected=[]
    for index,turn in enumerate(turns,1):
        if o.first_turn and index<o.first_turn or o.last_turn and index>o.last_turn: continue
        for row in turn:
            try: stamp=datetime.fromisoformat(row["created_at"].replace("Z","+00:00")).astimezone(HONG_KONG)
            except (ValueError,TypeError): continue
            if after and stamp<after or before and stamp>=before+timedelta(days=1): continue
            selected.append(dict(row))
    if o.format=="json":
        call_ids=set()
        for row in selected:
            if row.get("role")=="tool" and row.get("tool_call_id"): call_ids.add(row["tool_call_id"])
            for call in json.loads(row.get("tool_calls_json") or "[]"): call_ids.add(call.get("id"))
        ids={r["id"] for r in selected}
        for row in rows:
            if row["role"]=="tool" and row["tool_call_id"] in call_ids: ids.add(row["id"])
            if row["role"]=="assistant" and row["tool_calls_json"] and any(c.get("id") in call_ids for c in json.loads(row["tool_calls_json"])): ids.add(row["id"])
        selected=[dict(r) for r in rows if r["id"] in ids]
    return selected


def render(title,character,rows,o):
    if o.format=="json":
        keep=("role","content","created_at","model","tool_calls_json","tool_call_id","tool_name","tool_runs","reasoning_content","monologue_content","reasoning_source","generation_status","usage_json")
        return json.dumps({"format":"yosaki-conversation","version":2,"title":title,"character":character,"messages":[{k:r.get(k) for k in keep if k in r} for r in rows]},ensure_ascii=False,indent=2).encode()
    lines=["# "+title] if o.format=="markdown" else ["YOSAKI-CHAT-TXT/1",title]
    for row in rows:
        if row["role"]=="tool" and not o.include_tools: continue
        label=o.user_name if row["role"]=="user" else o.assistant_name if row["role"]=="assistant" else "工具"
        value=row["content"] or ""
        if not o.include_code: value=re.sub(r"```[\s\S]*?```","",value)
        if o.paragraph_mode=="compact": value=re.sub(r"\n\s*\n+","\n",value)
        for old,new in o.replacements:
            if old: value=value.replace(old,new)
        stamp=(" · "+row["created_at"]) if o.include_time else ""
        if o.format=="markdown": lines.extend(["",f"## {label}{stamp}","",re.sub(r"(?m)^## ",r"\\## ",value)])
        else: lines.extend(["",f"@{row['role']}{stamp}",re.sub(r"(?m)^@end$",r"\\@end",value),"@end"])
        if o.include_monologue and row.get("monologue_content"): lines.extend(["",f"内心独白：{row['monologue_content']}"])
        if o.include_reasoning and row.get("reasoning_content"): lines.extend(["",f"公开推理：{row['reasoning_content']}"])
        if o.include_tools and row.get("tool_calls_json"): lines.extend(["",f"工具调用：{row['tool_calls_json']}"])
        if o.include_tools:
            for run in row.get("tool_runs") or []:
                lines.extend(["",f"工具记录：{run.get('server_name','MCP')} / {run.get('tool_name','')} · {run.get('status','')} · 参数 {json.dumps(run.get('arguments'),ensure_ascii=False)} · 结果 {json.dumps(run.get('result'),ensure_ascii=False)}"])
    return ("\n".join(lines)+"\n").encode()


def load_file(name,data):
    if len(data)>MAX_FILE: raise HTTPException(413,"聊天档案文件超过 8 MiB")
    try: text=data.decode("utf-8-sig")
    except UnicodeDecodeError as exc: raise HTTPException(422,"文件必须是 UTF-8") from exc
    if name.lower().endswith(".json"):
        try: doc=json.loads(text)
        except (ValueError,RecursionError) as exc: raise HTTPException(422,"JSON 格式错误") from exc
        if not isinstance(doc,dict) or doc.get("format")!="yosaki-conversation" or doc.get("version") not in (1,2): raise HTTPException(422,"不支持的聊天档案版本")
        conversation=doc.get("conversation") or {}
        if not isinstance(conversation,dict): raise HTTPException(422,"对话信息格式错误")
        title=doc.get("title") or conversation.get("title") or "导入的对话"
        rows=doc.get("messages",[])
    elif name.lower().endswith(".md"):
        if not text.startswith("# ") or re.search(r"(?m)^## (?!用户(?: ·|\s*$)|助手(?: ·|\s*$))",text):
            raise HTTPException(422,"Markdown 必须使用 YOSAKI 的默认用户/助手标题格式")
        title=text.splitlines()[0].removeprefix("# ")
        parts=re.split(r"(?m)^## (用户|助手)(?: · ([^\n]+))?\s*$",text)
        if parts[0].strip()!=f"# {title}": raise HTTPException(422,"Markdown 消息标题前存在无法识别的内容")
        rows=[{"role":"user" if parts[i]=="用户" else "assistant","created_at":parts[i+1] or None,"content":re.sub(r"(?m)^\\## ","## ",parts[i+2].strip())} for i in range(1,len(parts)-2,3)]
    elif name.lower().endswith(".txt"):
        if not text.startswith("YOSAKI-CHAT-TXT/1\n"): raise HTTPException(422,"TXT 缺少 YOSAKI 格式标记")
        lines=text.splitlines()
        if len(lines)<2 or not lines[1].strip(): raise HTTPException(422,"TXT 缺少对话标题")
        title=lines[1]
        rows=[]
        index=2
        while index<len(lines):
            if not lines[index].strip():
                index+=1
                continue
            header=re.fullmatch(r"@(user|assistant)(?: · (.+))?",lines[index])
            if not header: raise HTTPException(422,"TXT 包含无法识别的消息边界")
            index+=1
            body=[]
            while index<len(lines) and lines[index]!="@end":
                body.append("@end" if lines[index]==r"\@end" else lines[index])
                index+=1
            if index==len(lines): raise HTTPException(422,"TXT 消息缺少 @end")
            rows.append({"role":header[1],"created_at":header[2],"content":"\n".join(body).strip()})
            index+=1
    else: raise HTTPException(422,"仅支持 JSON、Markdown 和 TXT")
    if not isinstance(title,str): raise HTTPException(422,"对话标题格式错误")
    if not isinstance(rows,list) or not rows or len(rows)>MAX_MESSAGES: raise HTTPException(422,"聊天档案无消息或超过 20000 条")
    for row in rows:
        if not isinstance(row,dict) or row.get("role") not in ("user","assistant","tool","system") or not isinstance(row.get("content"),str): raise HTTPException(422,"聊天消息格式错误")
        if row.get("created_at"):
            try: datetime.fromisoformat(row["created_at"].replace("Z","+00:00"))
            except (AttributeError,ValueError) as exc: raise HTTPException(422,"消息时间格式错误") from exc
        if row.get("tool_calls_json") is not None:
            try: calls=json.loads(row["tool_calls_json"])
            except (TypeError,ValueError) as exc: raise HTTPException(422,"工具调用记录格式错误") from exc
            if not isinstance(calls,list): raise HTTPException(422,"工具调用记录格式错误")
        runs=row.get("tool_runs") or []
        if not isinstance(runs,list) or len(runs)>100 or any(not isinstance(run,dict) for run in runs): raise HTTPException(422,"工具运行记录格式错误")
    return title[:120],rows


def install(app,connect,now):
    def enriched(db,rows):
        result=[]
        for row in rows:
            item=dict(row)
            if item["role"]=="assistant" and item.get("tool_calls_json"):
                if item.get("generation_status")=="imported" and item.get("imported_tool_runs_json"):
                    item["tool_runs"]=json.loads(item["imported_tool_runs_json"])
                else:
                    runs=db.execute("SELECT tr.*,s.name AS server_name FROM tool_runs tr LEFT JOIN mcp_servers s ON s.id=tr.server_id WHERE tr.assistant_message_id=? ORDER BY tr.id",(item["id"],)).fetchall()
                    item["tool_runs"]=[{"tool_name":r["display_name"] or r["tool_name"],"server_name":r["server_name"] or "MCP","plugin_name":r["plugin_name"],"arguments":json.loads(r["arguments"] or "{}"),"status":r["status"],"result":json.loads(r["result"]) if r["result"] else None,"error":r["error"]} for r in runs]
            result.append(item)
        return result

    @router.post("/export-preview")
    def export_preview(options:ExportOptions):
        check_options(options)
        with connect() as db:
            cid=options.conversation_ids[0]
            conv=db.execute("SELECT c.*,ch.name AS character_name FROM conversations c JOIN characters ch ON ch.id=c.character_id WHERE c.id=?",(cid,)).fetchone()
            if not conv: raise HTTPException(404,"所选对话不存在")
            rows=db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id",(cid,)).fetchall()
            chosen=enriched(db,filtered(rows,options))
        original=render(conv["title"],conv["character_name"],chosen,ExportOptions(conversation_ids=[cid],format="markdown",include_tools=True,include_reasoning=True,include_monologue=True)).decode()
        selected=render(conv["title"],conv["character_name"],chosen,options).decode()
        added=len(filtered(rows,options))-len(filtered(rows,options.model_copy(update={"format":"markdown"}))) if options.format=="json" else 0
        return {"original":original[:5000],"selected":selected[:5000],"truncated":len(original)>5000 or len(selected)>5000,"tool_messages_added":added}

    @router.post("/export")
    def export_many(options:ExportOptions):
        check_options(options)
        archive=io.BytesIO()
        with connect() as db, zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as z:
            for cid in options.conversation_ids:
                conv=db.execute("SELECT c.*,ch.name AS character_name FROM conversations c JOIN characters ch ON ch.id=c.character_id WHERE c.id=?",(cid,)).fetchone()
                if not conv: raise HTTPException(404,"所选对话不存在")
                rows=db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id",(cid,)).fetchall()
                body=render(conv["title"],conv["character_name"],enriched(db,filtered(rows,options)),options)
                z.writestr(f"conversation-{cid}."+{"json":"json","markdown":"md","txt":"txt"}[options.format],body)
        return Response(archive.getvalue(),media_type="application/zip",headers={"Content-Disposition":"attachment; filename=yosaki-chats.zip","Cache-Control":"no-store"})

    @router.post("/preview")
    async def preview(files:list[UploadFile]=File(...)):
        if len(files)>200: raise HTTPException(413,"最多 200 个文件")
        results=[]
        total=0
        with connect() as db:
            for file in files:
                data=await file.read(MAX_FILE+1)
                total+=len(data)
                if total>MAX_TOTAL:
                    results.append({"name":file.filename,"error":"批量文件总量超过 64 MiB"}); continue
                digest=hashlib.sha256(data).hexdigest()
                try:
                    title,rows=load_file(file.filename or "",data)
                    duplicate=bool(db.execute("SELECT 1 FROM imported_archives WHERE digest=?",(digest,)).fetchone())
                    results.append({"name":file.filename,"title":title,"messages":len(rows),"digest":digest,"duplicate":duplicate})
                except HTTPException as exc: results.append({"name":file.filename,"error":exc.detail})
                except (TypeError,ValueError,IndexError): results.append({"name":file.filename,"error":"文件内容不符合聊天档案格式"})
        return results

    @router.post("/import")
    async def import_many(character_id:int,files:list[UploadFile]=File(...)):
        if len(files)>200: raise HTTPException(413,"最多 200 个文件")
        results=[]
        total=0
        for file in files:
            data=await file.read(MAX_FILE+1)
            total+=len(data)
            if total>MAX_TOTAL:
                results.append({"name":file.filename,"status":"failed","error":"批量文件总量超过 64 MiB"}); continue
            try:
                title,rows=load_file(file.filename or "",data)
                digest=hashlib.sha256(data).hexdigest()
                with connect() as db:
                    if not db.execute("SELECT 1 FROM characters WHERE id=?",(character_id,)).fetchone(): raise HTTPException(404,"目标角色不存在")
                    if db.execute("SELECT 1 FROM imported_archives WHERE digest=?",(digest,)).fetchone():
                        results.append({"name":file.filename,"status":"skipped"}); continue
                    stamp=now()
                    cid=db.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) VALUES(?,?,?,?)",(character_id,title,stamp,stamp)).lastrowid
                    for row in rows:
                        archived=[{"run_id":-(index+1),"tool_name":str(run.get("tool_name") or "工具")[:160],"server_name":str(run.get("server_name") or "MCP")[:160],"plugin_name":str(run.get("plugin_name") or "")[:160],"description":f"历史状态：{str(run.get('status') or '未知')[:40]}","arguments":run.get("arguments") if isinstance(run.get("arguments"),dict) else {},"status":"archived","result":run.get("result"),"error":str(run.get("error") or "")[:500]} for index,run in enumerate(row.get("tool_runs") or [])]
                        db.execute("INSERT INTO messages(conversation_id,role,content,created_at,model,tool_calls_json,tool_call_id,tool_name,reasoning_content,monologue_content,reasoning_source,generation_status,imported_tool_runs_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,row["role"],row["content"],row.get("created_at") or stamp,row.get("model"),row.get("tool_calls_json"),row.get("tool_call_id"),row.get("tool_name"),row.get("reasoning_content"),row.get("monologue_content"),row.get("reasoning_source"),"imported",json.dumps(archived,ensure_ascii=False) if archived else None))
                    db.execute("INSERT INTO imported_archives(digest,conversation_id,imported_at) VALUES(?,?,?)",(digest,cid,stamp))
                results.append({"name":file.filename,"status":"imported","conversation_id":cid})
            except HTTPException as exc: results.append({"name":file.filename,"status":"failed","error":exc.detail})
            except (sqlite3.DatabaseError,TypeError,ValueError): results.append({"name":file.filename,"status":"failed","error":"聊天档案无法写入"})
        return results
    app.include_router(router)
