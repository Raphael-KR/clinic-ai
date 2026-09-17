"""Real streaming engine, PCM held only in RAM. Supply an existing synthetic file."""
import argparse
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from clinic_ai.app import ClinicServer

parser=argparse.ArgumentParser()
parser.add_argument('audio')
parser.add_argument('--realtime',action='store_true')
parser.add_argument('--bridge',type=Path,help='Optional comparison build; production default stays unchanged')
parser.add_argument('--report',type=Path,help='Local synthetic-only result artifact')
args=parser.parse_args()
if args.bridge:
    from clinic_ai import live_speech
    live_speech.BINARY=args.bridge.resolve()
pcm=subprocess.run(['ffmpeg','-v','error','-i',args.audio,'-t','12','-ar','16000','-ac','1','-f','f32le','pipe:1'],capture_output=True,check=True).stdout
with tempfile.TemporaryDirectory() as directory:
    server=ClinicServer(0,Path(directory)/'test.sqlite3')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(method,path,payload=None,raw=False,seq=0):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=40)
        headers={'Content-Type':'application/octet-stream' if raw else 'application/json','X-CSRF-Token':server.token(),'X-Sequence':str(seq)}
        conn.request(method,path,payload if raw else json.dumps(payload or {}),headers)
        r=conn.getresponse();data=json.loads(r.read());conn.close();assert r.status==200,data;return data
    try:
        r=server.store.save('sessions',{'properties':{'세션명':'메모리 PCM 시험'},'document':{'transcript':'보존할 기존 전사'}})
        jid=request('POST','/api/live/start/'+r['id'],{'revision':r['revision']})['id']
        started=time.monotonic();first_partial=first_final=None;partial_changes=0;previous_partial='';seen=False
        for seq,offset in enumerate(range(0,len(pcm),12800)):
            request('POST',f'/api/live/{jid}/chunk',pcm[offset:offset+12800],raw=True,seq=seq)
            if args.realtime:
                deadline=started+min(offset+12800,len(pcm))/64000
                time.sleep(max(0,deadline-time.monotonic()))
                state=request('GET','/api/live/'+jid)
                elapsed=round(time.monotonic()-started,3)
                if state['partial']:
                    seen=True
                    if first_partial is None:first_partial=elapsed
                    if state['partial']!=previous_partial:partial_changes+=1
                if state['text']:
                    seen=True
                    if first_final is None:first_final=elapsed
                previous_partial=state['partial']
            else:time.sleep(.02)
        request('PATCH',f'/api/workspace/{r["id"]}',{'changes':{'notes':'실제 엔진 전사 중 메모'},'base':{'notes':''}})
        for _ in range(30):
            state=request('GET','/api/live/'+jid)
            if state['partial'] or state['text']:seen=True;break
            time.sleep(.1)
        result=request('POST',f'/api/live/{jid}/stop')['record']['document']
        assert seen
        assert result['notes']=='실제 엔진 전사 중 메모'
        assert result['transcript'].startswith('보존할 기존 전사\n\n')
        assert result['transcript_source']['audio_storage']=='none'
        assert result['transcript_source']['segments']
        assert not server.live.jobs
        assert sorted(p.name for p in Path(directory).iterdir())==['test.sqlite3']
        metrics={'pass':True,'interim_seen':seen,'segments':len(result['transcript_source']['segments']), 'audio_seconds':result['transcript_source']['duration'],'created_files':['test.sqlite3'],'audio_files_created':0,'realtime':args.realtime,'first_partial_seconds':first_partial,'first_final_seconds':first_final,'partial_changes':partial_changes}
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps({'metrics':metrics,'transcript':result['transcript_source']['text']},ensure_ascii=False,indent=2))
        print(json.dumps(metrics,ensure_ascii=False))
    finally:server.shutdown();server.server_close();thread.join()
