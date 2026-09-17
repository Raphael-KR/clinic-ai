"""Explicit model selection and the Shortcuts transport; no automatic fallback."""
import json
from contextlib import closing
import hashlib
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import tempfile
from uuid import uuid4
from clinic_ai.store import Problem

CONFIG = Path(__file__).resolve().parent.parent / 'data' / 'ai-shortcuts.json'
SHORTCUTS_DB = Path.home() / 'Library/Shortcuts/Shortcuts.sqlite'
MODELS = {
    'local': ('AFM 3 Core Advanced', '이 Mac', None),
    'cloud': ('AFM 3 Cloud', 'Apple Private Cloud Compute', 'AFM Cloud 호출'),
    'cloud-pro': ('AFM 3 Cloud Pro', 'Apple Private Cloud Compute', 'AFM Cloud Pro 호출'),
}
SCHEMA_VERSION = 'clinic-output-v3'


def configuration():
    try:
        value = json.loads(CONFIG.read_text())
        return {k: v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def catalog():
    config = configuration()
    return [dict(id=k, label=label, location=location, shortcut_name=name,
                 configured=k == 'local' or bool(config.get(k, {}).get('shortcut_id')))
            for k, (label, location, name) in MODELS.items()]


def resolve(model='local'):
    if not isinstance(model, str) or model not in MODELS:
        raise Problem('지원하지 않는 모델입니다.')
    label, location, name = MODELS[model]
    result = dict(id=model, label=label, location=location,
                  transport='swift' if model == 'local' else 'shortcuts', schema_version=SCHEMA_VERSION)
    if model != 'local':
        entry = configuration().get(model, {})
        identifier = entry.get('shortcut_id', '')
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', identifier) or not entry.get('workflow_sha256'):
            raise Problem(f'{name} 단축어 연결을 먼저 설정해 주세요. 다른 모델로 전환하지 않았습니다.', 503)
        result.update(shortcut_id=identifier, shortcut_name=name,
                      workflow_sha256=entry.get('workflow_sha256', ''), output_type='public.json' if model=='cloud' else 'public.plain-text', identity_source='verified_shortcut_configuration')
    return result


def workflow_digest(identifier):
    """Read the pinned workflow only; never write to Shortcuts' private database."""
    try:
        with closing(sqlite3.connect(SHORTCUTS_DB.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            row = db.execute('SELECT a.ZDATA FROM ZSHORTCUT s JOIN ZSHORTCUTACTIONS a ON a.Z_PK=s.ZACTIONS WHERE s.ZWORKFLOWID=? AND s.ZTOMBSTONED=0', (identifier,)).fetchone()
        if not row:
            raise ValueError('missing workflow')
        actions = plistlib.loads(row[0])
        return hashlib.sha256(plistlib.dumps(actions, sort_keys=True)).hexdigest()
    except (sqlite3.Error, OSError, ValueError, plistlib.InvalidFileException) as error:
        # Keep the operational cause visible without exposing database contents.
        detail = type(error).__name__
        if isinstance(error, sqlite3.Error):
            detail += ': ' + getattr(error, 'sqlite_errorname', 'SQLITE_ERROR')
        raise Problem(f'연결된 단축어 설정을 읽지 못했습니다 ({detail}). 앱의 접근 권한 또는 단축어 연결을 확인해 주세요.', 503) from error


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def parse_response(raw, request_id):
    if len(raw) > 1_000_000:
        raise Problem('모델 응답 크기가 허용 범위를 넘었습니다. 기존 기록은 유지됩니다.')
    raw = raw.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
    if fenced:
        raw = fenced[1]
    try:
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite')))
    except (ValueError, TypeError):
        raise Problem('단축어 응답이 유효한 JSON이 아닙니다. 기존 기록은 유지됩니다.')
    if not isinstance(value, dict) or value.get('request_id') != request_id:
        raise Problem('단축어가 이번 입력에 대응하는 결과를 반환하지 않았습니다. 고정 프롬프트와 출력 설정을 확인해 주세요.')
    return value


def prepare(request):
    """Freeze the exact CLI text and its response ID with the queued request."""
    nonce = uuid4().hex
    fields = {'request_id': nonce, 'content': request.get('contentDescription') or '검토용 요약'}
    if request['stage'] == 'b':
        fields.update(subjective='환자 진술', objective='검사 사실', assessment='평가', plan='계획')
    if request['stage'] == 'c':
        fields.update(guide='환자 안내', rx_guide='처방 설명 또는 해당 없음', message='짧은 안내 메시지')
    contract = ('응답은 프로그램이 JSON으로 파싱한다. 아래 JSON 객체의 모든 키와 문자열 자료형을 반드시 지킨다. '
                'request_id는 환자 정보가 아닌 실행 식별자이므로 정확히 복사한다. '
                'content와 나머지 필드의 값 안에만 임상 초안을 작성한다. 일반 문서나 머리말을 JSON 밖에 쓰면 실패다. '
                '전체 JSON을 하나의 json 코드 펜스로 감싼다. 각 문자열 값은 한 줄로 작성한다. 값 안에 줄바꿈을 넣지 말고 항목은 세미콜론으로 구분한다. '
                '확인할 수 없는 항목도 문자열로 확인 필요라고 쓴다.\n' + json.dumps(fields, ensure_ascii=False))
    prompt = (contract + '\n\n<작성 지침>\n' + request['instructions'] + '\n</작성 지침>\n'
              + '<입력 자료>\n' + request['prompt'] + '\n</입력 자료>\n\n' + contract)
    if len(prompt.encode()) > 750_000:
        raise Problem('입력이 너무 깁니다. 원문을 보존한 채 생성할 자료 범위를 줄여 주세요.')
    return dict(request, shortcut_prompt=prompt, request_id=nonce, schema_version=SCHEMA_VERSION)


def shortcut(request, selection, timeout=180):
    if workflow_digest(selection['shortcut_id']) != selection.get('workflow_sha256'):
        raise Problem('단축어 동작이 연결 당시와 달라졌습니다. 모델·입출력 설정을 확인하고 다시 연결해 주세요.', 409)
    prepared = request if request.get('shortcut_prompt') else prepare(request)
    prompt, nonce = prepared['shortcut_prompt'], prepared['request_id']
    with tempfile.TemporaryDirectory(prefix='clinic-ai-shortcut-') as folder:
        root = Path(folder); root.chmod(0o700)
        source, target = root / 'input.txt', root / 'output.txt'
        for path, text in ((source, prompt), (target, '')):
            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                stream.write(text)
        try:
            proc = subprocess.run(['/usr/bin/shortcuts', 'run', selection['shortcut_id'],
                                   '--input-path', str(source), '--output-path', str(target),
                                   '--output-type', selection.get('output_type', 'public.plain-text')],
                                  capture_output=True, timeout=timeout)
            if proc.returncode:
                diagnostic = proc.stderr or b''
                if isinstance(diagnostic, bytes):
                    diagnostic = diagnostic.decode('utf-8', errors='replace')
                if '사용 한도에 도달' in diagnostic or 'usage limit' in diagnostic.lower():
                    raise Problem('선택한 Apple 모델의 사용 한도에 도달했습니다. 나중에 다시 시도해 주세요. 다른 모델로 자동 전환하지 않았으며 기존 기록은 유지됩니다.', 429)
                raise Problem('선택한 단축어 실행이 실패했습니다. 권한·연결·모델 사용 가능 상태를 확인해 주세요. 기존 기록은 유지됩니다.', 503)
            if target.stat().st_size > 1_000_000:
                raise Problem('모델 응답 크기가 허용 범위를 넘었습니다.')
            return parse_response(target.read_text(encoding='utf-8-sig'), nonce)
        except subprocess.TimeoutExpired:
            raise Problem('단축어 응답 시간이 초과되었습니다. 원격 처리 종료 여부는 확인되지 않았으며 자동 재요청하지 않습니다. 기존 기록은 유지됩니다.', 504)
        except (OSError, UnicodeError):
            raise Problem('단축어 결과를 읽지 못했습니다. 기존 기록은 유지됩니다.', 503)


def normalize(result, stage):
    if not isinstance(result, dict):
        raise Problem('모델 결과 형식이 올바르지 않습니다.')
    def field(key):
        value = result.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 100_000:
            raise Problem(f'모델 결과의 {key} 항목이 비었거나 형식이 올바르지 않습니다. 기존 기록은 유지됩니다.')
        return value.strip()
    output = {'content': field('content')}
    if stage == 'b':
        keys = ('subjective', 'objective', 'assessment', 'plan')
        if any(k in result for k in keys):
            output['soap'] = '\n\n'.join(f'{label}: {field(key)}' for label, key in zip('SOAP', keys))
        else:
            soap = field('soap')
            headings = list(re.finditer(r'(?m)^([SOAP]):[ \t]*', soap))
            if [m[1] for m in headings] != list('SOAP') or any(
                    not soap[m.end():headings[i+1].start() if i < 3 else None].strip()
                    for i, m in enumerate(headings)):
                raise Problem('SOAP의 S/O/A/P가 완전하지 않습니다. 기존 기록은 유지됩니다.')
            output['soap'] = soap
    if stage == 'c':
        output.update({key: field(key) for key in ('guide', 'rx_guide', 'message')})
    for key in ('variant', 'inputTokens'):
        if key in result:
            output[key] = result[key]
    return output
