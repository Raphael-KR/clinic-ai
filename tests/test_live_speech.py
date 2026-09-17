import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from clinic_ai.live_speech import LiveManager, LiveSession
from clinic_ai import speech
from clinic_ai.store import Store, Problem

class FakeLive:
    def __init__(self,sid,revision):
        self.sid=sid;self.revision=revision;self.last=10**20
        self.ready=threading.Event();self.ready.set();self.error='';self.proc=MagicMock();self.proc.poll.return_value=None
        self.text='새 마이크 전사';self.segments=[{'text':self.text,'start':0,'end':1}]
        self.closed=False
    def snapshot(self):return {'text':self.text,'partial':'','error':self.error,'done':True,'seconds':1}
    def close(self,finalize=False):self.closed=True

class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'clinic.sqlite3',mvp=True)
        self.record=self.store.save('sessions',{'properties':{'세션명':'합성 마이크 시험'},'document':{'transcript':'기존 전사','soap':'기존 SOAP'}})
        self.manager=LiveManager(self.store)
        self.patch=patch('clinic_ai.live_speech.LiveSession',FakeLive);self.patch.start()
    def tearDown(self):self.manager.close();self.patch.stop();self.temp.cleanup()
    def start(self):return self.manager.start(self.record['id'],self.record['revision'])['id']
    def test_stop_appends_text_and_preserves_file_workflow_metadata(self):
        jid=self.start();job=self.manager.get(jid);r=self.manager.finish(jid,True)['record']
        d=r['document'];self.assertEqual(d['transcript'],'기존 전사\n\n새 마이크 전사')
        self.assertEqual(d['soap'],'기존 SOAP');self.assertEqual(d['transcript_source']['audio_storage'],'none')
        self.assertEqual(d['transcript_history'][0]['text'],'기존 전사');self.assertTrue(job.closed)
        self.assertFalse(speech.LOCK.locked());self.assertEqual([p.name for p in Path(self.temp.name).iterdir()],['clinic.sqlite3'])
    def test_cancel_preserves_existing_text_and_releases_lock(self):
        jid=self.start();self.manager.finish(jid,False)
        self.assertEqual(self.store.get('sessions',self.record['id'])['document']['transcript'],'기존 전사')
        self.assertFalse(speech.LOCK.locked())
    def test_no_speech_does_not_replace_existing(self):
        jid=self.start();self.manager.get(jid).text='';self.assertTrue(self.manager.finish(jid,True)['empty'])
        self.assertEqual(self.store.get('sessions',self.record['id'])['revision'],self.record['revision'])
    def test_concurrent_edit_returns_unsaved_text_for_recovery(self):
        jid=self.start();self.store.save('sessions',{'revision':self.record['revision'],'properties':{} ,'document':{'transcript':'다른 화면 전사 수정'}},self.record['id'])
        r=self.manager.finish(jid,True);self.assertTrue(r['unsaved']);self.assertEqual(r['transcript']['text'],'새 마이크 전사')
        self.assertEqual(self.store.get('sessions',self.record['id'])['document']['transcript'],'다른 화면 전사 수정')
        self.assertFalse(speech.LOCK.locked())
    def test_notes_saved_while_listening_merge_with_transcript(self):
        jid=self.start()
        self.store.save('sessions',{'revision':self.record['revision'],'properties':{},'document':{'notes':'문진 중 의사 메모'}},self.record['id'])
        r=self.manager.finish(jid,True)['record']
        self.assertEqual(r['document']['notes'],'문진 중 의사 메모')
        self.assertIn('새 마이크 전사',r['document']['transcript'])
    def test_parallel_start_is_rejected_and_cancel_recovers(self):
        jid=self.start()
        with self.assertRaises(Problem):self.start()
        self.manager.finish(jid,False);self.manager.finish(self.start(),False)
    def test_pcm_order_and_length_validation(self):
        # Exercise validation before any pipe write.
        j=LiveSession.__new__(LiveSession);j.lock=threading.RLock();j.error='';j.proc=MagicMock();j.proc.poll.return_value=None;j.sequence=0;j.samples=0
        for seq,raw in [(1,b'1234'),(0,b''),(0,b'123'),(0,b'0'*64004)]:
            with self.assertRaises(Problem):j.chunk(seq,raw)
