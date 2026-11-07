(() => {
  'use strict';
  let key = '';
  let selected = 'dottie-hill';
  let lastAnswer = '';
  const $ = (id) => document.getElementById(id);
  const kinds = { 'dottie-hill':'Governing intelligence', 'aegis-dugan':'Aegis / Field leadership', 'aegis-jones':'Aegis / Coordination', 'aegis-morita':'Aegis / Systems', 'aegis-falsworth':'Aegis / Strategic judgment', 'aegis-dernier':'Aegis / Reconnaissance' };
  const shortName = (id) => id.replace(/^aegis-/, '').replace(/^./, (x) => x.toUpperCase());
  function uniqueRequestId(){
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    if (!globalThis.crypto?.getRandomValues) throw Error('Cryptographic request ID generation unavailable.');
    const b=new Uint8Array(16); globalThis.crypto.getRandomValues(b);
    b[6]=(b[6]&15)|64; b[8]=(b[8]&63)|128;
    const hex=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function toast(msg){const t=$('toast');t.textContent=msg;t.hidden=false;setTimeout(()=>{t.hidden=true;},4500);}
  async function api(path, options={}) {
    if(!key) throw Error('Enter the operator key and connect first.');
    const response = await fetch(path,{...options, cache:'no-store',headers:{'Authorization':'Bearer '+key,'Content-Type':'application/json',...(options.headers||{})}});
    let payload;
    try { payload=await response.json(); } catch { throw Error('Non-JSON gateway reply.'); }
    if(!response.ok)throw Error(payload?.error?.message||`Gateway returned ${response.status}`);
    return payload;
  }
  function textNode(tag, val, cls) {const e=document.createElement(tag);if(cls)e.className=cls;e.textContent=val;return e;}
  function selectIdentity(id){selected=id;$('identity').value=id;document.querySelectorAll('.agent').forEach(b=>{b.classList.toggle('current',b.dataset.id===id);b.setAttribute('aria-current',b.dataset.id===id?'true':'false');});}
  async function refresh(){
    const [status,identities,missions]=await Promise.all([api('/api/v1/status'),api('/api/v1/identities'),api('/api/v1/missions')]);
    $('system-state').textContent=(status.configured_pi_identities?.length ? 'INTEGRATION CONFIGURED' : 'MODEL BINDING REQUIRED');
    $('field-acceptance').textContent=status.field_accepted?'ACCEPTED':'NOT VERIFIED';
    $('identity-count').textContent=`${identities.identities.length} / 6`;
    $('mission-count').textContent=missions.missions.length.toString();
    $('uncertain-count').textContent=String(missions.missions.filter(m=>m.status==='reconcile_required').length);
    $('binding-count').textContent=String(status.configured_pi_identities?.length||0)+' / 6';
    $('audit').textContent=status.receipts?.ok ? `${status.receipts.count} local receipts validated · ${status.receipts.head.slice(0,12)}…` : 'WARNING: integrity verification failed';
    const roster=$('roster');roster.replaceChildren();const select=$('identity');select.replaceChildren();
    for(const row of identities.identities){
      const option=document.createElement('option');option.value=row.id;option.textContent=row.display_name;select.append(option);
      const btn=document.createElement('button');btn.type='button';btn.className='agent';btn.dataset.id=row.id;
      btn.append(textNode('span',row.id==='dottie-hill'?'DH':row.id.slice(6,8).toUpperCase(),'initials'));
      const content=document.createElement('span');content.append(textNode('b',row.display_name));content.append(textNode('small',kinds[row.id]||'Independent intelligence'));btn.append(content);
      btn.addEventListener('click',()=>selectIdentity(row.id));roster.append(btn);
    }
    if(identities.identities.some(x=>x.id===selected))selectIdentity(selected);else selectIdentity('dottie-hill');
    const list=$('missions');list.replaceChildren();
    if(!missions.missions.length)list.append(textNode('p','No mission receipts recorded yet.','muted'));
    for(const m of missions.missions.slice(0,24)){
      const item=document.createElement('div');item.className='mission';
      item.append(textNode('span',m.status.replaceAll('_',' '),'mstatus '+m.status));
      item.append(textNode('p',`${shortName(m.identity_id)} · ${m.kind} · ${m.content.slice(0,85)}`));
      item.append(textNode('code',m.id));
      if(m.status==='reconcile_required' && m.submission_ref){const button=textNode('button','Reconcile','flat');button.type='button';button.addEventListener('click',async()=>{try{await api('/api/v1/reconcile',{method:'POST',body:JSON.stringify({mission_id:m.id})});await refresh();}catch(e){toast(e.message);}});item.append(button);}
      list.append(item);
    }
  }
  $('connect').addEventListener('click',async()=>{
    key=$('key').value.trim();$('key').value='';
    if(!key){toast('An operator key is required.');return;}
    try{await refresh();toast('Authenticated local session ready.');}
    catch(e){key='';toast(e.message);$('system-state').textContent='NOT CONNECTED';}
  });
  $('refresh').addEventListener('click',async()=>{try{await refresh();toast('Ledger refreshed.');}catch(e){toast(e.message);}});
  $('identity').addEventListener('change',()=>selectIdentity($('identity').value));
  $('inquiry-form').addEventListener('submit',async(event)=>{
    event.preventDefault();const btn=$('submit-inquiry');btn.disabled=true;btn.textContent='Dispatching…';
    try{
      const body={identity_id:selected,kind:$('kind').value,content:$('inquiry').value,request_id:uniqueRequestId()};
      const data=await api('/api/v1/infer',{method:'POST',body:JSON.stringify(body)});
      const m=data.mission;
      lastAnswer=m.answer||'';
      $('answer').textContent=lastAnswer||`Mission ${m.status}. No verified model response yet.`;
      $('answer').classList.remove('empty');
      $('receipt-ref').textContent=`Mission ${m.id} · State ${m.status} · ${m.model_ref||'Model not bound'} · ${m.submission_ref||'No submission reference'}`;
      $('copy-result').disabled=!lastAnswer;
      await refresh();
    }catch(e){$('answer').textContent=e.message;$('answer').classList.remove('empty');toast('Dispatch not verified. See mission ledger for outcome.');try{await refresh();}catch{}}
    finally{btn.disabled=false;btn.textContent='Dispatch inquiry ↗';}
  });
  $('dispatch-aegis').addEventListener('click',async()=>{
    const btn=$('dispatch-aegis');const goal=$('inquiry').value.trim();
    if(!goal){toast('Enter a mission goal before delegating.');$('inquiry').focus();return;}
    btn.disabled=true;btn.textContent='Delegating…';
    try {
      const data=await api('/api/v1/orchestrate',{method:'POST',body:JSON.stringify({goal,approved_team:[$('aegis-recipient').value],request_id:uniqueRequestId()})});
      const leader=data.leader_plan_id;
      const workerLines=(data.aegis_results||[]).map(w=>`${w.identity_id} / ${w.status}: ${w.answer||'No verified reply'}`);
      const final=data.final||{};
      lastAnswer=[...workerLines,`Dottie Hill / ${final.status||'unknown'}: ${final.answer||'Synthesis not verified'}`].join('\n\n');
      $('answer').textContent=lastAnswer;
      $('answer').classList.remove('empty');
      $('receipt-ref').textContent=`Dottie plan ${leader} · ${data.aegis_results.map(x=>x.mission_id).join(', ')} · Final ${final.id||'not issued'}`;
      $('copy-result').disabled=!lastAnswer;
      await refresh();
    }catch(e){$('answer').textContent=e.message;$('answer').classList.remove('empty');toast('Delegation not verified. Check mission ledger.');try{await refresh();}catch{}}
    finally{btn.disabled=false;btn.textContent='Delegate & synthesize ↗';}
  });
  $('copy-result').addEventListener('click',()=>{if(lastAnswer)navigator.clipboard?.writeText(lastAnswer).then(()=>toast('Copied.')).catch(()=>toast('Clipboard denied.'));});
  $('evidence-form').addEventListener('submit',async(event)=>{
    event.preventDefault();try{
      const payload={source_ref:$('source-ref').value,proposition:$('proposition').value,state:$('evidence-status').value,author:'operator'};
      const data=await api('/api/v1/evidence',{method:'POST',body:JSON.stringify(payload)});
      toast(`Evidence recorded: ${data.evidence.id}`);$('proposition').value='';await refresh();
    }catch(e){toast(e.message);}
  });
  function clock(){try{$('clock').textContent=new Intl.DateTimeFormat('en-US',{hour:'2-digit',minute:'2-digit',timeZone:'America/Chicago',hour12:false}).format(new Date())+' CT';}catch{$('clock').textContent='GMRI';}}
  clock();setInterval(clock,60000);
})();
