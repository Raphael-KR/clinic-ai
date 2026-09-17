"""On-device file transcription; no remote fallback or model installation."""
import json
import math
from pathlib import Path
import subprocess
import threading
from clinic_ai.store import Problem

BINARY = Path(__file__).resolve().parent.parent / '.build' / 'speech-bridge'
MAX_BYTES = 256 * 1024 * 1024
EXTENSIONS = {'.wav', '.m4a', '.mp3', '.aiff', '.aif', '.caf'}
LOCK = threading.Lock()


def bridge(request, timeout=900):
    if not BINARY.exists():
        raise Problem('음성 전사 연결 프로그램을 먼저 빌드해 주세요.', 503)
    try:
        result = subprocess.run([str(BINARY)], input=json.dumps(request), capture_output=True,
                                text=True, timeout=timeout)
        if result.returncode:
            raise Problem('음성 전사 프로그램이 종료되었습니다. 기존 전사는 유지됩니다.', 503)
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise Problem('전사 시간이 15분을 초과했습니다. 녹음을 나누어 다시 시도해 주세요.', 504)
    except (ValueError, OSError):
        raise Problem('음성 전사 응답을 읽지 못했습니다.', 503)
    if 'error' in data:
        raise Problem(data.get('message', '음성 전사 실패'))
    return data


def status():
    return bridge({'action': 'status'}, timeout=20)


def validate_result(result):
    if not isinstance(result, dict) or not isinstance(result.get('text'), str) or not result['text'].strip():
        raise Problem('인식된 발화가 없습니다. 기존 전사는 유지됩니다.')
    if len(result['text']) > 100000:
        raise Problem('전사문이 100,000자를 초과했습니다. 녹음을 나누어 다시 전사해 주세요.')
    segments = result.get('segments')
    if not isinstance(segments, list) or not segments:
        raise Problem('전사의 시간 정보를 확인할 수 없습니다.')
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('text'), str):
            raise Problem('전사 구간 형식이 올바르지 않습니다.')
        start, end = segment.get('start'), segment.get('end')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or not 0 <= start <= end:
            raise Problem('전사 구간의 시간 정보가 올바르지 않습니다.')
        confidence = segment.get('confidence')
        if confidence is not None and (type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise Problem('전사 신뢰도 값이 올바르지 않습니다.')
    return result


def transcribe(store, sid, revision, path, digest, size):
    if not LOCK.acquire(blocking=False):
        raise Problem('다른 녹음을 전사 중입니다. 완료 후 다시 시도해 주세요.', 409)
    try:
        row = store.get('sessions', sid)
        if row['revision'] != revision:
            raise Problem('세션이 변경되었습니다. 새로고침한 뒤 전사해 주세요.', 409)
        result = validate_result(bridge({'action': 'transcribe', 'path': str(path)}))
        result['sha256'] = digest
        result['file_size'] = size
        return store.save_transcript(sid, revision, result)
    finally:
        LOCK.release()
