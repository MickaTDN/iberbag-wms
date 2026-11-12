"""Require complete, disjoint PostgreSQL execution and every critical assertion."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
from scripts.verify_test_report import REQUIRED_POSTGRES, ORIGINAL_POSTGRES


def validate_selection(selection, passed):
    def reject():
        raise ValueError('Partição PostgreSQL ausente, incompleta ou contraditória.')
    if not isinstance(selection, dict) or type(passed) is not int or passed < 1:
        reject()
    index, count = selection.get('index'), selection.get('count')
    if type(index) is not int or type(count) is not int or not 0 <= index < count <= 16:
        reject()
    for name in ('collected', 'selected', 'completed'):
        values = selection.get(name)
        if not isinstance(values, list) or not values or values != sorted(set(values)):
            reject()
        if any(not isinstance(v, str) or re.fullmatch('[a-f0-9]{64}', v) is None for v in values):
            reject()
    expected = [value for value in selection['collected'] if int(value, 16) % count == index]
    if (selection.get('exitstatus') != 0 or expected != selection['selected']
            or selection['completed'] != expected or passed != len(expected)):
        reject()
    return selection


def combine(reports, count, required=None, commit=None):
    if not 1 <= count <= 16 or len(reports) != count:
        raise ValueError('Faltam provas de partições PostgreSQL.')
    required = REQUIRED_POSTGRES if required is None else required
    collected = None
    indices, covered = set(), set()
    totals, executed = Counter(), Counter()
    digests = []
    commits = set()
    for report in reports:
        counts = report.get('counts', {})
        if (report.get('backend') != 'postgres' or report.get('report_scope') != 'shard'
                or report.get('valid') is not True or counts.get('failed', 0)
                or counts.get('skipped', 0)):
            raise ValueError('Uma partição PostgreSQL falhou, foi omitida ou não foi comprovada.')
        functions = report.get('passed_functions', {})
        if (not isinstance(functions, dict) or not functions
                or any(not re.fullmatch('[A-Za-z0-9_.]+', key)
                       or type(value) is not int or value < 1 for key, value in functions.items())
                or sum(functions.values()) != counts.get('passed')
                or not re.fullmatch('[a-f0-9]{64}', report.get('junit_sha256', ''))):
            raise ValueError('Contagens ou identidade do relatório PostgreSQL inválidas.')
        selection = validate_selection(report.get('selection', {}), counts.get('passed', 0))
        if selection['count'] != count or selection['index'] in indices:
            raise ValueError('Partições repetidas ou de execuções diferentes.')
        if collected is None:
            collected = selection['collected']
        if collected != selection['collected'] or covered.intersection(selection['completed']):
            raise ValueError('Coleção divergente ou testes executados em duplicado.')
        indices.add(selection['index']); covered.update(selection['completed'])
        commits.add(report.get('commit'))
        digests.append(report.get('junit_sha256', ''))
        totals.update(counts); executed.update(functions)
    if covered != set(collected) or indices != set(range(count)):
        raise ValueError('Existem testes recolhidos que não foram executados.')
    if len(commits) != 1 or (commit is not None and commits != {commit}):
        raise ValueError('Provas de commits diferentes da revisão atual.')
    missing = sorted(required - set(executed))
    return dict(backend='postgres', commit=next(iter(commits)), report_scope='complete',
                junit_sha256=hashlib.sha256(''.join(sorted(digests)).encode()).hexdigest(),
                counts=dict(totals), required_passed=sorted(required & set(executed)),
                original_critical_passed=sorted(ORIGINAL_POSTGRES & set(executed)),
                passed_functions=dict(sorted(executed.items())), missing_required=missing,
                shards=count, collected=len(collected), completed=len(covered),
                valid=not missing)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--shards', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        reports = [json.loads(p.read_text(encoding='utf-8'))
                   for p in args.folder.rglob('postgres-shard-proof.json')]
        result = combine(reports, args.shards, commit=os.getenv('GITHUB_SHA'))
    except (OSError, ValueError, TypeError, KeyError):
        raise SystemExit('Regressão PostgreSQL completa não comprovada; integração recusada.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    counts = result['counts']
    print(f"::notice title=DDN postgres evidence::passed={counts.get('passed',0)}; skipped={counts.get('skipped',0)}; required={len(result['required_passed'])}; original={len(result['original_critical_passed'])}; collected={result['collected']}; completed={result['completed']}; valid={result['valid']}")
    if not result['valid']:
        raise SystemExit('Verificações críticas PostgreSQL em falta; integração recusada.')


if __name__ == '__main__':
    main()
