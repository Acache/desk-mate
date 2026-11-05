#!/usr/bin/env python3
"""Acceptance check with a real recording and the published app's real adapter.

No production database mutations or device pairing. --execute explicitly permits
playing the visible first/second video. An isolated in-process protocol session
uses real STT, context, planner, executor and player verification; no fake adapter.
"""
import argparse,hashlib,json,os,plistlib,struct,sys,tempfile,time,uuid,wave
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',type=Path,required=True)
    p.add_argument('--recording',type=Path,required=True)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    resources=args.app.resolve()/'Contents/Resources'
    sys.path.insert(0,str(resources/'mac/src'))
    # The actual STT worker is a subprocess; give it the published modules too.
    os.environ['PYTHONPATH']=str(resources/'mac/src')
    os.environ['PYTHONNOUSERSITE']='1'
    os.environ['VOICE_ASSISTANT_DISABLE_DISCOVERY']='1'
    os.environ.pop('VOICE_ASSISTANT_DESKTOP',None)
    os.environ['VOICE_STT_MODEL']=str(resources/'models/sensevoice')
    os.environ['HF_HUB_OFFLINE']='1'
    from fastapi.testclient import TestClient
    from voice_assistant import contracts
    from voice_assistant.api import create_app
    from voice_assistant.config import load_config
    from voice_assistant.storage import Database
    from voice_assistant.adapters.base import AdapterRegistry
    from voice_assistant.adapters.bilibili import BilibiliAdapter
    from voice_assistant.stt import SenseVoiceSTT
    from voice_assistant.intent import normalize_intent_text,rule_based_proposal

    class NoModelNeeded:
        model_digest='not-used-exact-command'
        def health(self):return {'available':True,'backend':'not-used-exact-command'}
        def propose(self,*a,**kw):raise AssertionError('Exact ordinal unexpectedly invoked model')

    stt=SenseVoiceSTT(str(resources/'models/sensevoice'))
    adapter=None
    info=plistlib.loads((args.app/'Contents/Info.plist').read_bytes())
    report={'source':'real failed-command recording','actual_stt':True,'actual_adapter':True,
            'app':str(args.app.resolve()),'version':info['CFBundleShortVersionString'],
            'build':info['CFBundleVersion'],'decision_model':'not used by exact ordinal command'}
    try:
        transcript=stt.transcribe(args.recording,time.time()+300)
        text=transcript.normalized_text
        report['transcript']=text
        if normalize_intent_text(text) not in ('播放第一个视频','播放第二个视频'):
            raise RuntimeError('Recording did not decode to the explicitly supported acceptance command')
        with tempfile.TemporaryDirectory(prefix='desk-mate-real-play-') as temp:
            cfg=load_config(Path(temp));db=Database(cfg.db_path)
            device,token=db.register_device('isolated local acceptance')
            adapter=BilibiliAdapter(cfg,db,helper=resources/'mac/bin/ax_helper')
            page=adapter.context_snapshot()
            if not page:raise RuntimeError('Current page context unavailable')
            proposal=rule_based_proposal(text,contracts.TrustedContext(now=time.time(),page_context=page))
            if not proposal or len(proposal.actions)!=1 or not proposal.actions[0].params.get('bv'):
                raise RuntimeError('Current visible target is ambiguous')
            report.update(page=page.get('page'),expected_bv=proposal.actions[0].params['bv'],proposal=proposal.model_dump(mode='json'))
            if not args.execute:
                report['executed']=False
            else:
                registry=AdapterRegistry();registry.register(adapter,contracts.ENABLED_ACTIONS)
                app=create_app(cfg,stt=stt,decision=NoModelNeeded(),registry=registry)
                with wave.open(str(args.recording),'rb') as wav:
                    if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(16000,1,2):raise RuntimeError('Unsupported WAV format')
                    pcm=wav.readframes(wav.getnframes())
                pcm=pcm[:len(pcm)//640*640]
                digest=hashlib.sha256(pcm).hexdigest();cid=str(uuid.uuid4())
                with TestClient(app) as client:
                    with client.websocket_connect('/v1/remote',headers={'Authorization':'Bearer '+token,'X-Device-ID':device}) as ws:
                        ws.send_json({'v':1,'type':'hello','msg_id':1,'device_id':device,'server_id':cfg.server.server_id,'firmware_version':'local-acceptance','boot_id':str(uuid.uuid4())})
                        hello=ws.receive_json()
                        if hello.get('type')!='hello_ack':raise RuntimeError(str(hello))
                        ws.send_json({'v':1,'type':'begin','msg_id':2,'command_id':cid,'kind':'audio','format':{'encoding':'s16le','rate':16000,'channels':1},'trigger':'esp32_ptt'})
                        if ws.receive_json().get('type')!='begin_ack':raise RuntimeError('Begin rejected')
                        for seq,off in enumerate(range(0,len(pcm),640)):
                            ws.send_bytes(struct.pack('<IHH',seq,640,0)+pcm[off:off+640])
                        ws.send_json({'v':1,'type':'seal','msg_id':3,'command_id':cid,'frame_count':len(pcm)//640,'pcm_bytes':len(pcm),'sha256':digest})
                        while ws.receive_json().get('type')!='seal_ack':pass
                        ws.send_json({'v':1,'type':'commit','msg_id':4,'command_id':cid,'sha256':digest})
                        while True:
                            result=ws.receive_json()
                            if result.get('type')=='result':break
                        report['executed']=True;report['result']=result
                    with db.transaction() as tx:
                        steps=tx.execute('select action,state,error_code,output_json,evidence_json from action_steps where command_id=? order by step_index',(cid,)).fetchall()
                    report['steps']=[dict(row) for row in steps]
                    if result.get('state')!='SUCCEEDED':raise RuntimeError('Real execution was not verified as succeeded')
                report['accepted']=True
            db.close()
    except BaseException as exc:
        report['accepted']=False;report['error']=str(exc)
        raise
    finally:
        stt.worker.stop()
        if adapter is not None and adapter._ax_daemon:adapter._ax_daemon.close()
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
