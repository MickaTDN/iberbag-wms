"""Only reviewed upstream assets; no latest URLs or unverified executable fallback."""
import hashlib,json,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def download(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    lock=json.loads((ROOT/'windows/components.lock.json').read_text())
    for item in lock.values():
        target=destination/item['file']
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Existing component differs')
            continue
        with urllib.request.urlopen(item['url'],timeout=90) as response:
            data=response.read(64*1024*1024+1)
        if len(data)>64*1024*1024 or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise ValueError('Upstream component integrity refused')
        with target.open('xb') as stream:stream.write(data)
    return lock

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--destination',required=True)
    download(p.parse_args().destination)
