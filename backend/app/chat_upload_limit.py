"""Bound multipart bytes before the framework spools untrusted uploads."""
import json
from .chat_attachments import MAX_BYTES


class UploadBodyLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method')!='POST' or scope.get('path')!='/api/chat/attachments':
            return await self.app(scope,receive,send)
        limit=MAX_BYTES+128*1024
        headers=dict(scope.get('headers',[]))
        try:declared=int(headers.get(b'content-length',b'0'))
        except ValueError:declared=limit+1
        async def reject():
            body=json.dumps({'detail':'上传请求超过 8 MiB 文件及表单容量限制'},ensure_ascii=False).encode()
            await send({'type':'http.response.start','status':413,'headers':[(b'content-type',b'application/json; charset=utf-8')]})
            await send({'type':'http.response.body','body':body})
        if declared>limit:return await reject()
        chunks=[];size=0
        while True:
            event=await receive()
            if event['type']=='http.disconnect':return
            size+=len(event.get('body',b''))
            if size>limit:return await reject()
            chunks.append(event.get('body',b''))
            if not event.get('more_body',False):break
        data=b''.join(chunks);del chunks
        replayed=False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed=True
                return {'type':'http.request','body':data,'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
