'use strict';
const form=document.querySelector('form');
const choice=document.querySelector('#medication-mode'),detail=document.querySelector('#medication');
function medication(){detail.hidden=choice.value!=='yes';detail.required=choice.value==='yes';if(choice.value==='none')detail.value='없음';else if(detail.value==='없음')detail.value='';}
choice.addEventListener('change',medication);medication();
for(const control of form.querySelectorAll('input,textarea,select')){const key=control.name==='medication_mode'?'medication':control.name;const error=document.querySelector('#error-'+key);if(error){control.setAttribute('aria-describedby',error.id);if(error.textContent)control.setAttribute('aria-invalid','true');control.addEventListener('invalid',()=>{error.textContent='이 항목을 확인해 주세요.';control.setAttribute('aria-invalid','true');});control.addEventListener('input',()=>{if(control.validity.valid){error.textContent='';control.removeAttribute('aria-invalid');}});}}
