"""Explicit synthetic-only split SOAP experiment; writes no clinic database."""
import argparse
import json
from pathlib import Path
import time
from clinic_ai import ai_models, local_ai, soap_pipeline

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--models',nargs='+',default=['local','cloud','cloud-pro']);p.add_argument('--lanes',nargs='+',default=['gold','asr']);p.add_argument('--repeat',type=int,default=1);p.add_argument('--strategy',choices=['split','single','fidelity'],default='split');p.add_argument('--bridge');p.add_argument('--cases',default='.build/soap-quality/cases.json');p.add_argument('--output',required=True);a=p.parse_args()
    if not a.run:p.error('--run required for synthetic cloud calls')
    cases=json.loads(Path(a.cases).read_text())
    if a.bridge:local_ai.BINARY=Path(a.bridge).resolve()
    if any(c.get('시험 자료')!='교육용 합성 사례' for c in cases.values()):raise ValueError('synthetic fixtures required')
    output=Path(a.output);output.parent.mkdir(parents=True,exist_ok=True);rows=[]
    for model in a.models:
        provider=ai_models.resolve(model)
        def call(request):
            raw=local_ai.bridge(request) if model=='local' else ai_models.shortcut(request,provider)
            with (output.parent/(output.stem+f'-{model}-{lane}-{repeat+1}-raw.jsonl')).open('a') as stream:stream.write(json.dumps({'request':request,'response':raw},ensure_ascii=False)+'\n')
            return raw
        for lane in a.lanes:
            for repeat in range(a.repeat):
                request=soap_pipeline.make_request(cases[lane]) if a.strategy=='split' else local_ai.make_request(cases[lane],'b')
                if a.strategy=='fidelity':
                    from scripts.evaluate_soap_quality import REVISION
                    request['instructions']+='\n'+REVISION
                start=time.monotonic()
                try:result=ai_models.normalize(call(request),'b') if a.strategy!='split' else soap_pipeline.run(request,call,lambda event: (output.parent/(output.stem+f'-{model}-{lane}-{repeat+1}-trace.jsonl')).open('a').write(json.dumps(event,ensure_ascii=False)+'\n'))
                except Exception as e:result={'error':str(e)}
                row=dict(model=model,lane=lane,repeat=repeat+1,version=soap_pipeline.VERSION if a.strategy=='split' else a.strategy,seconds=round(time.monotonic()-start,2),result=result)
                rows.append(row);output.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
                print(json.dumps({k:v for k,v in row.items() if k!='result'}|{'error':result.get('error')},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
