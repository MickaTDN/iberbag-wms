"""Read-only gates: pinned actions, read-only tokens, no revival of flagged binaries."""
from pathlib import Path
import re

root=Path(__file__).resolve().parent.parent
for p in (root/'.github/workflows').glob('*.yml'):
    content=p.read_text()
    for action in re.findall(r'uses:\s*([^\s#]+)',content):
        if not re.fullmatch(r'[\w.-]+/[\w./-]+@[a-f0-9]{40}',action):raise SystemExit('Action not pinned: '+str(p))
    if re.search(r'pull_request_target|write-all|contents:\s*write',content):raise SystemExit('Unsafe workflow permissions/event: '+str(p))
# The unmerged demo PR must retain its own build suspension.
entry=root/'scripts/build_demo.py'
if entry.exists():
    code=entry.read_text()
    if 'if standalone:' not in code or "raise RuntimeError('Executável autónomo suspenso" not in code:
        raise SystemExit('Custom executable suspension must remain in force.')
print('Pinned Actions, read-only workflow tokens and executable suspension policy verified.')
