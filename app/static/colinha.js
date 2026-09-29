
(function(){
  var COLINHA_SLOTS=[
    {key:'federal',office:'federal',label:'DEPUTADO FEDERAL',digits:4,proportional:true},
    {key:'estadual',office:'estadual',label:'DEPUTADO ESTADUAL',digits:5,proportional:true},
    {key:'senador1',office:'senador',label:'SENADOR · 1º VOTO',digits:3},
    {key:'senador2',office:'senador',label:'SENADOR · 2º VOTO',digits:3},
    {key:'governador',office:'governador',label:'GOVERNADOR',digits:2},
    {key:'presidente',office:'presidente',label:'PRESIDENTE',digits:2}
  ];
  var SLOT_BY_KEY={};
  COLINHA_SLOTS.forEach(function(slot){SLOT_BY_KEY[slot.key]=slot});

  var cstate={
    scope:null,
    votes:{},
    directories:{},
    directoryErrors:{},
    pickerSlot:null,
    pickerResults:[],
    loadSeq:0
  };
  var ustate={
    index:0,
    digits:'',
    blank:false,
    actual:[],
    startedAt:0,
    finished:false,
    sound:true
  };

  function slotLabel(slot){
    if(slot.key==='estadual'&&state.scope==='df')return 'DEPUTADO DISTRITAL';
    return slot.label;
  }
  function cleanNumber(value,max){
    return String(value||'').replace(/\D/g,'').slice(0,max);
  }
  function emptyVotes(){
    var out={};
    COLINHA_SLOTS.forEach(function(slot){out[slot.key]=''});
    return out;
  }
  function storageKey(scope){
    return 'resultado_eleicoes_2026:colinha:'+String(scope||'br');
  }
  function cloudKey(scope){
    return 'colinha_'+String(scope||'br');
  }
  function normalizeVotes(value){
    var out=emptyVotes();
    if(value&&typeof value==='object'){
      COLINHA_SLOTS.forEach(function(slot){out[slot.key]=cleanNumber(value[slot.key],slot.digits)});
    }
    return out;
  }
  async function restoreVotes(scope){
    var parsed=null;
    try{
      var raw=localStorage.getItem(storageKey(scope));
      if(raw)parsed=JSON.parse(raw);
    }catch(e){}
    if(!parsed){
      try{
        var cloud=await cloudGet(cloudKey(scope));
        if(cloud)parsed=JSON.parse(cloud);
      }catch(e){}
    }
    cstate.votes=normalizeVotes(parsed);
  }
  function persistVotes(){
    if(!cstate.scope||cstate.scope==='br')return;
    var raw=JSON.stringify(cstate.votes);
    try{localStorage.setItem(storageKey(cstate.scope),raw)}catch(e){}
    try{cloudSet(cloudKey(cstate.scope),raw)}catch(e){}
  }
  function dirKey(office,scope){
    return office+':'+scope;
  }
  function scopeFor(slot){
    return slot.office==='presidente'?'br':state.scope;
  }
  function directoryFor(slot){
    return cstate.directories[dirKey(slot.office,scopeFor(slot))]||null;
  }
  function candidatesFor(slot){
    var d=directoryFor(slot);
    return d&&Array.isArray(d.candidates)?d.candidates:[];
  }
  function candidateName(candidate){
    return String((candidate&&(candidate.ballot_name||candidate.name))||'Candidatura');
  }
  function initials(name){
    var parts=String(name||'?').trim().split(/\s+/).filter(Boolean);
    if(!parts.length)return '?';
    return (parts[0][0]+(parts.length>1?parts[parts.length-1][0]:'')).toUpperCase();
  }
  function photoHtml(candidate,cls){
    cls=cls||'colinha-match-photo';
    if(candidate&&candidate.photo){
      return '<img class="'+cls+'" src="'+esc(candidate.photo)+'" alt="" loading="lazy" decoding="async">';
    }
    var fallbackClass='colinha-picker-avatar';
    if(cls==='colinha-match-photo')fallbackClass='colinha-match-placeholder';
    else if(cls==='urna-candidate-photo')fallbackClass='urna-candidate-placeholder';
    else if(cls==='urna-summary-photo')fallbackClass='urna-summary-avatar';
    return '<span class="'+fallbackClass+'">'+esc(initials(candidateName(candidate)))+'</span>';
  }
  function exactCandidate(slot,value){
    if(!value||value.length!==slot.digits)return null;
    var list=candidatesFor(slot);
    for(var i=0;i<list.length;i++){
      if(String(list[i].number||'')===value)return list[i];
    }
    return null;
  }
  function partyFromPrefix(slot,value){
    if(!slot.proportional||!value||value.length<2)return null;
    var prefix=value.slice(0,2);
    var list=candidatesFor(slot);
    for(var i=0;i<list.length;i++){
      var n=String(list[i].number||'');
      if(n.slice(0,2)===prefix){
        return {number:prefix,party:list[i].party||'',party_name:list[i].party_name||''};
      }
    }
    return null;
  }
  function voteMeta(slot,value){
    value=cleanNumber(value,slot.digits);
    if(!value)return {type:'empty',value:''};
    var key=dirKey(slot.office,scopeFor(slot));
    if(!directoryFor(slot)){
      if(cstate.directoryErrors[key])return {type:'unavailable',value:value,error:cstate.directoryErrors[key]};
      return {type:'loading',value:value};
    }
    var exact=exactCandidate(slot,value);
    if(exact)return {type:'candidate',value:value,candidate:exact};
    if(slot.proportional&&(value.length===2||value.length===slot.digits)){
      var party=partyFromPrefix(slot,value);
      if(party)return {type:'party',value:value,party:party};
    }
    if(value.length===slot.digits)return {type:'invalid',value:value};
    return {type:'partial',value:value};
  }
  function boxesHtml(slot,value){
    var out='';
    value=String(value||'');
    for(var i=0;i<slot.digits;i++){
      var digit=value[i]||'';
      out+='<span class="colinha-box'+(digit?' filled':'')+'" data-box-index="'+i+'">'+esc(digit)+'</span>';
    }
    return out;
  }
  function cardHtml(slot){
    var value=cstate.votes[slot.key]||'';
    return '<article class="colinha-card" data-colinha-slot="'+slot.key+'">'+
      '<div class="colinha-card-head"><h3>'+esc(slotLabel(slot))+'</h3><button class="colinha-clear" type="button" data-colinha-clear="'+slot.key+'">limpar ×</button></div>'+
      '<div class="colinha-number-row">'+
        '<div class="colinha-digit-input">'+
          '<div class="colinha-boxes" data-colinha-boxes="'+slot.key+'">'+boxesHtml(slot,value)+'</div>'+
          '<input class="colinha-number-input" data-colinha-input="'+slot.key+'" value="'+esc(value)+'" maxlength="'+slot.digits+'" inputmode="numeric" pattern="[0-9]*" autocomplete="off" aria-label="Número para '+esc(slotLabel(slot))+'">'+
        '</div>'+
        '<div class="colinha-match" data-colinha-match="'+slot.key+'"></div>'+
      '</div>'+
      '<button class="colinha-name-search" type="button" data-colinha-search="'+slot.key+'">Busque pelo nome</button>'+
      '<div class="colinha-warning" data-colinha-warning="'+slot.key+'" hidden></div>'+
    '</article>';
  }
  function renderCards(){
    var root=qs('#colinhaCards');
    root.innerHTML=COLINHA_SLOTS.map(cardHtml).join('');
    COLINHA_SLOTS.forEach(function(slot){updateCard(slot.key)});
  }
  function updateCard(key){
    var slot=SLOT_BY_KEY[key];
    var card=qs('[data-colinha-slot="'+key+'"]');
    if(!slot||!card)return;
    var value=cleanNumber(cstate.votes[key],slot.digits);
    cstate.votes[key]=value;
    var input=card.querySelector('[data-colinha-input]');
    if(input&&input.value!==value)input.value=value;
    var boxes=card.querySelectorAll('.colinha-box');
    for(var i=0;i<boxes.length;i++){
      boxes[i].textContent=value[i]||'';
      boxes[i].classList.toggle('filled',!!value[i]);
      boxes[i].classList.toggle('next',card.classList.contains('has-focus')&&i===value.length&&value.length<slot.digits);
    }
    card.classList.remove('is-match','is-invalid');
    var match=card.querySelector('[data-colinha-match]');
    var meta=voteMeta(slot,value);
    if(meta.type==='candidate'){
      card.classList.add('is-match');
      match.innerHTML=photoHtml(meta.candidate,'colinha-match-photo')+
        '<span class="colinha-match-copy"><b>'+esc(candidateName(meta.candidate))+'</b><small>'+esc(meta.candidate.party||'')+'</small></span>';
    }else if(meta.type==='party'){
      card.classList.add('is-match');
      match.innerHTML='<span class="colinha-match-placeholder">'+esc(meta.party.number)+'</span>'+
        '<span class="colinha-match-copy"><b class="legend-label">Voto de legenda</b><small>'+esc(meta.party.party||meta.party.party_name||'Partido')+'</small></span>';
    }else if(meta.type==='invalid'){
      card.classList.add('is-invalid');
      match.innerHTML='<span class="colinha-match-copy"><b>Número não identificado</b><small>Confira o número ou busque pelo nome.</small></span>';
    }else if(meta.type==='partial'){
      match.innerHTML='<span class="colinha-match-copy"><small>Continue digitando ou busque pelo nome.</small></span>';
    }else if(meta.type==='loading'){
      match.innerHTML='<span class="colinha-match-copy"><small>Carregando candidatura…</small></span>';
    }else if(meta.type==='unavailable'){
      card.classList.add('is-invalid');
      match.innerHTML='<span class="colinha-match-copy"><b>Fonte indisponível</b><small>Tente atualizar a colinha.</small></span>';
    }else{
      match.innerHTML='';
    }
    var clear=card.querySelector('[data-colinha-clear]');
    if(clear)clear.style.visibility=value?'visible':'hidden';
    renderSenateWarning();
  }
  function renderSenateWarning(){
    var warning=qs('[data-colinha-warning="senador2"]');
    if(!warning)return;
    var first=cstate.votes.senador1||'';
    var second=cstate.votes.senador2||'';
    var duplicate=first.length===3&&second.length===3&&first===second;
    warning.hidden=!duplicate;
    warning.textContent=duplicate?'Atenção: o mesmo número foi anotado nas duas vagas do Senado. O segundo voto para a mesma candidatura seria anulado.':'';
  }
  async function fetchDirectory(office,scope,force){
    var key=dirKey(office,scope);
    if(!force&&cstate.directories[key])return cstate.directories[key];
    try{
      var data=await requestJson('/api/candidates?office='+encodeURIComponent(office)+'&scope='+encodeURIComponent(scope)+'&include_poll=false',{cache:force?'no-cache':'force-cache',timeout:12000});
      cstate.directories[key]=data;
      delete cstate.directoryErrors[key];
      return data;
    }catch(error){
      cstate.directoryErrors[key]=String(error&&error.message||'Falha ao carregar candidaturas');
      throw error;
    }
  }
  async function loadDirectories(force){
    var uniq={};
    COLINHA_SLOTS.forEach(function(slot){
      var scope=scopeFor(slot);
      uniq[dirKey(slot.office,scope)]={office:slot.office,scope:scope};
    });
    var jobs=Object.keys(uniq).map(function(key){
      var item=uniq[key];
      return fetchDirectory(item.office,item.scope,force).catch(function(){return null});
    });
    var results=await Promise.all(jobs);
    var failures=results.filter(function(item){return !item}).length;
    if(failures)toast(failures===1?'Uma lista de candidaturas não pôde ser carregada.':failures+' listas de candidaturas não puderam ser carregadas.');
  }
  function updateColinhaHeader(){
    var code=state.scope==='br'?'BR':String(state.scope||'').toUpperCase();
    qs('#colinhaUfText').textContent=code;
  }
  async function loadColinha(force){
    updateColinhaHeader();
    var needs=qs('#colinhaStateNeeded');
    var body=qs('#colinhaBody');
    if(state.scope==='br'){
      needs.hidden=false;
      body.hidden=true;
      return;
    }
    needs.hidden=true;
    body.hidden=false;
    var seq=++cstate.loadSeq;
    var scope=state.scope;
    if(cstate.scope!==scope){
      cstate.scope=scope;
      await restoreVotes(scope);
      if(seq!==cstate.loadSeq)return;
    }
    renderCards();
    var loading=qs('#colinhaLoading');
    loading.hidden=false;
    try{
      await loadDirectories(!!force);
      if(seq!==cstate.loadSeq||state.scope!==scope)return;
      renderCards();
    }catch(e){
      if(seq!==cstate.loadSeq)return;
      toast('Não foi possível atualizar todas as candidaturas.');
    }finally{
      if(seq===cstate.loadSeq)loading.hidden=true;
    }
  }
  async function openPicker(key){
    var slot=SLOT_BY_KEY[key];
    if(!slot)return;
    if(state.scope==='br'&&slot.office!=='presidente'){
      qs('#locationTrigger').click();
      return;
    }
    cstate.pickerSlot=key;
    qs('#colinhaPickerTitle').textContent='Escolher · '+slotLabel(slot);
    qs('#colinhaPickerSearch').value='';
    qs('#colinhaPickerList').innerHTML='<div class="colinha-picker-empty">Carregando candidaturas…</div>';
    openSheet('#colinhaPickerSheet');
    var loaded=true;
    try{await fetchDirectory(slot.office,scopeFor(slot),false)}catch(e){loaded=false}
    if(cstate.pickerSlot!==key)return;
    if(!loaded){
      qs('#colinhaPickerList').innerHTML='<div class="colinha-picker-empty">Não foi possível carregar as candidaturas agora. Feche esta janela, atualize a colinha e tente novamente.</div>';
      return;
    }
    renderPicker('');
    setTimeout(function(){try{qs('#colinhaPickerSearch').focus()}catch(e){}},220);
  }
  function renderPicker(query){
    var slot=SLOT_BY_KEY[cstate.pickerSlot];
    if(!slot)return;
    var q=norm(query||'');
    var list=candidatesFor(slot).filter(function(c){
      if(!q)return true;
      return [c.ballot_name,c.name,c.number,c.party,c.party_name].some(function(v){return norm(v).indexOf(q)>=0});
    }).slice(0,100);
    cstate.pickerResults=list;
    var root=qs('#colinhaPickerList');
    if(!list.length){
      root.innerHTML='<div class="colinha-picker-empty">Nenhuma candidatura encontrada para esta busca.</div>';
      return;
    }
    root.innerHTML=list.map(function(c,i){
      return '<button class="colinha-picker-item" type="button" data-colinha-pick="'+i+'">'+
        photoHtml(c,'colinha-picker-photo')+
        '<span class="colinha-picker-copy"><b>'+esc(candidateName(c))+'</b><small>'+esc(c.party||c.party_name||'')+'</small></span>'+
        '<span class="colinha-picker-num">'+esc(c.number||'')+'</span>'+
      '</button>';
    }).join('');
  }
  function choosePicker(index){
    var candidate=cstate.pickerResults[index];
    var key=cstate.pickerSlot;
    var slot=SLOT_BY_KEY[key];
    if(!candidate||!slot)return;
    cstate.votes[key]=cleanNumber(candidate.number,slot.digits);
    persistVotes();
    updateCard(key);
    closeSheets();
    toast(candidateName(candidate)+' adicionado à colinha.');
  }
  function toast(message){
    var el=document.querySelector('.colinha-toast');
    if(!el){
      el=document.createElement('div');
      el.className='colinha-toast';
      document.body.appendChild(el);
    }
    el.textContent=String(message||'');
    el.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer=setTimeout(function(){el.classList.remove('show')},2200);
  }
  function setBusy(button,busy,text){
    if(!button)return;
    if(!button.dataset.originalText)button.dataset.originalText=button.textContent.trim();
    button.disabled=!!busy;
    button.textContent=busy?(text||'Gerando...'):button.dataset.originalText;
  }

  function roundedRect(ctx,x,y,w,h,r){
    r=Math.min(r,w/2,h/2);
    ctx.beginPath();
    ctx.moveTo(x+r,y);
    ctx.arcTo(x+w,y,x+w,y+h,r);
    ctx.arcTo(x+w,y+h,x,y+h,r);
    ctx.arcTo(x,y+h,x,y,r);
    ctx.arcTo(x,y,x+w,y,r);
    ctx.closePath();
  }
  function fittedText(ctx,text,x,y,maxWidth,startSize,minSize,weight,color){
    var size=startSize;
    ctx.fillStyle=color||'#111';
    while(size>minSize){
      ctx.font=(weight||700)+' '+size+'px Arial, sans-serif';
      if(ctx.measureText(text).width<=maxWidth)break;
      size-=1;
    }
    ctx.fillText(text,x,y);
  }
  function loadCanvasImage(url){
    return new Promise(function(resolve){
      if(!url)return resolve(null);
      var img=new Image();
      img.crossOrigin='anonymous';
      var done=false;
      var finish=function(value){if(done)return;done=true;resolve(value)};
      img.onload=function(){finish(img)};
      img.onerror=function(){finish(null)};
      img.src=url;
      setTimeout(function(){finish(null)},5000);
    });
  }
  async function drawCandidateImage(ctx,candidate,x,y,w,h){
    var img=await loadCanvasImage(candidate&&candidate.photo);
    ctx.save();
    roundedRect(ctx,x,y,w,h,13);
    ctx.clip();
    if(img){
      var ratio=Math.max(w/img.naturalWidth,h/img.naturalHeight);
      var sw=w/ratio,sh=h/ratio;
      var sx=(img.naturalWidth-sw)/2;
      var sy=Math.max(0,(img.naturalHeight-sh)*0.12);
      ctx.drawImage(img,sx,sy,sw,sh,x,y,w,h);
    }else{
      ctx.fillStyle='#ececec';
      ctx.fillRect(x,y,w,h);
      ctx.fillStyle='#707070';
      ctx.font='800 28px Arial';
      ctx.textAlign='center';
      ctx.textBaseline='middle';
      ctx.fillText(initials(candidateName(candidate)),x+w/2,y+h/2);
      ctx.textAlign='left';
      ctx.textBaseline='alphabetic';
    }
    ctx.restore();
  }
  async function generateImageBlob(){
    if(state.scope==='br')throw new Error('Selecione um estado primeiro.');
    await loadDirectories(false);
    var canvas=qs('#colinhaCanvas');
    canvas.width=1080;
    canvas.height=1350;
    var ctx=canvas.getContext('2d');
    ctx.fillStyle='#ffffff';
    ctx.fillRect(0,0,canvas.width,canvas.height);

    ctx.fillStyle='#5a5a5d';
    ctx.font='800 23px Arial';
    ctx.letterSpacing='2px';
    ctx.fillText('ELEIÇÕES 2026 · '+String(state.scope).toUpperCase(),64,72);
    ctx.fillStyle='#111111';
    ctx.font='900 58px Arial';
    ctx.fillText('MINHA COLINHA',64,142);
    ctx.fillStyle='#f2b33c';
    ctx.fillRect(338,157,345,9);
    ctx.fillStyle='#dedede';
    ctx.fillRect(64,188,952,2);

    var y=236;
    for(var r=0;r<COLINHA_SLOTS.length;r++){
      var slot=COLINHA_SLOTS[r];
      var value=cstate.votes[slot.key]||'';
      var meta=voteMeta(slot,value);
      ctx.fillStyle='#55585d';
      ctx.font='800 22px Arial';
      ctx.fillText(slotLabel(slot),64,y);

      var boxY=y+20,boxW=62,boxH=72,gap=10;
      for(var i=0;i<slot.digits;i++){
        var digit=value[i]||'';
        ctx.lineWidth=4;
        ctx.strokeStyle=(meta.type==='candidate'||meta.type==='party')?'#3e9a4c':'#222222';
        roundedRect(ctx,64+i*(boxW+gap),boxY,boxW,boxH,9);
        ctx.stroke();
        if(digit){
          ctx.fillStyle='#111';
          ctx.font='900 38px Arial';
          ctx.textAlign='center';
          ctx.textBaseline='middle';
          ctx.fillText(digit,64+i*(boxW+gap)+boxW/2,boxY+boxH/2+2);
          ctx.textAlign='left';
          ctx.textBaseline='alphabetic';
        }
      }

      var infoX=64+slot.digits*(boxW+gap)+22;
      if(meta.type==='candidate'){
        fittedText(ctx,candidateName(meta.candidate),infoX,boxY+31,420,27,17,900,'#111');
        ctx.font='500 20px Arial';
        ctx.fillStyle='#666';
        ctx.fillText(meta.candidate.party||'',infoX,boxY+59);
        await drawCandidateImage(ctx,meta.candidate,906,boxY-5,106,106);
      }else if(meta.type==='party'){
        fittedText(ctx,'VOTO DE LEGENDA',infoX,boxY+31,420,24,16,900,'#111');
        ctx.font='500 20px Arial';
        ctx.fillStyle='#666';
        ctx.fillText(meta.party.party||meta.party.party_name||'',infoX,boxY+59);
      }else if(value){
        fittedText(ctx,'NÚMERO A CONFERIR',infoX,boxY+31,420,21,15,800,'#666');
      }
      y+=168;
    }

    ctx.fillStyle='#e1e1e1';
    ctx.fillRect(64,1240,952,2);
    ctx.fillStyle='#555';
    ctx.font='500 20px Arial';
    ctx.fillText('Resultado Eleições 2026 · colinha pessoal para consulta e treinamento',64,1285);
    ctx.font='500 16px Arial';
    ctx.fillStyle='#777';
    ctx.fillText('Confira os números antes de votar. Ferramenta independente; não registra voto.',64,1316);

    return await new Promise(function(resolve,reject){
      canvas.toBlob(function(blob){if(blob)resolve(blob);else reject(new Error('Falha ao gerar imagem.'))},'image/png',1);
    });
  }
  function downloadBlob(blob){
    var url=URL.createObjectURL(blob);
    var a=document.createElement('a');
    a.href=url;
    a.download='minha-colinha-'+String(state.scope||'br').toUpperCase()+'.png';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function(){URL.revokeObjectURL(url)},5000);
  }
  function prefersNativeFileSheet(){
    var ua=String(navigator.userAgent||'');
    return !!navigator.share&&(/iPhone|iPad|iPod/i.test(ua)||!!window.Telegram?.WebApp);
  }
  async function saveImage(button){
    try{
      setBusy(button,true,'Gerando...');
      var blob=await generateImageBlob();
      var file=new File([blob],'minha-colinha-'+String(state.scope||'br').toUpperCase()+'.png',{type:'image/png'});
      if(prefersNativeFileSheet()&&(!navigator.canShare||navigator.canShare({files:[file]}))){
        await navigator.share({title:'Minha Colinha · Eleições 2026',files:[file]});
        toast('Escolha “Salvar imagem” ou “Salvar em Arquivos” no menu do aparelho.');
      }else{
        downloadBlob(blob);
        toast('Imagem da colinha gerada.');
      }
    }catch(e){
      toast(e.message||'Não foi possível gerar a imagem.');
    }finally{
      setBusy(button,false);
    }
  }
  async function shareImage(button){
    try{
      setBusy(button,true,'Gerando...');
      var blob=await generateImageBlob();
      var file=new File([blob],'minha-colinha-'+String(state.scope||'br').toUpperCase()+'.png',{type:'image/png'});
      if(navigator.share&&(!navigator.canShare||navigator.canShare({files:[file]}))){
        await navigator.share({title:'Minha Colinha · Eleições 2026',text:'Minha colinha para as Eleições 2026.',files:[file]});
      }else{
        downloadBlob(blob);
        toast('Compartilhamento de arquivo não disponível; a imagem foi salva.');
      }
    }catch(e){
      if(e&&e.name!=='AbortError')toast(e.message||'Não foi possível compartilhar.');
    }finally{
      setBusy(button,false);
    }
  }

  function urnSlot(){
    return COLINHA_SLOTS[ustate.index]||COLINHA_SLOTS[0];
  }
  function urnMeta(slot){
    if(ustate.blank)return {type:'blank',value:''};
    return voteMeta(slot,ustate.digits);
  }
  function urnCanConfirm(slot,meta){
    if(ustate.blank)return true;
    if(meta.type==='candidate'||meta.type==='invalid')return ustate.digits.length===slot.digits;
    if(meta.type==='party')return ustate.digits.length===2||ustate.digits.length===slot.digits;
    return false;
  }
  function urnActualNumber(meta){
    if(meta.type==='blank')return 'BRANCO';
    return ustate.digits||'';
  }
  function firstSenateRecord(){
    for(var i=0;i<ustate.actual.length;i++)if(ustate.actual[i].key==='senador1')return ustate.actual[i];
    return null;
  }
  function duplicateSecondSenate(slot,meta){
    if(slot.key!=='senador2'||meta.type==='blank'||!ustate.digits)return false;
    var first=firstSenateRecord();
    return !!(first&&first.number===ustate.digits&&ustate.digits.length===3);
  }
  function urnCandidateHtml(meta){
    if(meta.type==='candidate'){
      return '<div class="urna-candidate-card">'+
        '<div class="urna-candidate-copy"><small>Nome:</small><b>'+esc(candidateName(meta.candidate))+'</b><small style="margin-top:13px">Partido:</small><span>'+esc(meta.candidate.party||'')+'</span></div>'+
        photoHtml(meta.candidate,'urna-candidate-photo')+
      '</div>';
    }
    if(meta.type==='party'){
      return '<div class="urna-candidate-copy"><small>Voto para:</small><b>LEGENDA</b><span>'+esc(meta.party.party||meta.party.party_name||'Partido')+'</span><span class="urna-kind">Voto proporcional de legenda</span></div>';
    }
    if(meta.type==='blank')return '<div class="urna-white-vote">VOTO EM BRANCO</div>';
    if(meta.type==='invalid')return '<div class="urna-invalid-vote">NÚMERO NÃO IDENTIFICADO</div>';
    return '<div class="urna-empty-candidate">Digite o número para visualizar a candidatura.</div>';
  }
  function urnComparison(slot,meta){
    var complete=urnCanConfirm(slot,meta);
    if(!complete)return null;
    var expected=cstate.votes[slot.key]||'';
    var actual=urnActualNumber(meta);
    if(duplicateSecondSenate(slot,meta)){
      return {kind:'warn',html:'Atenção: é a mesma candidatura do 1º voto para o Senado. O segundo voto seria anulado.'};
    }
    if(expected&&actual===expected){
      return {kind:'ok',html:'✓ É o número que você anotou na sua colinha.'};
    }
    if(!expected){
      return {kind:'warn',html:'× Você não anotou um número para este cargo na colinha.'};
    }
    if(meta.type==='invalid'){
      return {kind:'bad',html:'× Número não identificado. Na sua colinha você anotou '+esc(expected)+'.'};
    }
    return {kind:'warn',html:'× Não é o número que você anotou. Sua colinha: '+esc(expected)+'.'};
  }
  function renderUrna(){
    if(ustate.finished){renderUrnaFinish();return}
    qs('#urnaVotingView').hidden=false;
    qs('#urnaFinishView').hidden=true;
    var slot=urnSlot();
    var meta=urnMeta(slot);
    qs('#urnaOfficeTitle').textContent=slotLabel(slot).replace(' · 1º VOTO','').replace(' · 2º VOTO','');
    qs('#urnaScopeLabel').textContent='Eleições 2026 · '+String(state.scope).toUpperCase();

    var progress='';
    for(var p=0;p<COLINHA_SLOTS.length;p++){
      progress+='<i class="'+(p<ustate.index?'done':p===ustate.index?'current':'')+'"></i>';
    }
    qs('#urnaProgress').innerHTML=progress;

    var boxes='';
    for(var i=0;i<slot.digits;i++){
      var digit=ustate.digits[i]||'';
      boxes+='<span class="urna-number-box '+(digit?'filled ':'')+(i===ustate.digits.length&&!ustate.blank?'active':'')+'">'+esc(digit)+'</span>';
    }
    qs('#urnaNumberBoxes').innerHTML=boxes;
    qs('#urnaCandidate').innerHTML=urnCandidateHtml(meta);

    var cmp=urnComparison(slot,meta);
    var cmpEl=qs('#urnaCompare');
    if(cmp){
      cmpEl.hidden=false;
      cmpEl.className='urna-compare '+cmp.kind;
      cmpEl.innerHTML=cmp.html;
    }else{
      cmpEl.hidden=true;
      cmpEl.className='urna-compare';
      cmpEl.innerHTML='';
    }
    qs('#urnaConfirm').disabled=!urnCanConfirm(slot,meta);
  }
  function playTone(kind){
    if(!ustate.sound)return;
    try{
      var AudioCtx=window.AudioContext||window.webkitAudioContext;
      if(!AudioCtx)return;
      if(!playTone.ctx)playTone.ctx=new AudioCtx();
      var ctx=playTone.ctx;
      if(ctx.state==='suspended')ctx.resume();
      var osc=ctx.createOscillator();
      var gain=ctx.createGain();
      var freq=kind==='confirm'?880:kind==='correct'?310:540;
      osc.frequency.value=freq;
      gain.gain.setValueAtTime(.055,ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(.001,ctx.currentTime+.075);
      osc.connect(gain);gain.connect(ctx.destination);
      osc.start();osc.stop(ctx.currentTime+.08);
    }catch(e){}
  }
  function urnDigit(digit){
    var slot=urnSlot();
    if(ustate.finished)return;
    if(ustate.blank){ustate.blank=false;ustate.digits=''}
    if(ustate.digits.length>=slot.digits)return;
    ustate.digits+=String(digit);
    playTone('digit');
    renderUrna();
  }
  function urnBlank(){
    if(ustate.finished)return;
    ustate.blank=true;
    ustate.digits='';
    playTone('digit');
    renderUrna();
  }
  function urnCorrect(){
    if(ustate.finished)return;
    ustate.blank=false;
    ustate.digits='';
    playTone('correct');
    renderUrna();
  }
  function urnConfirm(){
    if(ustate.finished)return;
    var slot=urnSlot();
    var meta=urnMeta(slot);
    if(!urnCanConfirm(slot,meta))return;
    var expected=cstate.votes[slot.key]||'';
    var number=urnActualNumber(meta);
    var duplicate=duplicateSecondSenate(slot,meta);
    var error=!expected||number!==expected||duplicate;
    var reason='';
    if(duplicate)reason='Segundo voto repetido para o Senado';
    else if(!expected)reason='Número não conferido na colinha';
    else if(number!==expected)reason='Número diferente da colinha';
    ustate.actual.push({
      key:slot.key,
      office:slotLabel(slot),
      number:number,
      expected:expected,
      type:meta.type,
      candidate:meta.candidate||null,
      party:meta.party||null,
      error:error,
      reason:reason
    });
    playTone('confirm');
    if(ustate.index>=COLINHA_SLOTS.length-1){
      ustate.finished=true;
      renderUrna();
      return;
    }
    ustate.index+=1;
    ustate.digits='';
    ustate.blank=false;
    renderUrna();
  }
  function summaryName(record){
    if(record.candidate)return candidateName(record.candidate);
    if(record.type==='party')return 'VOTO DE LEGENDA · '+String((record.party&&(record.party.party||record.party.party_name))||'PARTIDO');
    if(record.type==='blank')return 'VOTO EM BRANCO';
    return 'Número não identificado';
  }
  function summaryPhoto(record){
    if(record.candidate)return photoHtml(record.candidate,'urna-summary-photo');
    return '<span class="urna-summary-avatar">'+esc(record.type==='blank'?'B':record.type==='party'?'L':'?')+'</span>';
  }
  function renderUrnaFinish(){
    qs('#urnaVotingView').hidden=true;
    var root=qs('#urnaFinishView');
    root.hidden=false;
    var errors=ustate.actual.filter(function(r){return r.error}).length;
    var seconds=Math.max(1,Math.round((Date.now()-ustate.startedAt)/1000));
    var rows=ustate.actual.map(function(r){
      return '<div class="urna-summary-row'+(r.error?' error':'')+'">'+
        summaryPhoto(r)+
        '<span class="urna-summary-copy"><small>'+esc(r.office)+'</small><b>'+esc(summaryName(r))+'</b></span>'+
        '<span class="urna-summary-num">'+esc(r.number||'—')+'</span>'+
      '</div>';
    }).join('');
    root.innerHTML=
      '<h2>FIM</h2>'+
      '<div class="urna-finish-score">'+(errors===0?'Sem divergências':errors+' divergência'+(errors===1?'':'s')+' pelo caminho')+'</div>'+
      '<div class="urna-finish-sub">6 etapas em '+seconds+' segundo'+(seconds===1?'':'s')+'</div>'+
      '<div class="urna-summary">'+rows+'</div>'+
      '<div class="urna-finish-actions">'+
        '<button type="button" id="urnaRestart">Treinar de novo</button>'+
        '<button type="button" class="primary" id="urnaBackColinha">Voltar à colinha</button>'+
        '<button type="button" id="urnaFinishSave">Salvar imagem</button>'+
        '<button type="button" class="primary" id="urnaFinishShare">Compartilhar</button>'+
      '</div>';
    qs('#urnaRestart').onclick=resetUrna;
    qs('#urnaBackColinha').onclick=function(){closeUrna();showView('colinha')};
    qs('#urnaFinishSave').onclick=function(e){saveImage(e.currentTarget)};
    qs('#urnaFinishShare').onclick=function(e){shareImage(e.currentTarget)};
  }
  function resetUrna(){
    ustate.index=0;
    ustate.digits='';
    ustate.blank=false;
    ustate.actual=[];
    ustate.startedAt=Date.now();
    ustate.finished=false;
    renderUrna();
  }
  async function startUrna(){
    if(state.scope==='br'){
      qs('#locationTrigger').click();
      return;
    }
    if(cstate.scope!==state.scope)await loadColinha(false);
    await loadDirectories(false);
    openFull('#urnaModal');
    resetUrna();
  }
  function closeUrna(){
    qs('#urnaModal').classList.remove('show');
    document.body.style.overflow='';
  }

  var baseShowView=showView;
  showView=function(name){
    if(name!=='colinha'){
      qs('#colinhaView').hidden=true;
      return baseShowView(name);
    }
    qs('#resultsView').hidden=true;
    qs('#pollsView').hidden=true;
    qs('#favoritesView').hidden=true;
    qs('#colinhaView').hidden=false;
    qsa('.nav-btn').forEach(function(b){b.classList.toggle('active',b.dataset.view==='colinha')});
    window.scrollTo({top:0,behavior:'smooth'});
    loadColinha(false);
  };

  var baseSelectState=selectState;
  selectState=function(scope,source){
    var returnToColinha=!qs('#colinhaView').hidden;
    baseSelectState(scope,source||'manual');
    if(returnToColinha){
      showView('colinha');
      loadColinha(true);
    }
  };

  function openColinhaStatePicker(){
    var title=qs('#locationSheet .sheet-head h2');
    if(title)title.textContent='Selecionar estado para a colinha';
    updateGeoStatus();
    renderStates();
    var brazil=qs('#stateList [data-state="br"]');
    if(brazil)brazil.remove();
    openSheet('#locationSheet');
  }
  qs('#colinhaUfBtn').onclick=openColinhaStatePicker;
  qs('#colinhaChooseState').onclick=openColinhaStatePicker;
  qs('#colinhaTrain').onclick=startUrna;
  qs('#colinhaSaveImage').onclick=function(e){saveImage(e.currentTarget)};
  qs('#colinhaShare').onclick=function(e){shareImage(e.currentTarget)};
  qs('#colinhaPickerSearch').oninput=function(e){renderPicker(e.target.value)};
  qs('#colinhaPickerList').onclick=function(e){
    var item=e.target.closest('[data-colinha-pick]');
    if(item)choosePicker(Number(item.dataset.colinhaPick));
  };

  qs('#colinhaCards').addEventListener('input',function(e){
    var input=e.target.closest('[data-colinha-input]');
    if(!input)return;
    var key=input.dataset.colinhaInput;
    var slot=SLOT_BY_KEY[key];
    var value=cleanNumber(input.value,slot.digits);
    input.value=value;
    cstate.votes[key]=value;
    persistVotes();
    updateCard(key);
  });
  function syncKeyboardState(){
    var vv=window.visualViewport;
    var active=document.activeElement;
    var colinhaInput=active&&active.matches&&active.matches('[data-colinha-input]');
    var reduced=vv?vv.height<window.innerHeight-120:false;
    document.body.classList.toggle('keyboard-open',!!(colinhaInput&&reduced&&!qs('#colinhaView').hidden));
  }
  if(window.visualViewport){
    window.visualViewport.addEventListener('resize',syncKeyboardState);
    window.visualViewport.addEventListener('scroll',syncKeyboardState);
  }
  qs('#colinhaCards').addEventListener('focusin',function(e){
    var input=e.target.closest('[data-colinha-input]');
    if(!input)return;
    var card=input.closest('.colinha-card');
    if(card){
      card.classList.add('has-focus');
      updateCard(input.dataset.colinhaInput);
      setTimeout(function(){try{card.scrollIntoView({block:'center',behavior:'smooth'})}catch(err){};syncKeyboardState()},120);
    }
  });
  qs('#colinhaCards').addEventListener('focusout',function(e){
    var input=e.target.closest('[data-colinha-input]');
    if(!input)return;
    var card=input.closest('.colinha-card');
    if(card){card.classList.remove('has-focus');updateCard(input.dataset.colinhaInput)}
    setTimeout(syncKeyboardState,80);
  });
  qs('#colinhaCards').addEventListener('click',function(e){
    var clear=e.target.closest('[data-colinha-clear]');
    if(clear){
      var key=clear.dataset.colinhaClear;
      cstate.votes[key]='';
      persistVotes();
      updateCard(key);
      return;
    }
    var search=e.target.closest('[data-colinha-search]');
    if(search)openPicker(search.dataset.colinhaSearch);
  });

  qsa('[data-urna-digit]').forEach(function(button){button.onclick=function(){urnDigit(button.dataset.urnaDigit)}});
  qs('#urnaBlank').onclick=urnBlank;
  qs('#urnaCorrect').onclick=urnCorrect;
  qs('#urnaConfirm').onclick=urnConfirm;
  qs('#urnaClose').onclick=closeUrna;
  qs('#urnaSound').onclick=function(){
    ustate.sound=!ustate.sound;
    qs('#urnaSound').classList.toggle('off',!ustate.sound);
    qs('#urnaSound').setAttribute('aria-pressed',ustate.sound?'true':'false');
    qs('#urnaSound').textContent=ustate.sound?'Som':'Mudo';
  };

  var refresh=qs('#refreshBtn');
  refresh.onclick=function(){
    refresh.classList.add('refreshing');
    setTimeout(function(){refresh.classList.remove('refreshing')},360);
    if(!qs('#colinhaView').hidden)loadColinha(true);
    else if(!qs('#resultsView').hidden)loadResults();
    else if(!qs('#pollsView').hidden)loadPoll(false);
    else renderFavorites();
  };

  updateColinhaHeader();
})();
