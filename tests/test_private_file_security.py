"""Negative filesystem tests; every modified ACL belongs to a fresh fictitious file."""
import os
from pathlib import Path
import subprocess

import pytest
from iberbag.private_files import private_folder, owned_file


def icacls(path, *args):
    result = subprocess.run(['icacls', str(path), *args], capture_output=True)
    assert result.returncode == 0, 'Unable to arrange the synthetic Windows ACL fixture.'


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows DACL/owner semantics')
@pytest.mark.parametrize('filename', ['database.dump', 'manifest.json', 'restore-fictional.json'])
def test_windows_refuses_permissive_backup_file(tmp_path, filename):
    folder = private_folder(tmp_path/'private', create=True)
    path = folder/filename
    path.write_bytes(b'FICTITIOUS')
    icacls(path, '/grant', '*S-1-1-0:(R,W)')
    with pytest.raises(ValueError): owned_file(folder, filename)


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows DACL/owner semantics')
def test_windows_refuses_moved_permissive_file(tmp_path):
    folder = private_folder(tmp_path/'private', create=True)
    external = tmp_path/'external.json'
    external.write_bytes(b'FICTITIOUS')
    icacls(external, '/grant', '*S-1-1-0:(R,W)')
    external.replace(folder/'manifest.json')
    with pytest.raises(ValueError): owned_file(folder, 'manifest.json')


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows owner semantics')
def test_windows_refuses_different_file_owner(tmp_path):
    folder = private_folder(tmp_path/'private', create=True)
    path = folder/'manifest.json'
    path.write_bytes(b'FICTITIOUS')
    icacls(path, '/setowner', '*S-1-5-32-544')
    with pytest.raises(ValueError): owned_file(folder, 'manifest.json')

