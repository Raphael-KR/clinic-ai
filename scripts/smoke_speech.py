"""Synthetic speech -> HTTP upload -> Apple ASR -> local SOAP, temporary DB only."""
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from clinic_ai.app import ClinicServer
from clinic_ai.speech import status
from clinic_ai.local_ai import generate

print(json.dumps(status(),ensure_ascii=False),flush=True)
with tempfile.TemporaryDirectory() as temp:
    audio=Path(temp)/'synthetic.aiff'
    source='교육용 합성 문진입니다. 오늘은 특별히 불편한 곳이 없습니다. 복용하는 약도 없습니다. 측정 검사는 하지 않았습니다. 오늘은 생활 기록 방법만 안내하고 한약 처방은 없습니다.'
    subprocess.run(['say','-v','Yuna','-o',str(audio),source],check=True,timeout=30)
    server=ClinicServer(0,Path(temp)/'test.sqlite3')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        server.store.submit_survey(dict(name='합성음성',phone='010-0000-0000',birth='000101',identity='3000000',
            complaint='교육용 합성',onset='오늘',details='실제 환자가 아닌 합성 시험',medication='없음',privacy='on'),'b'*32)
        session=server.store.list('sessions')[0]
        raw=audio.read_bytes();start=time.monotonic()
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=180)
        c.request('POST','/api/transcribe/'+session['id'],raw,{'Content-Type':'application/octet-stream',
            'X-CSRF-Token':server.token(),'X-Revision':str(session['revision']),'X-Audio-Extension':'.aiff'})
        response=c.getresponse();result=json.loads(response.read());c.close()
        assert response.status==200,result
        source_record=result['document']['transcript_source']
        assert source_record['sha256']==hashlib.sha256(raw).hexdigest()
        assert source_record['segments'] and result['document']['transcript']
        print(json.dumps({'seconds':round(time.monotonic()-start,2),'audio_seconds':source_record['duration'],
            'segments':len(source_record['segments']),'confidence_segments':sum('confidence' in s for s in source_record['segments']),
            'synthetic_transcript':result['document']['transcript']},ensure_ascii=False),flush=True)
        result=generate(server.store,session['id'],'b')
        assert result['document']['soap'] and result['document']['transcript_source']==source_record
        print('PASS: real SpeechTranscriber -> HTTP/SQLite with timestamps -> real AFM SOAP; original transcript preserved.',flush=True)
    finally:
        server.shutdown();server.server_close();thread.join()
