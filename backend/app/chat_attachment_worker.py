"""Untrusted extraction child with bounded lifetime and memory, no network."""
import base64
import json
import os
import sys


def main():
    def audit(event,args):
        if event.startswith('socket.'):raise RuntimeError('attachment_network_disabled')
    sys.addaudithook(audit)
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_=[('per_process',ctypes.c_int64),('per_job',ctypes.c_int64),('flags',wintypes.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ('r','w','o','rb','wb','ob')]
        class Limits(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process',ctypes.c_size_t),('job',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        dll=ctypes.WinDLL('kernel32',use_last_error=True)
        dll.CreateJobObjectW.restype=wintypes.HANDLE;dll.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        job=dll.CreateJobObjectW(None,None)
        limits=Limits();limits.basic.flags=0x100;limits.process=512*1024*1024
        dll.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        dll.GetCurrentProcess.restype=wintypes.HANDLE
        dll.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        if not job or not dll.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not dll.AssignProcessToJobObject(job,dll.GetCurrentProcess()):raise RuntimeError('resource_limit_unavailable')
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(12,12))
    from .chat_attachments import parse
    request=json.loads(sys.stdin.buffer.read(12*1024*1024))
    try:
        result=parse(request['name'],base64.b64decode(request['data'],validate=True))
        print(json.dumps({'result':result},ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'error':getattr(exc,'detail','文件无法安全提取'),'status':getattr(exc,'status_code',422)},ensure_ascii=True))


if __name__=='__main__':main()
