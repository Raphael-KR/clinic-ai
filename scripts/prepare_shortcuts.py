"""Prepare reviewable CLI-input workflows; import through Shortcuts separately."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import unicodedata
from uuid import uuid4
from clinic_ai import ai_models


def workflow(model):
    text_id = str(uuid4()).upper()
    model_id = str(uuid4()).upper()
    token = lambda value: {'WFSerializationType': 'WFTextTokenString', 'Value': {
        'string': '\ufffc', 'attachmentsByRange': {'{0, 1}': value}}}
    return {
        'WFWorkflowClientVersion': '9999', 'WFWorkflowMinimumClientVersion': 900,
        'WFWorkflowIcon': {'WFWorkflowIconStartColor': 2071128575, 'WFWorkflowIconGlyphNumber': 62213},
        'WFWorkflowTypes': [], 'WFWorkflowImportQuestions': [],
        'WFWorkflowInputContentItemClasses': ['WFGenericFileContentItem', 'WFStringContentItem'],
        'WFWorkflowOutputContentItemClasses': ['WFStringContentItem'],
        'WFWorkflowHasShortcutInputVariables': True, 'WFWorkflowHasOutputFallback': False,
        'WFWorkflowActions': [
            {'WFWorkflowActionIdentifier': 'is.workflow.actions.gettext', 'WFWorkflowActionParameters': {
                'UUID': text_id, 'WFTextActionText': token({'Type': 'ExtensionInput'})}},
            {'WFWorkflowActionIdentifier': 'is.workflow.actions.askllm', 'WFWorkflowActionParameters': {
                'UUID': model_id, 'WFLLMModel': model, 'WFGenerativeResultType': 'Dictionary' if model == 'Apple Intelligence' else 'Text',
                'WFLLMPrompt': token({'Type': 'ActionOutput', 'OutputUUID': text_id, 'OutputName': 'Text'})}},
            {'WFWorkflowActionIdentifier': 'is.workflow.actions.output', 'WFWorkflowActionParameters': {
                'UUID': str(uuid4()).upper(),
                'WFOutput': token({'Type': 'ActionOutput', 'OutputUUID': model_id, 'OutputName': '응답'})}},
        ],
    }


def connect():
    def token_value(value):
        result = value.get('Value') if isinstance(value, dict) else None
        return result if isinstance(result, dict) else {}
    config = {}
    with closing(sqlite3.connect(ai_models.SHORTCUTS_DB.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT s.ZNAME,s.ZWORKFLOWID,a.ZDATA FROM ZSHORTCUT s JOIN ZSHORTCUTACTIONS a ON a.Z_PK=s.ZACTIONS WHERE s.ZTOMBSTONED=0').fetchall()
    for key, model in [('cloud', 'Apple Intelligence'), ('cloud-pro', 'Apple Intelligence Pro')]:
        name = ai_models.MODELS[key][2]
        matches = [r for r in rows if unicodedata.normalize('NFC', r[0]) == name]
        if len(matches) != 1:
            raise SystemExit(f'{name}: 단축어 앱에서 이름이 일치하는 단축어 하나를 가져와 주세요.')
        _, identifier, raw = matches[0]
        actions = plistlib.loads(raw)
        expected = workflow(model)['WFWorkflowActions']
        if not isinstance(actions, list) or len(actions) != 3 or not all(isinstance(a, dict) for a in actions) or [a.get('WFWorkflowActionIdentifier') for a in actions] != [a['WFWorkflowActionIdentifier'] for a in expected]:
            raise SystemExit(f'{name}: 입력 텍스트 → 모델 사용 → 결과 출력의 세 동작을 확인해 주세요.')
        parameters = [a.get('WFWorkflowActionParameters') for a in actions]
        if not all(isinstance(p, dict) and p.get('UUID') for p in parameters):
            raise SystemExit(f'{name}: 동작 매개변수를 확인해 주세요.')
        first, second, third = parameters
        source = token_value(first.get('WFTextActionText'))
        prompt = token_value(second.get('WFLLMPrompt'))
        output = token_value(third.get('WFOutput'))
        if (second.get('WFLLMModel') != model or second.get('WFGenerativeResultType') != ('Dictionary' if key == 'cloud' else 'Text') or source.get('string') != '\ufffc' or prompt.get('string') != '\ufffc'
            or source.get('attachmentsByRange', {}).get('{0, 1}', {}).get('Type') != 'ExtensionInput'
            or prompt.get('attachmentsByRange', {}).get('{0, 1}', {}).get('OutputUUID') != first.get('UUID')
            or output.get('string') != '\ufffc'
            or output.get('attachmentsByRange', {}).get('{0, 1}', {}).get('OutputUUID') != second.get('UUID')):
            raise SystemExit(f'{name}: 모델 또는 입력 토큰이 예상과 다릅니다.')
        config[key] = dict(shortcut_id=identifier, workflow_sha256=ai_models.workflow_digest(identifier), output_type='public.json' if key=='cloud' else 'public.plain-text')
    target = ai_models.CONFIG
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    with open(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
        json.dump(config, stream, indent=2)
    temp.replace(target)
    print('연결 완료: AFM Cloud 호출 / AFM Cloud Pro 호출 (UUID·동작 해시 고정)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sign', action='store_true', help='Send workflow definitions, without patient data, to Apple for signing')
    parser.add_argument('--connect', action='store_true', help='Verify imported workflows and save this Mac’s local binding')
    args = parser.parse_args()
    if args.connect:
        connect()
        raise SystemExit(0)
    root = Path('.build/shortcuts'); root.mkdir(parents=True, exist_ok=True)
    for name, model in [('AFM Cloud 호출', 'Apple Intelligence'), ('AFM Cloud Pro 호출', 'Apple Intelligence Pro')]:
        source = root / (name + '-unsigned.shortcut')
        source.write_bytes(plistlib.dumps(workflow(model), fmt=plistlib.FMT_XML))
        print(source)
        if args.sign:
            target = root / (name + '.shortcut')
            subprocess.run(['/usr/bin/shortcuts', 'sign', '--mode', 'anyone', '--input', str(source), '--output', str(target)], check=True, timeout=60)
            print(target)
