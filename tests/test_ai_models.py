import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from clinic_ai import ai_models, local_ai
from clinic_ai.store import Problem

SELECTION = dict(id='cloud', shortcut_id='12345678-1234-1234-1234-123456789abc', workflow_sha256='hash', label='AFM 3 Cloud', location='Apple Private Cloud Compute', transport='shortcuts')

class ModelTests(unittest.TestCase):
    def test_strict_json_and_nonce(self):
        for raw in ('', '2', '[]', '{"content":"old"}', '{"request_id":"n","content":NaN}', '{"request_id":"n","content":"a","content":"b"}', 'prefix {"request_id":"n"}'):
            with self.subTest(raw=raw), self.assertRaises(Problem):ai_models.parse_response(raw,'n')
        self.assertEqual(ai_models.parse_response('```json\n{"request_id":"n","content":"new"}\n```','n')['content'],'new')
    def test_stage_validation(self):
        for result,stage in [({'content':42},'a'),({'content':'x'},'b'),({'content':'x','subjective':'s','objective':'o'},'b'),({'content':'x','soap':'S: x\nO: \nA: x\nP: x'},'b'),({'content':'x','guide':'g','rx_guide':'r'},'c')]:
            with self.subTest(result=result), self.assertRaises(Problem):ai_models.normalize(result,stage)
        result=ai_models.normalize(dict(content='x',subjective='없음',objective='미검사',assessment='미확정',plan='3일 뒤'),'b')
        self.assertEqual(result['soap'],'S: 없음\n\nO: 미검사\n\nA: 미확정\n\nP: 3일 뒤')
    def test_model_whitelist_configuration_and_local_default(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ai_models,'CONFIG',Path(folder)/'config.json'):
            ai_models.CONFIG.write_text('{"cloud":null,"cloud-pro":42}')
            self.assertEqual(ai_models.resolve()['id'],'local')
            self.assertFalse(ai_models.catalog()[1]['configured'])
            for m in ('cloud','cloud-pro','unknown',[],None):
                with self.subTest(model=m),self.assertRaises(Problem):ai_models.resolve(m)
    def test_temporary_files_permissions_cli_args_and_cleanup(self):
        paths=[]
        def run(args,**kwargs):
            self.assertEqual(args[:3],['/usr/bin/shortcuts','run',SELECTION['shortcut_id']])
            source=Path(args[args.index('--input-path')+1]);target=Path(args[args.index('--output-path')+1]);paths.extend([source,target])
            for file in paths:self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode),0o700)
            text=source.read_text();nonce=json.loads(text[text.rindex('\n')+1:])['request_id']
            self.assertIn('synthetic-marker',text)
            target.write_text(json.dumps({'request_id':nonce,'content':'new'}))
            return subprocess.CompletedProcess(args,0)
        with patch.object(ai_models,'workflow_digest',return_value='hash'),patch.object(ai_models.subprocess,'run',side_effect=run):
            self.assertEqual(ai_models.shortcut(local_ai.make_request({'case':'synthetic-marker'},'a'),SELECTION)['content'],'new')
        self.assertTrue(paths);self.assertTrue(all(not p.exists() for p in paths))
    def test_failure_timeout_cleanup_and_no_fallback(self):
        for failure in ('exit','timeout','empty','malformed'):
            paths=[]
            def run(args,**kwargs):
                source=Path(args[args.index('--input-path')+1]);target=Path(args[args.index('--output-path')+1]);paths.extend([source,target])
                if failure=='timeout':raise subprocess.TimeoutExpired(args,180)
                if failure=='malformed':target.write_text('2')
                return subprocess.CompletedProcess(args,1 if failure=='exit' else 0)
            with self.subTest(failure=failure),patch.object(ai_models,'workflow_digest',return_value='hash'),patch.object(ai_models.subprocess,'run',side_effect=run),patch.object(local_ai,'bridge') as local:
                with self.assertRaises(Problem):local_ai.draft({},'a',selection=SELECTION)
                local.assert_not_called()
            self.assertTrue(all(not p.exists() for p in paths))
    def test_workflow_drift_prevents_wrong_model(self):
        with patch.object(ai_models,'workflow_digest',return_value='changed'),patch.object(ai_models.subprocess,'run') as run:
            with self.assertRaises(Problem):ai_models.shortcut(local_ai.make_request({},'a'),SELECTION)
            run.assert_not_called()
    def test_cloud_metadata_is_configuration_not_self_report(self):
        with patch.object(ai_models,'shortcut',return_value={'content':'x','variant':'fabricated','inputTokens':42}):
            r=local_ai.draft({},'a',selection=SELECTION)
        self.assertNotIn('variant',r);self.assertNotIn('inputTokens',r)
        self.assertEqual(r['generation']['id'],'cloud')
        self.assertEqual(r['generation']['prompt_version'],local_ai.PROMPT_VERSION)
    def test_input_limit_never_silently_truncates(self):
        with patch.object(ai_models,'workflow_digest',return_value='hash'),patch.object(ai_models.subprocess,'run') as run:
            with self.assertRaises(Problem):ai_models.shortcut(local_ai.make_request({'text':'가'*300000},'b'),SELECTION)
            run.assert_not_called()

    def test_prepared_request_remains_identical_after_prompt_builder_changes(self):
        request=ai_models.prepare(local_ai.make_request({'test':'frozen'},'a'))
        def run(args,**kwargs):
            source=Path(args[args.index('--input-path')+1]);target=Path(args[args.index('--output-path')+1])
            self.assertEqual(source.read_text(),request['shortcut_prompt'])
            target.write_text(json.dumps({'request_id':request['request_id'],'content':'frozen response'}))
            return subprocess.CompletedProcess(args,0)
        with patch.object(ai_models,'prepare',side_effect=AssertionError('must use persisted text')),patch.object(ai_models,'workflow_digest',return_value='hash'),patch.object(ai_models.subprocess,'run',side_effect=run):
            self.assertEqual(ai_models.shortcut(request,SELECTION)['content'],'frozen response')

    def test_usage_limit_is_distinct_and_does_not_expose_stderr(self):
        for diagnostic in ['Error: 이 모델의 사용 한도에 도달했습니다. 나중에 다시 시도하십시오.', 'Error: usage limit reached']:
            proc=subprocess.CompletedProcess([],1,stderr=(diagnostic+' PRIVATE_DETAIL').encode())
            with patch.object(ai_models,'workflow_digest',return_value='hash'),patch.object(ai_models.subprocess,'run',return_value=proc),patch.object(local_ai,'bridge') as local:
                with self.assertRaises(Problem) as error:local_ai.draft({},'a',selection=SELECTION)
                self.assertEqual(error.exception.status,429)
                self.assertIn('사용 한도',str(error.exception))
                self.assertNotIn('PRIVATE_DETAIL',str(error.exception))
                local.assert_not_called()
