"""Explicit maintainer command: resolve wheels, record every platform wheel hash from PyPI."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request


def generate(source,target):
    with tempfile.TemporaryDirectory() as folder:
        report=Path(folder)/'report.json'
        subprocess.run([sys.executable,'-m','pip','install','--dry-run','--ignore-installed','--only-binary=:all:',
                        '-r',str(source),'--report',str(report)],check=True,stdout=subprocess.DEVNULL)
        packages=json.loads(report.read_text())['install']
    def record(package):
        name=package['metadata']['name'];version=package['metadata']['version']
        with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json',timeout=30) as response: data=json.load(response)
        hashes=sorted({f['digests']['sha256'] for f in data['urls'] if f['filename'].endswith('.whl') and not f['yanked']})
        if not hashes: raise ValueError('No verified wheels: '+name)
        continuation=' '+chr(92)+chr(10)
        return name+'=='+version+continuation+''.join('    --hash=sha256:'+h+(continuation if i<len(hashes)-1 else chr(10)) for i,h in enumerate(hashes))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: lines=sorted(pool.map(record,packages))
    Path(target).write_text('# Generated from '+str(source)+'; review changes and test Python 3.12 Linux/Windows.\n--require-hashes\n--only-binary=:all:\n'+''.join(lines))


if __name__=='__main__': generate(Path(sys.argv[1]),Path(sys.argv[2]))
