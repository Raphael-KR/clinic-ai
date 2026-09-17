"""Seven local collections with validated relations and optimistic revisions."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / 'source_schema.json').read_text())
DOCS = json.loads((ROOT / 'source_docs.json').read_text())
DEFAULT_DB = ROOT.parent / 'data' / 'clinic.sqlite3'
KST = ZoneInfo('Asia/Seoul')
TABLES = ('surveys', 'exams', 'sessions', 'knowledge', 'crm', 'templates', 'settings')
SOURCE_KEYS = {v['url']: k for k, v in SCHEMA.items()}
# Only one side is stored. Reverse relations are computed on read.
RELATIONS = {
    ('sessions', '연결된 초진설문'): ('surveys', '연결된 진료 세션', 1),
    ('exams', '연결된 진료 세션'): ('sessions', '관련 신체검사', 1),
    ('sessions', '관련 한의학 자료'): ('knowledge', '관련 진료 세션', None),
    ('crm', '연결된 진료 세션'): ('sessions', '관련 CRM 발송', 1),
    ('crm', '메시지 템플릿'): ('templates', '관련 CRM 발송', 1),
}
REVERSE = {(target, back): (table, field) for (table, field), (target, back, limit) in RELATIONS.items()}
REVIEWED = {'안내준비', '완료'}
DOC_FIELDS = {'a', 'transcript', 'b', 'soap', 'c', 'prescription', 'explanation', 'notes', 'guide', 'rx_guide', 'message', 'soap_s', 'soap_o', 'soap_a', 'soap_p'}


class Problem(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def demographics(p):
    prefix, suffix = p.get('주민등록번호 앞 6자리', ''), p.get('주민등록번호 뒤 7자리', '')
    if not re.fullmatch(r'[0-9]{6}', prefix) or not re.fullmatch(r'[0-9]{7}', suffix):
        return {'나이': '확인 필요', '성별': '확인 필요'}
    marker = suffix[0]
    century = 1800 if marker in '90' else 1900 if marker in '1256' else 2000
    try:
        birth = date(century + int(prefix[:2]), int(prefix[2:4]), int(prefix[4:6]))
        today = datetime.now(KST).date()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        if age < 0:
            raise ValueError()
        return {'나이': f'만 {age}세', '성별': '남성' if int(marker) % 2 else '여성'}
    except ValueError:
        return {'나이': '확인 필요', '성별': '확인 필요'}


class Store:
    def __init__(self, path=DEFAULT_DB, *, mvp=False):
        self.mvp = mvp
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self.connection() as db:
            for table in TABLES:
                db.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL, properties TEXT NOT NULL, document TEXT NOT NULL)''')
            if db.execute('PRAGMA user_version').fetchone()[0] < 1:
                db.execute('PRAGMA user_version = 1')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def check_table(self, table):
        if table not in TABLES:
            raise Problem('존재하지 않는 DB입니다.', 404)

    def row(self, db, table, rid):
        self.check_table(table)
        item = db.execute(f'SELECT * FROM {table} WHERE id=?', (rid,)).fetchone()
        if not item:
            raise Problem('기록을 찾을 수 없습니다.', 404)
        result = dict(item)
        result['properties'] = json.loads(result['properties'])
        result['document'] = json.loads(result['document'])
        return result

    def all_rows(self, db, table):
        self.check_table(table)
        return [self.row(db, table, r[0]) for r in db.execute(f'SELECT id FROM {table} ORDER BY created_at DESC, id')]

    def decorate(self, db, table, row):
        p = row['properties']
        for (target, back), (owner, field) in REVERSE.items():
            if target == table:
                p[back] = [r['id'] for r in self.all_rows(db, owner) if row['id'] in r['properties'].get(field, [])]
        if table == 'surveys':
            p.update(demographics(p))
            p['제출일'] = row['created_at']
        row['mvp'] = self.mvp
        return row

    def get(self, table, rid):
        with self.connection() as db:
            return self.decorate(db, table, self.row(db, table, rid))

    def list(self, table, search=''):
        with self.connection() as db:
            rows = [self.decorate(db, table, row) for row in self.all_rows(db, table)]
        if search:
            rows = [r for r in rows if search.casefold() in json.dumps(r['properties'], ensure_ascii=False).casefold()]
        return rows

    def counts(self):
        with self.connection() as db:
            return {t: db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in TABLES}

    def validate(self, db, table, incoming, old=None):
        if not isinstance(incoming, dict):
            raise Problem('속성 형식이 올바르지 않습니다.')
        schema = SCHEMA[table]['schema']
        if set(incoming) - set(schema):
            raise Problem('알 수 없는 속성이 있습니다.')
        result = dict(old or {})
        for name, value in incoming.items():
            spec = schema[name]
            kind = spec['type']
            if (table, name) in REVERSE or kind in ('formula', 'created_time'):
                continue
            if kind == 'checkbox':
                if type(value) is not bool:
                    raise Problem(f'{name}: 동의/체크 값을 확인해 주세요.')
            elif kind == 'number':
                if value == '' or value is None:
                    value = None
                elif type(value) not in (int, float) or not math.isfinite(value):
                    raise Problem(f'{name}: 유효한 숫자를 입력해 주세요.')
            elif kind in ('relation', 'multi_select'):
                if not isinstance(value, list) or any(not isinstance(v, str) for v in value) or len(value) > 100:
                    raise Problem(f'{name}: 목록 형식이 필요합니다.')
                value = list(dict.fromkeys(value))
                if kind == 'relation':
                    target, _, limit = RELATIONS[(table, name)]
                    if limit and len(value) > limit:
                        raise Problem(f'{name}: {limit}개만 연결할 수 있습니다.')
                    for rid in value:
                        self.row(db, target, rid)
                elif any(len(v) > 100 for v in value):
                    raise Problem(f'{name}: 태그가 너무 깁니다.')
            else:
                if not isinstance(value, str) or len(value) > 30000:
                    raise Problem(f'{name}: 30,000자 이내의 텍스트가 필요합니다.')
                value = value.strip()
                if kind == 'select' and value and value not in {o['name'] for o in spec.get('options', [])}:
                    raise Problem(f'{name}: 지정된 선택값을 사용해 주세요.')
                if kind == 'url' and value and not value.startswith(('http://', 'https://')):
                    raise Problem(f'{name}: http 또는 https 주소를 입력해 주세요.')
                if kind == 'date' and value:
                    try:
                        datetime.fromisoformat(value)
                    except ValueError:
                        raise Problem(f'{name}: 날짜 형식을 확인해 주세요.')
            result[name] = value
        title = next(k for k, v in schema.items() if v['type'] == 'title')
        if not result.get(title) and table != 'exams':
            raise Problem(f'{title} 항목을 입력해 주세요.')
        if table == 'exams' and not result.get('연결된 진료 세션'):
            raise Problem('검사를 연결할 진료 세션을 선택해 주세요.')
        return result

    def write(self, db, table, rid, properties, document=None, existing=None):
        timestamp = now()
        doc = document if document is not None else (existing['document'] if existing else {})
        revision = existing['revision'] + 1 if existing else 1
        db.execute(f'''INSERT INTO {table} VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   updated_at=excluded.updated_at, revision=excluded.revision,
                   properties=excluded.properties, document=excluded.document''',
                   (rid, existing['created_at'] if existing else timestamp, timestamp, revision,
                    json.dumps(properties, ensure_ascii=False), json.dumps(doc, ensure_ascii=False)))

    def invalidate_crm(self, db, sid):
        for crm in self.all_rows(db, 'crm'):
            p = crm['properties']
            if sid in p.get('연결된 진료 세션', []) and p.get('발송 상태') != '발송완료':
                p['승인 상태'] = '승인대기'
                self.write(db, 'crm', crm['id'], p, existing=crm)

    def invalidate_session(self, db, sid):
        session = self.row(db, 'sessions', sid)
        p = session['properties']
        if p.get('세션 상태') in REVIEWED:
            p['세션 상태'] = '한의사검토'
        p['EMR 복사 여부'] = False
        self.write(db, 'sessions', sid, p, existing=session)
        self.invalidate_crm(db, sid)

    def survey_for_session(self, db, sid):
        session = self.row(db, 'sessions', sid)
        ids = session['properties'].get('연결된 초진설문', [])
        if not ids:
            raise Problem('진료 세션에 초진설문이 연결되어 있지 않습니다.')
        return self.row(db, 'surveys', ids[0])

    def check_crm(self, db, p, old=None):
        sid = p.get('연결된 진료 세션', [])
        active = p.get('승인 상태') in ('승인대기', '승인완료') or p.get('발송 상태') == '발송완료'
        if active:
            if not sid:
                raise Problem('CRM 준비에는 연결된 진료 세션이 필요합니다.')
            survey = self.survey_for_session(db, sid[0])['properties']
            if not survey.get('카카오 안내 동의'):
                raise Problem('카카오 안내 동의가 없어 발송 준비를 진행할 수 없습니다.')
            if not p.get('메시지 본문') or not p.get('휴대전화번호') or not p.get('수신자 이름'):
                raise Problem('수신자·연락처·최종 메시지를 입력해 주세요.')
            if p['수신자 이름'] != survey.get('이름') or p['휴대전화번호'] != survey.get('휴대폰 번호'):
                raise Problem('수신자 이름과 연락처가 연결된 설문과 다릅니다.')
        if p.get('승인 상태') == '승인완료':
            session = self.row(db, 'sessions', sid[0])
            if session['properties'].get('세션 상태') not in REVIEWED:
                raise Problem('진료 세션의 SOAP 검토를 먼저 완료해 주세요.')
        if p.get('발송 상태') == '발송완료':
            if not old or old.get('승인 상태') != '승인완료' or p.get('승인 상태') != '승인완료':
                raise Problem('먼저 최종 메시지를 승인한 뒤 수동 발송 결과를 기록해 주세요.')
            if not p.get('실제 발송일'):
                raise Problem('실제 발송일을 기록해 주세요.')
            if datetime.fromisoformat(p['실제 발송일']).replace(tzinfo=KST) > datetime.now(KST):
                raise Problem('실제 발송일은 미래일 수 없습니다.')

    def save(self, table, payload, rid=None):
        self.check_table(table)
        if not isinstance(payload, dict):
            raise Problem('객체 형식이 필요합니다.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.row(db, table, rid) if rid else None
            if old and payload.get('revision') != old['revision']:
                raise Problem('다른 화면에서 변경되었습니다. 새로고침 후 다시 저장해 주세요.', 409)
            rid = rid or uuid4().hex
            p = self.validate(db, table, payload.get('properties', {}), old['properties'] if old else None)
            doc = dict(old['document']) if old else {}
            if 'document' in payload:
                if table != 'sessions' or not isinstance(payload['document'], dict):
                    raise Problem('진료 세션 문서만 편집할 수 있습니다.')
                for k, v in payload['document'].items():
                    if k not in DOC_FIELDS or not isinstance(v, str) or len(v) > 100000:
                        raise Problem('문서 항목 또는 길이가 올바르지 않습니다.')
                    doc[k] = v
            if table == 'surveys':
                from clinic_ai.survey import FIELDS, validate
                mapped = {key: ('on' if p.get(label) else '') if kind == 'checkbox' else p.get(label, '') for key, label, kind, req in FIELDS}
                _, errors = validate(mapped, mvp=self.mvp)
                if errors:
                    raise Problem('\n'.join(errors))
            if table == 'sessions':
                ids = p.get('연결된 초진설문', [])
                for other in self.all_rows(db, 'sessions'):
                    if other['id'] != rid and set(ids) & set(other['properties'].get('연결된 초진설문', [])):
                        raise Problem('이 설문은 이미 다른 세션과 연결되어 있습니다.', 409)
                changed = old and (any(doc.get(k) != old['document'].get(k) for k in ('transcript', 'soap', 'prescription', 'explanation')) or
                                   any(p.get(k) != old['properties'].get(k) for k in ('연결된 초진설문', '관련 한의학 자료')))
                if changed:
                    if p.get('세션 상태') in REVIEWED:
                        p['세션 상태'] = '한의사검토'
                    p['EMR 복사 여부'] = False
                    self.invalidate_crm(db, rid)
                if not self.mvp and p.get('세션 상태') in REVIEWED and not doc.get('soap', '').strip():
                    raise Problem('SOAP를 작성한 뒤 검토 완료 상태로 변경해 주세요.')
            if table == 'crm':
                if old and any(p.get(k) != old['properties'].get(k) for k in ('메시지 본문', '수신자 이름', '휴대전화번호', '연결된 진료 세션', '메시지 템플릿', '발송 목적', '발송 예정일')):
                    if not self.mvp and old['properties'].get('발송 상태') == '발송완료':
                        raise Problem('발송완료 기록은 수정할 수 없습니다. 새 요청을 만들어 주세요.')
                    p['승인 상태'] = '승인대기'
                if not self.mvp:
                    self.check_crm(db, p, old['properties'] if old else None)
            if table == 'exams' and not p.get('검사 기록명'):
                session = self.row(db, 'sessions', p['연결된 진료 세션'][0])
                p['검사 기록명'] = session['properties']['세션명'] + ' / 검사 ' + datetime.now(KST).strftime('%m-%d %H:%M')
            self.write(db, table, rid, p, doc, old)
            if table == 'surveys' and not old:
                p['설문 상태'] = '세션연결완료'
                self.write(db, table, rid, p, doc, self.row(db, table, rid))
                self.write(db, 'sessions', uuid4().hex, {
                    '세션명': p['이름'] + datetime.now(KST).strftime(' (%Y-%m-%d %H:%M)'),
                    '세션 상태': '접수', '연결된 초진설문': [rid]})
            if table == 'sessions':
                old_ids = old['properties'].get('연결된 초진설문', []) if old else []
                for survey_id in set(old_ids) | set(p.get('연결된 초진설문', [])):
                    survey = self.row(db, 'surveys', survey_id)
                    survey['properties']['설문 상태'] = '세션연결완료' if survey_id in p.get('연결된 초진설문', []) else '제출완료'
                    self.write(db, 'surveys', survey_id, survey['properties'], existing=survey)
            if table == 'exams':
                sids = set(p['연결된 진료 세션']) | set(old['properties'].get('연결된 진료 세션', []) if old else [])
                for sid in sids:
                    session = self.row(db, 'sessions', sid)
                    if session['properties'].get('세션 상태') in ('접수', '설문확인'):
                        session['properties']['세션 상태'] = '검사확인'
                        self.write(db, 'sessions', sid, session['properties'], existing=session)
                    else:
                        self.invalidate_session(db, sid)
            if table == 'surveys' and old and p != old['properties']:
                for session in self.all_rows(db, 'sessions'):
                    if rid in session['properties'].get('연결된 초진설문', []):
                        self.invalidate_session(db, session['id'])
            if table == 'knowledge' and old:
                for session in self.all_rows(db, 'sessions'):
                    if rid in session['properties'].get('관련 한의학 자료', []):
                        self.invalidate_session(db, session['id'])
        return self.get(table, rid)

    def submit_survey(self, values, token_id):
        from clinic_ai.survey import validate
        answers, errors = validate(values, mvp=self.mvp)
        if errors:
            raise Problem('\n'.join(errors))
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT id FROM surveys WHERE id=?', (token_id,)).fetchone()
            if found:
                old = self.row(db, 'surveys', token_id)['properties']
                if any(old.get(k) != v for k, v in answers.items()):
                    raise Problem('이미 제출된 설문입니다. 새 설문을 열어 주세요.', 409)
                return token_id
            answers['설문 상태'] = '세션연결완료'
            self.write(db, 'surveys', token_id, answers)
            session = {'세션명': answers['이름'] + datetime.now(KST).strftime(' (%Y-%m-%d %H:%M)'),
                       '세션 상태': '접수', '연결된 초진설문': [token_id]}
            self.write(db, 'sessions', uuid4().hex, session)
        return token_id

    def delete(self, table, rid, revision):
        self.check_table(table)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.row(db, table, rid)
            if old['revision'] != revision:
                raise Problem('기록이 변경되었습니다. 새로고침해 주세요.', 409)
            for (owner, field), (target, _, _) in RELATIONS.items():
                if target == table:
                    if any(rid in r['properties'].get(field, []) for r in self.all_rows(db, owner)):
                        raise Problem('다른 기록에서 참조 중입니다. 연결을 해제한 뒤 삭제해 주세요.', 409)
            db.execute(f'DELETE FROM {table} WHERE id=?', (rid,))
            if table == 'sessions':
                for survey_id in old['properties'].get('연결된 초진설문', []):
                    survey = self.row(db, 'surveys', survey_id)
                    survey['properties']['설문 상태'] = '제출완료'
                    self.write(db, 'surveys', survey_id, survey['properties'], existing=survey)
            if table == 'exams':
                for sid in old['properties'].get('연결된 진료 세션', []):
                    self.invalidate_session(db, sid)

    def ai_snapshot(self, sid, stage):
        if stage not in ('a', 'b', 'c'):
            raise Problem('알 수 없는 AI 단계입니다.')
        session = self.get('sessions', sid)
        p, doc = session['properties'], session['document']
        ids = p.get('연결된 초진설문', [])
        if not ids:
            raise Problem('초진설문을 먼저 연결해 주세요.')
        survey = self.get('surveys', ids[0])
        if not self.mvp and stage == 'b' and not doc.get('transcript', '').strip():
            raise Problem('나 단계는 문진 전사 텍스트가 필요합니다.')
        if not self.mvp and stage == 'c' and (p.get('세션 상태') not in REVIEWED or not doc.get('soap')):
            raise Problem('SOAP를 검토하고 세션 상태를 안내준비로 변경해 주세요.')
        exams = [self.get('exams', rid) for rid in p.get('관련 신체검사', [])]
        exams.sort(key=lambda r: (r['created_at'], r['id']), reverse=True)
        knowledge = [self.get('knowledge', rid) for rid in p.get('관련 한의학 자료', [])]
        knowledge = [r for r in knowledge if r['properties'].get('사용 상태') == '사용중']
        allowed = ('나이', '성별', '주소증', '발병 시점 또는 경과', '증상 상세', '복용약', '카카오 안내 동의')
        context = {'설문': {k: survey['properties'].get(k, '확인 필요') for k in allowed},
                   '검사': [{'기준 시각': r['created_at'], **{k: v for k, v in r['properties'].items() if k not in ('연결된 진료 세션', '검사 기록명')}} for r in exams[:2]],
                   '참고 자료': [{'자료 ID': r['id'], **{k: v for k, v in r['properties'].items() if k != '관련 진료 세션'}} for r in knowledge],
                   '문진 전사': doc.get('transcript', '') if stage == 'b' else '',
                   '의사 메모': doc.get('notes', '') if stage in ('a','b') else '',
                   '검토한 SOAP': doc.get('soap', '') if stage == 'c' else '',
                   '한의사 처방/복용법/기간/주의': doc.get('prescription', ''),
                   '설명 범위': doc.get('explanation', '')}
        signature = json.dumps([session, survey, exams, knowledge], sort_keys=True, ensure_ascii=False)
        return context, signature

    def save_ai(self, sid, stage, result, signature):
        # A single write transaction makes re-check + persistence indivisible.
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            _, current = self.ai_snapshot(sid, stage)
            if current != signature:
                raise Problem('생성 중 입력 자료가 변경되었습니다. 새 자료로 다시 생성해 주세요.', 409)
            row = self.row(db, 'sessions', sid)
            doc = row['document']
            doc[stage] = result['content']
            if stage == 'b':
                if not result.get('soap', '').strip():
                    raise Problem('모델이 SOAP를 반환하지 않았습니다. 기존 문서는 유지됩니다.')
                doc['soap'] = result['soap']
                row['properties']['세션 상태'] = '한의사검토'
                row['properties']['EMR 복사 여부'] = False
                self.invalidate_crm(db, sid)
            doc[f'{stage}_meta'] = {k: result[k] for k in ('variant', 'inputTokens') if k in result} | {'generated_at': now()}
            self.write(db, 'sessions', sid, row['properties'], doc, row)
        return self.get('sessions', sid)

    def save_transcript(self, sid, revision, result, *, append=False, base_transcript=None):
        from clinic_ai.speech import validate_result
        validate_result(result)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.row(db, 'sessions', sid)
            if row['revision'] != revision and (base_transcript is None or row['document'].get('transcript','') != base_transcript):
                raise Problem('전사 중 세션이 변경되어 저장하지 않았습니다. 원본 녹음으로 다시 시도해 주세요.', 409)
            doc = row['document']
            if doc.get('transcript') or doc.get('transcript_source'):
                history = list(doc.get('transcript_history', []))
                history.append({'text': doc.get('transcript', ''),
                                'source': doc.get('transcript_source'), 'replaced_at': now()})
                doc['transcript_history'] = history
            combined = (doc.get('transcript', '').rstrip() + '\n\n' + result['text']).strip() if append else result['text']
            if len(combined)>100000:raise Problem('합친 전사문이 100,000자를 초과했습니다. 기존 전사는 유지됩니다.')
            doc['transcript'] = combined
            doc['transcript_source'] = dict(result) | {'transcribed_at': now()}
            p = row['properties']
            if p.get('세션 상태') in REVIEWED:
                p['세션 상태'] = '한의사검토'
            elif p.get('세션 상태') in ('접수', '설문확인', '검사확인'):
                p['세션 상태'] = '문진중'
            p['EMR 복사 여부'] = False
            self.invalidate_crm(db, sid)
            self.write(db, 'sessions', sid, p, doc, row)
        return self.get('sessions', sid)
