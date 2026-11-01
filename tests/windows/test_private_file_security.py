"""Negative filesystem tests; every modified ACL belongs to a fresh fictitious file."""
import os
from pathlib import Path
import subprocess

import pytest
from iberbag.private_files import (private_folder, owned_file, private_binary_file,
                                  private_temporary, save_private_json)


def fictional_file(folder,name):
    with private_binary_file(folder,name) as stream:stream.write(b'FICTITIOUS')
    return folder/name


def icacls(path, *args):
    result = subprocess.run(['icacls', str(path), *args], capture_output=True)
    assert result.returncode == 0, 'Unable to arrange the synthetic Windows ACL fixture.'


@pytest.mark.parametrize('filename', ['database.dump', 'manifest.json', 'restore-fictional.json'])
def test_windows_refuses_permissive_backup_file(tmp_path, filename):
    folder = private_folder(tmp_path/'private', create=True)
    path = fictional_file(folder,filename)
    icacls(path, '/grant', '*S-1-1-0:(R,W)')
    with pytest.raises(ValueError): owned_file(folder, filename)


def test_windows_refuses_moved_permissive_file(tmp_path):
    folder = private_folder(tmp_path/'private', create=True)
    outside = private_folder(tmp_path/'outside',create=True)
    external = fictional_file(outside,'external.json')
    icacls(external, '/grant', '*S-1-1-0:(R,W)')
    external.replace(folder/'manifest.json')
    with pytest.raises(ValueError): owned_file(folder, 'manifest.json')


def test_windows_refuses_different_file_owner(tmp_path):
    folder = private_folder(tmp_path/'private', create=True)
    path = fictional_file(folder,'manifest.json')
    icacls(path, '/setowner', '*S-1-5-32-544')
    with pytest.raises(ValueError): owned_file(folder, 'manifest.json')


def test_windows_refuses_different_folder_owner(tmp_path):
    folder=private_folder(tmp_path/'private',create=True)
    icacls(folder,'/setowner','*S-1-5-32-544')
    with pytest.raises(ValueError):private_folder(folder)


def test_windows_accepts_only_safe_inheritance_and_private_temporary(tmp_path):
    folder=private_folder(tmp_path/'private',create=True)
    save_private_json(folder/'manifest.json',{'fictitious':True})
    assert owned_file(folder,'manifest.json').is_file()
    icacls(folder/'manifest.json','/reset')
    assert owned_file(folder,'manifest.json').is_file()  # Safe legacy inherited ACL.
    with private_temporary(folder) as stream:
        stream.write(b'FICTITIOUS PLAIN');stream.flush()
        names=[p.name for p in folder.glob('wms-plain-*')]
        assert len(names)==1 and owned_file(folder,names[0]).is_file()
    assert not list(folder.glob('wms-plain-*'))


def test_windows_refuses_junctions_and_hardlinked_files(tmp_path):
    folder=private_folder(tmp_path/'private',create=True)
    fictional_file(folder,'manifest.json')
    os.link(folder/'manifest.json',tmp_path/'alias.json')
    with pytest.raises(ValueError):owned_file(folder,'manifest.json')
    link=tmp_path/'junction'
    result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(folder)],capture_output=True)
    assert result.returncode==0,'Unable to arrange synthetic junction.'
    try:
        with pytest.raises(ValueError):private_folder(link)
        with pytest.raises(ValueError):owned_file(link,'manifest.json')
    finally:link.rmdir()

