"""Bounded multipart parsing without application or database imports."""
from email import policy
from email.parser import BytesParser
from fastapi import Request,HTTPException

async def parse_upload(request:Request,*,filename=False):
    content_type=request.headers.get('content-type','')
    if not content_type.startswith('multipart/form-data;') or len(content_type)>200 or '\r' in content_type or '\n' in content_type:raise HTTPException(400,'Selecionar um ficheiro Excel.')
    body=getattr(request.state,'wms_body',b'')
    if not body:raise HTTPException(400,'Ficheiro ausente.')
    try:
        envelope=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\n\r\n')
        boundary=envelope.get_boundary()
        if not boundary or not 1<=len(boundary)<=120 or '\r' in boundary or '\n' in boundary:raise ValueError()
        parts=body.split(b'--'+boundary.encode('ascii'))
        if len(parts)!=6 or parts[0] or parts[-1] not in (b'--',b'--\r\n'):raise ValueError()
        data={}
        for raw in parts[1:-1]:
            if not raw.startswith(b'\r\n') or not raw.endswith(b'\r\n'):raise ValueError()
            header,sep,payload=raw[2:-2].partition(b'\r\n\r\n')
            if not sep or len(header)>4096:raise ValueError()
            part=BytesParser(policy=policy.default).parsebytes(header+b'\r\n\r\n')
            name=part.get_param('name',header='content-disposition')
            if part.defects or part.get_content_maintype()=='multipart' or part.get_content_disposition()!='form-data' or name not in ('excel','csrf','context_id','request_key') or name in data or part.get('Content-Transfer-Encoding'):raise ValueError()
            if name=='excel':
                data[name]=payload
                original_name=part.get_filename() or 'listagem.xlsx'
                if filename and (not 1<=len(original_name)<=240 or any(ord(c)<32 for c in original_name) or '/' in original_name or chr(92) in original_name):raise ValueError()
            else:
                if len(payload)>200:raise ValueError()
                data[name]=payload.decode('utf-8','strict')
        if set(data)!= {'excel','csrf','context_id','request_key'}:raise ValueError()
        if filename:data['source_name']=original_name
        return data
    except (ValueError,UnicodeError,TypeError):raise HTTPException(400,'Upload inválido; selecionar novamente o Excel.')


async def upload_form(request:Request):return await parse_upload(request)
async def named_upload_form(request:Request):return await parse_upload(request,filename=True)
