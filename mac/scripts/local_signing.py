#!/usr/bin/env python3
"""Keep one private, self-signed identity for this Mac's Desk-Mate builds.

This does not add trust settings or change the user's keychain search list.
The certificate is for local testing; it does not replace Developer ID/notarization.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import tempfile

DEFAULT_DIRECTORY = Path.home()/'Library/Application Support/desk-mate/signing'
COMMON_NAME = 'Desk-Mate Local Development'


def command(*args, env=None, input=None, secrets_to_hide=()):
    result = subprocess.run([str(arg) for arg in args], env=env, input=input, capture_output=True)
    if result.returncode:
        detail = result.stderr.decode(errors='replace').strip()
        for secret in secrets_to_hide:
            detail = detail.replace(secret, '[redacted]')
        # Never print argv: security's password arguments are intentionally private.
        raise RuntimeError(f'{Path(args[0]).name} failed ({result.returncode}): {detail}')
    return result.stdout


def private_file(path, content):
    with open(path,'x',opener=lambda name, flags:os.open(name,flags,0o600)) as handle:
        handle.write(content)


def require_private(path, directory=False):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or (directory and not stat.S_ISDIR(info.st_mode)):
        raise RuntimeError(f'Invalid private signing path: {path}')
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError(f'Signing path must be owned by this user and private: {path}')


def ensure_local_identity(directory=DEFAULT_DIRECTORY):
    """Create once, then reuse. Partial/missing state fails instead of rotating keys."""
    directory=Path(directory)
    manifest=directory/'identity.json'
    password_path=directory/'keychain-password'
    keychain=directory/'Desk-Mate.keychain-db'
    if directory.exists():
        require_private(directory,directory=True)
        if not all(path.is_file() for path in (manifest,password_path,keychain)):
            raise RuntimeError(f'Incomplete signing identity at {directory}; preserve it and inspect before retrying')
    else:
        directory.mkdir(mode=0o700,parents=True)
        try:
            password=secrets.token_urlsafe(48)
            private_file(password_path,password)
            # Plaintext key/PKCS12 exist only inside this private temporary folder.
            # The final keychain imports the private key as non-extractable.
            with tempfile.TemporaryDirectory(prefix='.create-',dir=directory) as temporary:
                temporary=Path(temporary)
                config=temporary/'openssl.cnf'
                config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                    '[dn]\nCN='+COMMON_NAME+'\n[ext]\nbasicConstraints=critical,CA:FALSE\n'
                    'keyUsage=critical,digitalSignature\nextendedKeyUsage=critical,codeSigning\n'
                    'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n')
                command('/usr/bin/openssl','req','-x509','-newkey','rsa:2048','-nodes','-sha256','-days','3650',
                    '-config',config,'-keyout',temporary/'key.pem','-out',temporary/'cert.pem')
                (temporary/'key.pem').chmod(0o600)
                env=dict(os.environ,DESKMATE_P12_PASSWORD=password)
                command('/usr/bin/openssl','pkcs12','-export','-inkey',temporary/'key.pem','-in',temporary/'cert.pem',
                    '-name',COMMON_NAME,'-out',temporary/'identity.p12','-passout','env:DESKMATE_P12_PASSWORD',env=env,secrets_to_hide=(password,))
                command('/usr/bin/security','create-keychain','-p',password,keychain,secrets_to_hide=(password,))
                command('/usr/bin/security','unlock-keychain','-p',password,keychain,secrets_to_hide=(password,))
                command('/usr/bin/security','import',temporary/'identity.p12','-k',keychain,'-P',password,
                    '-T','/usr/bin/codesign','-x',secrets_to_hide=(password,))
                command('/usr/bin/security','set-key-partition-list','-S','apple-tool:,apple:,codesign:',
                    '-s','-k',password,keychain,secrets_to_hide=(password,))
                certificate=command('/usr/bin/openssl','x509','-in',temporary/'cert.pem','-outform','DER')
                fingerprint=hashlib.sha1(certificate).hexdigest().upper()
            keychain.chmod(0o600)
            private_file(manifest,json.dumps({'schema':1,'common_name':COMMON_NAME,
                'identity':fingerprint,'keychain':keychain.name},indent=2)+'\n')
        except BaseException:
            # Retain a partially created identity for recovery. Never silently
            # replace an existing certificate after an interrupted first setup.
            if keychain.is_file():
                subprocess.run(['/usr/bin/security','lock-keychain',str(keychain)],capture_output=True)
            raise
    for path in (manifest,password_path,keychain):require_private(path)
    data=json.loads(manifest.read_text())
    if data.get('schema')!=1 or data.get('keychain')!=keychain.name:
        raise RuntimeError('Unsupported local signing manifest; refusing to replace it')
    identity=data.get('identity','')
    if len(identity)!=40 or any(character not in '0123456789ABCDEF' for character in identity):
        raise RuntimeError('Invalid certificate fingerprint in local signing manifest')
    password=password_path.read_text()
    try:
        command('/usr/bin/security','unlock-keychain','-p',password,keychain,secrets_to_hide=(password,))
        certificate=command('/usr/bin/security','find-certificate','-c',COMMON_NAME,'-p',keychain)
        certificate_der=command('/usr/bin/openssl','x509','-outform','DER',input=certificate)
        if hashlib.sha1(certificate_der).hexdigest().upper()!=identity:
            raise RuntimeError('Local signing certificate differs from its saved fingerprint; refusing to rotate identity')
    except BaseException:
        subprocess.run(['/usr/bin/security','lock-keychain',str(keychain)],capture_output=True)
        raise
    return {'identity':identity,'keychain':str(keychain),'directory':str(directory)}


def designated_requirement(path):
    result=subprocess.run(['/usr/bin/codesign','-d','-r-',str(path)],capture_output=True,text=True,check=True)
    for line in (result.stdout+result.stderr).splitlines():
        if line.startswith('designated => '):return line.removeprefix('designated => ')
    raise RuntimeError(f'No designated requirement found for {path}')


def verify_local_signature(path, identity, identifier):
    # This is Apple's normal certificate-based requirement, not a custom
    # weakened DR. Cross-build verification checks the certificate and bundle ID.
    expected=f'identifier "{identifier}" and certificate leaf = H"{identity.lower()}"'
    command('/usr/bin/codesign','--verify','--strict','-R','='+expected,path)
    actual=designated_requirement(path)
    if actual!=expected:
        raise RuntimeError('Unexpected designated requirement for local signature')
    return actual


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=DEFAULT_DIRECTORY)
    args=parser.parse_args()
    signer=ensure_local_identity(args.directory)
    try:print(json.dumps(signer,ensure_ascii=False))
    finally:command('/usr/bin/security','lock-keychain',signer['keychain'])


if __name__=='__main__':main()
