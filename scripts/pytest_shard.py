"""Explicit deterministic CI partitioning; no production or inherited-env switch."""
import hashlib
import json
from pathlib import Path
import pytest


def identity(nodeid):
    # Parameter values may be private: persist only one-way identities.
    return hashlib.sha256(nodeid.encode('utf-8')).hexdigest()


def partition(value):
    try:
        index, count = map(int, value.split('/'))
        if 0 <= index < count <= 16:
            return index, count
    except (ValueError, AttributeError):
        pass
    raise pytest.UsageError('Partição: indicar índice/total, com 0 <= índice < total <= 16.')


def pytest_addoption(parser):
    parser.addoption('--ddn-shard', help='Explicit zero-based INDEX/COUNT partition.')
    parser.addoption('--ddn-shard-report', help='Private-free collection/execution evidence path.')


def pytest_configure(config):
    value = config.getoption('--ddn-shard')
    if not value or not config.getoption('--ddn-shard-report'):
        raise pytest.UsageError('Partição e relatório são ambos obrigatórios neste plugin.')
    config._ddn_partition = partition(value)
    config._ddn_finished = set()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    index, count = config._ddn_partition
    all_ids = sorted(identity(item.nodeid) for item in items)
    if len(all_ids) != len(set(all_ids)):
        raise pytest.UsageError('Identidades de testes duplicadas na coleção.')
    chosen, excluded = [], []
    for item in items:
        (chosen if int(identity(item.nodeid), 16) % count == index else excluded).append(item)
    config._ddn_selection = dict(index=index, count=count, collected=all_ids,
                                 selected=sorted(identity(item.nodeid) for item in chosen))
    items[:] = chosen
    config.hook.pytest_deselected(items=excluded)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    yield
    if call.when == 'teardown':
        item.config._ddn_finished.add(identity(item.nodeid))


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    evidence = dict(getattr(config, '_ddn_selection', {}),
                    completed=sorted(config._ddn_finished), exitstatus=int(exitstatus))
    target = Path(config.getoption('--ddn-shard-report'))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, sort_keys=True), encoding='utf-8')
