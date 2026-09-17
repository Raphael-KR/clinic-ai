"""Initial-visit form rendering and validation shared by the clinic workspace."""
from html import escape
import re

# Field order, labels and required flags match the fetched Notion Form.
FIELDS = (
    ('name', '이름', 'text', True),
    ('phone', '휴대폰 번호', 'tel', True),
    ('birth', '주민등록번호 앞 6자리', 'text', True),
    ('identity', '주민등록번호 뒤 7자리', 'password', True),
    ('complaint', '주소증', 'text', True),
    ('onset', '발병 시점 또는 경과', 'textarea', False),
    ('details', '증상 상세', 'textarea', True),
    ('medication', '복용약', 'textarea', True),
    ('privacy', '개인정보 수집 동의', 'checkbox', True),
    ('kakao', '카카오 안내 동의', 'checkbox', False),
)
STYLE = '''
:root {font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", sans-serif; color:#213934; background:#f3f5f2; line-height:1.6;}
* {box-sizing:border-box} body {margin:0; padding:40px 20px 70px}
main {max-width:640px; margin:auto; background:white; padding:40px; border:1px solid #e0e7e1; border-radius:20px; box-shadow:0 12px 40px #223c3408}
.eyebrow {color:#58786b; font-size:13px; letter-spacing:.1em} h1 {font-size:32px; margin:6px 0 10px; letter-spacing:-.04em}
p {color:#61736b} .field {margin-top:25px} label {display:block; font-weight:600; margin-bottom:8px}
small {color:#6b7e74; font-weight:400; margin-left:8px} input:not([type=checkbox]),textarea {width:100%; border:1px solid #bbc9bf; border-radius:9px; padding:12px; font:inherit; background:#fcfdfb; color:#213934}
textarea {min-height:100px; resize:vertical} input:focus,textarea:focus,button:focus,a:focus {outline:3px solid #9acbb6; outline-offset:3px}
.check label {display:flex; align-items:center; gap:10px} input[type=checkbox] {width:20px; height:20px; accent-color:#28664f}
button,.link {display:block; width:100%; background:#28664f; color:white; text-align:center; padding:14px; border:0; border-radius:10px; font:inherit; font-weight:600; margin-top:32px; text-decoration:none; cursor:pointer}
.error {padding:16px; border:1px solid #cf9287; background:#fff4f1; border-radius:10px; color:#853526} .foot {font-size:13px; margin-top:24px}
select {width:100%;padding:12px;font:inherit;border:1px solid #bbc9bf;border-radius:9px;background:white}.field-error{color:#923f36;font-size:13px;margin:6px 0}.field-error:empty{display:none}
@media(max-width:520px) {body {padding:16px 12px 40px} main {padding:26px 20px} h1 {font-size:28px}}
'''


def document(body):
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>초진설문</title><script src="/survey.js" defer></script><style>' + STYLE + '</style></head><body><main>'
            + body + '</main></body></html>').encode()


def form_page(token, values=None, errors=(), *, mvp=False):
    values = values or {}
    body = '<div class="eyebrow">진료 준비</div><h1>초진설문</h1><p>진료 준비를 위한 초진 정보를 입력해 주세요.</p><p>필수 표시가 있는 항목을 작성해 주세요.</p>'
    if errors:
        body += '<div class="error" role="alert">' + '<br>'.join(escape(e) for e in errors) + '</div>'
    body += '<form method="post" action="/submit" autocomplete="off"><input type="hidden" name="token" value="' + escape(token, quote=True) + '">'
    for key, label, kind, required in FIELDS:
        if mvp and key == 'privacy':
            required = False
        display_label = {'주소증':'오늘 가장 불편한 증상', '발병 시점 또는 경과':'언제부터, 어떻게 달라졌나요?'}.get(label,label)
        field_errors = [line for err in errors for line in err.split('\n') if label in line]
        mark = '<small>' + ('필수' if required else '선택') + '</small>'
        req = ' required' if required else ''
        # Do not reflect the identity suffix in error responses.
        value = '' if key == 'identity' else escape(values.get(key, ''), quote=True)
        body += '<div class="field' + (' check' if kind == 'checkbox' else '') + '">'
        if kind == 'checkbox':
            checked = ' checked' if values.get(key) == 'on' else ''
            body += f'<label for="{key}"><input id="{key}" name="{key}" type="checkbox"{req}{checked}>{display_label}{mark}</label>'
        else:
            body += f'<label for="{key}">{display_label}{mark}</label>'
            if key == 'medication':
                mode=values.get('medication_mode') or ('none' if values.get(key)=='없음' else 'yes' if values.get(key) else '')
                body += '<select id="medication-mode" name="medication_mode" aria-label="복용약 유무" required><option value="">선택해 주세요</option><option value="none"'+(' selected' if mode=='none' else '')+'>없음</option><option value="yes"'+(' selected' if mode=='yes' else '')+'>있음</option></select>'
                body += f'<textarea id="medication" name="medication" aria-label="복용약 상세" maxlength="5000" placeholder="약 이름과 복용 방법">{value}</textarea>'
            elif kind == 'textarea':
                body += f'<textarea id="{key}" name="{key}" maxlength="5000"{req}>{value}</textarea>'
            else:
                extra = ' maxlength="200"'
                if key in ('birth', 'identity'):
                    n = 6 if key == 'birth' else 7
                    extra = f' inputmode="numeric" pattern="[0-9]{{{n}}}" minlength="{n}" maxlength="{n}"'
                body += f'<input id="{key}" name="{key}" type="{kind}" value="{value}"{extra}{req}>'
        body += f'<p class="field-error" id="error-{key}" role="alert">' + '<br>'.join(escape(err) for err in field_errors) + '</p></div>'
    body += '<button type="submit">설문 제출</button></form><p class="foot">작성한 응답은 이 컴퓨터에 저장됩니다.</p>'
    return document(body)


def validate(values, *, mvp=False):
    values=dict(values)
    if values.get('medication_mode')=='none': values['medication']='없음'
    if values.get('medication_mode')=='yes' and values.get('medication')=='없음': values['medication']=''
    errors, answers = [], {}
    for key, label, kind, required in FIELDS:
        if mvp and key == 'privacy':
            required = False
        value = values.get(key, '').strip()
        if kind == 'checkbox':
            if value not in ('', 'on'):
                errors.append(f'{label} 항목을 확인해 주세요.')
            if required and value != 'on':
                errors.append(f'{label} 항목을 확인해 주세요.')
            answers[label] = value == 'on'
        else:
            if required and not value:
                errors.append(f'{label} 항목을 입력해 주세요.')
            limit = 5000 if kind == 'textarea' else 200
            if len(value) > limit:
                errors.append(f'{label} 항목은 {limit}자 이내로 입력해 주세요.')
            if key in ('birth', 'identity') and value:
                n = 6 if key == 'birth' else 7
                if not re.fullmatch(r'[0-9]{' + str(n) + '}', value):
                    errors.append(f'{label} 항목은 숫자 {n}자리로 입력해 주세요.')
            answers[label] = value
    return answers, errors
