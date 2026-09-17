"""Synthetic-only live AFM smoke; never writes to the runtime database."""
import json
from pathlib import Path
import tempfile
import time
from clinic_ai.store import Store
from clinic_ai.local_ai import generate, status

print(json.dumps(status(),ensure_ascii=False),flush=True)
with tempfile.TemporaryDirectory() as temp:
    store=Store(Path(temp)/'smoke.sqlite3')
    store.submit_survey(dict(name='합성시험',phone='010-0000-0000',birth='000101',identity='3000000',
        complaint='교육용 자료 정리',onset='오늘',details='실제 환자가 아닌 교육용 합성 입력. 통증 및 특이 증상 없음.',
        medication='없음',privacy='on',kakao='on'),'a'*32)
    session=store.list('sessions')[0]
    for stage in ('a','b','c'):
        if stage=='b':
            session=store.save('sessions',{'revision':session['revision'],'properties':{},'document':{
                'transcript':'교육용 합성 대본입니다. 환자: 오늘은 특별히 불편한 곳이 없습니다. 복용 중인 약도 없습니다. 한의사: 측정 검사는 하지 않았습니다. 오늘은 생활 기록 방법만 안내하며 처방은 없습니다.',
                'prescription':'한약 처방 없음','explanation':'생활 기록 방법만 안내'}},session['id'])
        if stage=='c':
            session=store.save('sessions',{'revision':session['revision'],'properties':{'세션 상태':'안내준비'}},session['id'])
        start=time.monotonic()
        session=generate(store,session['id'],stage)
        assert session['document'][stage].strip()
        print(json.dumps({'stage':stage,'seconds':round(time.monotonic()-start,1),'characters':len(session['document'][stage]),'metadata':session['document'][stage+'_meta']},ensure_ascii=False),flush=True)
    assert session['document']['transcript'].startswith('교육용 합성')
    assert all(session['document'].get(k) for k in ('a','b','soap','c'))
    print('PASS: real AFM a/b/c generation, stored results, transcript and stages preserved; temporary DB only.',flush=True)
