"""Published revisions are immutable contracts, including on CRLF checkouts."""
import hashlib
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
PUBLISHED=json.loads((ROOT/'migrations/published_sha256.json').read_text())

@pytest.mark.parametrize('name',sorted(PUBLISHED))
def test_published_migration_fingerprints(name):
    raw=(ROOT/name).read_bytes().replace(b'\r\n',b'\n')
    assert hashlib.sha256(raw).hexdigest()==PUBLISHED[name], 'Add a revision; do not rewrite published migrations.'
