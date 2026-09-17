// PCM stays in memory. Neither recording files nor audio Blobs are created.
let liveMic=null,micRecovery=null,micStarting=false;
function renderMic(){
 const dock=document.querySelector('#mic-dock');if(!dock)return;const s=liveMic;
 dock.hidden=!s&&!micRecovery&&!current;
 if(!s){
  dock.dataset.mode=micRecovery?'recovery':'idle';
  dock.innerHTML=micRecovery?`<strong>마이크 정지 · 전사 복구 필요</strong><p>${e(micRecovery.message)}</p><textarea id="recover-mic" readonly>${e(micRecovery.text)}</textarea><a class="button" href="#sessions/${micRecovery.sid}/interview">해당 진료 열기</a><button id="save-mic-recovery">편집용 전사에 가져오기</button><button id="discard-mic-recovery">복구문 버리기</button>`:current?`<span class="mic-idle-label">음성 전사 <small>이 Mac에서 처리 · 음성 파일 저장 안 함</small></span><button id="mic-start" class="primary">마이크 시작</button>`:'';
  document.querySelector('#mic-start')?.addEventListener('click',()=>startMicrophone(current));
  document.querySelector('#save-mic-recovery')?.addEventListener('click',()=>{const target=editors.get(micRecovery.sid);if(!target)return notice('먼저 해당 진료를 열어 주세요.');target.values.transcript=[target.values.transcript,micRecovery.text].filter(Boolean).join('\n\n');scheduleSave(target);syncFields(target);micRecovery=null;sessionStorage.removeItem('clinic-mic-recovery');renderMic();});
  document.querySelector('#discard-mic-recovery')?.addEventListener('click',()=>{micRecovery=null;sessionStorage.removeItem('clinic-mic-recovery');renderMic();});
  renderTranscript();return;
 }
 // Keep the controls mounted across results: keyboard focus and open menus survive.
 if(dock.dataset.mode!=='live'){
  dock.dataset.mode='live';dock.innerHTML=`<div class="mic-session"><strong id="mic-patient"></strong><span id="mic-state" role="status"></span></div><div class="mic-signal"><div id="mic-wave" class="mic-wave" role="img" aria-label="마이크 입력 음량">${Array.from({length:11},()=>'<i></i>').join('')}</div><span id="mic-clock">0:00</span></div><button id="mic-stop" class="primary">종료 · 저장</button><details class="mic-more"><summary aria-label="마이크 추가 작업">•••</summary><div><a id="mic-visit">전사 본문 보기</a><button id="mic-cancel">이번 전사 버리기</button></div></details>`;
  document.querySelector('#mic-stop').onclick=()=>stopMicrophone(true);document.querySelector('#mic-cancel').onclick=()=>stopMicrophone(false);
 }
 document.querySelector('#mic-patient').textContent=s.name;
 document.querySelector('#mic-visit').href=`#sessions/${s.sid}/interview`;
 document.querySelector('#mic-stop').disabled=s.ending||!s.node;
 document.querySelector('#mic-cancel').disabled=s.ending;
 document.querySelector('#mic-state').textContent=s.ending?'전사를 마무리하고 있어요…':!s.node?'마이크 준비 중…':'듣고 있어요 · 종료하면 저장';
 paintMicSignal(s);renderTranscript();
}
function paintMicSignal(s){
 const wave=document.querySelector('#mic-wave');if(!wave)return;
 const level=Math.min(1,(s.level||0)*12);wave.dataset.active=String(!s.ending&&level>.035);
 wave.querySelectorAll('i').forEach((bar,i)=>{bar.style.height=(3+level*(12+14*Math.abs(Math.sin(i*1.7))))+'px';});
 wave.setAttribute('aria-label',level>.035?'마이크 소리 입력 중':'마이크 입력 대기');
 const seconds=Math.floor(s.capturedSeconds||s.seconds||0);document.querySelector('#mic-clock').textContent=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
 const listening=document.querySelector('#transcript-listening');if(listening&&current?.id===s.sid&&!listening.hidden)listening.textContent=!s.node?'마이크를 준비하고 있어요…':level>.035?'듣고 있어요 · 문장을 인식하고 있어요…':'듣고 있어요…';
}
function animateMic(s){
 if(liveMic!==s||s.ending)return;
 if(s.analyser){s.analyser.getFloatTimeDomainData(s.waveData);let sum=0;for(const v of s.waveData)sum+=v*v;s.level=Math.sqrt(sum/s.waveData.length);paintMicSignal(s);}
 s.frame=requestAnimationFrame(()=>animateMic(s));
}
async function releaseMic(s){clearTimeout(s.poll);cancelAnimationFrame(s.frame);s.analyser?.disconnect();s.stream?.getTracks().forEach(t=>{t.onended=null;t.stop();});s.source?.disconnect();s.node?.disconnect();s.node?.port.close();if(s.context&&s.context.state!=='closed')await s.context.close();}
function recovery(s,message,text){if(text?.trim()){micRecovery={sid:s.sid,message,text};try{sessionStorage.setItem('clinic-mic-recovery',JSON.stringify(micRecovery));}catch{}}}
async function failMic(message){const s=liveMic;if(!s||s.ending)return;s.ending=true;await releaseMic(s);if(s.id)try{await api(`/api/live/${s.id}/cancel`,{method:'POST',body:'{}'});}catch{}recovery(s,message,s.text);liveMic=null;renderMic();notice(message+' 마이크를 해제했습니다.',true);}
async function pollMic(s){if(liveMic!==s||s.ending)return;try{const r=await api('/api/live/'+s.id);if(s.ending)return;if(r.error)throw Error(r.error);s.finalText=r.text;s.partial=r.partial;s.text=r.text+r.partial;s.seconds=r.seconds;renderTranscript();}catch(err){if(liveMic===s&&!s.ending)await failMic(err.message);return;}s.poll=setTimeout(()=>pollMic(s),250);}
async function startMicrophone(editor){if(liveMic||micStarting)return notice('이미 전사 중이거나 준비 중입니다. 대상 환자를 바꾸려면 정지해 주세요.');micStarting=true;try{if(!await saveState(editor))return;}finally{micStarting=false;}const s={editor,sid:editor.id,name:editor.workspace.patient.name,chain:Promise.resolve(),pending:0,sequence:0,ending:false,text:'',finalText:'',partial:'',seconds:0,capturedSeconds:0};liveMic=s;location.hash=`sessions/${s.sid}/interview`;renderMic();try{if(!navigator.mediaDevices?.getUserMedia)throw Error('마이크를 지원하는 localhost 브라우저를 사용해 주세요.');s.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});if(s.ending||liveMic!==s){s.stream.getTracks().forEach(t=>t.stop());return;}s.context=new AudioContext({sampleRate:16000});if(s.context.sampleRate!==16000)throw Error('16kHz 마이크 처리를 지원하지 않습니다.');s.source=s.context.createMediaStreamSource(s.stream);s.analyser=s.context.createAnalyser();s.analyser.fftSize=512;s.waveData=new Float32Array(s.analyser.fftSize);s.source.connect(s.analyser);await s.context.resume();animateMic(s);await s.context.audioWorklet.addModule('/pcm-worklet.js');const r=await api('/api/live/start/'+s.sid,{method:'POST',body:JSON.stringify({revision:editor.record.revision})});s.id=r.id;if(s.ending||liveMic!==s){await releaseMic(s);await api(`/api/live/${s.id}/cancel`,{method:'POST',body:'{}'});return;}s.node=new AudioWorkletNode(s.context,'clinic-pcm');s.node.port.onmessage=ev=>{if(ev.data.flushed){s.flushed?.();return;}if(!ev.data.pcm)return;s.capturedSeconds+=ev.data.pcm.byteLength/4/16000;if(++s.pending>10){failMic('오디오 처리 지연으로 전사를 정지했습니다.');return;}const seq=s.sequence++,pcm=ev.data.pcm;s.chain=s.chain.then(()=>api(`/api/live/${s.id}/chunk`,{method:'POST',body:pcm,headers:{'Content-Type':'application/octet-stream','X-Sequence':String(seq)}})).then(()=>s.pending--).catch(err=>{s.failure=err;if(liveMic===s)failMic(err.message);});};s.source.connect(s.node);s.node.connect(s.context.destination);for(const t of s.stream.getTracks())t.onended=()=>failMic('마이크 연결이 종료되었습니다.');await s.context.resume();renderMic();pollMic(s);}catch(err){if(liveMic===s)await failMic(err.name==='NotAllowedError'?'마이크 권한이 허용되지 않았습니다.':err.name==='NotFoundError'?'마이크를 찾을 수 없습니다.':err.message);}}
async function stopMicrophone(save=true){const s=liveMic;if(!s||s.ending)return;s.ending=true;renderMic();try{if(s.node){const flushed=new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('마이크 버퍼 종료 실패')),3000);s.flushed=()=>{clearTimeout(t);resolve();};});s.node.port.postMessage('stop');await flushed;}await releaseMic(s);await s.chain;if(s.failure)throw s.failure;if(s.id){const result=await api(`/api/live/${s.id}/${save?'stop':'cancel'}`,{method:'POST',body:'{}'});if(result.unsaved){recovery(s,result.message,result.transcript.text);}else if(result.record){await reconcileRecord(s.editor,result.record);if(current?.id===s.sid)syncFields(s.editor);}notice(save?(result.empty?result.message:result.unsaved?'전사 저장 충돌 · 복구문을 확인해 주세요.':'전사 저장됨 · 음성 파일 생성 없음'):'마이크를 껐습니다. 이번 전사는 저장하지 않았습니다.');}if(!save){micRecovery=null;sessionStorage.removeItem('clinic-mic-recovery');}}catch(err){await releaseMic(s);if(s.id)try{await api(`/api/live/${s.id}/cancel`,{method:'POST',body:'{}'});}catch{}recovery(s,err.message,s.text);notice(err.message,true);}finally{liveMic=null;renderMic();}}
window.addEventListener('pagehide',()=>{const s=liveMic;if(!s)return;s.stream?.getTracks().forEach(t=>t.stop());s.context?.close();recovery(s,'페이지 이동으로 마이크가 종료되었습니다.',s.text);if(s.id)fetch(`/api/live/${s.id}/cancel`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':boot.csrf},body:'{}',keepalive:true}).catch(()=>{});});
window.addEventListener('DOMContentLoaded',()=>{try{micRecovery=JSON.parse(sessionStorage.getItem('clinic-mic-recovery')||'null');}catch{}renderMic();});
