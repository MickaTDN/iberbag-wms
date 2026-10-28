"""Expose CI failures through Checks annotations without complete test/fixture dumps."""
from pathlib import Path
import re
import sys
from defusedxml import ElementTree

p=Path(sys.argv[1])
if not p.exists():
    print('::warning::Pytest did not produce its report. Inspect runner setup.')
    raise SystemExit(0)
for case in ElementTree.parse(p).iter('testcase'):
    for error in list(case):
        if error.tag not in ('failure','error'): continue
        message=error.attrib.get('message','')
        # Redact quoted values, paths in temporary runners, URLs and parametrizations.
        summary=message.splitlines()[0][:400]
        summary=re.sub(r''''(?:[^']*)'|"(?:[^"]*)"''','<value>',summary)
        summary=re.sub(r'https?://\S+','<url>',summary)
        name=case.attrib.get('name','collection').split('[')[0]
        # Collection errors need the final exception class, not the whole traceback.
        classes=re.findall(r'(?:^|\n)(?:E\s+)?((?:[A-Za-z_]+Error|[A-Za-z_]+Exception):[^\n]*)',error.text or '')
        if classes:
            detail=re.sub(r''''(?:[^']*)'|"(?:[^"]*)"''','<value>',classes[-1][:300])
            if '://' not in detail:summary+=' / '+detail
        text=(name+': '+summary).replace('%','%25').replace('\r','%0D').replace('\n','%0A')
        print('::error title=Test failure::'+text)
