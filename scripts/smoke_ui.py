"""Run DOM-only UI checks against a real temporary HTTP server (no browser)."""
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch
from clinic_ai.app import ClinicServer
from clinic_ai.store import Problem

def generated(*args, **kwargs):
    time.sleep(0.5)
    return {'content':'DOM 생성 초안', 'soap':'S: 합성\nO: 미검사\nA: 확인 필요\nP: 없음', 'variant':'test', 'guide':'환자용 안내', 'rx_guide':'해당 없음 — 한약 처방 없음', 'message':'로컬 안내 메시지'}

cloud_attempts = []
def cloud_generated(request, selection, **kwargs):
    cloud_attempts.append(selection['id'])
    if selection['id']=='cloud-pro' and cloud_attempts.count('cloud-pro')==1:
        raise Problem('시험 Cloud Pro 오류')
    return generated()

with tempfile.TemporaryDirectory() as tmp:
    server=ClinicServer(0,Path(tmp)/'ui.sqlite3')
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with patch('clinic_ai.ai_models.configuration',return_value={m:{'shortcut_id':'12345678-1234-1234-1234-123456789abc','workflow_sha256':'test'} for m in ('cloud','cloud-pro')}), patch('clinic_ai.ai_models.shortcut',side_effect=cloud_generated), patch('clinic_ai.local_ai.bridge', side_effect=generated), patch('clinic_ai.speech.bridge', return_value={'text':'DOM 합성 음성 전사', 'segments':[{'text':'DOM 합성 음성 전사','start':0,'end':1,'confidence':0.8}], 'duration':1, 'engine':'test SpeechTranscriber', 'locale':'ko_KR'}):
            result=subprocess.run(['node','scripts/smoke_ui.cjs',f'http://127.0.0.1:{server.server_port}'],timeout=150)
    finally:
        server.shutdown();server.server_close();thread.join()
    raise SystemExit(result.returncode)
