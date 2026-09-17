"""Real local AI candidates, edit preservation and structured guidance; temporary DB."""
import json
from pathlib import Path
import tempfile
import time
from clinic_ai.store import Store
from clinic_ai.workspace import Workspace, normalized
from clinic_ai.local_ai import status

print(json.dumps(status(),ensure_ascii=False),flush=True)
with tempfile.TemporaryDirectory() as directory:
    store=Store(Path(directory)/'ux.sqlite3',mvp=True)
    store.submit_survey(dict(name='합성 UX 시험',phone='010-0000-0000',birth='000101',identity='3000000',complaint='교육용 합성 요통',onset='3일 전',details='요통 NRS 4. 다리 방사통, 저림, 근력저하, 발열 없음.',medication='없음'),'a'*32)
    sid=store.list('sessions')[0]['id'];ws=Workspace(store)
    try:
        ws.patch(sid,{'changes':{'transcript':'교육용 합성 사례. 환자: 상자를 옮긴 뒤 허리가 아픕니다. 다리 방사통, 저림, 근력저하, 발열은 없습니다. 의사: 보행 정상, 요부 압통. 오늘은 처방을 정하지 않았고 3일 뒤 경과 확인을 설명합니다.','prescription':'한약 처방 없음','explanation':'3일 뒤 경과 확인','soap_s':'사용자가 작성한 원문'},'base':dict.fromkeys(('transcript','prescription','explanation','soap_s'),'')})
        store.save('exams',{'properties':{'연결된 진료 세션':[sid],'수축기혈압':116,'이완기혈압':74,'맥박':70,'체온':36.6}})
        reports=[]
        for stage in 'abc':
            start=time.monotonic();jid=ws.enqueue(sid,stage,{'request_id':stage*32})['id']
            for i in range(200):
                j=next(j for j in ws.jobs(sid) if j['id']==jid)
                if j['state'] in ('completed','failed'):break
                time.sleep(1)
            assert j['state']=='completed',j['error']
            assert store.get('sessions',sid)['document']['soap_s']=='사용자가 작성한 원문' or stage=='c'
            r=j['result'];report={'stage':stage,'seconds':round(time.monotonic()-start,1),'fields':list(r),'candidate_only':True,'clinical_accuracy':'not_asserted'};reports.append(report)
            print(json.dumps(report,ensure_ascii=False),flush=True)
            if stage=='b':
                candidate=normalized({'soap':r['soap']});keys=['soap_o','soap_a','soap_p'];d=ws.encounter(sid)['record']['document']
                for k in keys:ws.patch(sid,{'changes':{k:candidate[k]},'base':{k:d.get(k,'')},'source_job':jid,'source_field':k})
                assert store.get('sessions',sid)['document']['soap_s']=='사용자가 작성한 원문'
            if stage=='c':
                assert all(r.get(k) for k in ('guide','rx_guide','message'))
                assert 'CRM 발송 DB 입력값' not in r['message']
                for k in ('guide','rx_guide','message'):ws.patch(sid,{'changes':{k:r[k]},'base':{k:''},'source_job':jid,'source_field':k})
                crm=ws.guidance(sid,{})
                assert crm['properties']['메시지 본문']==r['message']
                print('GUIDANCE_PREVIEW: '+json.dumps({k:r[k] for k in ('guide','rx_guide','message')},ensure_ascii=False),flush=True)
        Path('.build/ux-loop/ai-result.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
        print('FUNCTIONAL PASS: real AFM queued candidates, manual edit preserved, partial apply and non-consent guidance saved. Clinical accuracy requires separate review.',flush=True)
    finally:ws.close()
