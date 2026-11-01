"""Private output is created exclusively; no fake plaintext remains after errors."""
import os
import pytest
from iberbag.private_files import (private_folder, owned_file, private_binary_file,
                                  private_temporary, save_private_json)


def test_private_outputs_are_exclusive_and_temporaries_removed_on_error(tmp_path):
    folder=private_folder(tmp_path/'private',create=True)
    save_private_json(folder/'manifest.json',{'fictitious':True})
    assert owned_file(folder,'manifest.json').is_file()
    if os.name!='nt':assert (folder/'manifest.json').stat().st_mode & 0o077==0
    with pytest.raises(FileExistsError):
        with private_binary_file(folder,'manifest.json'):pass
    with pytest.raises(RuntimeError):
        with private_temporary(folder) as stream:
            stream.write(b'FICTITIOUS');stream.flush()
            assert len(list(folder.glob('wms-plain-*')))==1
            raise RuntimeError('Synthetic cancellation')
    assert not list(folder.glob('wms-plain-*'))


def test_private_files_refuse_hardlinks_and_path_escape(tmp_path):
    folder=private_folder(tmp_path/'private',create=True)
    with private_binary_file(folder,'manifest.json') as stream:stream.write(b'FICTITIOUS')
    os.link(folder/'manifest.json',tmp_path/'alias.json')
    with pytest.raises(ValueError):owned_file(folder,'manifest.json')
    with pytest.raises(ValueError):owned_file(folder,'../outside.json')


def test_private_files_refuse_unsafe_write_permissions(tmp_path):
    folder=private_folder(tmp_path/'private',create=True)
    with private_binary_file(folder,'manifest.json') as stream:stream.write(b'FICTITIOUS')
    if os.name=='nt':
        import subprocess
        result=subprocess.run(['icacls',str(folder/'manifest.json'),'/grant','*S-1-1-0:(R,W)'],capture_output=True)
        assert result.returncode==0
    else:(folder/'manifest.json').chmod(0o666)
    with pytest.raises(ValueError):owned_file(folder,'manifest.json')
