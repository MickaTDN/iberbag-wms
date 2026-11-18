"""Fail on new upstream NuGet advisories; reviewed exception is feature-specific."""
import json,os,urllib.request,urllib.parse
DEPENDENCIES={'log4net':'3.6.0','YamlDotNet':'8.1.2'}
# No advisory exceptions: any new applicable issue blocks the component gate.
REVIEWED=frozenset()

def audit(fetch=None):
    def request(url):
        headers={'Accept':'application/vnd.github+json','User-Agent':'DDN-component-review'}
        if os.getenv('GH_TOKEN'):headers['Authorization']='Bearer '+os.environ['GH_TOKEN']
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=30) as r:return json.load(r)
    fetch=fetch or request
    found=set()
    for name,version in DEPENDENCIES.items():
        query=urllib.parse.urlencode({'ecosystem':'nuget','affects':name+'@'+version,'per_page':100})
        rows=fetch('https://api.github.com/advisories?'+query)
        if not isinstance(rows,list) or len(rows)>=100:raise ValueError('Incomplete advisory evidence')
        for row in rows:
            ident=row['ghsa_id'];found.add(ident)
            if ident not in REVIEWED:raise ValueError('Unreviewed Windows component advisory')
    print('Windows component advisory review passed; applicable advisories:',len(found))
    return found

if __name__=='__main__':audit()
