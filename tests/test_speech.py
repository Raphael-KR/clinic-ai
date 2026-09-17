import copy
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from clinic_ai.app import ClinicServer
from clinic_ai.store import Store, Problem
from clinic_ai.speech import transcribe, validate_result, MAX_BYTES


def result():
    return {'text':'합성 문진입니다.', 'segments':[{'text':'합성 문진입니다.','start':0.1,'end':1.2,'confidence':0.9}],
            'duration':2.0,'locale':'ko_KR','engine':'Apple SpeechTranscriber'}


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=ClinicServer(0,Path(self.temp.name)/'test.sqlite3')
        self.store=self.server.store
        self.store.submit_survey(dict(name='합성',phone='010-0000-0000',birth='000101',identity='3000000',complaint='교육',
            details='합성 시험',medication='없음',privacy='on',kakao='on'),'c'*32)
        self.session=self.store.list('sessions')[0]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def upload(self,raw=b'audio',extra=None):
        headers={'Content-Type':'application/octet-stream','X-CSRF-Token':self.server.token(),
                 'X-Revision':str(self.session['revision']),'X-Audio-Extension':'.wav'}
        headers.update(extra or {})
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        c.request('POST','/api/transcribe/'+self.session['id'],raw,headers)
        r=c.getresponse();raw=r.read();out=(r.status,json.loads(raw) if raw else {});c.close();return out

    def test_upload_preserves_metadata_and_cleans_file(self):
        paths=[]
        def engine(req):
            paths.append(Path(req['path']))
            self.assertEqual(paths[-1].read_bytes(),b'audio')
            self.assertEqual(paths[-1].stat().st_mode&0o777,0o600)
            return result()
        with patch('clinic_ai.speech.bridge',side_effect=engine):
            status,record=self.upload()
        self.assertEqual(status,200)
        self.assertFalse(paths[0].exists())
        source=record['document']['transcript_source']
        self.assertEqual(source['sha256'],hashlib.sha256(b'audio').hexdigest())
        self.assertEqual(source['segments'],result()['segments'])
        self.assertEqual(record['document']['transcript'],result()['text'])

    def test_replacement_keeps_history_and_invalidates_review(self):
        row=self.store.save('sessions',{'revision':self.session['revision'],'properties':{},'document':{'transcript':'이전 전사','soap':'기존 SOAP','a':'기존 가','c':'기존 다'}},self.session['id'])
        row=self.store.save('sessions',{'revision':row['revision'],'properties':{'세션 상태':'안내준비','EMR 복사 여부':True}},row['id'])
        crm=self.store.save('crm',{'properties':{'요청명':'안내','연결된 진료 세션':[row['id']],'수신자 이름':'합성','휴대전화번호':'010-0000-0000','메시지 본문':'안내','승인 상태':'승인완료','발송 상태':'발송대기'}})
        row=self.store.save_transcript(row['id'],row['revision'],result())
        self.assertEqual(row['document']['transcript_history'][0]['text'],'이전 전사')
        self.assertEqual(row['document']['a'],'기존 가')
        self.assertEqual(row['document']['soap'],'기존 SOAP')
        self.assertEqual(row['document']['c'],'기존 다')
        self.assertEqual(row['properties']['세션 상태'],'한의사검토')
        self.assertFalse(row['properties']['EMR 복사 여부'])
        self.assertEqual(self.store.get('crm',crm['id'])['properties']['승인 상태'],'승인대기')
        row=self.store.save('sessions',{'revision':row['revision'],'properties':{},'document':{'transcript':'사람이 교정'}},row['id'])
        self.assertEqual(row['document']['transcript_source']['text'],result()['text'])

    def test_failure_and_concurrent_edit_leave_existing_content(self):
        paths=[]
        def fail(req):
            paths.append(Path(req['path']))
            raise Problem('시험 실패')
        with patch('clinic_ai.speech.bridge',side_effect=fail):
            status,_=self.upload()
        self.assertEqual(status,422)
        self.assertFalse(paths[0].exists())
        self.assertEqual(self.store.get('sessions',self.session['id'])['document'],{})
        def changed(req):
            self.store.save('sessions',{'revision':self.session['revision'],'properties':{},'document':{'transcript':'동시 편집'}},self.session['id'])
            return result()
        with patch('clinic_ai.speech.bridge',side_effect=changed):
            status,_=self.upload()
        self.assertEqual(status,409)
        self.assertEqual(self.store.get('sessions',self.session['id'])['document']['transcript'],'동시 편집')

    def test_upload_rejects_bad_extension_size_csrf(self):
        for headers,expected in [({'X-Audio-Extension':'../../evil'},415),({'Content-Length':str(MAX_BYTES+1)},413),
                                 ({'X-CSRF-Token':'bad'},403),({'X-Revision':'9999'},409),({'Origin':'https://example.com'},403)]:
            with self.subTest(headers=headers):
                self.assertEqual(self.upload(extra=headers)[0],expected)

    def test_result_validation_and_missing_confidence(self):
        valid=result();del valid['segments'][0]['confidence']
        self.assertEqual(validate_result(valid),valid)
        for invalid in ({'text':'','segments':[]},{'text':'x','segments':[]},
                        {'text':'x','segments':[{'text':'x','start':-1,'end':0}]},
                        {'text':'x','segments':[{'text':'x','start':0,'end':1,'confidence':float('nan')}]},
                        {'text':'x'*100001,'segments':result()['segments']}):
            with self.assertRaises(Problem):validate_result(invalid)
