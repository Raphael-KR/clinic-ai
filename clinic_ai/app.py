"""Local clinic workspace: server-rendered patient form + staff web interface."""
import argparse
import hashlib
import hmac
import re
from uuid import uuid4
import tempfile
import time
from datetime import datetime
import json
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from clinic_ai.survey import form_page, document
from clinic_ai.store import Store, SCHEMA, DOCS, TABLES, DEFAULT_DB, Problem
from clinic_ai import local_ai, speech, ai_models
from clinic_ai.live_speech import LiveManager
from clinic_ai.workspace import Workspace

STATIC = Path(__file__).resolve().parent / 'static'


class ClinicServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8766, db_path=DEFAULT_DB):
        self.store = Store(db_path, mvp=True)
        self.live = LiveManager(self.store)
        self.workspace = Workspace(self.store)
        self.db_path = Path(db_path)
        self.secret = secrets.token_bytes(32)
        super().__init__(('127.0.0.1', port), Handler)

    def token(self):
        uid = uuid4().hex
        return uid + '.' + hmac.new(self.secret, uid.encode(), hashlib.sha256).hexdigest()

    def verify_token(self, token):
        if not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{64}', token):
            return None
        uid, signature = token.split('.')
        expected = hmac.new(self.secret, uid.encode(), hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(signature, expected) else None

    def server_close(self):
        self.live.close()
        self.workspace.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Request paths and patient answers are never logged.

    def allowed_request(self):
        port = self.server.server_port
        hosts = (f'127.0.0.1:{port}', f'localhost:{port}')
        if self.headers.get('Host') not in hosts:
            return False
        origin = self.headers.get('Origin')
        if origin is not None and origin != 'http://' + self.headers.get('Host'):
            return False
        return self.headers.get('Sec-Fetch-Site') != 'cross-site'

    def reply(self, status, body=b'', content_type='text/html; charset=utf-8', location=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        if location:
            self.send_header('Location', location)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json_reply(self, value, status=200):
        self.reply(status, json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def read_body(self):
        if self.headers.get('Transfer-Encoding'):
            raise Problem('지원하지 않는 요청 형식입니다.', 400)
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise Problem('요청 길이가 올바르지 않습니다.', 400)
        if not 0 < n <= 1_000_000:
            raise Problem('요청 크기가 허용 범위를 넘었습니다.', 413)
        raw = self.rfile.read(n)
        if len(raw) != n:
            raise Problem('요청이 완전하지 않습니다.', 400)
        return raw

    def do_GET(self):
        if not self.allowed_request():
            return self.reply(403)
        url = urlsplit(self.path)
        path = url.path
        try:
            if path == '/':
                return self.reply(200, (STATIC / 'index.html').read_bytes())
            if path in ('/app.js', '/app.css', '/mic.js', '/pcm-worklet.js', '/survey.js'):
                return self.reply(200, (STATIC / path[1:]).read_bytes(), 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
            if path == '/survey':
                return self.reply(200, form_page(self.server.token(), mvp=True))
            if path == '/complete':
                return self.reply(200, document('<h1>제출되었습니다</h1><p>설문이 저장되었습니다. 감사합니다.</p><a class="link" href="/survey">새 설문 작성</a>'))
            if path == '/health':
                return self.json_reply({'status': 'ok', 'counts': self.server.store.counts()})
            if path == '/api/bootstrap':
                return self.json_reply({'mvp': True, 'schema': SCHEMA, 'docs': DOCS, 'csrf': self.server.token(), 'counts': self.server.store.counts(), 'models': ai_models.catalog()})
            if path == '/api/models':
                return self.json_reply(ai_models.catalog())
            if path == '/api/speech':
                return self.json_reply(speech.status())
            if path == '/api/model':
                return self.json_reply(local_ai.status())
            parts = path.strip('/').split('/')
            if parts == ['api','workspace']:
                return self.json_reply(self.server.workspace.overview())
            if parts == ['api','patients']:
                return self.json_reply(self.server.workspace.patients())
            if len(parts)==3 and parts[:2]==['api','workspace']:
                return self.json_reply(self.server.workspace.encounter(parts[2]))
            if len(parts)==5 and parts[:2]==['api','workspace'] and parts[3]=='versions':
                return self.json_reply(self.server.workspace.version(parts[2],parts[4]))
            if len(parts)==3 and parts[:2]==['api','live']:
                return self.json_reply(self.server.live.get(parts[2]).snapshot())
            if len(parts) in (2, 3) and parts[0] == 'api' and parts[1] in TABLES:
                if len(parts) == 2:
                    return self.json_reply(self.server.store.list(parts[1], parse_qs(url.query).get('q', [''])[0]))
                return self.json_reply(self.server.store.get(parts[1], parts[2]))
            return self.reply(404)
        except Problem as error:
            self.json_reply({'error': str(error)}, error.status)
        except sqlite3.Error:
            self.json_reply({'error': 'DB 조회에 실패했습니다.'}, 503)

    def mutate(self, method):
        if not self.allowed_request():
            return self.reply(403)
        try:
            if method == 'POST' and self.path == '/submit':
                if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    return self.reply(415)
                parsed = parse_qs(self.read_body().decode(), keep_blank_values=True, max_num_fields=20, errors='strict')
                if any(len(v) != 1 for v in parsed.values()):
                    raise Problem('중복 입력 항목이 있습니다.', 400)
                values = {k: v[0] for k, v in parsed.items()}
                token = values.pop('token', '')
                uid = self.server.verify_token(token)
                if not uid:
                    return self.reply(403, document('<h1>입력 화면이 만료되었습니다</h1><a class="link" href="/survey">설문 다시 열기</a>'))
                try:
                    self.server.store.submit_survey(values, uid)
                except Problem as error:
                    return self.reply(error.status, form_page(token, values, [str(error)], mvp=True))
                return self.reply(303, location='/complete')
            if not self.server.verify_token(self.headers.get('X-CSRF-Token', '')):
                return self.json_reply({'error': '화면이 만료되었습니다. 새로고침해 주세요.'}, 403)
            parts = urlsplit(self.path).path.strip('/').split('/')
            if len(parts)==4 and parts[:2]==['api','live'] and parts[3]=='chunk' and method=='POST':
                if self.headers.get_content_type()!='application/octet-stream':raise Problem('PCM 업로드 형식 오류',415)
                return self.json_reply(self.server.live.get(parts[2]).chunk(int(self.headers.get('X-Sequence','-1')),self.read_body()))
            if len(parts) == 3 and parts[:2] == ['api', 'transcribe'] and method == 'POST':
                return self.upload_audio(parts[2])
            if self.headers.get_content_type() != 'application/json':
                return self.reply(415)
            payload = json.loads(self.read_body())
            if not isinstance(payload, dict):
                raise Problem('객체 형식이 필요합니다.', 400)
            parts = urlsplit(self.path).path.strip('/').split('/')
            if len(parts)>=3 and parts[:2]==['api','workspace']:
                workspace=self.server.workspace;sid=parts[2]
                if len(parts)==3 and method=='PATCH':return self.json_reply(workspace.patch(sid,payload))
                if len(parts)==4 and method=='POST':
                    if parts[3]=='visit':return self.json_reply(workspace.new_visit(sid,payload),201)
                    if parts[3]=='assign':return self.json_reply(workspace.assign(sid,payload))
                    if parts[3]=='guidance':return self.json_reply(workspace.guidance(sid,payload),201)
                if len(parts)==5 and parts[3]=='jobs' and method=='POST':
                    return self.json_reply(workspace.enqueue(sid,parts[4],payload),202)
                return self.reply(405)
            if len(parts)==4 and parts[:2]==['api','live'] and method=='POST':
                if parts[2]=='start':return self.json_reply(self.server.live.start(parts[3],payload.get('revision')))
                if parts[3] in ('stop','cancel'):return self.json_reply(self.server.live.finish(parts[2],parts[3]=='stop'))
            if len(parts) == 4 and parts[:2] == ['api', 'generate'] and method == 'POST':
                # No input or output is sent to a remote provider.
                return self.json_reply(local_ai.generate(self.server.store, parts[2], parts[3]))
            if len(parts) not in (2, 3) or parts[0] != 'api' or parts[1] not in TABLES:
                return self.reply(404)
            table = parts[1]
            if method == 'POST' and len(parts) == 2:
                return self.json_reply(self.server.store.save(table, payload), 201)
            if method == 'PATCH' and len(parts) == 3:
                return self.json_reply(self.server.store.save(table, payload, parts[2]))
            if method == 'DELETE' and len(parts) == 3:
                self.server.store.delete(table, parts[2], payload.get('revision'))
                return self.json_reply({'deleted': True})
            return self.reply(405)
        except Problem as error:
            self.json_reply({'error': str(error)}, error.status)
        except (ValueError, UnicodeError, TimeoutError):
            self.json_reply({'error': '요청 형식이 올바르지 않습니다.'}, 400)
        except sqlite3.Error:
            self.json_reply({'error': 'DB 저장에 실패했습니다. 다시 시도해 주세요.'}, 503)

    def upload_audio(self, sid):
        if self.headers.get_content_type() != 'application/octet-stream' or self.headers.get('Transfer-Encoding'):
            raise Problem('지원하지 않는 오디오 업로드 형식입니다.', 415)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            revision = int(self.headers.get('X-Revision', ''))
        except ValueError:
            raise Problem('파일 크기 또는 세션 버전이 올바르지 않습니다.', 400)
        extension = self.headers.get('X-Audio-Extension', '').lower()
        if extension not in speech.EXTENSIONS:
            raise Problem('WAV, M4A, MP3, AIFF, CAF 파일을 선택해 주세요.', 415)
        if not 0 < size <= speech.MAX_BYTES:
            raise Problem('256MB 이하의 녹음 파일을 선택해 주세요.', 413)
        if self.server.store.get('sessions', sid)['revision'] != revision:
            raise Problem('세션이 변경되었습니다. 새로고침해 주세요.', 409)
        if speech.LOCK.locked():
            raise Problem('다른 녹음을 전사 중입니다.', 409)
        with tempfile.TemporaryDirectory(prefix='clinic-speech-') as directory:
            path = Path(directory) / ('audio' + extension)
            path.touch(mode=0o600)
            digest = hashlib.sha256()
            remaining = size
            deadline = time.monotonic() + 120
            with path.open('wb') as audio:
                while remaining:
                    if time.monotonic() > deadline:
                        raise Problem('오디오 업로드 시간이 초과되었습니다.', 408)
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise Problem('오디오 업로드가 중단되었습니다.', 400)
                    audio.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            record = speech.transcribe(self.server.store, sid, revision, path, digest.hexdigest(), size)
        return self.json_reply(record)

    def do_POST(self):
        self.mutate('POST')

    def do_PATCH(self):
        self.mutate('PATCH')

    def do_DELETE(self):
        self.mutate('DELETE')


def reset(path):
    store = Store(path)
    counts = store.counts()
    backup = None
    with store.connection() as db:
        auxiliary = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ux_patients','ux_visits','ux_links','ux_versions','ux_jobs')")]
        has_auxiliary = any(db.execute(f'SELECT 1 FROM {t} LIMIT 1').fetchone() for t in auxiliary)
    if any(counts.values()) or has_auxiliary:
        backup = store.path.with_name(store.path.stem + '-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
        with store.connection() as source:
            dest = sqlite3.connect(backup)
            try:
                source.backup(dest)
            finally:
                dest.close()
        backup.chmod(0o600)
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for table in (*TABLES, *auxiliary):
            db.execute(f'DELETE FROM {table}')
    return {'before': counts, 'after': store.counts(), 'backup': str(backup) if backup else None}


def main():
    parser = argparse.ArgumentParser(description='로컬 한의원 진료 지원')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--counts', action='store_true')
    parser.add_argument('--reset', action='store_true', help='기존 레코드 백업 후 7개 DB 비우기')
    args = parser.parse_args()
    if args.reset:
        print(json.dumps(reset(args.db), ensure_ascii=False)); return
    if args.counts:
        print(json.dumps(Store(args.db).counts(), ensure_ascii=False)); return
    server = ClinicServer(args.port, args.db)
    print(f'진료 지원: http://127.0.0.1:{server.server_port}/', flush=True)
    print(f'초진설문: http://127.0.0.1:{server.server_port}/survey', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
