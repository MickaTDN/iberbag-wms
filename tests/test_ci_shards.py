"""No partition may masquerade as complete PostgreSQL regression evidence."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from scripts import pytest_shard as plugin
from scripts.verify_postgres_shards import combine, validate_selection


def example_reports(count=4):
    collected=sorted(plugin.identity(f'tests/test_synthetic.py::test_case[{i}]') for i in range(80))
    reports=[]
    for index in range(count):
        selected=[value for value in collected if int(value,16)%count==index]
        reports.append(dict(backend='postgres',report_scope='shard',commit='a'*40,valid=True,
            junit_sha256='b'*64,counts={'passed':len(selected)},passed_functions={'test_synthetic.test_case':len(selected)},
            selection=dict(index=index,count=count,collected=collected,selected=selected,completed=selected[:],exitstatus=0)))
    return reports


def test_partition_selects_every_case_once_and_preserves_local_order():
    items=[SimpleNamespace(nodeid=f'tests/test_synthetic.py::test_case[{i}]') for i in range(80)]
    selected=[]
    for index in range(4):
        excluded=[]
        config=SimpleNamespace(_ddn_partition=(index,4),hook=SimpleNamespace(pytest_deselected=lambda items:excluded.extend(items)))
        current=items[:];plugin.pytest_collection_modifyitems(config,current)
        assert [item for item in items if item not in excluded]==current
        assert len(current)+len(excluded)==len(items)
        selected.extend(item.nodeid for item in current)
    assert sorted(selected)==sorted(item.nodeid for item in items)
    assert plugin.partition('0/1')==(0,1)


@pytest.mark.parametrize('value',['','-1/4','4/4','0/0','0/17','1','a/b'])
def test_partition_rejects_invalid_configuration(value):
    with pytest.raises(pytest.UsageError):plugin.partition(value)


def test_partition_requires_explicit_options_and_rejects_duplicate_identity():
    config=SimpleNamespace(getoption=lambda name:None)
    with pytest.raises(pytest.UsageError):plugin.pytest_configure(config)
    config._ddn_partition=(0,1)
    item=SimpleNamespace(nodeid='tests/test_synthetic.py::test_case')
    with pytest.raises(pytest.UsageError):plugin.pytest_collection_modifyitems(config,[item,item])


def test_partition_completion_proof_has_only_digests(tmp_path):
    target=tmp_path/'selection.json'
    config=SimpleNamespace(_ddn_selection={'index':0,'count':1,'collected':[], 'selected':[]},_ddn_finished=set(),getoption=lambda name:str(target))
    item=SimpleNamespace(nodeid='test_case[PRIVATE-SENTINEL]',config=config)
    hook=plugin.pytest_runtest_makereport(item,SimpleNamespace(when='teardown'))
    next(hook)
    with pytest.raises(StopIteration):hook.send(SimpleNamespace(get_result=lambda:SimpleNamespace(failed=False)))
    plugin.pytest_sessionfinish(SimpleNamespace(config=config),0)
    report=json.loads(target.read_text())
    assert report['completed']==[plugin.identity(item.nodeid)]
    assert 'PRIVATE-SENTINEL' not in target.read_text()


def test_aggregate_requires_all_cases_and_critical_functions():
    reports=example_reports()
    result=combine(reports,4,{'test_synthetic.test_case'},commit='a'*40)
    assert result['valid'] and result['collected']==result['completed']==result['counts']['passed']==80
    assert not combine(reports,4,{'test_synthetic.test_missing'})['valid']


@pytest.mark.parametrize('case',['missing_shard','duplicate_shard','different_collection','missing_case','wrong_partition','incomplete_execution','failed_session','skipped_test','failed_test','invalid_proof','wrong_commit','foreign_commit','non_shard','wrong_count','bad_digest','duplicate_case','count_mismatch','function_mismatch','invalid_junit_digest'])
def test_aggregate_rejects_incomplete_contradictory_or_replayed_evidence(case):
    reports=deepcopy(example_reports());s=reports[0]['selection'];commit='a'*40
    if case=='missing_shard':reports.pop()
    elif case=='duplicate_shard':reports[0]=deepcopy(reports[1])
    elif case=='different_collection':s['collected'].pop()
    elif case=='missing_case':s['selected'].pop();s['completed']=s['selected'][:];reports[0]['counts']['passed']-=1
    elif case=='wrong_partition':s['index']=1
    elif case=='incomplete_execution':s['completed'].pop()
    elif case=='failed_session':s['exitstatus']=1
    elif case=='skipped_test':reports[0]['counts']['skipped']=1
    elif case=='failed_test':reports[0]['counts']['failed']=1
    elif case=='invalid_proof':reports[0]['valid']=False
    elif case=='wrong_commit':reports[0]['commit']='c'*40
    elif case=='foreign_commit':commit='c'*40
    elif case=='non_shard':reports[0]['report_scope']='complete'
    elif case=='wrong_count':s['count']=3
    elif case=='bad_digest':s['completed'][0]='PRIVATE-SENTINEL'
    elif case=='duplicate_case':s['completed'].append(s['completed'][0])
    elif case=='count_mismatch':reports[0]['counts']['passed']-=1
    elif case=='function_mismatch':reports[0]['passed_functions']['test_synthetic.test_case']-=1
    elif case=='invalid_junit_digest':reports[0]['junit_sha256']='invalid'
    with pytest.raises(ValueError):combine(reports,4,set(),commit=commit)


def test_selection_rejects_absent_evidence():
    with pytest.raises(ValueError):validate_selection({},1)


def test_failure_annotation_omits_parameters_and_exception_data(monkeypatch,capsys):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    item=SimpleNamespace(nodeid='tests/test_synthetic.py::test_case[PRIVATE-SENTINEL]')
    hook=plugin.pytest_runtest_makereport(item,SimpleNamespace(when='call'))
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda:SimpleNamespace(failed=True,longrepr='PRIVATE-EXCEPTION')))
    output=capsys.readouterr().out
    assert '::error title=DDN failed test::tests/test_synthetic.py::test_case' in output
    assert 'PRIVATE' not in output
