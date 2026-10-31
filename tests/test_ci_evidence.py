"""A green job alone must not mask omitted or skipped PostgreSQL assertions."""
import json
from scripts.verify_test_report import verify
import pytest


def test_postgres_evidence_rejects_omitted_and_skipped_cases(tmp_path):
    path=tmp_path/'report.xml'
    path.write_text('<testsuites><testsuite><testcase classname="tests.test_ddn" name="test_ok"/></testsuite></testsuites>')
    assert not verify(path,'postgres')['valid']
    assert verify(path,'postgres',{'test_ddn.test_ok'})['valid']
    path.write_text('<testsuite><testcase classname="tests.test_ddn" name="test_ok"><skipped/></testcase></testsuite>')
    assert not verify(path,'postgres',{'test_ddn.test_ok'})['valid']


def test_evidence_contains_no_failure_trace_or_test_parameters(tmp_path):
    path=tmp_path/'report.xml'
    path.write_text('<testsuite><testcase classname="tests.test_ddn" name="test_ok[PRIVATE-SENTINEL]"><failure>PRIVATE-SENTINEL</failure></testcase></testsuite>')
    result=verify(path,'sqlite')
    assert not result['valid']
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)


def test_evidence_rejects_empty_report_and_entities(tmp_path):
    path=tmp_path/'report.xml';path.write_text('<testsuites/>')
    assert not verify(path,'sqlite')['valid']
    path.write_text('<!DOCTYPE a [<!ENTITY a "entity">]><testsuites/>')
    with pytest.raises(ValueError):verify(path,'sqlite')
