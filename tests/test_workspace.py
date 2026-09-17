import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from clinic_ai.app import ClinicServer, reset
from clinic_ai.store import Store, TABLES, Problem, demographics
from clinic_ai import local_ai


def survey_values(consent=True):
    values = dict(name='교육가상',phone='010-0000-0000',birth='000101',identity='3000000',
                  complaint='교육용 증상',onset='어제',details='합성 입력',medication='없음',privacy='on')
    if consent:
        values['kakao']='on'
    return values


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'clinic.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def intake(self,consent=True):
        self.store.submit_survey(survey_values(consent),'1'*32)
        return self.store.list('sessions')[0]

    def update(self,table,row,properties=None,document=None):
        payload={'revision':row['revision'],'properties':properties or {}}
        if document is not None:
            payload['document']=document
        return self.store.save(table,payload,row['id'])

    def reviewed(self,consent=True):
        session=self.intake(consent)
        session=self.update('sessions',session,document={'transcript':'교육용 문진 원문','soap':'S: 합성\nO: 미측정\nA: 확인 필요\nP: 확인 필요'})
        return self.update('sessions',session,{'세션 상태':'안내준비'})

    def crm(self,session,approval='승인대기'):
        return self.store.save('crm',{'properties':{'요청명':'교육 요청','수신자 이름':'교육가상',
            '휴대전화번호':'010-0000-0000','메시지 본문':'검토된 안내','승인 상태':approval,
            '발송 상태':'발송대기','연결된 진료 세션':[session['id']]}})

    def test_empty_seven_collections(self):
        self.assertEqual(self.store.counts(),dict.fromkeys(TABLES,0))
        self.assertEqual(self.store.path.stat().st_mode&0o777,0o600)

    def test_atomic_intake_duplicate_reverse_relation(self):
        session=self.intake()
        self.store.submit_survey(survey_values(),'1'*32)
        counts=self.store.counts()
        self.assertEqual((counts['surveys'],counts['sessions']),(1,1))
        survey=self.store.get('surveys','1'*32)
        self.assertEqual(survey['properties']['연결된 진료 세션'],[session['id']])
        self.assertEqual(survey['properties']['설문 상태'],'세션연결완료')
        bad=survey_values();bad['details']='변경된 응답'
        with self.assertRaises(Problem):self.store.submit_survey(bad,'1'*32)
        self.assertEqual(self.store.counts(),counts)

    def test_missing_required_rolls_back(self):
        values=survey_values();values['privacy']=''
        with self.assertRaises(Problem):self.store.submit_survey(values,'1'*32)
        self.assertEqual(sum(self.store.counts().values()),0)

    def test_exam_relation_title_state_and_no_backward_transition(self):
        s=self.intake()
        exam=self.store.save('exams',{'properties':{'연결된 진료 세션':[s['id']],'수축기혈압':120,'이완기혈압':80}})
        self.assertIn('교육가상',exam['properties']['검사 기록명'])
        s=self.store.get('sessions',s['id']);self.assertEqual(s['properties']['세션 상태'],'검사확인')
        self.assertIn(exam['id'],s['properties']['관련 신체검사'])
        s=self.update('sessions',s,{'세션 상태':'문진중'})
        self.store.save('exams',{'properties':{'연결된 진료 세션':[s['id']],'맥박':72}})
        self.assertEqual(self.store.get('sessions',s['id'])['properties']['세션 상태'],'문진중')

    def test_bad_relations_values_and_revisions(self):
        with self.assertRaises(Problem):self.store.save('exams',{'properties':{'연결된 진료 세션':['missing']}})
        s=self.intake()
        with self.assertRaises(Problem):self.update('sessions',s,{'세션 상태':'가짜상태'})
        self.update('sessions',s,{'내부 메모':'변경'})
        with self.assertRaises(Problem) as cm:self.update('sessions',s,{'내부 메모':'이전 화면'})
        self.assertEqual(cm.exception.status,409)
        with self.assertRaises(Problem):self.store.save('exams',{'properties':{'연결된 진료 세션':[s['id']],'체온':float('nan')}})

    def test_relation_uniqueness_and_delete_restriction(self):
        s=self.intake()
        with self.assertRaises(Problem):self.store.save('sessions',{'properties':{'세션명':'중복','연결된 초진설문':['1'*32]}})
        survey=self.store.get('surveys','1'*32)
        with self.assertRaises(Problem):self.store.delete('surveys',survey['id'],survey['revision'])
        self.assertEqual(self.store.counts()['surveys'],1)

    def test_ai_inputs_require_transcript_and_review(self):
        s=self.intake()
        with self.assertRaises(Problem):self.store.ai_snapshot(s['id'],'b')
        with self.assertRaises(Problem):self.store.ai_snapshot(s['id'],'c')
        context,_=self.store.ai_snapshot(s['id'],'a')
        text=json.dumps(context,ensure_ascii=False)
        self.assertNotIn('010-0000-0000',text)
        self.assertNotIn('3000000',text)
        self.assertNotIn('교육가상',text)

    def test_ai_generation_preserves_other_sections(self):
        s=self.intake()
        s=self.update('sessions',s,document={'transcript':'전사 원문','a':'기존 가','c':'기존 다'})
        _,sig=self.store.ai_snapshot(s['id'],'b')
        self.store.save_ai(s['id'],'b',{'content':'새 나','soap':'S: 근거','variant':'mock'},sig)
        r=self.store.get('sessions',s['id'])
        self.assertEqual(r['document']['a'],'기존 가')
        self.assertEqual(r['document']['c'],'기존 다')
        self.assertEqual(r['document']['transcript'],'전사 원문')
        self.assertEqual(r['properties']['세션 상태'],'한의사검토')

    def test_ai_stale_input_and_failure_preserve(self):
        s=self.intake();_,sig=self.store.ai_snapshot(s['id'],'a')
        self.update('sessions',s,{'내부 메모':'생성 중 수정'})
        with self.assertRaises(Problem):self.store.save_ai(s['id'],'a',{'content':'낡은 결과'},sig)
        with patch('clinic_ai.local_ai.bridge',side_effect=Problem('model failed')):
            with self.assertRaises(Problem):local_ai.generate(self.store,s['id'],'a')
        self.assertFalse(self.store.get('sessions',s['id'])['document'])

    def test_crm_no_consent_and_approval_invalidation(self):
        s=self.reviewed(False)
        with self.assertRaises(Problem):self.crm(s)
        survey=self.store.get('surveys','1'*32)
        self.update('surveys',survey,{'카카오 안내 동의':True})
        s=self.store.get('sessions',s['id'])
        self.assertEqual(s['properties']['세션 상태'],'한의사검토')
        s=self.update('sessions',s,{'세션 상태':'안내준비'})
        crm=self.crm(s,'승인완료')
        self.update('sessions',s,document={'soap':'수정한 SOAP'})
        crm=self.store.get('crm',crm['id'])
        self.assertEqual(crm['properties']['승인 상태'],'승인대기')
        with self.assertRaises(Problem):self.update('crm',crm,{'발송 상태':'발송완료','실제 발송일':'2026-09-17T01:00'})

    def test_crm_approved_message_change_requires_review(self):
        s=self.reviewed();crm=self.crm(s,'승인완료')
        crm=self.update('crm',crm,{'메시지 본문':'수정된 메시지'})
        self.assertEqual(crm['properties']['승인 상태'],'승인대기')
        with self.assertRaises(Problem):self.update('crm',crm,{'승인 상태':'승인완료','발송 상태':'발송완료','실제 발송일':'2020-01-01T12:00'})
        crm=self.update('crm',crm,{'승인 상태':'승인완료'})
        crm=self.update('crm',crm,{'발송 상태':'발송완료','실제 발송일':'2020-01-01T12:00'})
        self.assertEqual(crm['properties']['발송 상태'],'발송완료')

    def test_crud_knowledge_templates_settings(self):
        for table,p in [('knowledge',{'자료명':'교육 자료','관련 증상':['교육'],'사용 상태':'사용중'}),('templates',{'템플릿명':'교육 템플릿','메시지 본문':'안내'}),('settings',{'항목명':'체크','상태':'대기'})]:
            r=self.store.save(table,{'properties':p})
            r=self.update(table,r,{'내부 메모':'수정'})
            self.assertEqual(len(self.store.list(table,'수정')),1)
            self.store.delete(table,r['id'],r['revision'])
            self.assertEqual(self.store.list(table),[])

    def test_reset_backup_and_restart(self):
        self.intake()
        self.assertEqual(Store(self.store.path).counts()['surveys'],1)
        outcome=reset(self.store.path)
        self.assertEqual(sum(outcome['after'].values()),0)
        self.assertEqual(Store(outcome['backup']).counts()['surveys'],1)

    def test_demographics_invalid_and_centuries(self):
        self.assertEqual(demographics({'주민등록번호 앞 6자리':'000230','주민등록번호 뒤 7자리':'3000000'})['나이'],'확인 필요')
        self.assertEqual(demographics({'주민등록번호 앞 6자리':'000101','주민등록번호 뒤 7자리':'4000000'})['성별'],'여성')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.server=ClinicServer(0,Path(self.temp.name)/'clinic.sqlite3')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        _,data=self.request('GET','/api/bootstrap');self.csrf=json.loads(data)['csrf']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def request(self,method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        h={'Content-Type':'application/json','X-CSRF-Token':getattr(self,'csrf','')};h.update(headers or {})
        c.request(method,path,body,h);r=c.getresponse();result=r.status,r.read().decode();c.close();return result
    def test_routes_create_patch_and_reject_cross_origin(self):
        for path in ('/','/app.css','/app.js','/survey','/health'):
            self.assertEqual(self.request('GET',path)[0],200)
        for table in TABLES:
            self.assertEqual(json.loads(self.request('GET','/api/'+table)[1]),[])
        status,html=self.request('GET','/survey')
        token=re.search('name="token" value="([^"]+)"',html)[1]
        values=survey_values();values['token']=token
        status,_=self.request('POST','/submit',urlencode(values),{'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(status,303)
        self.assertEqual(self.server.store.counts()['sessions'],1)
        payload=json.dumps({'properties':{'項目':'bad'}})
        self.assertEqual(self.request('POST','/api/settings',payload,{'Origin':'https://example.com'})[0],403)
        self.assertEqual(self.request('POST','/api/settings',payload,{'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.request('GET','/health',headers={'Host':'evil.test'})[0],403)
        status,raw=self.request('POST','/api/settings',json.dumps({'properties':{'항목명':'운영 점검'}}))
        self.assertEqual(status,201)
        item=json.loads(raw)
        status,_=self.request('PATCH','/api/settings/'+item['id'],json.dumps({'revision':item['revision'],'properties':{'상태':'완료'}}))
        self.assertEqual(status,200)

    def test_form_referrer_policy_and_origin(self):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        c.request('GET','/survey');r=c.getresponse();r.read()
        self.assertEqual(r.getheader('Referrer-Policy'),'same-origin');c.close()
        for origin in ('null','http://evil.test'):
            self.assertEqual(self.request('POST','/submit','x=1',{'Origin':origin})[0],403)
        _,html=self.request('GET','/survey')
        token=re.search('name="token" value="([^\"]+)"',html)[1]
        values=survey_values();values['token']=token
        code,_=self.request('POST','/submit',urlencode(values),{'Content-Type':'application/x-www-form-urlencoded','Origin':f'http://127.0.0.1:{self.server.server_port}'})
        self.assertEqual(code,303)

    def test_mvp_workflow_does_not_block_local_testing(self):
        values=survey_values(consent=False);values.pop('privacy')
        store=self.server.store
        store.submit_survey(values,'b'*32)
        session=store.list('sessions')[0]
        self.assertFalse(store.list('surveys')[0]['properties']['개인정보 수집 동의'])
        for stage in ('b','c'):
            context,_=store.ai_snapshot(session['id'],stage)
            self.assertFalse(context['설문']['카카오 안내 동의'])
        session=store.save('sessions',{'revision':session['revision'],'properties':{'세션 상태':'안내준비'}},session['id'])
        crm=store.save('crm',{'properties':{'요청명':'MVP 미동의 시험','연결된 진료 세션':[session['id']], '승인 상태':'승인완료','발송 상태':'발송완료'}})
        self.assertEqual(crm['properties']['발송 상태'],'발송완료')
        crm=store.save('crm',{'revision':crm['revision'],'properties':{'메시지 본문':'실제 발송 없는 로컬 시험'}},crm['id'])
        self.assertTrue(crm['mvp'])
        with self.assertRaises(Problem):
            store.save('crm',{'revision':0,'properties':{'메시지 본문':'stale'}},crm['id'])
