"""Conservative BOBINA-column preview, isolated and bounded; never executes PDF code."""
from io import BytesIO
import json
import multiprocessing
import os
import re
from threading import BoundedSemaphore

MAX_BYTES = 5 * 1024 * 1024
_PARSERS = BoundedSemaphore(2)

def _memory_limit():
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        return None
    # Windows native Job Object limits this worker, never the web process.
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [('ProcessTime',ctypes.c_longlong),('JobTime',ctypes.c_longlong),('Flags',w.DWORD),('Min',ctypes.c_size_t),('Max',ctypes.c_size_t),('Processes',w.DWORD),('Affinity',ctypes.c_size_t),('Priority',w.DWORD),('Scheduling',w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name,ctypes.c_ulonglong) for name in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
    class Limits(ctypes.Structure):
        _fields_ = [('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),('PeakProcess',ctypes.c_size_t),('PeakJob',ctypes.c_size_t)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]; k.CreateJobObjectW.restype=w.HANDLE
    k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]; k.SetInformationJobObject.restype=w.BOOL
    k.GetCurrentProcess.restype=w.HANDLE
    k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]; k.AssignProcessToJobObject.restype=w.BOOL
    k.CloseHandle.argtypes=[w.HANDLE]
    handle = k.CreateJobObjectW(None,None)
    limits = Limits(); limits.Basic.Flags=0x100|0x2000; limits.ProcessMemory=512*1024*1024
    if not handle or not k.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not k.AssignProcessToJobObject(handle,k.GetCurrentProcess()):
        if handle: k.CloseHandle(handle)
        raise ValueError('Isolamento PDF indisponível; usar transcrição manual comprovada.')
    return handle  # Retained until worker exit; kill-on-close, process memory cap.

def _extract(content):
    from pypdf import PdfReader
    import pypdf.filters
    pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH = 4*1024*1024
    reader = PdfReader(BytesIO(content), strict=True)
    if reader.is_encrypted: raise ValueError('PDF cifrado: transcrição manual necessária.')
    root = reader.trailer['/Root']
    if any(k in root for k in ('/OpenAction','/AA','/AcroForm','/Names')): raise ValueError('PDF ativo/anexos não suportados.')
    if not 1 <= len(reader.pages) <= 10: raise ValueError('PDF: limite de 10 páginas.')
    candidates=[]; issues=[]; fragments_total=0; expanded=0
    for number,page in enumerate(reader.pages,1):
        if '/AA' in page or '/Annots' in page: raise ValueError('PDF com ações/anotações não suportadas.')
        stream=page.get_contents()
        if stream:
            expanded+=len(stream.get_data())
            if expanded>8*1024*1024: raise ValueError('Conteúdo PDF expandido excede o limite.')
        fragments=[]
        def visit(raw, cm, tm, font, size):
            nonlocal fragments_total
            val=raw.strip()
            if not val: return
            fragments_total+=1
            if fragments_total>10000 or len(val)>2000: raise ValueError('Estrutura PDF demasiado extensa.')
            x=tm[4]*cm[0]+tm[5]*cm[2]+cm[4];y=tm[4]*cm[1]+tm[5]*cm[3]+cm[5]
            fragments.append((x,y,val))
        page.extract_text(visitor_text=visit)
        heads=[f for f in fragments if f[2]=='BOBINA']
        if len(heads)!=1:
            issues.append(f'Página {number}: coluna BOBINA não delimitada inequivocamente.');continue
        x,y,_=heads[0]
        right=[f[0] for f in fragments if abs(f[1]-y)<2 and f[0]>x+10]
        ends=[f[1] for f in fragments if f[2] in ('TOTAL','FIM DA TABELA') and f[1]<y]
        if not right or len(ends)!=1:
            issues.append(f'Página {number}: faltam limites explícitos da tabela; transcrição manual.');continue
        for xx,yy,val in fragments:
            if ends[0]<yy<y-2 and x-2<=xx<min(right)-2:
                # A neighboring right-aligned value may begin left of its own
                # header. Header distance alone does not prove column identity.
                if abs(xx-x)>2:
                    issues.append(f'Página {number}, y={yy:.1f}: alinhamento da coluna ambíguo; transcrição manual.')
                    continue
                if not re.fullmatch(r'[0-9]{1,120}',val):
                    issues.append(f'Página {number}, y={yy:.1f}: célula BOBINA ambígua.');continue
                if any(r['page']==number and abs(r['y']-yy)<2 for r in candidates):
                    issues.append(f'Página {number}, y={yy:.1f}: fragmentos sobrepostos na coluna BOBINA.')
                candidates.append(dict(number=val,page=number,x=round(xx,2),y=round(yy,2),original=val))
        if len(candidates)>300: raise ValueError('Guia: limite de 300 bobines.')
    if not candidates: issues.append('Nenhum número extraído de uma coluna BOBINA fiável.')
    if len({r['number'].casefold() for r in candidates})!=len(candidates): issues.append('Números repetidos: rever a guia, sem eliminar linhas automaticamente.')
    return dict(reliable=not issues,candidates=candidates,issues=issues,pages=len(reader.pages))

def _worker(pipe, content):
    handle=None
    try:
        import logging
        logging.getLogger('pypdf').disabled=True
        handle=_memory_limit()
        result=_extract(content)
    except Exception:
        result=dict(reliable=False,candidates=[],issues=['PDF não extraível com segurança; transcrição manual comprovada necessária.'])
    try: pipe.send_bytes(json.dumps(result,ensure_ascii=False).encode())
    finally: pipe.close()

def analyze(content):
    if not isinstance(content,bytes) or not content.startswith(b'%PDF-') or not 1<=len(content)<=MAX_BYTES:
        raise ValueError('Selecionar PDF até 5 MiB.')
    if not _PARSERS.acquire(blocking=False):
        return dict(reliable=False,candidates=[],issues=['Análise PDF ocupada; tentar novamente ou usar transcrição manual comprovada.'])
    try: return _analyze_worker(content)
    finally: _PARSERS.release()

def _analyze_worker(content):
    ctx=multiprocessing.get_context('spawn'); parent,child=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_worker,args=(child,content),daemon=True)
    try:
        process.start();child.close()
        if not parent.poll(10): return dict(reliable=False,candidates=[],issues=['Tempo de análise excedido; usar transcrição manual comprovada.'])
        try: return json.loads(parent.recv_bytes(128*1024))
        except (EOFError,OSError,ValueError): return dict(reliable=False,candidates=[],issues=['Análise PDF interrompida; usar transcrição manual comprovada.'])
    finally:
        child.close();parent.close()
        if process.pid:
            if process.is_alive(): process.terminate()
            process.join(2)
            if process.is_alive(): process.kill();process.join(2)
        process.close()
