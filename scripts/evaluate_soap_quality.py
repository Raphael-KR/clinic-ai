"""Synthetic SOAP fidelity comparison. Cloud execution requires explicit --run.
Does not write clinical DBs or change the application's active prompt.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from clinic_ai import ai_models, local_ai

ROOT = Path('.build/soap-quality')
REVISION = '''
원문 충실도 추가 규칙:
1. S의 위험 신호 문답에서 여러 질문 뒤 '모두 없다'는 답은 각 항목을 개별적으로 풀어 쓴다. 현재 증상 부정과 향후 발생 시 행동 지침은 서로 대신하지 않는다.
2. 한정어(큰, 심한, 이번, 이전), 시간, 수치를 보존한다. '큰 외상 없음'을 '외상력 없음'으로 넓히지 않는다.
3. '정하지 않았다/미정'을 '없다/하지 않는다'로 확정하지 않는다. 진단과 치료 결정 상태를 그대로 유지한다.
4. 구두점·띄어쓰기·용어가 불명확한 전사로 서로 다른 치료를 합치거나 교정하지 않는다. 약·침과 약침은 구분한다. '약 침'처럼 애매하면 원문을 인용하고 의미 확인 필요라고 쓴다.
5. 전사의 발음상 유사한 전문용어와 뒤집힌 수치가 명확하지 않으면 추정 대신 해당 SOAP 항목에 원문과 확인 필요를 남긴다.
6. 반환 전 원문의 부정 항목 목록, 수치, 미검사, 진단 미확정, 처방 미정을 대조하여 빠진 항목을 보완한다. 새 진단·치료·일정·사실을 넣지 않는다.
'''

def call(model, request):
    if model == 'local':
        p = subprocess.run([str(ROOT/'bridge')],input=json.dumps(request,ensure_ascii=False),capture_output=True,text=True,timeout=180)
        if p.returncode: return {'error':'bridge_exit','returncode':p.returncode}
        raw=json.loads(p.stdout)
        if 'error' in raw:return raw
        return ai_models.normalize(raw,'b')
    return ai_models.normalize(ai_models.shortcut(ai_models.prepare(request),ai_models.resolve(model)),'b')

def run_model(model,cases):
    rows=[]
    for version,repeats in [('baseline',1),('fidelity-v1',2)]:
        for lane,context in cases.items():
            for repeat in range(repeats):
                request=local_ai.make_request(context,'b')
                if version!='baseline':request['instructions']+='\n'+REVISION
                fingerprint=hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                start=time.monotonic()
                try:result=call(model,request)
                except Exception as error:result={'error':type(error).__name__,'message':str(error)}
                row={'model':model,'version':version,'lane':lane,'repeat':repeat+1,'seconds':round(time.monotonic()-start,2),'request_hash':fingerprint,'result':result}
                rows.append(row);(ROOT/(model+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
                print(json.dumps({k:v for k,v in row.items() if k!='result'}|{'status':'failed' if 'error' in result else 'generated','error':result.get('error','')},ensure_ascii=False),flush=True)
    return rows

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if not args.run:parser.error('Explicit --run sends only the prepared synthetic cases to the configured Apple cloud shortcuts.')
    cases=json.loads((ROOT/'cases.json').read_text())
    if set(cases)!={'gold','asr'} or any(c.get('시험 자료')!='교육용 합성 사례' for c in cases.values()):raise ValueError('Synthetic fixtures required')
    env=dict(os.environ,DEVELOPER_DIR='/Applications/Xcode.app/Contents/Developer')
    sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],env=env,text=True).strip()
    source=Path('clinic_ai/native/FoundationBridge.swift').read_text()
    source=source.replace('"errorType": String(describing: type(of: error))','"errorType": String(describing: type(of: error)), "diagnostic": String(describing: error)')
    (ROOT/'Bridge.swift').write_text(source)
    subprocess.run(['xcrun','swiftc','-sdk',sdk,'-parse-as-library','-O',str(ROOT/'Bridge.swift'),'-o',str(ROOT/'bridge')],env=env,check=True)
    (ROOT/'run-config.json').write_text(json.dumps({'bridge_sha256':hashlib.sha256(source.encode()).hexdigest(),'baseline_prompt':local_ai.PROMPT_VERSION,'revision':REVISION,'models':{m:ai_models.resolve(m) for m in ('local','cloud','cloud-pro')},'core':'not tested: no public variant selection path verified'},ensure_ascii=False,indent=2))
    # Local and cloud are separate services; avoid competing local jobs or overlapping shortcuts.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        local=pool.submit(run_model,'local',cases)
        cloud=pool.submit(lambda:run_model('cloud',cases)+run_model('cloud-pro',cases))
        rows=local.result()+cloud.result()
    (ROOT/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    print('Generation complete. Semantic adjudication remains required; no automatic quality-pass claim.',flush=True)
if __name__=='__main__':main()
