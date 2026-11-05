"""The development signing identity must persist, never silently rotate."""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess

import pytest


def load_signing():
    path=Path(__file__).resolve().parents[2]/'scripts/local_signing.py'
    spec=importlib.util.spec_from_file_location('local_signing_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def existing_identity(tmp_path):
    directory=tmp_path/'signing';directory.mkdir(mode=0o700)
    fingerprint=hashlib.sha1(b'existing certificate').hexdigest().upper()
    (directory/'identity.json').write_text(json.dumps({'schema':1,'identity':fingerprint,'keychain':'Desk-Mate.keychain-db'}))
    (directory/'keychain-password').write_text('private-password')
    (directory/'Desk-Mate.keychain-db').write_bytes(b'existing keychain')
    for path in directory.iterdir():path.chmod(0o600)
    return directory,fingerprint


def test_reuses_existing_certificate_and_unlocks_only_its_keychain(tmp_path,monkeypatch):
    mod=load_signing();directory,fingerprint=existing_identity(tmp_path);calls=[]
    def command(*args,**kwargs):
        calls.append(args)
        if 'find-certificate' in args:return b'certificate PEM'
        if '-outform' in args:return b'existing certificate'
        return b''
    monkeypatch.setattr(mod,'command',command)
    first=mod.ensure_local_identity(directory);second=mod.ensure_local_identity(directory)
    assert first==second
    assert first['identity']==fingerprint
    assert not any('create-keychain' in args or 'import' in args or 'set-key-partition-list' in args for args in calls)
    assert all(str(directory/'Desk-Mate.keychain-db')==str(args[-1]) for args in calls if args[0].endswith('/security'))


def test_first_setup_keeps_secrets_private_and_cleans_temporary_key_files(tmp_path,monkeypatch):
    mod=load_signing();directory=tmp_path/'signing';calls=[]
    def command(*args,**kwargs):
        calls.append(args)
        if '-keyout' in args:Path(args[args.index('-keyout')+1]).write_bytes(b'temporary private key')
        if 'create-keychain' in args:Path(args[-1]).write_bytes(b'non-extractable identity')
        if '-outform' in args:return b'new certificate'
        if 'find-certificate' in args:return b'certificate PEM'
        return b''
    monkeypatch.setattr(mod,'command',command)
    result=mod.ensure_local_identity(directory)
    assert result['identity']==hashlib.sha1(b'new certificate').hexdigest().upper()
    assert directory.stat().st_mode & 0o777 == 0o700
    assert {path.name for path in directory.iterdir()}=={'identity.json','keychain-password','Desk-Mate.keychain-db'}
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    assert not any('add-trusted-cert' in args or 'list-keychains' in args for args in calls)


def test_partial_identity_is_preserved_instead_of_rotating(tmp_path,monkeypatch):
    mod=load_signing();directory=tmp_path/'signing';directory.mkdir(mode=0o700)
    keychain=directory/'Desk-Mate.keychain-db';keychain.write_bytes(b'precious existing key')
    monkeypatch.setattr(mod,'command',lambda *a,**kw:pytest.fail('must not recreate keys'))
    with pytest.raises(RuntimeError,match='Incomplete signing identity'):mod.ensure_local_identity(directory)
    assert keychain.read_bytes()==b'precious existing key'


def test_different_certificate_fails_without_replacing_saved_identity(tmp_path,monkeypatch):
    mod=load_signing();directory,fingerprint=existing_identity(tmp_path)
    monkeypatch.setattr(mod,'command',lambda *a,**kw:b'different certificate')
    with pytest.raises(RuntimeError,match='differs from its saved fingerprint'):mod.ensure_local_identity(directory)
    assert json.loads((directory/'identity.json').read_text())['identity']==fingerprint


def test_group_readable_password_is_rejected(tmp_path,monkeypatch):
    mod=load_signing();directory,_=existing_identity(tmp_path)
    (directory/'keychain-password').chmod(0o640)
    monkeypatch.setattr(mod,'command',lambda *a,**kw:pytest.fail('must not use exposed credentials'))
    with pytest.raises(RuntimeError,match='owned by this user and private'):mod.ensure_local_identity(directory)


def test_command_error_redacts_password_and_does_not_echo_argv(monkeypatch):
    mod=load_signing()
    monkeypatch.setattr(mod.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,1,b'',b'bad private-password'))
    with pytest.raises(RuntimeError) as error:
        mod.command('/usr/bin/security','unlock-keychain','-p','private-password',secrets_to_hide=('private-password',))
    assert 'private-password' not in str(error.value)
    assert '[redacted]' in str(error.value)
