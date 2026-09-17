"""Synthetic-only, equal-input Core/Cloud/Cloud Pro comparison, including actual ASR.

Cloud execution is explicit in this command. Never load a patient database.
Outputs are local ignored QA artifacts; clinical correctness is reviewed separately.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4
from clinic_ai import local_ai, speech
from clinic_ai.store import Store
from clinic_ai.workspace import Workspace

ROOT = Path('.build/cloud-loop')
GOLD = '교육용 합성 사례입니다. 환자가 상자를 옮긴 뒤 사흘 전부터 허리가 아프며 통증은 십 점 중 사 점이라고 말했습니다. 다리 방사통, 저림, 근력 저하, 발열은 모두 없다고 말했습니다. 의사는 보행이 정상이고 허리에 압통이 있다고 기록했습니다. 혈압은 백십육에 칠십사, 맥박은 칠십, 체온은 삼십육 점 육 도입니다. 에이치알브이 검사는 하지 않았습니다. 진단은 확정하지 않았고 한약 처방은 없습니다. 의사는 삼 일 뒤 경과를 확인하자고 설명했습니다.'
SOAP = 'S: 3일 전 상자를 옮긴 뒤 요통 NRS 4/10. 다리 방사통·저림·근력저하·발열 없음.\nO: 보행 정상, 요부 압통. 혈압 116/74 mmHg, 맥박 70회/분, 체온 36.6℃. HRV 미검사.\nA: 진단 미확정.\nP: 한약 처방 없음. 3일 뒤 경과 확인.'

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    report={'local_status':local_ai.status(),'gold':GOLD,'reviewed_soap':SOAP,'runs':[]}
    with tempfile.TemporaryDirectory(prefix='clinic-model-comparison-') as temp:
        audio=Path(temp)/'synthetic.aiff'
        subprocess.run(['say','-v','Yuna','-o',str(audio),GOLD],check=True,timeout=30)
        asr=speech.validate_result(speech.bridge({'action':'transcribe','path':str(audio)}))
        report['asr']={k:asr[k] for k in ('text','segments','duration')}
        print(json.dumps({'asr_seconds':asr['duration'],'asr_text':asr['text']},ensure_ascii=False),flush=True)
        store=Store(Path(temp)/'synthetic.sqlite3',mvp=True)
        store.submit_survey(dict(name='모델 비교 합성',phone='010-0000-0000',birth='000101',identity='3000000',complaint='교육용 합성 요통',onset='3일 전',details='상자를 옮긴 뒤 요통 NRS 4/10. 다리 방사통·저림·근력저하·발열 없음.',medication='없음'),'1'*32)
        sid=store.list('sessions')[0]['id'];ws=Workspace(store)
        try:
            fields={'transcript':GOLD,'soap':SOAP,'notes':'진단 미확정. 보행 정상, 요부 압통. HRV 미검사.','explanation':'3일 뒤 경과 확인.','prescription':'한약 처방 없음'}
            ws.patch(sid,{'changes':fields,'base':dict.fromkeys(fields,'')})
            store.save('exams',{'properties':{'연결된 진료 세션':[sid],'수축기혈압':116,'이완기혈압':74,'맥박':70,'체온':36.6,'검사 메모':'HRV 미검사'}})
            for lane,stages in [('gold','abc'),('asr','b')]:
                if lane=='asr':ws.patch(sid,{'changes':{'transcript':asr['text']},'base':{'transcript':GOLD}})
                for stage in stages:
                    for model in ('local','cloud','cloud-pro'):
                        before=store.get('sessions',sid)
                        start=time.monotonic();jid=ws.enqueue(sid,stage,{'request_id':uuid4().hex,'model':model})['id']
                        while time.monotonic()-start<200:
                            job=next(j for j in ws.jobs(sid) if j['id']==jid)
                            if job['state'] in ('completed','failed'):break
                            time.sleep(.2)
                        assert store.get('sessions',sid)==before,'Candidate overwrote original document'
                        row=dict(lane=lane,stage=stage,model=model,seconds=round(time.monotonic()-start,1),state=job['state'],error=job['error'],result=job['result'],input_signature=job['signature'],provider=job['provider'])
                        report['runs'].append(row)
                        (ROOT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                        print(json.dumps({k:v for k,v in row.items() if k not in ('result','provider','input_signature')},ensure_ascii=False),flush=True)
            assert all(r['state']=='completed' for r in report['runs']), 'Some model calls failed; inspect comparison.json'
            for lane,stage in {(r['lane'],r['stage']) for r in report['runs']}:
                assert len({r['input_signature'] for r in report['runs'] if r['lane']==lane and r['stage']==stage})==1
            print('PASS: 12 real queued candidates / equal input fingerprints / no auto-apply. Content accuracy requires reading comparison.json.',flush=True)
        finally:ws.close()

if __name__=='__main__':main()
