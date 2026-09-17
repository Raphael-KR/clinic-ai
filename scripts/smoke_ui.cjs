// UI integration via jsdom + real temporary HTTP/SQLite; no browser globals mocked for saves.
const assert=require('node:assert/strict');
const {JSDOM}=require('../.build/qa/node_modules/jsdom');
const fs=require('node:fs');
const vm=require('node:vm');
const base=process.argv[2];
let dom,w,failSave=false,delaySave=0,delayRoute=false;const errors=[];
function setup(hash='',stored={}){
 dom=new JSDOM(fs.readFileSync('clinic_ai/static/index.html','utf8'),{url:base+'/'+hash,runScripts:'outside-only',pretendToBeVisual:true});w=dom.window;
 for(const [k,v] of Object.entries(stored))w.localStorage.setItem(k,v);
 w.fetch=async(url,opt)=>{if(delayRoute&&String(url)==='/api/workspace')await delay(350);if(delaySave&&opt?.method==='PATCH'&&String(url).includes('/workspace/'))await delay(delaySave);if(failSave&&opt?.method==='PATCH'&&String(url).includes('/workspace/'))return Promise.reject(Error('시험 연결 실패'));return fetch(new URL(url,base),opt);};
 w.confirm=()=>true;Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async t=>w.copied=t}});
 w.addEventListener('error',ev=>errors.push(ev.message));
 for(const file of ['mic.js','app.js'])vm.runInContext(fs.readFileSync('clinic_ai/static/'+file,'utf8'),dom.getInternalVMContext());
}
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label){for(let i=0;i<300;i++){if(fn())return;await delay(25);}throw Error('Timed out '+label+'\n'+w.document.body.textContent.slice(-1500));}
const $=s=>w.document.querySelector(s);
function click(s){assert.ok($(s),s);$(s).click();}
function fill(s,v){assert.ok($(s),s);$(s).value=v;$(s).dispatchEvent(new w.Event('input',{bubbles:true}));}
async function route(hash,selector){w.location.hash=hash;await until(()=>$(selector),hash);}
function saved(){return $('#save-status')?.textContent.startsWith('저장됨');}
(async()=>{
 setup();await until(()=>$('#queue-query'),'home');assert.match(w.document.body.textContent,/이 조건의 진료가 없습니다/);
 const boot=await (await fetch(base+'/api/bootstrap')).json();const api=async(path,method='GET',body)=>{const r=await fetch(base+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':boot.csrf},body:body?JSON.stringify(body):undefined});const data=await r.json();assert.ok(r.ok,JSON.stringify(data));return data;};
 for(const t of ['surveys','exams','sessions','knowledge','crm','templates','settings'])assert.deepEqual(await api('/api/'+t),[]);
 await route('manage/settings/new','#admin-form');fill('textarea[name="항목명"]','DOM 설정');$('#admin-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await until(()=>w.location.hash!=='#manage/settings/new','settings created');await until(()=>$('#admin-form'),'settings form');
 await api('/api/surveys','POST',{properties:{'이름':'DOM 환자','휴대폰 번호':'010-0000-0000','주민등록번호 앞 6자리':'000101','주민등록번호 뒤 7자리':'3000000','주소증':'DOM 요통','증상 상세':'방사통 없음','복용약':'없음','개인정보 수집 동의':false,'카카오 안내 동의':false}});
 const sid=(await api('/api/sessions'))[0].id;
 // An older delayed response must not replace the newer selected route.
 delayRoute=true;w.location.hash='home';await delay(40);await route('library','a[href="#knowledge"]');await delay(450);assert.ok($('a[href="#knowledge"]'));delayRoute=false;

 await route('home','#queue-query');assert.match(w.document.body.textContent,/DOM 요통/);fill('#queue-query','없는환자');assert.match($('#queue-list').textContent,/이 조건/);fill('#queue-query','DOM');assert.ok($('#queue-list a'));
 await route(`sessions/${sid}/before`,'#exam-form');assert.match($('.patient-heading').textContent,/DOM 환자/);
 fill('input[name="수축기혈압"]','120');fill('input[name="이완기혈압"]','80');fill('input[name="맥박"]','70');$('#exam-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await until(()=>$('.measurement')?.textContent.includes('120/80'),'exam summary');
 await route(`sessions/${sid}/interview`,'#doc-notes');fill('#doc-notes','문진 중 메모');await until(saved,'note autosave');assert.equal((await api('/api/sessions/'+sid)).document.notes,'문진 중 메모');
 // Input edited after a request was sent keeps its own base and status.
 delaySave=350;fill('#doc-notes','지연 저장');await until(()=>$('#save-status').textContent==='저장 중…','pending save');
 $('#session-status').value='문진중';$('#session-status').dispatchEvent(new w.Event('change',{bubbles:true}));fill('#doc-soap_o','응답 대기 중 진찰');
 await until(saved,'pending edits preserved');delaySave=0;
 let delayedRecord=await api('/api/sessions/'+sid);assert.equal(delayedRecord.properties['세션 상태'],'문진중');assert.equal(delayedRecord.document.soap_o,'응답 대기 중 진찰');
 await route('home','#queue-filter');$('#queue-filter').value='완료';$('#queue-filter').dispatchEvent(new w.Event('change'));assert.match($('#queue-list').textContent,/이 조건/);click('[data-filter="진료 중"]');assert.match($('#queue-list').textContent,/DOM 환자/);assert.ok($('#queue-list a[href$="/interview"]'));await route(`sessions/${sid}/interview`,'#doc-notes');
 // Text import uses the actual file change handler and autosave path.
 const textFile=new w.File(['합성 문진 텍스트'],'transcript.txt',{type:'text/plain'});textFile.text=async()=> '합성 문진 텍스트';
 Object.defineProperty($('#transcript-file'),'files',{value:[textFile],configurable:true});$('#transcript-file').dispatchEvent(new w.Event('change',{bubbles:true}));await until(()=>$('#doc-transcript').value==='합성 문진 텍스트','text imported');await until(saved,'text autosaved');assert.equal((await api('/api/sessions/'+sid)).document.transcript,'합성 문진 텍스트');
 Object.defineProperty(w.navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{const e=Error('denied');e.name='NotAllowedError';throw e;}}});click('#mic-start');await until(()=>$('#notice').textContent.includes('권한'),'mic denied');assert.equal($('#doc-notes').disabled,false);
 // Device loss and page departure release capture; live engine is independently tested on the Mac.
 let stoppedTracks=0,closedContexts=0,cancelledMic=0,openedStreams=0,liveReadDelay=1200;const track={stop(){stoppedTracks++},onended:null};
 let liveState={text:'메모리 전사',partial:'임시 문장',seconds:1};const realFetch=w.fetch;w.fetch=async(url,opt)=>{if(String(url).startsWith('/api/live/')){if(!opt?.method||opt.method==='GET')await delay(liveReadDelay);if(String(url).endsWith('/cancel'))cancelledMic++;return new Response(JSON.stringify(String(url).includes('/start/')?{id:'mock-live'}:liveState),{status:200,headers:{'Content-Type':'application/json'}});}return realFetch(url,opt);};
 Object.defineProperty(w.navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{openedStreams++;return {getTracks:()=>[track]}}}});
 w.AudioContext=class{constructor(){this.sampleRate=16000;this.state='running';this.audioWorklet={addModule:async()=>{}};}createMediaStreamSource(){return {connect(){},disconnect(){}};}createAnalyser(){return {fftSize:512,getFloatTimeDomainData(a){a.fill(.03)},disconnect(){}};}async resume(){}async close(){this.state='closed';closedContexts++;}};
 w.AudioWorkletNode=class{constructor(){this.port={onmessage:null,close(){},postMessage:()=>this.port.onmessage({data:{flushed:true}})};}connect(){}disconnect(){}};
 const startButton=$('#mic-start');startButton.click();startButton.click();await until(()=>$('#mic-wave')?.dataset.active==='true','wave before server transcription');assert.equal($('#transcript-final').textContent,'');assert.equal(openedStreams,1);await until(()=>$('#transcript-final').textContent==='메모리 전사','mock live ready');liveReadDelay=0;assert.equal($('#doc-notes').disabled,false);
 assert.equal($('#mic-text'),null);assert.equal($('#edit-transcript').hidden,true);assert.equal($('[data-edit-field="transcript"]').disabled,true);
 await until(()=>$('#mic-wave').dataset.active==='true','local analyser feedback');
 assert.equal($('#transcript-partial').textContent,'임시 문장');assert.equal($('#transcript-saved').textContent,'합성 문진 텍스트');
 const stopButton=$('#mic-stop');stopButton.focus();liveState={text:'메모리 전사',partial:'고친 임시 문장',seconds:2};
 await until(()=>$('#transcript-partial').textContent==='고친 임시 문장','provisional replacement');assert.equal($('#mic-stop'),stopButton);assert.equal(w.document.activeElement,stopButton);
 liveState={text:'메모리 전사 고친 확정 문장',partial:'다음 문장',seconds:3};await until(()=>$('#transcript-final').textContent.endsWith('확정 문장'),'final transition');
 assert.equal($('#transcript-partial').textContent,'다음 문장');assert.equal($('#transcript-final').textContent.includes('임시'),false);
 await route(`sessions/${sid}/review`,'#doc-soap_s');assert.equal($('#mic-stop'),stopButton);assert.equal($('#mic-wave').dataset.active,'true');
 await route(`sessions/${sid}/interview`,'#doc-notes');track.onended();await until(()=>$('#recover-mic'),'device loss recovery');assert.ok(stoppedTracks&&closedContexts&&cancelledMic);click('#discard-mic-recovery');
 click('#mic-start');await until(()=>$('#mic-stop')&&!$('#mic-stop').disabled,'restart after device loss');const stopsBefore=stoppedTracks;w.dispatchEvent(new w.Event('pagehide'));await until(()=>stoppedTracks>stopsBefore&&cancelledMic===2,'pagehide releases mic');click('#mic-cancel');await until(()=>$('#mic-dock').dataset.mode==='idle','explicit cleanup');w.fetch=realFetch;

 // File import remains reachable in the secondary menu and follows the existing save path.
 $('.import-menu').open=true;const audioFile=new w.File(['synthetic fixture'],'synthetic.wav',{type:'audio/wav'});
 Object.defineProperty($('#audio-file'),'files',{value:[audioFile],configurable:true});click('#audio-transcribe');
 await until(()=>$('#transcript-saved').textContent==='DOM 합성 음성 전사','file transcript reading view');
 assert.equal((await api('/api/sessions/'+sid)).document.transcript,'DOM 합성 음성 전사');
 await route(`sessions/${sid}/review`,'#doc-soap_s');assert.equal($('#edit-soap_s').hidden,true);click('[data-edit-field="soap_s"]');assert.equal($('#edit-soap_s').hidden,false);fill('#doc-soap_s','내가 수정한 S');await until(saved,'soap save');click('[data-edit-field="soap_s"]');assert.equal($('#edit-soap_s').hidden,true);assert.equal($('[data-reading="soap_s"]').textContent,'내가 수정한 S');click('#copy-soap');await until(()=>w.copied?.includes('내가 수정한 S'),'copy editor');
 click('[data-generate="a"]');await until(()=>$('#jobs-panel').textContent.includes('생성 중')||$('#jobs-panel').textContent.includes('후보 준비'),'job starts');fill('#doc-notes','AI 생성 중에도 메모');await until(saved,'notes during job');await until(()=> $('[data-apply-job][data-key="a"]'),'a candidate');assert.equal((await api('/api/sessions/'+sid)).document.a,undefined);click('[data-apply-job][data-key="a"]');await until(saved,'candidate save');assert.equal((await api('/api/sessions/'+sid)).document.a,'DOM 생성 초안');
 $('#ai-model').value='cloud';$('#ai-model').dispatchEvent(new w.Event('change'));assert.match($('#model-location').textContent,/Apple Private Cloud Compute/);const beforeJobs=(await api('/api/workspace/'+sid)).jobs.length;await delay(80);assert.equal((await api('/api/workspace/'+sid)).jobs.length,beforeJobs);click('[data-generate="b"]');await until(()=> $('[data-apply-job][data-key="soap_o"]'),'b candidate');click('[data-apply-job][data-key="soap_o"]');await until(saved,'partial apply');let record=await api('/api/sessions/'+sid);assert.equal(record.document.soap_s,'내가 수정한 S');assert.equal(record.document.soap_o,'미검사');assert.equal(record.document.ux_sources.soap_o.generation.id,'cloud');assert.match($('#jobs-panel').textContent,/AFM 3 Cloud/);
 click('#copy-soap');await until(()=>w.copied?.includes('O: 미검사'),'copy applied SOAP');
 w.URL.createObjectURL=blob=>{w.exported=blob;return 'blob:test-export';};w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=function(){w.exportName=this.download;};click('#download');
 const exported=await new Promise(resolve=>{const reader=new w.FileReader();reader.onload=()=>resolve(reader.result);reader.readAsText(w.exported);});assert.match(exported,/내가 수정한 S/);assert.match(exported,/O: 미검사/);assert.equal(w.exportName,'진료문서.txt');
 await route(`sessions/${sid}/guide`,'#doc-guide');$('#ai-model').value='cloud-pro';$('#ai-model').dispatchEvent(new w.Event('change'));click('[data-generate="c"]');await until(()=> $('[data-retry-job]'),'cloud failure');assert.match($('#jobs-panel').textContent,/시험 Cloud Pro 오류/);$('#ai-model').value='local';$('#ai-model').dispatchEvent(new w.Event('change'));click('[data-retry-job]');await until(()=> $('[data-apply-job][data-key="message"]'),'c candidate');assert.equal((await api('/api/workspace/'+sid)).jobs.find(j=>j.stage==='c'&&j.state==='completed').model,'cloud-pro');for(const k of ['guide','rx_guide','message']){click(`[data-apply-job][data-key="${k}"]`);await until(saved,'apply '+k);}
 click('[data-preview="guide"]');assert.match($('[data-preview-box="guide"]').textContent,/환자용 안내/);click('#crm-prepare');await until(()=>$('#guidance-status').textContent.includes('저장됨'),'guidance saved');assert.equal((await api('/api/crm'))[0].properties['메시지 본문'],'로컬 안내 메시지');
 // Same-field race preserves both values and resolves explicitly.
 record=await api('/api/sessions/'+sid);await api('/api/workspace/'+sid,'PATCH',{changes:{notes:'다른 화면 값'},base:{notes:record.document.notes}});fill('#doc-notes','이 화면 값');await until(()=> $('[data-conflict="notes"]'),'conflict');assert.match($('#conflict').textContent,/다른 화면 값/);click('[data-conflict="notes"][data-choice="mine"]');await until(saved,'conflict resolution');assert.equal((await api('/api/sessions/'+sid)).document.notes,'이 화면 값');
 await until(()=> $('[data-version]'),'version history');click('[data-version]');await until(()=> $('[data-restore-field]'),'version preview');assert.ok($('#version-preview').textContent.length>20);const restore=$('[data-restore-field="notes"]');const restoreText=restore.parentElement.querySelector('pre').textContent;restore.click();await until(saved,'version restored');assert.equal((await api('/api/sessions/'+sid)).document.notes,restoreText);
 $('[data-version]:last-child').click();await until(()=> $('[data-restore-field="notes"]')?.parentElement.querySelector('pre').textContent==='비어 있음','empty historical field');click('[data-restore-field="notes"]');await until(saved,'restore empty field');assert.equal((await api('/api/sessions/'+sid)).document.notes,'');
 // Network failure persists a local draft, including base values, across reload.
 failSave=true;fill('#doc-notes','실패 후 복구할 초안');await until(()=>$('#save-status').textContent.includes('저장 실패'),'save failed');const stored=Object.fromEntries(Object.keys(w.localStorage).map(k=>[k,w.localStorage.getItem(k)]));dom.window.close();failSave=false;setup(`#sessions/${sid}/interview`,stored);await until(()=>$('#restore-draft'),'recovery offered');click('#restore-draft');await until(saved,'recovery save');assert.equal((await api('/api/sessions/'+sid)).document.notes,'실패 후 복구할 초안');
 click('#new-visit');await until(()=>!w.location.hash.includes(sid),'new visit');await until(()=>$('#doc-notes'),'new visit rendered');const visits=await api('/api/sessions');assert.equal(visits.length,2);assert.equal($('#doc-notes').value,'');click('[data-history]');await until(()=>$('#visit-preview').textContent.includes('이전 방문'),'past record');assert.match($('#visit-preview').textContent,/내가 수정한 S/);
 await route('library','a[href="#knowledge"]');click('a[href="#docs"]');await until(()=>$('a[href="#docs/a"]'),'docs list');click('a[href="#docs/a"]');await until(()=>$('.rendered'),'source document');
 assert.deepEqual(errors,[]);console.log('PASS: care navigation, queue filters, linked exam, autosave, notes during AI, partial candidate apply, clipboard, guidance, conflicts, draft reload recovery, versions, new visit/history, source docs, text import/export, empty-field restore, delayed saves/routes, mic denial/device loss/page departure, responsive analyser before delayed ASR, partial replacement/finalization, stable focus, single-start guard, read/edit SOAP, file import, explicit model selection without generation, cloud candidate metadata, retry pinned to original model.');
 dom.window.close();
})().catch(err=>{console.error(err);dom?.window.close();process.exitCode=1;});
