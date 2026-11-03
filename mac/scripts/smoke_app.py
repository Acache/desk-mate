#!/usr/bin/env python3
"""Exercise a bundle with isolated state; no BLE or player commands are sent."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def wait_for(check, seconds=90):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        value = check()
        if value:
            return value
        time.sleep(.5)
    raise TimeoutError("bundle smoke check timed out")


def alive(pid):
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app",type=Path,required=True)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--port",type=int,default=18766)
    p.add_argument("--ollama-port",type=int,default=11436)
    args=p.parse_args()
    app=args.app.resolve(); root=args.root.resolve()
    if root.exists():
        raise ValueError("use a fresh private test directory")
    root.mkdir(parents=True,mode=0o700)
    resources=app/"Contents/Resources"
    env={"PATH":"/usr/bin:/bin","HOME":str(root),"TMPDIR":os.environ.get("TMPDIR","/tmp"),
         "PYTHONPATH":str(resources/"mac/src"),"PYTHONNOUSERSITE":"1","PYTHONDONTWRITEBYTECODE":"1",
         "VOICE_ASSISTANT_DISABLE_DISCOVERY":"1"}
    command=[str(resources/"python/bin/python3"),"-m","voice_assistant.app_runtime",
             "--resources",str(resources),"--root",str(root),"--port",str(args.port),
             "--ollama-port",str(args.ollama_port),"serve"]
    state_path=root/"desktop-state.json"
    def state():
        try:return json.loads(state_path.read_text())
        except (OSError,ValueError):return {}
    report={"app":str(app),"player_actions_executed":False,"developer_path_removed":True}
    with (root/"supervisor.log").open("wb") as log:
        proc=subprocess.Popen(command,env=env,cwd=root,stdout=log,stderr=log)
        try:
            def ready():
                s=state(); h=s.get("health",{})
                return s if h.get("stt",{}).get("available") and h.get("decision",{}).get("model_loaded") else None
            initial=wait_for(ready)
            report["initial_health"]=initial
            switch=subprocess.run(command[:-1]+["select-device"],env=env,cwd=root,capture_output=True,text=True,timeout=10)
            reply=json.loads(switch.stdout)
            assert switch.returncode==0 and reply["code"]=="BLE_DISCONNECTED"
            switched=wait_for(lambda:(s if (s:=ready()) and s["health"]["ble"].get("phase")=="disconnected" else None),10)
            assert switched["pids"]==initial["pids"]
            assert switched["health"]["stt"]["pid"]==initial["health"]["stt"]["pid"]
            assert switched["health"]["decision"]["model_loaded"]
            report["hot_ble_control_without_model_restart"]=True
            duplicate=subprocess.run(command,env=env,cwd=root,stdout=log,stderr=log,timeout=5)
            assert duplicate.returncode == 2, "second supervisor acquired the same data root"
            report["duplicate_instance_rejected"]=True
            first=initial["pids"]["service"]
            os.kill(first,signal.SIGTERM)
            def recovered():
                s=ready()
                return s if s and s["pids"].get("service") != first else None
            recovered_state=wait_for(recovered,60)
            report["backend_recovered"]=recovered_state["pids"]["service"] != first
            owned=[*recovered_state["pids"].values(),recovered_state["health"]["stt"]["pid"]]
        finally:
            proc.terminate()
            proc.wait(timeout=20)
        wait_for(lambda:not any(alive(pid) for pid in owned),10)
        report["owned_children_cleaned"]=True
        report["final_phase"]=state().get("phase")
        assert report["final_phase"] == "stopped"
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k != "initial_health"},ensure_ascii=False))


if __name__ == "__main__":
    main()
