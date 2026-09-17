import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from clinic_ai.store import Store, Problem, TABLES
from clinic_ai.workspace import Workspace, normalized

VALUES=dict(name='동명이인',phone='010-0000-0000',birth='000101',identity='3000000',complaint='합성 증상',onset='어제',details='없음이라는 부정문',medication='없음')
class UXTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3';self.store=Store(self.path,mvp=True)
        self.store.submit_survey(VALUES,'1'*32);self.sid=self.store.list('sessions')[0]['id'];self.ws=Workspace(self.store)
    def tearDown(self):self.ws.close();self.tmp.cleanup()
    def edit(self,changes,base=None):return self.ws.patch(self.sid,{'changes':changes,'base':base or {k:'' for k in changes}})
    def wait_job(self,jid):
        for _ in range(100):
            j=next(j for j in self.ws.jobs(self.sid) if j['id']==jid)
            if j['state'] in ('completed','failed'):return j
            time.sleep(.03)
        self.fail('job did not finish')
    def test_field_merge_same_field_conflict_and_history(self):
        self.edit({'notes':'note'})
        self.edit({'soap_s':'없는 증상','soap_o':'120/80'})
        doc=self.store.get('sessions',self.sid)['document']
        self.assertEqual(doc['notes'],'note');self.assertIn('S: 없는 증상',doc['soap'])
        with self.assertRaises(Problem) as cm:self.edit({'notes':'other'})
        self.assertEqual(cm.exception.status,409)
        self.assertEqual(self.store.get('sessions',self.sid)['document']['notes'],'note')
        versions=self.ws.encounter(self.sid)['versions'];old=self.ws.version(self.sid,versions[0]['id'])
        self.assertEqual(old['document']['notes'],'note')
        self.edit({'soap_s':''},{'soap_s':'없는 증상'})
        self.assertEqual(self.store.get('sessions',self.sid)['document']['notes'],'note')
    def test_noop_save_does_not_create_version(self):
        before=self.store.get('sessions',self.sid)['revision'];self.edit({'notes':''})
        self.assertEqual(self.store.get('sessions',self.sid)['revision'],before)
    def test_structural_legacy_preservation(self):
        self.assertEqual(normalized({'soap':'자유 서술 원문'})['soap'],'자유 서술 원문')
        self.assertNotIn('soap_s',normalized({'soap':'S: 부분만'}))
    def test_new_patients_same_name_never_merge_and_reassign(self):
        self.store.submit_survey(VALUES,'2'*32);other=next(r['id'] for r in self.store.list('sessions') if r['id']!=self.sid)
        first=self.ws.encounter(self.sid);second=self.ws.encounter(other)
        self.assertNotEqual(first['patient']['id'],second['patient']['id'])
        self.ws.assign(other,{'patient_id':first['patient']['id'],'previous_id':second['patient']['id']})
        self.assertEqual(len(self.ws.encounter(self.sid)['visits']),2)
        self.ws.assign(other,{'patient_id':second['patient']['id'],'previous_id':first['patient']['id']})
        self.assertEqual(len(self.ws.encounter(self.sid)['visits']),1)
    def test_new_visit_idempotent_no_old_clinical_data(self):
        self.edit({'soap_s':'이전 진술','prescription':'이전 처방'})
        r=self.ws.new_visit(self.sid,{'request_id':'a'*32});self.assertEqual(r,self.ws.new_visit(self.sid,{'request_id':'a'*32}))
        w=self.ws.encounter(r['id'])
        self.assertEqual(w['patient']['id'],self.ws.encounter(self.sid)['patient']['id'])
        self.assertFalse(w['record']['document']);self.assertEqual(w['exams'],[])
        self.assertNotEqual(w['survey']['properties']['주소증'],VALUES['complaint'])
        self.assertEqual(self.store.counts()['sessions'],2)
    def test_migration_backup_preserves_business_rows(self):
        backups=list(self.path.parent.glob('*before-ux*'));self.assertEqual(len(backups),1)
        original=Store(backups[0],mvp=True)
        for table in TABLES:
            a=self.store.list(table);b=original.list(table)
            self.assertEqual(a,b)
        self.ws.close();self.ws=Workspace(self.store)
        self.assertEqual(len(list(self.path.parent.glob('*before-ux*'))),1)
        with self.store.connection() as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_empty_database_stays_empty(self):
        other=Store(Path(self.tmp.name)/'empty.sqlite3',mvp=True);w=Workspace(other)
        try:self.assertEqual(sum(other.counts().values()),0);self.assertEqual(w.patients(),[])
        finally:w.close()
    def test_guidance_no_consent_direct_message(self):
        self.edit({'message':'전달할 메시지'})
        r=self.ws.guidance(self.sid,{})
        self.assertEqual(r['properties']['메시지 본문'],'전달할 메시지')
        self.assertEqual(r['properties']['수신자 이름'],VALUES['name'])
        self.assertEqual(r['properties']['발송 상태'],'발송대기')
    def test_job_input_snapshot_preserves_newer_edit(self):
        entered=threading.Event();release=threading.Event()
        def model(context,stage,**kwargs):entered.set();release.wait(3);return {'content':'후보'}
        with patch('clinic_ai.local_ai.draft',side_effect=model):
            r=self.ws.enqueue(self.sid,'a',{'request_id':'b'*32})
            self.assertEqual(r,self.ws.enqueue(self.sid,'a',{'request_id':'b'*32}))
            self.assertTrue(entered.wait(2));self.edit({'notes':'생성 중 수정'})
            release.set();j=self.wait_job(r['id'])
        self.assertEqual(j['state'],'completed');self.assertEqual(j['result']['content'],'후보')
        self.assertEqual(self.store.get('sessions',self.sid)['document']['notes'],'생성 중 수정')
        self.assertNotIn('a',self.store.get('sessions',self.sid)['document'])
        self.assertTrue(self.ws.encounter(self.sid)['jobs'][0]['stale'])
    def test_job_failure_and_restart_preserve_records(self):
        self.edit({'a':'수정본'})
        with patch('clinic_ai.local_ai.draft',side_effect=Problem('모델 거부')):
            j=self.wait_job(self.ws.enqueue(self.sid,'a',{'request_id':'c'*32})['id'])
        self.assertEqual(j['state'],'failed');self.assertEqual(self.store.get('sessions',self.sid)['document']['a'],'수정본')
        self.ws.close()
        with self.store.connection() as db:db.execute("UPDATE ux_jobs SET state='running'")
        self.ws=Workspace(self.store);self.assertEqual(self.ws.jobs(self.sid)[0]['state'],'failed')
    def test_mvp_prompt_and_structured_guidance(self):
        from clinic_ai.local_ai import draft
        with patch('clinic_ai.local_ai.bridge',return_value={'content':'검토 요약','guide':'안내','rx_guide':'해당 없음','message':'안내 메시지'}) as bridge:
            r=draft({'설문':{'카카오 안내 동의':False}},'c')
        self.assertIn('미동의여도',bridge.call_args[0][0]['instructions'])
        self.assertNotIn('초안을 만들지 말고',bridge.call_args[0][0]['instructions'])
        self.assertEqual(r['message'],'안내 메시지')
    def test_bad_edit_does_not_write(self):
        for p in ({'changes':{'unknown':'x'},'base':{}},{'changes':{'notes':['bad']},'base':{'notes':''}},{'properties':{'세션 상태':'가짜'},'base_properties':{'세션 상태':'접수'}}):
            with self.assertRaises(Problem):self.ws.patch(self.sid,p)
        self.assertFalse(self.store.get('sessions',self.sid)['document'])

    def test_applied_source_scope_edit_and_staleness(self):
        with patch('clinic_ai.local_ai.draft',return_value={'content':'후보'}):
            j=self.wait_job(self.ws.enqueue(self.sid,'a',{'request_id':'d'*32})['id'])
        self.ws.patch(self.sid,{'changes':{'a':'후보','notes':'동시 메모'},'base':{'a':'','notes':''},'source_job':j['id'],'source_field':'a'})
        w=self.ws.encounter(self.sid)
        self.assertEqual(set(w['sources']),{'a'})
        self.assertTrue(w['sources']['a']['stale'])
        self.edit({'a':'사용자 수정'},{'a':'후보'})
        self.assertTrue(self.ws.encounter(self.sid)['sources']['a']['edited'])
    def test_full_backup_restore_preserves_patient_links_versions_and_records(self):
        self.edit({'notes':'재시작 후 보존','soap_s':'원문'})
        other=self.ws.new_visit(self.sid,{'request_id':'e'*32})['id']
        patient=self.ws.encounter(self.sid)['patient']['id']
        backup=self.path.parent/'restored.sqlite3'
        with self.store.connection() as source, sqlite3.connect(backup) as target:source.backup(target)
        self.ws.close();self.ws=Workspace(Store(backup,mvp=True))
        restored=self.ws.encounter(self.sid)
        self.assertEqual(restored['record']['document']['notes'],'재시작 후 보존')
        self.assertEqual(restored['patient']['id'],patient)
        self.assertEqual({r['id'] for r in restored['visits']},{self.sid,other})
        self.assertTrue(restored['versions'])
        with self.ws.store.connection() as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_explicit_reset_includes_new_structures_in_backup(self):
        from clinic_ai.app import reset
        self.edit({'notes':'백업 내용'})
        self.ws.close()
        result=reset(self.path)
        self.assertTrue(result['backup'])
        with self.store.connection() as db:
            for t in ('ux_patients','ux_visits','ux_links','ux_versions','ux_jobs'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0],0)
        restored=Workspace(Store(result['backup'],mvp=True))
        try:self.assertEqual(restored.encounter(self.sid)['record']['document']['notes'],'백업 내용')
        finally:restored.close()
        self.ws=Workspace(self.store)

    def test_model_and_prompt_persist_and_retry_id_cannot_switch_model(self):
        from clinic_ai import local_ai
        provider={'id':'cloud','label':'AFM 3 Cloud','location':'Apple Private Cloud Compute','transport':'shortcuts','shortcut_id':'pinned'}
        captured=[]
        def model(context,stage,**kwargs):
            captured.append(kwargs)
            return {'content':'cloud candidate','generation':kwargs['selection']}
        with patch('clinic_ai.ai_models.resolve',return_value=provider),patch('clinic_ai.local_ai.draft',side_effect=model):
            job=self.ws.enqueue(self.sid,'a',{'request_id':'f'*32,'model':'cloud'})
            self.assertEqual(job,self.ws.enqueue(self.sid,'a',{'request_id':'f'*32,'model':'cloud'}))
            with self.assertRaises(Problem):self.ws.enqueue(self.sid,'a',{'request_id':'f'*32,'model':'local'})
            result=self.wait_job(job['id'])
        self.assertEqual(result['model'],'cloud')
        self.assertEqual(captured[0]['selection']['shortcut_id'],'pinned')
        self.assertEqual(captured[0]['request']['prompt_version'],local_ai.PROMPT_VERSION)
        self.assertEqual(result['provider']['prompt_version'],local_ai.PROMPT_VERSION)
        self.ws.patch(self.sid,{'changes':{'a':'cloud candidate'},'base':{'a':''},'source_job':job['id'],'source_field':'a'})
        self.assertEqual(self.ws.encounter(self.sid)['sources']['a']['generation']['id'],'cloud')

    def test_bad_cloud_output_does_not_change_existing_document(self):
        self.edit({'soap_s':'original S','soap_o':'original O','message':'original message'})
        before=self.store.get('sessions',self.sid)
        with patch('clinic_ai.ai_models.resolve',return_value={'id':'cloud'}),patch('clinic_ai.ai_models.shortcut',return_value={'content':'incomplete','subjective':'s'}),patch('clinic_ai.local_ai.bridge') as local:
            result=self.wait_job(self.ws.enqueue(self.sid,'b',{'request_id':'9'*32,'model':'cloud'})['id'])
            self.assertEqual(result['state'],'failed');local.assert_not_called()
        self.assertEqual(self.store.get('sessions',self.sid),before)

    def test_split_job_preserves_snapshot_strategy_and_failure(self):
        self.edit({'soap_s':'기존 진술'})
        before=self.store.get('sessions',self.sid)
        def fail_after_facts(r):
            if r['stage']=='b':return {'content':'facts','soap':'S: x\nO: y\nA: z\nP: t'}
            raise Problem('section failed',503)
        with patch('clinic_ai.local_ai.bridge',side_effect=fail_after_facts):
            job=self.ws.enqueue(self.sid,'b',{'request_id':'8'*32,'soap_strategy':'split'})
            result=self.wait_job(job['id'])
        self.assertEqual(result['state'],'failed')
        self.assertIn('1/S',result['error'])
        self.assertEqual(result['provider']['soap_strategy'],'split')
        self.assertEqual(self.store.get('sessions',self.sid),before)
        with self.assertRaises(Problem):self.ws.enqueue(self.sid,'b',{'request_id':'8'*32,'soap_strategy':'single'})
        with self.store.connection() as db:
            request=json.loads(db.execute('SELECT request FROM ux_jobs WHERE id=?',(job['id'],)).fetchone()[0])
        self.assertEqual(len(request['section_instructions']),4)

    def test_old_job_schema_migration_preserves_rows_and_defaults_local(self):
        self.ws.close()
        with self.store.connection() as db:
            for c in ('model','provider','request'):db.execute(f'ALTER TABLE ux_jobs DROP COLUMN {c}')
            db.execute("INSERT INTO ux_jobs VALUES ('legacy',?,'a','legacy','completed','old','old','{}','sig','{\"content\":\"previous\"}','')",(self.sid,))
            db.execute('PRAGMA user_version=2')
        self.ws=Workspace(self.store)
        j=self.ws.jobs(self.sid)[0]
        self.assertEqual(j['model'],'local');self.assertEqual(j['result']['content'],'previous')
        self.assertEqual(len(list(self.path.parent.glob('*before-models*'))),1)
        with self.store.connection() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
