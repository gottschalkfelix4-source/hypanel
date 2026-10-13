from pathlib import Path
from hypanel import runner
import pytest


def test_download_account_login_only_never_installs_or_replaces_server_files(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'GAME',tmp_path)
    monkeypatch.setenv('HYPANEL_DOWNLOAD_AUTH_ONLY','true')
    original=tmp_path/'HytaleServer.jar';original.write_bytes(b'keep-server')
    commands=[]
    monkeypatch.setattr(runner.subprocess,'call',lambda argv,cwd:commands.append(argv) or 0)
    with pytest.raises(SystemExit) as result:runner.install()
    assert result.value.code==0 and '-print-version' in commands[0]
    assert '-download-path' not in commands[0] and original.read_bytes()==b'keep-server'


def test_staged_update_preserves_worlds_config_mods_and_auth(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'GAME',tmp_path)
    server=tmp_path/'Server';server.mkdir()
    (server/'HytaleServer.jar').write_bytes(b'old-jar')
    (server/'config.json').write_text('{"keep":true}')
    (server/'auth.enc').write_bytes(b'keep-auth')
    mods=server/'mods';mods.mkdir();(mods/'plugin.jar').write_bytes(b'keep-mod')
    worlds=server/'universe/worlds';worlds.mkdir(parents=True);(worlds/'save').write_bytes(b'keep-world')
    staged=tmp_path/'updater/staging/Server';staged.mkdir(parents=True)
    (staged/'HytaleServer.jar').write_bytes(b'new-jar')
    (staged/'config.json').write_text('{"overwrite":true}')
    (staged.parent/'Assets.zip').write_bytes(b'new-assets')
    runner.apply_staged()
    assert (server/'HytaleServer.jar').read_bytes()==b'new-jar'
    assert (tmp_path/'Assets.zip').read_bytes()==b'new-assets'
    assert (server/'config.json').read_text()=='{"keep":true}'
    assert (server/'auth.enc').read_bytes()==b'keep-auth'
    assert (mods/'plugin.jar').read_bytes()==b'keep-mod'
    assert (worlds/'save').read_bytes()==b'keep-world'
    assert not (tmp_path/'updater/staging').exists()


def test_official_downloader_payload_is_installed_without_overwriting_settings(tmp_path,monkeypatch):
    import zipfile
    monkeypatch.setattr(runner,'GAME',tmp_path)
    server=tmp_path/'Server';server.mkdir()
    (server/'config.json').write_text('{"preserved":true}')
    (server/'auth.enc').write_bytes(b'keep-auth')
    def downloader(argv,cwd):
        assert '-credentials-path' in argv and str(tmp_path/'.hytale-downloader-credentials.json') in argv
        assert '-skip-update-check' in argv
        with zipfile.ZipFile(tmp_path/'.download.zip','w') as z:
            z.writestr('Server/HytaleServer.jar',b'new-server')
            z.writestr('Server/HytaleServer.aot',b'cache')
            z.writestr('Assets.zip',b'new-assets')
            z.writestr('start.sh','launcher')
            z.writestr('Server/config.json','{"overwrite":true}')
        return 0
    monkeypatch.setattr(runner.subprocess,'call',downloader)
    runner.install()
    assert (server/'HytaleServer.jar').read_bytes()==b'new-server'
    assert (tmp_path/'Assets.zip').read_bytes()==b'new-assets'
    assert (server/'config.json').read_text()=='{"preserved":true}'
    assert (server/'auth.enc').read_bytes()==b'keep-auth'
    assert not (tmp_path/'.download.zip').exists()
    assert not (tmp_path/'.download-staging').exists()
