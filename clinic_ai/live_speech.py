"""Ephemeral PCM pipes only. Never creates an audio file or logs audio/text."""
import json
import os
from pathlib import Path
import secrets
import select
import struct
import subprocess
import threading
import time
from clinic_ai import speech
from clinic_ai.store import Problem

BINARY = Path(__file__).resolve().parent.parent / '.build/live-speech-bridge'

class LiveSession:
    def __init__(self, sid, revision):
        self.sid, self.revision = sid, revision
        self.lock = threading.RLock()
        self.last = time.monotonic()
        self.sequence = self.samples = 0
        self.text = self.partial = self.error = ''
        self.segments = []
        self.ready = threading.Event()
        self.done = False
        self.proc = subprocess.Popen([str(BINARY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.proc.stdin.fileno(), False)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        try:
            for line in self.proc.stdout:
                event = json.loads(line)
                with self.lock:
                    if event.get('type') == 'ready': self.ready.set()
                    elif event.get('type') == 'result':
                        if event['final']:
                            self.text += event['text']
                            self.segments.extend(event['segments'])
                            self.partial = ''
                        else: self.partial = event['text']
                        if len(self.text) + len(self.partial) > 100000:
                            self.error = '전사문이 100,000자를 넘었습니다.'
                            self.proc.terminate()
                    elif event.get('type') == 'done': self.done = True
                    elif event.get('error'): self.error = event['error']
        except (ValueError, OSError):
            self.error = '실시간 전사 응답 오류'
        finally:
            self.ready.set()

    def snapshot(self):
        with self.lock:
            return {'text': self.text, 'partial': self.partial, 'error': self.error,
                    'done': self.done, 'seconds': self.samples / 16000, 'sequence': self.sequence}

    def chunk(self, seq, raw):
        with self.lock:
            if self.error or self.proc.poll() is not None:
                raise Problem(self.error or '전사 엔진이 종료되었습니다.', 409)
            if seq != self.sequence: raise Problem('오디오 순서가 달라 전사를 중단합니다.', 409)
            if not raw or len(raw) > 64000 or len(raw) % 4: raise Problem('PCM 형식 오류', 422)
            if self.samples + len(raw)//4 > 16000*7200: raise Problem('마이크 전사는 최대 2시간입니다.')
            payload = memoryview(struct.pack('<I',len(raw))+raw)
            deadline = time.monotonic()+3
            while payload:
                if time.monotonic()>deadline: raise Problem('전사 처리 속도를 초과했습니다.', 503)
                if not select.select([], [self.proc.stdin], [], .2)[1]: continue
                try: n=os.write(self.proc.stdin.fileno(),payload)
                except (BrokenPipeError,OSError): raise Problem('전사 엔진 연결이 끊어졌습니다.', 503)
                payload=payload[n:]
            self.sequence+=1;self.samples+=len(raw)//4;self.last=time.monotonic()
            return {'sequence':self.sequence}

    def close(self, finalize=False):
        try:
            if finalize:
                self.proc.stdin.close()
                self.proc.wait(timeout=20)
            elif self.proc.poll() is None:
                self.proc.terminate();self.proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            self.proc.kill();self.proc.wait()
            self.error = '전사 종료 시간이 초과되었습니다.'
        finally:
            self.reader.join(timeout=2)
            for pipe in (self.proc.stdin,self.proc.stdout):
                if not pipe.closed: pipe.close()

class LiveManager:
    def __init__(self, store):
        self.store=store;self.jobs={};self.lock=threading.RLock()
        self.closed=threading.Event()
        threading.Thread(target=self.reap,daemon=True).start()

    def start(self,sid,revision):
        if self.store.get('sessions',sid)['revision'] != revision: raise Problem('세션을 새로고침해 주세요.',409)
        if not speech.LOCK.acquire(blocking=False): raise Problem('다른 음성 전사를 마친 뒤 시작해 주세요.',409)
        job=None
        try:
            job=LiveSession(sid,revision)
            if not job.ready.wait(15) or job.error or job.proc.poll() is not None:
                raise Problem(job.error or '실시간 전사 엔진을 준비하지 못했습니다.',503)
            jid=secrets.token_hex(24)
            job.base_transcript=self.store.get('sessions',sid)['document'].get('transcript','')
            with self.lock:self.jobs[jid]=job
            return {'id':jid,'sampleRate':16000}
        except Exception:
            if job:job.close()
            speech.LOCK.release()
            raise

    def get(self,jid):
        with self.lock:
            if jid not in self.jobs:raise Problem('종료된 마이크 전사입니다.',404)
            return self.jobs[jid]

    def finish(self,jid,save):
        with self.lock:
            job=self.get(jid);del self.jobs[jid]
        try:
            job.close(finalize=save)
            result=job.snapshot()
            if not save:return {'cancelled':True}
            if result['error']:raise Problem(result['error'])
            if not result['done']:raise Problem('전사 엔진이 정상적으로 완료되지 않았습니다.')
            if not result['text'].strip():return {'empty':True,'message':'인식된 발화가 없습니다. 기존 전사는 유지했습니다.'}
            transcript={'text':result['text'],'segments':job.segments,'duration':result['seconds'],
                        'locale':'ko_KR','engine':'Apple SpeechTranscriber · 실시간 마이크','audio_storage':'none'}
            try:
                return {'record':self.store.save_transcript(job.sid,job.revision,transcript,append=True,base_transcript=job.base_transcript)}
            except Problem as error:
                return {'unsaved':True,'message':str(error),'transcript':transcript}
        finally:speech.LOCK.release()

    def reap(self):
        while not self.closed.wait(5):
            with self.lock:expired=[jid for jid,j in self.jobs.items() if time.monotonic()-j.last>30]
            for jid in expired:
                try:self.finish(jid,False)
                except Problem:pass

    def close(self):
        self.closed.set()
        for jid in list(self.jobs):
            try:self.finish(jid,False)
            except Problem:pass
