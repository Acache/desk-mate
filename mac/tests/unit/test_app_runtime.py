import importlib.util
import json
from pathlib import Path

from voice_assistant.app_runtime import bundle_environment


def test_bundle_paths_ignore_developer_environment(tmp_path,monkeypatch):
    resources = tmp_path/"My App.app/Contents/Resources"
    resources.mkdir(parents=True)
    (resources/"bundle.json").write_text(json.dumps({"model":"qwen2.5:1.5b"}))
    monkeypatch.setenv("PYTHONHOME","/developer/python")
    monkeypatch.setenv("DYLD_LIBRARY_PATH","/opt/homebrew/lib")
    monkeypatch.setenv("OLLAMA_HOST","0.0.0.0:11434")
    env = bundle_environment(resources,tmp_path/"data",11435)
    assert "PYTHONHOME" not in env and "DYLD_LIBRARY_PATH" not in env
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["VOICE_STT_MODEL"] == str(resources/"models/sensevoice")
    assert env["HF_HUB_OFFLINE"] == "1"


def test_builder_copies_only_manifest_referenced_blobs(tmp_path):
    spec = importlib.util.spec_from_file_location("builder",Path(__file__).parents[2]/"scripts/build_app.py")
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    source = tmp_path/"source"
    manifest = source/"manifests/registry.ollama.ai/library/model/tag"
    manifest.parent.mkdir(parents=True)
    blobs = source/"blobs"; blobs.mkdir()
    content = b"test model"
    import hashlib
    digest = hashlib.sha256(content).hexdigest()
    (blobs/f"sha256-{digest}").write_bytes(content)
    (blobs/"unrelated-private-model").write_bytes(b"do not ship")
    manifest.write_text(json.dumps({"config":{"digest":f"sha256:{digest}"},"layers":[]}))
    result = builder.copy_ollama_model(source,tmp_path/"bundle","model:tag")
    assert result["blobs"] == [f"sha256:{digest}"]
    assert not (tmp_path/"bundle/blobs/unrelated-private-model").exists()


def test_diagnostic_export_excludes_private_data(tmp_path):
    import zipfile
    from voice_assistant.app_runtime import export_diagnostics
    root = tmp_path/"data"; root.mkdir()
    (root/"logs").mkdir()
    (root/"logs/service.log").write_text("x"*300000)
    (root/"learning.db").write_text("private database")
    (root/"private").mkdir()
    (root/"private/ca.key").write_text("private key")
    resource = tmp_path/"resources";resource.mkdir()
    (resource/"bundle.json").write_text('{"model":"test"}')
    output = tmp_path/"diagnostics.zip"
    export_diagnostics(root,resource,output)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"bundle.json","service.log"}
        assert len(archive.read("service.log")) == 256*1024
