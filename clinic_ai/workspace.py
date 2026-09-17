"""Patient workspaces, field-level saves, version history and local AI jobs."""
import hashlib
import json
import re
import sqlite3
import threading
from uuid import uuid4
from clinic_ai.store import Problem, now

SOAP_KEYS = ('soap_s', 'soap_o', 'soap_a', 'soap_p')
EDITABLE = {'a', 'b', 'c', 'transcript', 'notes', 'explanation', 'prescription',
            'guide', 'rx_guide', 'message', 'soap', *SOAP_KEYS}

def normalized(document):
    d = dict(document)
    # Parse only all four explicit headings; retain unstructured originals verbatim.
    matches = list(re.finditer(r'(?m)^([SOAP]):\s*', d.get('soap', '')))
    if [m[1] for m in matches] == list('SOAP'):
        for i, m in enumerate(matches):
            d[SOAP_KEYS[i]] = d['soap'][m.end():matches[i+1].start() if i < 3 else None].strip()
    return d

def fingerprint(context):
    return hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

class Workspace:
    def __init__(self, store):
        self.store = store
        self.closed = threading.Event()
        self.wake = threading.Event()
        with store.connection() as db:
            migrated = db.execute("SELECT 1 FROM sqlite_master WHERE name='ux_patients'").fetchone()
            jobs_columns = {r[1] for r in db.execute('PRAGMA table_info(ux_jobs)')}
        if (not migrated or jobs_columns and 'model' not in jobs_columns) and any(store.counts().values()):
            suffix = '-before-models-' if migrated else '-before-ux-'
            backup = store.path.with_name(store.path.stem + suffix + uuid4().hex[:8] + '.sqlite3')
            with store.connection() as source:
                dest = sqlite3.connect(backup)
                try: source.backup(dest)
                finally: dest.close()
            backup.chmod(0o600)
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ux_patients (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ux_visits (sid TEXT PRIMARY KEY, pid TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ux_links (id TEXT PRIMARY KEY, sid TEXT NOT NULL, old_pid TEXT, new_pid TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ux_versions (id TEXT PRIMARY KEY, sid TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ux_jobs (id TEXT PRIMARY KEY, sid TEXT NOT NULL, stage TEXT NOT NULL, request_key TEXT UNIQUE NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, context TEXT NOT NULL, signature TEXT NOT NULL, result TEXT NOT NULL, error TEXT NOT NULL);
            ''')
            for column, default in [('model', 'local'), ('provider', '{}'), ('request', '{}')]:
                if column not in jobs_columns:
                    db.execute(f"ALTER TABLE ux_jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
            db.execute("UPDATE ux_jobs SET state='failed', error='서버가 재시작되어 생성이 중단되었습니다. 기존 기록은 유지됩니다.' WHERE state='running'")
            db.execute('PRAGMA user_version = 3')
        self.link_missing()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def link_missing(self):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in self.store.all_rows(db, 'sessions'):
                if db.execute('SELECT 1 FROM ux_visits WHERE sid=?', (row['id'],)).fetchone(): continue
                ids = row['properties'].get('연결된 초진설문', [])
                name = self.store.row(db, 'surveys', ids[0])['properties'].get('이름') if ids else row['properties']['세션명']
                pid = uuid4().hex
                db.execute('INSERT INTO ux_patients VALUES (?,?,?)', (pid, name, now()))
                db.execute('INSERT INTO ux_visits VALUES (?,?)', (row['id'], pid))

    def patients(self):
        self.link_missing()
        with self.store.connection() as db:
            return [dict(r) | {'visits': db.execute('SELECT COUNT(*) FROM ux_visits v JOIN sessions s ON s.id=v.sid WHERE pid=?', (r['id'],)).fetchone()[0]} for r in db.execute('SELECT * FROM ux_patients ORDER BY created_at DESC')]

    def overview(self):
        self.link_missing()
        with self.store.connection() as db:
            links = dict(db.execute('SELECT sid,pid FROM ux_visits'))
        surveys = {r['id']: r for r in self.store.list('surveys')}
        rows = self.store.list('sessions')
        for r in rows:
            ids = r['properties'].get('연결된 초진설문', [])
            p = surveys.get(ids[0], {}).get('properties', {}) if ids else {}
            r['patient'] = {'id': links.get(r['id']), 'name': p.get('이름', r['properties']['세션명']), 'age': p.get('나이', ''), 'complaint': p.get('주소증', '')}
        return rows

    def encounter(self, sid):
        self.link_missing()
        record = self.store.get('sessions', sid)
        record['document'] = normalized(record['document'])
        ids = record['properties'].get('연결된 초진설문', [])
        survey = self.store.get('surveys', ids[0]) if ids else None
        with self.store.connection() as db:
            patient = dict(db.execute('SELECT p.* FROM ux_patients p JOIN ux_visits v ON p.id=v.pid WHERE v.sid=?', (sid,)).fetchone())
            visit_ids = [r[0] for r in db.execute('SELECT v.sid FROM ux_visits v JOIN sessions s ON s.id=v.sid WHERE pid=? ORDER BY s.created_at DESC', (patient['id'],))]
            versions = [dict(r) for r in db.execute('SELECT id,label,created_at FROM ux_versions WHERE sid=? ORDER BY created_at DESC', (sid,))]
        jobs = self.jobs(sid)
        sources={}
        for key,meta in record['document'].get('ux_sources',{}).items():
            try: stale=meta['signature'] != fingerprint(self.store.ai_snapshot(sid,meta['stage'])[0])
            except Problem: stale=True
            sources[key]=meta | {'stale':stale}
        for j in jobs:
            try: j['stale'] = j['signature'] != fingerprint(self.store.ai_snapshot(sid, j['stage'])[0])
            except Problem: j['stale'] = True
        return {'record': record, 'survey': survey, 'exams': [self.store.get('exams', i) for i in record['properties'].get('관련 신체검사', [])],
                'patient': patient, 'visits': [self.store.get('sessions', i) for i in visit_ids], 'versions': versions, 'jobs': jobs, 'sources': sources}

    def patch(self, sid, payload, label='편집 전'):
        changes, base = payload.get('changes', {}), payload.get('base', {})
        props, bp = payload.get('properties', {}), payload.get('base_properties', {})
        if not all(isinstance(x, dict) for x in (changes, base, props, bp)) or set(changes) - EDITABLE or set(props) - {'세션 상태'}:
            raise Problem('편집 항목 형식이 올바르지 않습니다.')
        if any(not isinstance(v, str) or len(v) > 100000 for v in changes.values()): raise Problem('문서는 100,000자 이내로 입력해 주세요.')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.store.row(db, 'sessions', sid)
            doc = normalized(row['document'])
            conflicts = [k for k in changes if k not in base or (doc.get(k, '') != base[k] and doc.get(k, '') != changes[k])]
            conflicts += [k for k in props if k not in bp or (row['properties'].get(k, '') != bp[k] and row['properties'].get(k, '') != props[k])]
            if conflicts: raise Problem('같은 항목이 다른 화면에서 수정되었습니다: ' + ', '.join(conflicts), 409)
            if not any(doc.get(k, '') != v for k,v in changes.items()) and not any(row['properties'].get(k, '') != v for k,v in props.items()):
                return self.store.get('sessions', sid)
            db.execute('INSERT INTO ux_versions VALUES (?,?,?,?,?)', (uuid4().hex, sid, label, now(), json.dumps(doc, ensure_ascii=False)))
            sources=dict(doc.get('ux_sources',{}))
            for key in changes:
                if key in sources:sources[key]=sources[key] | {'edited':True}
            source_job=payload.get('source_job')
            if source_job:
                job=db.execute("SELECT * FROM ux_jobs WHERE id=? AND sid=? AND state='completed'",(source_job,sid)).fetchone()
                if not job:raise Problem('생성 후보를 찾을 수 없습니다.',404)
                key=payload.get('source_field')
                if key not in changes:raise Problem('적용할 생성 항목을 지정해 주세요.')
                result=json.loads(job['result'])
                candidates={'a':result.get('content','')} if job['stage']=='a' else {k:result.get(k,'') for k in ('guide','rx_guide','message')} if job['stage']=='c' else normalized({'soap':result.get('soap','')}) | {'b':result.get('content','')}
                if key not in candidates:raise Problem('해당 후보에 없는 항목입니다.')
                sources[key]={'job_id':job['id'],'stage':job['stage'],'signature':job['signature'],'generated_at':job['updated_at'],'edited':changes[key]!=candidates[key], 'generation':result.get('generation', json.loads(job['provider']))}
            doc.update(changes)
            if sources:doc['ux_sources']=sources
            if set(changes) & set(SOAP_KEYS): doc['soap'] = '\n\n'.join(f'{label}: {doc.get(key, "")}' for label,key in zip('SOAP',SOAP_KEYS))
            p = self.store.validate(db, 'sessions', props, row['properties'])
            if changes:
                if p.get('세션 상태') in ('안내준비', '완료'): p['세션 상태'] = '한의사검토'
                p['EMR 복사 여부'] = False
                self.store.invalidate_crm(db, sid)
            self.store.write(db, 'sessions', sid, p, doc, row)
        return self.store.get('sessions', sid)

    def version(self, sid, vid):
        with self.store.connection() as db:
            r = db.execute('SELECT * FROM ux_versions WHERE sid=? AND id=?', (sid, vid)).fetchone()
            if not r: raise Problem('이전 버전을 찾을 수 없습니다.', 404)
            return dict(r) | {'document': json.loads(r['document'])}

    def assign(self, sid, payload):
        self.link_missing()
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.row(db, 'sessions', sid)
            pid = payload.get('patient_id')
            old = db.execute('SELECT pid FROM ux_visits WHERE sid=?', (sid,)).fetchone()[0]
            if payload.get('previous_id') != old: raise Problem('환자 연결이 변경되었습니다. 다시 확인해 주세요.', 409)
            if not db.execute('SELECT 1 FROM ux_patients WHERE id=?', (pid,)).fetchone(): raise Problem('환자를 찾을 수 없습니다.', 404)
            db.execute('UPDATE ux_visits SET pid=? WHERE sid=?', (pid, sid))
            db.execute('INSERT INTO ux_links VALUES (?,?,?,?,?)', (uuid4().hex, sid, old, pid, now()))
        return self.encounter(sid)

    def new_visit(self, sid, payload):
        self.link_missing()
        key = payload.get('request_id')
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key): raise Problem('방문 요청 ID가 필요합니다.')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.store.row(db, 'sessions', sid)
            pid = db.execute('SELECT pid FROM ux_visits WHERE sid=?', (sid,)).fetchone()[0]
            existing = db.execute('SELECT pid FROM ux_visits WHERE sid=?', (key,)).fetchone()
            if existing:
                if existing[0] != pid: raise Problem('다른 환자의 요청 ID입니다.',409)
                return {'id': key}
            ids = old['properties'].get('연결된 초진설문', [])
            if not ids: raise Problem('기본 인적사항이 있는 설문을 먼저 연결해 주세요.')
            source = self.store.row(db,'surveys',ids[0])['properties']
            # Carry demographics only; never promote old clinical facts into today's visit.
            p = {k: source[k] for k in ('이름','휴대폰 번호','주민등록번호 앞 6자리','주민등록번호 뒤 7자리') if k in source}
            p.update({'주소증': '재진 — 오늘 증상 확인 필요', '발병 시점 또는 경과':'', '증상 상세':'오늘 문진에서 확인', '복용약':'오늘 문진에서 확인', '개인정보 수집 동의':False, '카카오 안내 동의':False, '설문 상태':'세션연결완료'})
            survey_id=uuid4().hex
            self.store.write(db,'surveys',survey_id,p)
            self.store.write(db,'sessions',key,{'세션명':p['이름']+' 재진','세션 상태':'접수','연결된 초진설문':[survey_id]})
            db.execute('INSERT INTO ux_visits VALUES (?,?)',(key,pid))
        return {'id': key}

    def guidance(self,sid,payload):
        record=self.store.get('sessions',sid)
        text=record['document'].get('message','')
        if not text.strip(): raise Problem('메시지 내용을 입력하거나 초안을 적용해 주세요.')
        with self.store.connection() as db: survey=self.store.survey_for_session(db,sid)['properties']
        crm_id=payload.get('id')
        p={'요청명':survey['이름']+' 진료 안내','수신자 이름':survey['이름'],'휴대전화번호':survey.get('휴대폰 번호',''), '메시지 본문':text,'연결된 진료 세션':[sid],'발송 목적':'진료 안내','승인 상태':'작성중','발송 상태':'발송대기'}
        if crm_id:
            old=self.store.get('crm',crm_id)
            if old['properties'].get('연결된 진료 세션') != [sid]:raise Problem('다른 진료의 안내입니다.',409)
        return self.store.save('crm',{'properties':p,'revision':payload.get('revision')},crm_id)

    def jobs(self,sid):
        with self.store.connection() as db:
            return [dict(r)|{'result':json.loads(r['result']), 'provider':json.loads(r['provider'])} for r in db.execute('SELECT id,sid,stage,model,provider,state,created_at,updated_at,signature,result,error FROM ux_jobs WHERE sid=? ORDER BY created_at DESC LIMIT 30',(sid,))]

    def enqueue(self,sid,stage,payload):
        from clinic_ai import ai_models, local_ai
        key=payload.get('request_id')
        if stage not in ('a','b','c') or not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):raise Problem('생성 요청 형식이 올바르지 않습니다.')
        model=payload.get('model','local')
        strategy=payload.get('soap_strategy','single')
        if strategy not in ('single','split') or (strategy=='split' and stage!='b'):raise Problem('지원하지 않는 SOAP 생성 방식입니다.')
        if not isinstance(model,str) or model not in ai_models.MODELS:raise Problem('지원하지 않는 모델입니다.')
        context,_=self.store.ai_snapshot(sid,stage)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM ux_jobs WHERE request_key=?',(key,)).fetchone()
            if old:
                if old['sid']!=sid or old['stage']!=stage or old['model']!=model or bool(json.loads(old['request']).get('pipeline')) != (strategy=='split'):raise Problem('다른 생성 요청 ID입니다.',409)
                return {'id':old['id']}
            if db.execute("SELECT 1 FROM ux_jobs WHERE sid=? AND stage=? AND state IN ('queued','running')",(sid,stage)).fetchone():raise Problem('이 단계는 이미 생성 중입니다.',409)
            jid=uuid4().hex
            provider=ai_models.resolve(model)
            request=local_ai.make_request(context,stage,soap_strategy=strategy)
            if model!='local' and not request.get('pipeline'):request=ai_models.prepare(request)
            provider['prompt_version']=request['prompt_version']
            provider['soap_strategy']=strategy
            db.execute('INSERT INTO ux_jobs (id,sid,stage,request_key,state,created_at,updated_at,context,signature,result,error,model,provider,request) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(jid,sid,stage,key,'queued',now(),now(),json.dumps(context,ensure_ascii=False),fingerprint(context),'{}','',model,json.dumps(provider,ensure_ascii=False),json.dumps(request,ensure_ascii=False)))
        self.wake.set()
        return {'id':jid}

    def run(self):
        from clinic_ai import local_ai
        while not self.closed.is_set():
            with self.store.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                r=db.execute("SELECT * FROM ux_jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
                if r:db.execute("UPDATE ux_jobs SET state='running',updated_at=? WHERE id=?",(now(),r['id']))
            if not r:
                self.wake.wait(1);self.wake.clear();continue
            try:
                with local_ai.LOCK:
                    provider=json.loads(r['provider'])
                    if r['model']!='local' and (not provider or provider.get('id')!=r['model']):raise Problem('저장된 모델 선택이 일치하지 않습니다. 새 요청을 만들어 주세요.')
                    result=local_ai.draft(json.loads(r['context']),r['stage'],selection=provider or None,request=json.loads(r['request']) or None)
                state,error='completed',''
            except Exception as e:
                result={};state='failed';error=str(e) if isinstance(e,Problem) else '생성하지 못했습니다. 기존 기록은 유지됩니다.'
            with self.store.connection() as db:
                db.execute('UPDATE ux_jobs SET state=?,updated_at=?,result=?,error=? WHERE id=?',(state,now(),json.dumps(result,ensure_ascii=False),error,r['id']))

    def close(self):
        self.closed.set();self.wake.set();self.worker.join(timeout=1)
