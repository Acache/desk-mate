#!/usr/bin/env python3
"""Update the one local test app in place; never invent a new release path.

Reuses the already validated offline runtime/models. Does not touch user data.
"""
from pathlib import Path
import argparse,json,os,plistlib,shutil,subprocess,tempfile,time
from local_signing import ensure_local_identity,verify_local_signature

REPO=Path(__file__).resolve().parents[2]
APP=REPO/'build/usability-release/Desk-Mate.app'
BUNDLE_ID='com.desk-mate.server'


def run(*args,**kwargs):
    return subprocess.run([str(a) for a in args],check=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity',default=os.environ.get('DESKMATE_SIGNING_IDENTITY','local'),
        help="'local' reuses this Mac's private certificate; explicit certificate identity or '-' overrides it")
    parser.add_argument('--keychain',type=Path,help='Keychain for an explicitly supplied signing identity')
    args=parser.parse_args()
    if not APP.is_dir():raise SystemExit(f'First create the base runtime bundle at {APP}')
    version=json.loads((REPO/'mac/app/version.json').read_text())
    staged=APP.parent/'.Desk-Mate-staging.app'
    if staged.exists():raise SystemExit(f'Unfinished staging exists: {staged}; inspect it before retrying')
    signer=ensure_local_identity() if args.identity=='local' else {'identity':args.identity,'keychain':str(args.keychain) if args.keychain else None}
    signing=['--sign',signer['identity']]
    if signer['keychain']:signing+=['--keychain',signer['keychain']]
    requirement=None
    try:
        run('cp','-cR',APP,staged)
        info=plistlib.loads((staged/'Contents/Info.plist').read_bytes())
        info.update(CFBundleName="Desk-Mate",CFBundleDisplayName="Desk-Mate",
            CFBundleShortVersionString=version['version'],CFBundleVersion=str(max(version['build'],int(info.get('CFBundleVersion','0'))+1)),LSUIElement=False)
        (staged/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        run('swiftc','-O','-module-cache-path',REPO/'build/desktop-swift-cache','-o',staged/'Contents/MacOS/DeskMate',REPO/'mac/app/DeskMate.swift')
        resources=staged/'Contents/Resources'
        helper=resources/'mac/bin/ax_helper'
        run('swiftc','-O','-module-cache-path',REPO/'build/desktop-swift-cache','-o',helper,REPO/'mac/scripts/ax_helper.swift')
        run('codesign','--force',*signing,'--options','runtime','--entitlements',REPO/'mac/app/entitlements.plist',helper)
        for name in ('src','prompts'):
            dest=resources/'mac'/name
            shutil.rmtree(dest)
            shutil.copytree(REPO/'mac'/name,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        manifest=resources/'bundle.json'
        if manifest.exists():
            data=json.loads(manifest.read_text())
            data.update(app_version=version['version'],app_build=info['CFBundleVersion'],signed_identity=signer['identity'],
                signing_kind='local-certificate' if args.identity=='local' else 'ad-hoc' if args.identity=='-' else 'certificate')
            manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        run('codesign','--force',*signing,'--options','runtime','--entitlements',REPO/'mac/app/entitlements.plist',staged)
        run('codesign','--verify','--deep','--strict',staged)
        if args.identity=='local':requirement=verify_local_signature(staged,signer['identity'],BUNDLE_ID)
        # Staging is data only. Never execute an .app from this path: NSApp,
        # CoreBluetooth or privacy preflights can register it as a TCC client.
        # Runtime self-checks run only after publication under the real name.
        # Ask only this product's native instances to quit through their normal
        # delegate, so its supervisor and model workers are cleaned up first.
        with tempfile.TemporaryDirectory(prefix='desk-mate-update-') as folder:
            swift=Path(folder)/'quit.swift'
            swift.write_text('import AppKit\nfor app in NSRunningApplication.runningApplications(withBundleIdentifier:"'+BUNDLE_ID+'") { app.terminate() }\n')
            run('swift','-module-cache-path',REPO/'build/desktop-swift-cache',swift)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            check=subprocess.run(['pgrep','-f','/Desk-Mate.app/Contents/MacOS/DeskMate'],stdout=subprocess.DEVNULL)
            if check.returncode:break
            time.sleep(.25)
        else:raise RuntimeError('App has not quit; publication cancelled')
        backup=APP.parent/'.Desk-Mate-previous.app'
        if backup.exists():raise RuntimeError('Previous backup exists; inspect it first')
        APP.rename(backup)
        try:staged.rename(APP)
        except BaseException:backup.rename(APP);raise
        try:
            run('codesign','--verify','--deep','--strict',APP)
            # Repair LaunchServices' display-name/path mapping only after the
            # final bundle exists. This does not modify TCC grants.
            register=Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')
            if register.is_file():
                for obsolete in (staged,REPO/'build/2026-11-05/Desk-Mate.app'):
                    # Remove only the two known aliases from prior releases,
                    # even if their directories have already been retired.
                    try:run(register,'-u',obsolete,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    except subprocess.CalledProcessError:pass
                run(register,'-f',APP)
            run(APP/'Contents/MacOS/DeskMate','--self-check',stdout=subprocess.DEVNULL)
            run(APP/'Contents/MacOS/DeskMate','--connection-self-check')
            run(APP/'Contents/MacOS/DeskMate','--notification-self-check')
        except BaseException:
            # Retain the last known working release if a post-install check
            # fails; no user data or privacy state is reset.
            APP.rename(staged)
            backup.rename(APP)
            raise
        shutil.rmtree(backup)
        # Retire only the dated duplicate created by the previous iteration.
        duplicate=REPO/'build/2026-11-05/Desk-Mate.app'
        if duplicate.exists():shutil.rmtree(duplicate)
        run('codesign','--verify','--deep','--strict',APP)
        version['build']=int(info['CFBundleVersion'])
        (REPO/'mac/app/version.json').write_text(json.dumps(version,indent=2)+'\n')
        print(json.dumps({'app':str(APP),'version':info['CFBundleShortVersionString'],'build':info['CFBundleVersion'],
            'signing':signer['identity'],'designated_requirement':requirement},ensure_ascii=False))
        run('open',APP)
    finally:
        try:
            if staged.exists():shutil.rmtree(staged)
        finally:
            if args.identity=='local':run('/usr/bin/security','lock-keychain',signer['keychain'],stdout=subprocess.DEVNULL)

if __name__=='__main__':main()
