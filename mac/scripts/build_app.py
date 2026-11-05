#!/usr/bin/env python3
"""Build a relocatable macOS app from explicit, local runtime/model inputs.

Build tools are needed on the builder only. No downloads or user-state copying.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args],check=True,**kwargs)


def macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in MAGIC


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def copy_ollama_model(source: Path, dest: Path, model: str) -> dict:
    # Deliberately accept only explicit local library models; no cloud models,
    # arbitrary paths or copying an entire personal model repository.
    import re
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+:[a-zA-Z0-9_.-]+",model):
        raise ValueError("model must be an explicit library name:tag")
    name, tag = model.split(":")
    manifest_rel = Path("manifests/registry.ollama.ai/library")/name/tag
    manifest = json.loads((source/manifest_rel).read_text())
    items = [manifest["config"],*manifest["layers"]]
    dest.joinpath(manifest_rel).parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source/manifest_rel,dest/manifest_rel)
    (dest/"blobs").mkdir(exist_ok=True)
    for item in items:
        expected = item["digest"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}",expected):
            raise ValueError("invalid model blob digest")
        blob = source/"blobs"/expected.replace(":","-")
        if digest(blob) != expected.split(":")[1]:
            raise ValueError(f"model checksum failed: {blob.name}")
        shutil.copy2(blob,dest/"blobs"/blob.name)
    return {"manifest":str(manifest_rel),"sha256":digest(dest/manifest_rel),
            "blobs":[item["digest"] for item in items]}


def ignore_runtime(directory, names):
    return [name for name in names if name in {"__pycache__",".DS_Store","__editable__"}
            or name.endswith((".pyc",".pth")) or name == "direct_url.json"]


def copy_runtime_packages(site: Path, destination: Path) -> dict[str,str]:
    """Copy only locked runtime distributions and their recorded package files."""
    expected = {}
    for line in (REPO/"mac/requirements-runtime.lock").read_text().splitlines():
        if line and not line.startswith("#"):
            name,version = line.split("==")
            expected[name.lower().replace("_","-")] = version
    available = {d.metadata["Name"].lower().replace("_","-"):d
                 for d in importlib.metadata.distributions(path=[str(site)])}
    for name,version in expected.items():
        dist = available.get(name)
        if dist is None or dist.version != version:
            raise ValueError(f"missing locked runtime dependency: {name}=={version}")
        if not dist.files:
            raise ValueError(f"runtime distribution has no RECORD: {name}")
        for entry in dist.files:
            source = site/entry
            if ".." in entry.parts or entry.name.endswith((".pyc",".pth")) or entry.name == "direct_url.json":
                continue
            if not source.is_file():
                continue
            target = destination/entry
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target,follow_symlinks=True)
    return expected


def libraries(path: Path) -> list[str]:
    output = run("/usr/bin/otool","-L",path,capture_output=True,text=True).stdout
    own = run("/usr/bin/otool","-D",path,capture_output=True,text=True).stdout.splitlines()[1:]
    own = {line.strip() for line in own}
    return [line.strip().split(" (compatibility")[0] for line in output.splitlines()[1:]
            if " (compatibility" in line and line.strip().split(" (compatibility")[0] not in own]


def rpaths(path: Path) -> list[str]:
    lines = run("/usr/bin/otool","-l",path,capture_output=True,text=True).stdout.splitlines()
    found = []
    for i,line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            found.append(lines[i+2].strip().split("path ",1)[1].split(" (offset",1)[0])
    return found


def minimum_macos(files: list[Path]) -> str:
    versions = [(14,0)]
    for path in files:
        lines = run("/usr/bin/otool","-l",path,capture_output=True,text=True).stdout.splitlines()
        for index,line in enumerate(lines):
            if line.strip() in ("cmd LC_BUILD_VERSION","cmd LC_VERSION_MIN_MACOSX"):
                key = "minos" if "LC_BUILD_VERSION" in line else "version"
                for candidate in lines[index+1:index+8]:
                    parts = candidate.strip().split()
                    if len(parts) == 2 and parts[0] == key:
                        versions.append(tuple(int(n) for n in parts[1].split(".")))
                        break
    return ".".join(str(n) for n in max(versions))


def relocate_libraries(app: Path) -> list[Path]:
    framework = app/"Contents/Frameworks"
    framework.mkdir(exist_ok=True)
    files = [p for p in app.rglob("*") if macho(p)]
    # Keep a source for a newly copied library, whose @loader_path is relative
    # to the original dependency until its own load commands are rewritten.
    origins = {}
    i = 0
    while i < len(files):
        path = files[i]; i += 1
        origin = origins.get(path,path)
        for lib in libraries(path):
            if lib.startswith(("/usr/lib/","/System/Library/")):
                continue
            resolved = None
            if lib.startswith("/"):
                resolved = Path(lib)
            elif lib.startswith("@loader_path/"):
                resolved = origin.parent/lib[len("@loader_path/"):]
            elif lib.startswith("@rpath/"):
                suffix = lib[len("@rpath/"):]
                for rpath in rpaths(origin):
                    candidate = Path(rpath.replace("@loader_path",str(origin.parent)))/suffix
                    if candidate.is_file():
                        resolved = candidate
                        break
                if resolved is None:
                    candidates = [p for p in files if p.name == Path(suffix).name]
                    if len(candidates) == 1:
                        resolved = candidates[0]
                    elif suffix == "libomp.dylib":
                        # Numba relies on the already-loaded OpenMP runtime.
                        # Share Torch's runtime instead of loading a second one.
                        resolved = next((p for p in candidates if "torch/lib" in str(p)),None)
            if resolved is None or not resolved.is_file():
                raise RuntimeError(f"unresolved dependency: {path.relative_to(app)} -> {lib}")
            resolved = resolved.resolve()
            if app.resolve() not in resolved.parents:
                folder = framework/hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
                destination = folder/resolved.name
                if not destination.exists():
                    folder.mkdir(exist_ok=True)
                    shutil.copy2(resolved,destination)
                    origins[destination] = resolved
                    files.append(destination)
                resolved = destination
            replacement = "@loader_path/"+os.path.relpath(resolved,path.parent)
            if lib != replacement:
                run("/usr/bin/install_name_tool","-change",lib,replacement,path,capture_output=True)
    # LC_RPATH must not retain a developer's Homebrew or home directory.
    for path in files:
        for rpath in set(rpaths(path)):
            if rpath.startswith("/") and not rpath.startswith(("/usr/lib","/System/Library")):
                run("/usr/bin/install_name_tool","-delete_rpath",rpath,path,capture_output=True)
        for lib in libraries(path):
            if lib.startswith(("/usr/lib/","/System/Library/")):
                continue
            if not lib.startswith("@loader_path/"):
                raise RuntimeError(f"external dependency remains: {path}: {lib}")
            target = (path.parent/lib[len("@loader_path/"):]).resolve()
            if app.resolve() not in target.parents or not target.is_file():
                raise RuntimeError(f"dependency escapes bundle: {path}: {lib}")
    return files


def build(args) -> Path:
    app = args.output.resolve()
    if app.exists():
        raise ValueError("output exists; choose a fresh .app path")
    if app.suffix != ".app":
        raise ValueError("output must end in .app")
    if (args.python_runtime/"pyvenv.cfg").exists():
        raise ValueError("provide a standalone Python distribution, not a venv")
    runtime_python = args.python_runtime/"bin/python3"
    version = run(runtime_python,"-I","-c","import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')",capture_output=True,text=True).stdout.strip()
    site = args.site_packages.resolve()
    if f"python{version}" not in str(site):
        raise ValueError("site-packages ABI does not match bundled Python")
    resources = app/"Contents/Resources"
    executable = app/"Contents/MacOS"
    resources.mkdir(parents=True); executable.mkdir()
    shutil.copytree(args.python_runtime,resources/"python",symlinks=False,ignore=ignore_runtime)
    bundled_site = resources/"python/lib"/f"python{version}/site-packages"
    bundled_site.mkdir(exist_ok=True)
    dependencies = copy_runtime_packages(site,bundled_site)
    for folder in ("src","prompts"):
        shutil.copytree(REPO/"mac"/folder,resources/"mac"/folder,ignore=ignore_runtime)
    (resources/"mac/bin").mkdir()
    cache = REPO/"build/desktop-swift-cache"
    cache.mkdir(parents=True,exist_ok=True)
    target = f"{platform.machine()}-apple-macos14.0"
    run("/usr/bin/swiftc","-O","-target",target,"-module-cache-path",cache,"-o",resources/"mac/bin/ax_helper",REPO/"mac/scripts/ax_helper.swift")
    run("/usr/bin/swiftc","-O","-target",target,"-module-cache-path",cache,"-o",executable/"DeskMate",REPO/"mac/app/DeskMate.swift")
    shutil.copytree(args.ollama_runtime,resources/"ollama",symlinks=False)
    if not (resources/"ollama/ollama").is_file():
        raise ValueError("ollama runtime must contain ollama executable and its lib/ directory")
    (resources/"models").mkdir()
    shutil.copytree(args.stt_model,resources/"models/sensevoice",symlinks=False,
                    ignore=shutil.ignore_patterns("example","fig",".git",".cache"))
    for name in ("model.pt","config.yaml","am.mvn","chn_jpn_yue_eng_ko_spectok.bpe.model"):
        if not (resources/"models/sensevoice"/name).is_file():
            raise ValueError(f"missing STT resource: {name}")
    model_info = copy_ollama_model(args.ollama_models,resources/"models/ollama",args.model)
    shutil.copy2(REPO/"LICENSE",resources/"LICENSE")
    notices = resources/"THIRD_PARTY_NOTICES.txt"
    notices.write_text("Python runtime licenses: python/LICENSE* and python/lib/python*/site-packages/*.dist-info/\n"
                       "Ollama runtime: ollama/LICENSE (builder must provide)\n"
                       "Qwen model license: models/ollama/blobs/ (license layer retained)\n"
                       "SenseVoiceSmall model documentation: models/sensevoice/README.md\n"
                       "This development bundle is not notarized unless the release pipeline signs and notarizes it.\n")
    if not (resources/"ollama/LICENSE").exists():
        raise ValueError("include Ollama LICENSE in its runtime distribution")
    app_version=json.loads((REPO/"mac/app/version.json").read_text())
    info = {"CFBundleExecutable":"DeskMate","CFBundleIdentifier":"com.desk-mate.server",
            "CFBundleName":"Desk-Mate","CFBundleDisplayName":"Desk-Mate",
            "CFBundlePackageType":"APPL","CFBundleShortVersionString":app_version["version"],"CFBundleVersion":str(app_version["build"]),
            "LSUIElement":False,"LSMinimumSystemVersion":"14.0","NSHighResolutionCapable":True,
            "NSBluetoothAlwaysUsageDescription":"连接 Desk-Mate，接收语音指令并回传执行结果。",
            "NSBluetoothPeripheralUsageDescription":"连接 Desk-Mate 蓝牙设备。",
            "NSLocalNetworkUsageDescription":"发现并连接本地 Desk-Mate 设备。",
            "NSBonjourServices":["_voice-assistant._tcp"],
            "NSScreenCaptureUsageDescription":"识别播放器文字并验证操作结果。"}
    with (app/"Contents/Info.plist").open("wb") as stream:
        plistlib.dump(info,stream)
    metadata = {"model":args.model,"architecture":platform.machine(),"python":version,
                "dependencies":dependencies,
                "ollama_model":model_info,"stt_model_sha256":digest(resources/"models/sensevoice/model.pt"),
                "signed_identity":args.identity,"notarized":False}
    (resources/"bundle.json").write_text(json.dumps(metadata,indent=2)+"\n")
    finish_app(app,args.identity)
    return app


def finish_app(app: Path, identity: str = "-") -> None:
    resources = app/"Contents/Resources"
    print("Relocating native dependencies…",flush=True)
    files = relocate_libraries(app)
    required_macos = minimum_macos(files)
    info_path = app/"Contents/Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    info["LSMinimumSystemVersion"] = required_macos
    with info_path.open("wb") as stream:
        plistlib.dump(info,stream)
    metadata_path = resources/"bundle.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["minimum_macos"] = required_macos
    metadata_path.write_text(json.dumps(metadata,indent=2)+"\n")
    env = dict(os.environ,PYTHONPATH=str(resources/"mac/src"),PYTHONNOUSERSITE="1",PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONHOME",None)
    # Dynamic-link edits invalidate signatures; resign BEFORE loading Python.
    entitlements = REPO/"mac/app/entitlements.plist"
    signing=["--sign",identity]
    local_identity=None
    if identity=="local":
        from local_signing import ensure_local_identity
        local_identity=ensure_local_identity()
        signing=["--sign",local_identity["identity"],"--keychain",local_identity["keychain"]]
    try:
        if local_identity:
            metadata.update(signed_identity=local_identity["identity"],signing_kind="local-certificate")
            metadata_path.write_text(json.dumps(metadata,indent=2)+"\n")
        for path in files:
            run("/usr/bin/codesign","--force",*signing,"--options","runtime",
                "--entitlements",entitlements,path,capture_output=True)
        run("/usr/bin/codesign","--force",*signing,"--options","runtime",app,capture_output=True)
        run("/usr/bin/codesign","--verify","--deep","--strict",app,capture_output=True)
        if local_identity:
            from local_signing import verify_local_signature
            verify_local_signature(app,local_identity["identity"],info["CFBundleIdentifier"])
    finally:
        if local_identity:run("/usr/bin/security","lock-keychain",local_identity["keychain"],capture_output=True)
    run(resources/"python/bin/python3","-c",
        "import ssl, sqlite3, torch, torchaudio, funasr, bleak, serial, websockets; import voice_assistant.api; print('Bundled imports OK')",env=env,cwd=app.parent)
    print(f"Built {app}",flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--python-runtime",type=Path,required=True)
    p.add_argument("--site-packages",type=Path,required=True)
    p.add_argument("--ollama-runtime",type=Path,required=True)
    p.add_argument("--ollama-models",type=Path,required=True)
    p.add_argument("--stt-model",type=Path,required=True)
    p.add_argument("--model",default="qwen2.5:1.5b")
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--identity",default="-",help="Certificate identity, 'local' to reuse this Mac's stable identity, or '-' for ad-hoc")
    build(p.parse_args())


if __name__ == "__main__":
    main()
