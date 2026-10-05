import {config} from './config.js';

export function createPrivacyClient(settings=config,request=globalThis.fetch){
  async function call(path,body){
    const response=await request(settings.baseUrl+'/api/privacy/'+path,{method:body===undefined?'GET':'POST',credentials:'include',headers:body===undefined?undefined:{'Content-Type':'application/json','X-Hema-Client':'1'},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Не удалось проверить режим обработки.');
    return result;
  }
  return {status:()=>call('status'),acknowledge:(policyVersion,dataKind)=>call('acknowledge',{policyVersion,dataKind,researchOnly:true})};
}

export function installResearchAccess(){
  const form=document.getElementById('analysis-form');
  if(!form||document.getElementById('research-dialog'))return;
  const api=createPrivacyClient();
  const notice=document.createElement('div');
  notice.className='research-notice';
  notice.innerHTML='<span id="research-status" role="status">Исследовательский режим</span><button type="button" class="text-button" id="research-open" aria-haspopup="dialog" aria-controls="research-dialog">Условия работы ↗</button>';
  form.prepend(notice);
  const dialog=document.createElement('dialog');
  dialog.id='research-dialog';
  dialog.className='research-dialog';
  dialog.setAttribute('aria-labelledby','research-title');
  dialog.setAttribute('aria-describedby','research-description');
  dialog.innerHTML=`<button type="button" class="close info-close" id="research-close" aria-label="Закрыть условия работы">×</button>
    <span class="eyebrow">ИССЛЕДОВАТЕЛЬСКИЙ РЕЖИМ</span>
    <h2 id="research-title">Перед началом работы</h2>
    <p id="research-description">Hema — исследовательский прототип. Результат нельзя использовать для диагностики, назначения лечения или исключения заболевания.</p>
    <div class="research-data-field"><label for="research-data-kind">Какие данные вы используете?</label>
    <select id="research-data-kind" autofocus><option value="">Выберите вид данных</option><option value="synthetic">Вымышленные — синтетические</option><option value="anonymized">Обезличенные — человека нельзя определить</option></select></div>
    <p class="research-data-hint">Реальные идентифицируемые анализы не загружайте. Удаление только ФИО не гарантирует обезличивание.</p>
    <details class="research-terms"><summary>Условия работы и хранения данных</summary><div>
      <p>Используйте только полностью вымышленные или действительно обезличенные данные, по которым нельзя определить человека с учётом доступных дополнительных сведений. Подтверждение не обезличивает загружаемый файл.</p>
      <p>${config.mode==='local'?'В этом режиме данные формы обрабатываются в браузере; серверная модель и PDF-импорт не подключены.':'Новые отчёты и PDF временно хранятся на этом компьютере до часа от подтверждения. Очистка сеанса, выход или перезапуск сервера удаляют их.'} Скачанные файлы и распечатки остаются у вас.</p>
      <p>Модель и тексты рекомендаций исследовательские; клиническая достоверность не установлена. Низкая оценка модели или нормальный гемоглобин не исключают заболевание.</p>
      <p>Это подтверждение исследовательских условий, а не согласие на обработку медицинских персональных данных. Для работы с реальными пациентами необходимы отдельные основания обработки и допуск продукта.</p>
    </div></details>
    <label class="research-confirm"><input type="checkbox" id="research-confirm">Подтверждаю выбранный вид данных и понимаю условия исследовательской работы.</label>
    <p id="research-message" class="research-message" role="status" hidden></p>
    <div class="research-actions"><button type="button" class="primary" id="research-start">Продолжить <span aria-hidden="true">→</span></button><button type="button" class="text-button" id="research-return" hidden>Вернуться к анализам ↗</button></div>`;
  document.body.append(dialog);
  const opener=notice.querySelector('#research-open');
  const status=notice.querySelector('#research-status');
  const button=dialog.querySelector('#research-start');
  const message=dialog.querySelector('#research-message');
  const input=dialog.querySelector('#research-confirm');
  const kind=dialog.querySelector('#research-data-kind');
  const returnButton=dialog.querySelector('#research-return');
  let acknowledged=false,version=null,timer,pending=false,stateRevision=0;
  function tell(text){message.textContent=text;message.hidden=!text;}
  function show(){if(!dialog.open)dialog.showModal();}
  function close(){if(pending)return;dialog.close();opener.focus();}
  function setState(active,expiresAt){
    stateRevision++;
    acknowledged=active;
    form.dataset.researchAcknowledged=String(active);
    notice.dataset.confirmed=String(active);
    document.getElementById('submit').disabled=!active;
    for(const id of ['table-file','pdf-file'])document.getElementById(id).disabled=!active;
    kind.disabled=active;input.disabled=active;
    button.hidden=active;returnButton.hidden=!active;
    dialog.querySelector('#research-title').textContent=active?'Условия исследовательской работы':'Перед началом работы';
    status.textContent=active?'Исследовательский режим · '+(kind.value==='synthetic'?'вымышленные данные':'обезличенные данные'):'Перед импортом и расчётом подтвердите условия';
    opener.textContent=active?'Условия работы ↗':'Начать работу ↗';
    clearTimeout(timer);
    if(expiresAt)timer=setTimeout(()=>{
      setState(false);input.checked=false;kind.value='';
      tell('Часовой сеанс завершён. Подтвердите условия для новой работы.');
      document.dispatchEvent(new Event('hema:research-expired'));
      show();
    },Math.max(0,expiresAt*1000-Date.now()));
  }
  form.addEventListener('submit',event=>{
    if(acknowledged)return;
    event.preventDefault();event.stopImmediatePropagation();
    tell('Перед расчётом выберите вид данных и подтвердите условия.');show();
  },true);
  for(const id of ['table-drop','pdf-drop'])document.getElementById(id).addEventListener('drop',event=>{
    if(acknowledged)return;
    event.preventDefault();event.stopImmediatePropagation();
    tell('Перед импортом выберите вид данных и подтвердите условия.');show();
  },true);
  opener.onclick=show;
  dialog.querySelector('#research-close').onclick=close;
  returnButton.onclick=close;
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('click',event=>{
    if(event.target!==dialog)return;
    const rect=dialog.getBoundingClientRect();
    if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)close();
  });
  button.onclick=async()=>{
    if(pending)return;
    if(!kind.value){tell('Выберите вид данных.');kind.focus();return;}
    if(!input.checked){tell('Подтвердите условия после прочтения.');input.focus();return;}
    const selectedKind=kind.value;
    pending=true;button.disabled=true;kind.disabled=true;input.disabled=true;
    try{
      if(config.mode==='local')setState(true,Date.now()/1000+3600);
      else{
        if(!version)version=(await api.status()).policyVersion;
        const result=await api.acknowledge(version,selectedKind);
        if(!result.acknowledged||!Number.isFinite(result.expiresAt)||result.expiresAt*1000<=Date.now())throw new Error('Не удалось подтвердить действующий сеанс. Повторите попытку.');
        setState(true,result.expiresAt);
      }
      tell('');dialog.close();opener.focus();
    }catch(err){tell(err.message);}
    finally{pending=false;button.disabled=false;kind.disabled=acknowledged;input.disabled=acknowledged;}
  };
  function requireAgain(showNow){setState(false);input.checked=false;kind.value='';tell('');if(showNow)show();}
  document.addEventListener('hema:research-required',()=>requireAgain(true));
  document.addEventListener('hema:research-cleared',()=>requireAgain(false));
  setState(false);
  if(config.mode==='api'){
    status.textContent='Проверяем исследовательский сеанс…';
    const initialRevision=stateRevision;
    api.status().then(result=>{
      if(stateRevision!==initialRevision||pending)return;
      version=result.policyVersion;
      if(result.acknowledged&&['synthetic','anonymized'].includes(result.dataKind)&&Number.isFinite(result.expiresAt)&&result.expiresAt*1000>Date.now()){
        input.checked=true;kind.value=result.dataKind;setState(true,result.expiresAt);tell('');if(dialog.open)dialog.close();
      }else{setState(false);show();}
    }).catch(err=>{if(stateRevision!==initialRevision||pending)return;setState(false);tell(err.message);show();});
  }else show();
}
