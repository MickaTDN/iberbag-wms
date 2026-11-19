"""Offline import of a manually obtained official archive. Never executes binaries."""
import argparse,hashlib,json,re,shutil,stat,struct,tempfile,zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit

MAX_ARCHIVE=1024**3
MAX_EXPANDED=3*1024**3
REQUIRED={'bin/postgres.exe','bin/pg_ctl.exe','bin/pg_controldata.exe','bin/initdb.exe','bin/psql.exe'}


def analyse(snapshot,origin,version,expected):
    url=urlsplit(origin)
    if url.scheme!='https' or url.hostname not in {'get.enterprisedb.com','www.enterprisedb.com','ftp.postgresql.org'} or url.username or url.password or url.query or url.fragment:
        raise ValueError('Origem oficial HTTPS sem credenciais obrigatória')
    if not re.fullmatch(r'17\.[0-9]+',version) or not re.fullmatch('[a-fA-F0-9]{64}',expected):raise ValueError('Versão 17.x e SHA-256 independente obrigatórios')
    named=re.search(r'postgresql-([0-9]+\.[0-9]+)[-_]',url.path)
    if named and named.group(1)!=version:raise ValueError('Versão contradiz origem')
    digest=hashlib.sha256();size=0
    while chunk:=snapshot.read(1024*1024):
        size+=len(chunk)
        if size>MAX_ARCHIVE:raise ValueError('Arquivo excessivo')
        digest.update(chunk)
    if digest.hexdigest()!=expected.lower():raise ValueError('SHA-256 divergente')
    # Bound central-directory allocation before zipfile parses untrusted metadata.
    snapshot.seek(max(0,size-65577));tail=snapshot.read(65577);end=tail.rfind(b'PK\x05\x06')
    if end<0 or len(tail)-end<22:raise ValueError('Diretório ZIP ausente')
    if end>=20 and tail[end-20:end-16]==b'PK\x06\x07':raise ValueError('ZIP64 fora dos limites deste importador')
    disk,central_disk,disk_entries,total_entries,central_size=struct.unpack_from('<HHHHI',tail,end+4)
    if disk or central_disk or disk_entries!=total_entries or total_entries>30000 or central_size>16*1024**2:raise ValueError('Metadados ZIP excessivos ou multidisco')
    snapshot.seek(0);rows=[];seen=set();expanded=0
    with zipfile.ZipFile(snapshot) as archive:
        if len(archive.infolist())>30000:raise ValueError('Demasiadas entradas')
        for item in archive.infolist():
            raw=item.filename
            if raw!=item.orig_filename:raise ValueError('Nome ZIP truncado')
            if '\\' in raw or ':' in raw or raw.startswith('/') or '..' in PurePosixPath(raw).parts or stat.S_ISLNK(item.external_attr>>16):raise ValueError('Caminho ou link inseguro')
            parts=PurePosixPath(raw).parts
            if not parts or parts[0]!='pgsql':raise ValueError('Layout oficial pgsql/ esperado; não renomear ficheiros para contornar')
            if item.is_dir():continue
            relative='/'.join(parts[1:])
            if not relative or any(p in ('.','..') or p.endswith((' ','.')) or re.search(r'[<>:"|?*\x00-\x1f]',p) for p in parts):raise ValueError('Nome incompatível com o catálogo aprovado')
            if any(p.upper().split('.')[0] in {'CON','PRN','AUX','NUL','CONIN$','CONOUT$',*(f'{prefix}{i}' for prefix in ('COM','LPT') for i in '¹²³'),*(f'COM{i}' for i in range(10)),*(f'LPT{i}' for i in range(10))} for p in parts):raise ValueError('Nome reservado Windows')
            if relative.casefold() in seen:raise ValueError('Identidades de ficheiro duplicadas')
            seen.add(relative.casefold());expanded+=item.file_size
            if item.flag_bits&1 or item.file_size>512*1024**2 or expanded>MAX_EXPANDED or item.file_size>1000*max(item.compress_size,1):raise ValueError('ZIP cifrado ou expansão excessiva')
            if relative.lower().startswith('data/') or relative.endswith('/PG_VERSION') or relative=='PG_VERSION':raise ValueError('Não importar clusters ou bases existentes')
            h=hashlib.sha256();count=0;header=b''
            with archive.open(item) as stream:
                while chunk:=stream.read(1024*1024):
                    count+=len(chunk);h.update(chunk)
                    if len(header)<4096:header=(header+chunk)[:4096]
                    if count>item.file_size:raise ValueError('Estrutura ZIP divergente')
            if count!=item.file_size:raise ValueError('Quantidade de bytes divergente')
            if relative in REQUIRED:
                if len(header)<64 or header[:2]!=b'MZ':raise ValueError('Executável PE inválido')
                offset=struct.unpack_from('<I',header,60)[0]
                if offset+6>len(header) or header[offset:offset+4]!=b'PE\0\0' or struct.unpack_from('<H',header,offset+4)[0]!=0x8664:raise ValueError('Exige distribuição Windows x64')
            rows.append(dict(path='components/postgres/'+relative,sha256=h.hexdigest(),size=count,source=raw))
    if not REQUIRED.issubset({r['path'].removeprefix('components/postgres/') for r in rows}):raise ValueError('Ferramentas PostgreSQL obrigatórias em falta')
    return dict(origin=origin,version=version,archive_sha256=digest.hexdigest(),files={r['path']:r['sha256'] for r in rows},entries=rows,expanded_bytes=expanded)


def process(source,origin,version,expected,output=None,approve=False):
    # Private bounded snapshot prevents source replacement between hash/preview/extraction.
    with tempfile.TemporaryFile() as snapshot:
        count=0
        with Path(source).open('rb') as stream:
            while chunk:=stream.read(1024*1024):
                count+=len(chunk)
                if count>MAX_ARCHIVE:raise ValueError('Arquivo excessivo')
                snapshot.write(chunk)
        snapshot.seek(0);report=analyse(snapshot,origin,version,expected)
        if output is None:return report
        if not approve:raise ValueError('Aprovação explícita obrigatória')
        output=Path(output).absolute()
        for parent in (output,*output.parents):
            if parent.is_symlink() or parent.is_junction():raise ValueError('Destino com ligação insegura')
        output.mkdir()  # Never overwrite a prior distribution, partial attempt or GVA.
        snapshot.seek(0)
        with zipfile.ZipFile(snapshot) as archive:
            for row in report['entries']:
                target=output/row['path'];target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(row['source']) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
                with target.open('rb') as check:digest=hashlib.file_digest(check,'sha256').hexdigest()
                if digest!=row['sha256']:raise ValueError('Reconciliação de extração falhou; preservar tentativa')
        from datetime import datetime,timezone
        report['approval']=dict(attestation='official-independent-hash-not-GVA',approved_utc=datetime.now(timezone.utc).isoformat())
        (output/'DDN_POSTGRES_APPROVED.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',required=True);p.add_argument('--origin',required=True);p.add_argument('--version',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output')
    a=p.parse_args()
    try:
        report=process(a.archive,a.origin,a.version,a.sha256)
        print(json.dumps(dict(version=report['version'],sha256=report['archive_sha256'],files=len(report['entries']),expanded_bytes=report['expanded_bytes']),indent=2))
        if a.output:
            token='APROVAR POSTGRESQL 17 '+a.sha256.lower()[:12]
            print('Confirme origem/hash por canal independente; não é cópia GVA. Hash calculado localmente não prova proveniência.')
            if input('Escreva '+token+': ')!=token:return 1
            process(a.archive,a.origin,a.version,a.sha256,a.output,True)
            print('Importação reconciliada. Aprovação é atestação técnica; proteger ACL antes do provisionamento.')
    except (ValueError,OSError,zipfile.BadZipFile):print('Importação recusada; origem preservada. Rever versão, origem, hash, layout e destino.');return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
