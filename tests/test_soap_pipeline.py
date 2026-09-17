import json
import unittest
from unittest.mock import patch
from clinic_ai import soap_pipeline as sp, local_ai
from clinic_ai.store import Problem

class SplitTests(unittest.TestCase):
    def test_question_and_answer_never_split(self):
        qa='의사: 열과 기침 있나요?\n환자: 모두 없어요.'
        context={'문진 전사':'\n'.join([qa]*100)}
        chunks=sp.chunks(context)
        self.assertGreater(len(chunks),1)
        self.assertEqual(sum(c.count(qa) for c in chunks),100)
        self.assertTrue(all(len(c)<=sp.LIMIT for c in chunks))
    def test_unbounded_unsegmented_source_not_silently_cut(self):
        with self.assertRaises(Problem):sp.make_request({'문진 전사':'가'*3000})
    def test_all_passes_see_original_and_assembly_is_not_model_rewrite(self):
        requests=[]
        def call(r):
            requests.append(r)
            return {'content':'시험 본문','soap':'S: 음성\nO: 미검사\nA: 미확정\nP: 미정','inputTokens':123}
        source='의사: 열 있나요?\n환자: 없어요.'
        result=sp.run(sp.make_request({'문진 전사':source}),call)
        self.assertEqual(len(requests),5)
        self.assertTrue(all(source in r['prompt'] for r in requests))
        self.assertEqual(result['soap'],'S: 시험 본문\n\nO: 시험 본문\n\nA: 시험 본문\n\nP: 시험 본문')
        self.assertEqual(len(result['pipeline']['calls']),5)
    def test_failure_stops_no_partial_soap(self):
        def call(r):
            if r['stage']=='b':return {'content':'facts','soap':'S: x\nO: y\nA: z\nP: t'}
            raise Problem('timeout',504)
        with self.assertRaises(Problem) as cm:sp.run(sp.make_request({}),call)
        self.assertEqual(cm.exception.status,504)
        self.assertIn('1/S',str(cm.exception))
    def test_invalid_facts_not_used(self):
        with self.assertRaises(Problem):sp.run(sp.make_request({}),lambda _: {'content':'partial'})
    def test_queued_plan_freezes_rules_and_input(self):
        request=json.loads(json.dumps(sp.make_request({'문진 전사':'original'})))
        captured=[]
        def call(r):
            captured.append(r);return {'content':'facts','soap':'S: x\nO: y\nA: z\nP: t'}
        with patch.object(sp,'RULES','changed'),patch.object(sp,'SECTIONS',{'S':'changed'}):sp.run(request,call)
        self.assertEqual(len(captured),5)
        self.assertTrue(all('changed' not in r['instructions'] for r in captured))
        self.assertTrue(all('original' in r['prompt'] for r in captured))

    def test_draft_preserves_provider_trace_and_legacy_route(self):
        request=sp.make_request({'문진 전사':'의사: 진단은 미정입니다.'})
        def response(r):return {'content':'본문','soap':'S: 없음\nO: 미검사\nA: 미정\nP: 미정','inputTokens':40}
        with patch('clinic_ai.local_ai.bridge',side_effect=response) as model:
            result=local_ai.draft({},'b',selection={'id':'local'},request=request)
        self.assertEqual(model.call_count,5)
        self.assertEqual(result['generation']['soap_strategy'],'split')
        self.assertEqual(len(result['pipeline']['calls']),5)
        legacy=local_ai.make_request({},'b')
        with patch('clinic_ai.local_ai.bridge',side_effect=response) as model:
            result=local_ai.draft({},'b',selection={'id':'local'},request=legacy)
        self.assertEqual(model.call_count,1)
        self.assertEqual(result['generation']['soap_strategy'],'single')
    def test_partial_failure_does_not_switch_provider(self):
        request=sp.make_request({})
        with patch('clinic_ai.local_ai.bridge') as local, patch('clinic_ai.ai_models.shortcut',side_effect=[{'content':'facts','soap':'S: x\nO: y\nA: z\nP: t'},Problem('failure',503)]) as cloud:
            with self.assertRaises(Problem):local_ai.draft({},'b',selection={'id':'cloud-pro'},request=request)
        local.assert_not_called();self.assertEqual(cloud.call_count,2)
        self.assertTrue(all(c.args[1]['id']=='cloud-pro' for c in cloud.call_args_list))

    def test_punctuation_is_not_a_candidate(self):
        def call(r):return {'content':'facts','soap':'S: x\nO: y\nA: z\nP: t'} if r['stage']=='b' else {'content':'{'}
        with self.assertRaises(Problem):sp.run(sp.make_request({}),call)
    def test_cloud_schema_uses_the_requested_section_description(self):
        from clinic_ai import ai_models
        request={'stage':'a','instructions':'항목 작성','prompt':'합성','contentDescription':'O 항목만: 실제 검사'}
        prepared=ai_models.prepare(request)
        self.assertIn('O 항목만: 실제 검사',prepared['shortcut_prompt'])
        self.assertNotIn('검토용 요약',prepared['shortcut_prompt'])
    def test_cloud_self_report_is_not_token_evidence(self):
        def call(*_):return {'content':'본문','soap':'S: x\nO: y\nA: z\nP: t','inputTokens':1,'variant':'invented'}
        with patch('clinic_ai.ai_models.shortcut',side_effect=call):result=local_ai.draft({},'b',selection={'id':'cloud-pro'},request=sp.make_request({}))
        self.assertTrue(all(c['inputTokens'] is None and c['variant'] is None for c in result['pipeline']['calls']))
