const qs=s=>document.querySelector(s),qsa=s=>[...document.querySelectorAll(s)];
const fmt=n=>new Intl.NumberFormat('pt-BR').format(Number(n||0));
const money=n=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(n||0));
const pct=n=>Number(n||0).toLocaleString('pt-BR',{minimumFractionDigits:0,maximumFractionDigits:2})+'%';
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const norm=s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
const palette=['#3d7df6','#65c66b','#f59c32','#ef5045','#9a58dc','#61b7e7','#e593ae','#d7b859'];
const states={br:'Brasil',ac:'Acre',al:'Alagoas',ap:'Amapá',am:'Amazonas',ba:'Bahia',ce:'Ceará',df:'Distrito Federal',es:'Espírito Santo',go:'Goiás',ma:'Maranhão',mt:'Mato Grosso',ms:'Mato Grosso do Sul',mg:'Minas Gerais',pa:'Pará',pb:'Paraíba',pr:'Paraná',pe:'Pernambuco',pi:'Piauí',rj:'Rio de Janeiro',rn:'Rio Grande do Norte',rs:'Rio Grande do Sul',ro:'Rondônia',rr:'Roraima',sc:'Santa Catarina',sp:'São Paulo',se:'Sergipe',to:'Tocantins'};
const officeLabels={presidente:'Presidente',governador:'Governador',senador:'Senador',federal:'Deputado Federal',estadual:'Deputado Estadual/Distrital'};
const stateFlagSlugs={ac:'acre',al:'alagoas',ap:'amapa',am:'amazonas',ba:'bahia',ce:'ceara',df:'distrito-federal',es:'espirito-santo',go:'goias',ma:'maranhao',mt:'mato-grosso',ms:'mato-grosso-do-sul',mg:'minas-gerais',pa:'para',pb:'paraiba',pr:'parana',pe:'pernambuco',pi:'piaui',rj:'rio-de-janeiro',rn:'rio-grande-do-norte',rs:'rio-grande-do-sul',ro:'rondonia',rr:'roraima',sc:'santa-catarina',sp:'sao-paulo',se:'sergipe',to:'tocantins'};
const STATE_FLAG_BASE='https://raw.githubusercontent.com/iconolatry/brazilian-states-flags/da53c2f1fe28ed67d2049b624f21536c337fd118/svg';
const POLL_OFFICES=new Set(['presidente','governador','senador']);
const CACHE_PREFIX='resultado_eleicoes_2026:';
const DIRECTORY_TTL=30*60*1000;
const POLL_TTL=10*60*1000;

let savedScope=localStorage.getItem('election_scope')||'br';
if(!states[savedScope])savedScope='br';
let savedScopeSource=localStorage.getItem('election_scope_source')||'';
if(savedScope!=='br'&&!savedScopeSource){
  savedScopeSource='manual';
  localStorage.setItem('election_scope_source','manual');
}
let state={
  scope:savedScope,office:'presidente',result:null,poll:null,selectedCandidate:null,catalog:null,
  directory:null,directoryContext:null,candidateLimit:24,candidateQuery:'',pendingOffice:null,pendingPollOffice:null,
  detailContext:null,requestSeq:0,pollContext:null,resultContext:null
};

function syncTelegramViewport(){
  const tg=window.Telegram?.WebApp;if(!tg)return;
  const safe=tg.safeAreaInset||{},content=tg.contentSafeAreaInset||{};
  const top=Math.max(Number(safe.top)||0,Number(content.top)||0);
  const right=Math.max(Number(safe.right)||0,Number(content.right)||0);
  const bottom=Math.max(Number(safe.bottom)||0,Number(content.bottom)||0);
  const left=Math.max(Number(safe.left)||0,Number(content.left)||0);
  const root=document.documentElement;
  root.style.setProperty('--tg-safe-top',top+'px');
  root.style.setProperty('--tg-safe-right',right+'px');
  root.style.setProperty('--tg-safe-bottom',bottom+'px');
  root.style.setProperty('--tg-safe-left',left+'px');
  if(Number(tg.viewportStableHeight)>0)root.style.setProperty('--tg-stable-height',Number(tg.viewportStableHeight)+'px');
  document.body.classList.toggle('tg-fullscreen',!!tg.isFullscreen);
}
function requestTelegramFullscreen(){
  const tg=window.Telegram?.WebApp;if(!tg)return;
  try{
    tg.ready();
    tg.expand();
    tg.setHeaderColor('#080c12');
    tg.setBackgroundColor('#080c12');
    if(typeof tg.setBottomBarColor==='function')tg.setBottomBarColor('#080c12');
    if(typeof tg.disableVerticalSwipes==='function')tg.disableVerticalSwipes();
    syncTelegramViewport();
    if(typeof tg.requestFullscreen==='function'&&!tg.isFullscreen)tg.requestFullscreen();
  }catch(e){syncTelegramViewport()}
}
function initTelegramMiniApp(){
  const tg=window.Telegram?.WebApp;if(!tg)return;
  try{
    if(typeof tg.onEvent==='function'){
      tg.onEvent('safeAreaChanged',syncTelegramViewport);
      tg.onEvent('contentSafeAreaChanged',syncTelegramViewport);
      tg.onEvent('viewportChanged',syncTelegramViewport);
      tg.onEvent('fullscreenChanged',syncTelegramViewport);
      tg.onEvent('fullscreenFailed',()=>{syncTelegramViewport();try{tg.expand()}catch(e){}});
      tg.onEvent('activated',()=>{syncTelegramViewport();if(!tg.isFullscreen)requestTelegramFullscreen()});
    }
  }catch(e){}
  requestTelegramFullscreen();
  document.addEventListener('pointerdown',()=>{if(!tg.isFullscreen)requestTelegramFullscreen()},{once:true,capture:true});
}
initTelegramMiniApp();

function isStateOffice(office){return office!=='presidente'}
async function requestJson(url,{cache='no-cache',timeout=8000}={}){
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const response=await fetch(url,{cache,signal:controller.signal});
    let data=null;
    try{data=await response.json()}catch(e){}
    if(!response.ok)throw new Error(data?.detail||`HTTP ${response.status}`);
    return data;
  }catch(error){
    if(error?.name==='AbortError')throw new Error('Tempo de resposta excedido');
    throw error;
  }finally{clearTimeout(timer)}
}
function stateFlagUrl(scope){if(scope==='br')return'https://flagcdn.com/br.svg';if(scope==='rj')return'https://thumb.wikimedia.org/wikipedia/commons/thumb/7/73/Bandeira_do_estado_do_Rio_de_Janeiro.svg/330px-Bandeira_do_estado_do_Rio_de_Janeiro.svg.png';const slug=stateFlagSlugs[scope];return slug?`${STATE_FLAG_BASE}/${slug}.svg`:''}
function flagBadgeHtml(scope,cls='state-flag'){const url=stateFlagUrl(scope),uf=scope==='br'?'BR':String(scope||'').toUpperCase();return url?`<span class="${cls}" data-uf="${esc(uf)}"><img src="${esc(url)}" alt="" loading="lazy" decoding="async" onerror="this.remove();this.parentElement.classList.add('flag-fallback')"></span>`:`<span class="${cls} flag-fallback" data-uf="${esc(uf)}"></span>`}
function cloudGet(key){return new Promise(resolve=>{try{const cloud=window.Telegram?.WebApp?.CloudStorage;if(!cloud?.getItem)return resolve('');cloud.getItem(key,(err,value)=>resolve(err?'':String(value||'')))}catch(e){resolve('')}})}
function cloudSet(key,value){try{const cloud=window.Telegram?.WebApp?.CloudStorage;if(cloud?.setItem)cloud.setItem(key,String(value||''),()=>{})}catch(e){}}
function persistScope(scope,source='manual'){localStorage.setItem('election_scope',scope);localStorage.setItem('election_scope_source',source);savedScopeSource=source;cloudSet('election_scope',scope);cloudSet('election_scope_source',source)}
function updateLocationUI(){const name=states[state.scope]||state.scope.toUpperCase();qs('#locationName').textContent=name;const target=qs('#locationFlag');if(target){const uf=state.scope==='br'?'BR':state.scope.toUpperCase();target.dataset.uf=uf;target.classList.remove('flag-fallback');target.innerHTML=`<img src="${esc(stateFlagUrl(state.scope))}" alt="" decoding="async" onerror="this.remove();this.parentElement.classList.add('flag-fallback')">`}}
function updateGeoStatus(message=''){const btn=qs('#geoBtn'),title=qs('#geoTitle'),small=qs('#geoStatus');if(!btn||!title||!small)return;btn.classList.remove('detecting','saved');if(message){small.textContent=message;return}const source=localStorage.getItem('election_scope_source')||savedScopeSource;if(state.scope!=='br'&&(source==='detected'||source==='manual')){btn.classList.add('saved');title.textContent=source==='detected'?'Localização salva':'Estado salvo';small.textContent=`${states[state.scope]} · toque para alterar`;return}title.textContent='Usar minha localização';small.textContent='Detectar estado automaticamente'}
async function restoreCloudLocation(){if(localStorage.getItem('election_scope'))return;const cloudScope=await cloudGet('election_scope');if(!states[cloudScope]||cloudScope==='br')return;const source=await cloudGet('election_scope_source');state.scope=cloudScope;savedScopeSource=source||'manual';localStorage.setItem('election_scope',cloudScope);localStorage.setItem('election_scope_source',savedScopeSource);updateLocationUI();updateGeoStatus();if(!qs('#resultsView').hidden)loadResults()}
function cacheRead(key,ttl){try{const raw=localStorage.getItem(CACHE_PREFIX+key);if(!raw)return null;const item=JSON.parse(raw);if(Date.now()-item.at>ttl){localStorage.removeItem(CACHE_PREFIX+key);return null}return item.data}catch(e){return null}}
function cacheWrite(key,data){try{localStorage.setItem(CACHE_PREFIX+key,JSON.stringify({at:Date.now(),data}));const keys=[];for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k&&k.startsWith(CACHE_PREFIX+'dir:')){try{keys.push([k,JSON.parse(localStorage.getItem(k)).at||0])}catch(e){}}}keys.sort((a,b)=>b[1]-a[1]);keys.slice(6).forEach(([k])=>localStorage.removeItem(k))}catch(e){}}
function directoryKey(office=state.office,scope=state.scope){return `dir:${office}:${office==='presidente'?'br':scope}:v5`}
function pollKey(office,scope,institute='',question=''){return `poll:${office}:${scope}:${institute||'auto'}:${question||'default'}:v4`}

function setGauge(valid,voidPct,subPct,nullPct,blankPct){
  const vals=[valid,voidPct,subPct,nullPct,blankPct].map(x=>Math.max(0,Number(x||0)));
  const sum=vals.reduce((a,b)=>a+b,0)||1,scaled=sum>100?vals.map(x=>x/sum*100):vals;
  const ids=['#gaugeValid','#gaugeVoid','#gaugeSubJudice','#gaugeNull','#gaugeBlank'];let offset=0;
  scaled.forEach((value,i)=>{const v=Math.max(0,Math.min(100,value));const el=qs(ids[i]);if(el){el.style.strokeDasharray=`${v} ${100-v}`;el.style.strokeDashoffset=`-${offset}`}offset+=v});
}
function initials(name){return (name||'?').split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0]).join('').toUpperCase()}
function avatarHtml(name,photo,cls='avatar'){
  if(!photo)return `<div class="${cls} avatar-placeholder">${esc(initials(name))}</div>`;
  const hero=cls==='avatar'||cls==='detail-photo';
  return `<img class="${cls}" src="${esc(photo)}" alt="" ${hero?'loading="eager" fetchpriority="high"':'loading="lazy"'} decoding="async" referrerpolicy="no-referrer">`;
}
function favoriteContext(c){return{office:c._office||state.detailContext?.office||state.office,scope:c._scope||state.detailContext?.scope||state.scope}}
function favoriteKey(c){const ctx=favoriteContext(c);return `${ctx.office}:${ctx.scope}:${c.id||c.candidate_id||c.number||''}:${norm(c.ballot_name||c.name)}`}
function getFavorites(){try{return JSON.parse(localStorage.getItem('election_favorites')||'{}')}catch(e){return{}}}
function saveFavorite(c){const f=getFavorites(),k=favoriteKey(c),ctx=favoriteContext(c);if(f[k])delete f[k];else f[k]={...c,_office:ctx.office,_scope:ctx.scope};localStorage.setItem('election_favorites',JSON.stringify(f));return!!f[k]}
function isFavorite(c){return!!getFavorites()[favoriteKey(c)]}

function setActiveOffice(office){
  state.office=office;
  qsa('.chip').forEach(x=>x.classList.toggle('active',x.dataset.office===office));
  qs('#officeTitle').textContent=officeLabels[office];
  qs('#candidateSearch').value='';state.candidateQuery='';state.candidateLimit=24;
}
function requireStateForOffice(office,kind='results'){
  if(!isStateOffice(office)||state.scope!=='br')return false;
  if(kind==='polls')state.pendingPollOffice=office;else state.pendingOffice=office;
  const title=qs('#locationSheet .sheet-head h2');if(title)title.textContent=`Selecionar estado para ${officeLabels[office]||office}`;
  renderStates();openSheet('#locationSheet');return true;
}

function applyDirectory(directory,fromCache=false){
  state.directory=directory;
  state.directoryContext=`${state.office}:${state.scope}`;
  qs('#locationName').textContent=states[state.scope]||state.scope.toUpperCase();
  qs('#officeTitle').textContent=officeLabels[state.office];
  qs('#sectionsInfo').textContent=`${directory.count||0} candidaturas`;
  qs('#candidateSearchWrap').style.display=(directory.count||0)>20?'flex':'none';
  renderRealCandidates();
  renderSummaryFromState();
}
function matchPollChoice(candidate,choices){
  const names=[norm(candidate.ballot_name),norm(candidate.name)].filter(Boolean);
  const optionNames=x=>[norm(x.name),norm(x.ballot_name)].filter(Boolean);
  return choices.find(x=>optionNames(x).some(v=>names.includes(v)))||choices.find(x=>optionNames(x).some(p=>p&&names.some(n=>p.length>=5&&n.length>=5&&(p.includes(n)||n.includes(p)))));
}
function mergePollIntoDirectory(poll){
  const pollContext=`${poll.office}:${poll.scope}`;
  if(!state.directory||state.directoryContext!==pollContext)return;
  const choices=poll.choices||[];
  state.directory.candidates.forEach(c=>{const m=matchPollChoice(c,choices);c.estimate_percentage=m?Number(m.percentage):0;c.estimate_history=m?(m.history||[]):[]});
  state.directory.estimate={available:true,source:'G1',institute:poll.institute,question:poll.question,date:poll.latest_date,margin_error_points:poll.margin_error_points,sample_size:poll.sample_size,field_period:poll.field_period,registrations:poll.registrations,source_url:poll.source_url};
  state.directory.candidates.sort((a,b)=>Number(b.estimate_percentage||0)-Number(a.estimate_percentage||0)||String(a.ballot_name).localeCompare(String(b.ballot_name),'pt-BR'));
  renderRealCandidates();
  renderSummaryFromState();
}
async function loadEstimateInBackground(seq){
  if(!POLL_OFFICES.has(state.office))return;
  if(isStateOffice(state.office)&&state.scope==='br')return;
  const office=state.office,scope=state.office==='presidente'?state.scope:state.scope;
  const context=`${office}:${scope}`;
  const cached=cacheRead(pollKey(office,scope),POLL_TTL);
  if(cached&&seq===state.requestSeq&&state.office===office&&state.scope===scope){state.poll=cached;state.pollContext=context;mergePollIntoDirectory(cached)}
  let url=`/api/g1/poll?office=${encodeURIComponent(office)}&scope=${encodeURIComponent(scope)}&round=1`;
  if(office==='presidente'&&scope==='br')url+='&institute=datafolha';
  try{
    const poll=await requestJson(url,{cache:'no-cache',timeout:8000});
    if(seq!==state.requestSeq||state.office!==office||state.scope!==scope)return;
    state.poll=poll;state.pollContext=context;cacheWrite(pollKey(office,scope),poll);mergePollIntoDirectory(poll);
  }catch(e){
    if(!cached&&seq===state.requestSeq&&state.directory){state.directory.estimate={available:false};state.directory.candidates.forEach(c=>{c.estimate_percentage=0});renderRealCandidates();renderSummaryFromState()}
  }
}
async function loadOfficialResultInBackground(seq){
  const office=state.office,scope=state.scope,context=`${office}:${scope}`;
  if(office!=='presidente'&&scope==='br'){state.result=null;state.resultContext=null;renderSummaryFromState();return}
  try{
    const d=await requestJson(`/api/result?scope=${encodeURIComponent(scope)}&office=${encodeURIComponent(office)}`,{cache:'no-cache',timeout:7000});
    if(seq!==state.requestSeq||state.office!==office||state.scope!==scope)return;
    const hasOfficialVotes=!d.simulation&&(Number(d.total_votes||0)>0||Number(d.sections_counted||0)>0||Number(d.sections_counted_pct||0)>0);
    if(hasOfficialVotes){
      state.result=d;state.resultContext=context;renderOfficialSummary(d);
    }else{
      state.result=null;state.resultContext=null;renderSummaryFromState();
    }
  }catch(e){
    if(seq!==state.requestSeq)return;
    state.result=null;state.resultContext=null;renderSummaryFromState();
  }
}
async function loadResults(){
  if(requireStateForOffice(state.office,'results'))return;
  const seq=++state.requestSeq,office=state.office,scope=state.scope,context=`${office}:${scope}`;
  qs('#summaryCard').style.display='block';
  state.result=null;state.resultContext=null;
  renderPollSummary(null);
  if(state.pollContext!==context){state.poll=null;state.pollContext=null}
  const key=directoryKey(office,scope),cached=cacheRead(key,DIRECTORY_TTL);
  if(cached){applyDirectory(cached,true)}else{
    state.directory=null;state.directoryContext=null;
    qs('#candidateArea').innerHTML='<div class="card coming skeleton" style="height:180px"></div>';
  }
  loadOfficialResultInBackground(seq);
  loadEstimateInBackground(seq);
  try{
    const directory=await requestJson(`/api/candidates?office=${encodeURIComponent(office)}&scope=${encodeURIComponent(scope)}&include_poll=false`,{cache:'no-cache',timeout:8000});
    if(seq!==state.requestSeq||state.office!==office||state.scope!==scope)return;
    cacheWrite(key,directory);
    applyDirectory(directory,false);
    if(state.poll&&state.pollContext===`${office}:${scope}`)mergePollIntoDirectory(state.poll);
    renderSummaryFromState();
  }catch(e){
    if(!cached&&seq===state.requestSeq){qs('#candidateArea').innerHTML=`<div class="card coming"><b>Não foi possível carregar as candidaturas</b><br>${esc(e.message)}</div>`;renderPollSummary(null)}
  }
}

function candidateEstimateText(c){return pct(Number(c.estimate_percentage||0))}
function renderRealCandidates(){
  const directory=state.directory;if(!directory)return;
  const query=norm(state.candidateQuery);
  let arr=(directory.candidates||[]).filter(c=>!query||[c.ballot_name,c.name,c.number,c.party,c.occupation].some(v=>norm(v).includes(query)));
  const visible=arr.slice(0,state.candidateLimit),hasEstimate=!!directory.estimate?.available;let html='';
  if(hasEstimate&&visible.length){
    const hero=visible.slice(0,2),rest=visible.slice(2);
    html+=hero.map((c,i)=>`<article class="card candidate-card candidate-hero animate" data-real-candidate="${i}"><div class="candidate-main">${avatarHtml(c.ballot_name,c.photo)}<div><div class="cand-name">${esc(c.ballot_name)}</div><div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div><div class="estimate-badge">${esc(directory.estimate.institute||'Pesquisa')} · ${esc(directory.estimate.date||'')}</div></div><div class="cand-pct">${candidateEstimateText(c)}</div></div><div class="progress"><i style="width:${Math.max(0,Math.min(100,Number(c.estimate_percentage||0)))}%"></i></div><div class="cand-votes">Intenção de voto · não é apuração</div></article>`).join('');
    html+=rest.map((c,idx)=>`<article class="candidate-row real animate" data-real-candidate="${idx+2}">${avatarHtml(c.ballot_name,c.photo,'mini-avatar')}<div><div class="row-name">${esc(c.ballot_name)}</div><div class="row-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div><div class="row-bar"><i style="width:${Math.max(0,Math.min(100,Number(c.estimate_percentage||0)))}%"></i></div></div><div class="row-pct">${candidateEstimateText(c)}</div></article>`).join('');
  }else{
    html=visible.map((c,i)=>`<article class="candidate-row real animate" data-real-candidate="${i}">${avatarHtml(c.ballot_name,c.photo,'mini-avatar')}<div><div class="row-name">${esc(c.ballot_name)}</div><div class="row-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div><div class="no-estimate">${esc(c.occupation||c.party_name||'Candidatura registrada')}</div></div><div class="row-pct">0%</div></article>`).join('');
  }
  qs('#candidateArea').innerHTML=html||'<div class="card coming"><b>Nenhuma candidatura encontrada</b><br>Tente outro termo de busca.</div>';
  qsa('[data-real-candidate]').forEach(el=>el.onclick=()=>openCandidate(visible[Number(el.dataset.realCandidate)],{office:state.office,scope:state.scope}));
  const more=qs('#candidateMore');more.style.display=arr.length>state.candidateLimit?'block':'none';more.textContent=`Mostrar mais (${Math.max(0,arr.length-state.candidateLimit)})`;
}

function setSummaryLabels(labels,values,colors=['#3d7df6','#65c66b','#f59c32','#9a58dc','#61b7e7']){
  const valueIds=['#validVotes','#voidVotes','#subJudiceVotes','#nullVotes','#blankVotes'];
  const labelIds=['#summaryLabel1','#summaryLabel2','#summaryLabel3','#summaryLabel4','#summaryLabel5'];
  const gaugeIds=['#gaugeValid','#gaugeVoid','#gaugeSubJudice','#gaugeNull','#gaugeBlank'];
  for(let i=0;i<5;i++){
    qs(valueIds[i]).textContent=values[i]??'0%';
    qs(labelIds[i]).textContent=labels[i]||'—';
    const sw=qs(`#summarySwatch${i+1}`);if(sw)sw.style.background=colors[i];
    const gauge=qs(gaugeIds[i]);if(gauge)gauge.style.stroke=colors[i];
  }
}
function renderPollSummary(poll){
  const card=qs('#summaryCard');card.style.display='block';card.classList.add('poll-mode');card.classList.remove('official-mode');
  const directory=state.directory,hasPoll=!!poll&&!!directory?.estimate?.available;
  const candidates=(directory?.candidates||[]).slice().sort((a,b)=>Number(b.estimate_percentage||0)-Number(a.estimate_percentage||0)).slice(0,5);
  while(candidates.length<5)candidates.push(null);
  const values=candidates.map(c=>Number(c?.estimate_percentage||0));
  const labels=candidates.map(c=>c?.ballot_name||'—');
  setGauge(...values);
  qs('#summaryModeTitle').textContent='PESQUISA';
  qs('#totalVotes').textContent=hasPoll?`${poll.institute||'Pesquisa'} · ${poll.latest_date||''}`:'0%';
  qs('#summaryShareLabel').textContent='Maior intenção de voto';
  qs('#validShare').textContent=pct(Math.max(0,...values));
  setSummaryLabels(labels,values.map(v=>pct(v)));
}
function renderOfficialSummary(d){
  const card=qs('#summaryCard');card.style.display='block';card.classList.add('official-mode');card.classList.remove('poll-mode');
  const total=Number(d.total_votes||0);
  const validPct=total?Number(d.valid_votes||0)/total*100:0;
  const voidPct=total?Number(d.void_votes||0)/total*100:0;
  const subPct=total?Number(d.void_sub_judice_votes||0)/total*100:0;
  const nullPct=total?Number(d.null_votes||0)/total*100:0;
  const blankPct=total?Number(d.blank_votes||0)/total*100:0;
  setGauge(validPct,voidPct,subPct,nullPct,blankPct);
  qs('#summaryModeTitle').textContent='VOTAÇÃO';
  qs('#totalVotes').textContent=`${fmt(total)} votos`;
  qs('#summaryShareLabel').textContent='Votos a candidatos concorrentes';
  qs('#validShare').textContent=pct(validPct);
  setSummaryLabels(['Votos válidos','Anulados','Sub judice','Nulos','Em branco'],[fmt(d.valid_votes),fmt(d.void_votes),fmt(d.void_sub_judice_votes),fmt(d.null_votes),fmt(d.blank_votes)],['#3569a8','#ef5045','#f59c32','#8d73d1','#e593ae']);
}
function renderSummaryFromState(){
  const context=`${state.office}:${state.scope}`;
  if(state.result&&state.resultContext===context)renderOfficialSummary(state.result);
  else renderPollSummary(state.pollContext===context?state.poll:null);
}
function renderSummary(){renderSummaryFromState()}
function openAnalysis(){
  const context=`${state.office}:${state.scope}`;
  const official=!!(state.result&&state.resultContext===context);
  const d=state.result,directory=state.directory,poll=state.pollContext===context?state.poll:null;
  qs('#analysisPlace').textContent=`● ${states[state.scope]||state.scope.toUpperCase()} · ${officeLabels[state.office]}`;
  let arr=[];
  if(official){
    qs('#analysisDistributionTitle').textContent='Distribuição dos votos';
    qs('#analysisStatsTitle').textContent='Estatísticas da eleição';
    qs('#eligibleText').textContent=`${fmt(d.electorate_total)} eleitores aptos`;
    qs('#participationLabel').textContent=`Participação: ${pct(d.turnout_pct)}`;
    qs('#participationBar').style.width=Math.min(100,Number(d.turnout_pct||0))+'%';
    const total=Number(d.total_votes||0)||1;
    const stats=[
      ['Votos válidos',fmt(d.valid_votes),pct(Number(d.valid_votes||0)/total*100),'#45a84d'],
      ['Votos brancos',fmt(d.blank_votes),pct(Number(d.blank_votes||0)/total*100),'#848d99'],
      ['Votos nulos',fmt(d.null_votes),pct(Number(d.null_votes||0)/total*100),'#ef5045'],
      ['Abstenções',fmt(d.abstention),pct(d.abstention_pct),'#f59c32']
    ];
    qs('#analysisStats').innerHTML=stats.map(s=>`<div class="statbox"><small><i class="legend-dot" style="background:${s[3]}"></i>${s[0]}</small><strong>${s[1]}</strong><em>${s[2]}</em></div>`).join('');
    qs('#analysisSourceNote').innerHTML='<span>ⓘ</span><span>Dados de apuração reproduzidos da fonte oficial configurada.</span>';
    arr=(d.candidates||[]).slice(0,8).map(c=>({ballot_name:c.ballot_name,estimate_percentage:Number(c.percentage||0)}));
  }else{
    qs('#analysisDistributionTitle').textContent='Distribuição da pesquisa';
    qs('#analysisStatsTitle').textContent='Detalhes da pesquisa';
    const hasPoll=!!poll&&!!directory?.estimate?.available;
    const all=(directory?.candidates||[]).slice().sort((a,b)=>Number(b.estimate_percentage||0)-Number(a.estimate_percentage||0));
    arr=all.slice(0,8).map(c=>({ballot_name:c.ballot_name,estimate_percentage:Number(c.estimate_percentage||0)}));
    const max=Math.max(0,...arr.map(c=>Number(c.estimate_percentage||0)));
    qs('#eligibleText').textContent=hasPoll&&poll.sample_size?`${fmt(poll.sample_size)} entrevistas`:'Sem pesquisa disponível para este cargo/local';
    qs('#participationLabel').textContent=`Maior percentual: ${pct(max)}`;
    qs('#participationBar').style.width=Math.min(100,max)+'%';
    const margin=poll?.margin_error_points!=null?`±${String(poll.margin_error_points).replace('.',',')} p.p.`:'—';
    const stats=[
      ['Instituto',poll?.institute||'—',poll?.latest_date||'—','#3d7df6'],
      ['Margem de erro',margin,poll?.field_period||'—','#65c66b'],
      ['Candidaturas',fmt(directory?.count||0),officeLabels[state.office],'#f59c32'],
      ['Dados disponíveis',hasPoll?'Sim':'Não',hasPoll?'Pesquisa':'0%','#9a58dc']
    ];
    qs('#analysisStats').innerHTML=stats.map(s=>`<div class="statbox"><small><i class="legend-dot" style="background:${s[3]}"></i>${s[0]}</small><strong>${esc(s[1])}</strong><em>${esc(s[2])}</em></div>`).join('');
    qs('#analysisSourceNote').innerHTML=hasPoll?'<span>ⓘ</span><span>Pesquisa de intenção de voto — não é apuração.</span>':'<span>ⓘ</span><span>Sem pesquisa integrada para este cargo/local; percentuais exibidos como 0%.</span>';
  }
  const sum=arr.reduce((a,c)=>a+Number(c.estimate_percentage||0),0);
  if(sum>0){
    let cursor=0,parts=[];
    arr.forEach((c,i)=>{const share=Number(c.estimate_percentage||0)/sum*100;parts.push(`${palette[i%palette.length]} ${cursor}% ${cursor+share}%`);cursor+=share});
    qs('#candidateDonut').style.background=`conic-gradient(${parts.join(',')})`;
  }else{
    qs('#candidateDonut').style.background='#252d38';
  }
  qs('#donutLegend').innerHTML=arr.map((c,i)=>`<div class="legend-item"><b><i class="legend-dot" style="background:${palette[i%palette.length]}"></i>${esc(c.ballot_name)}</b><span>${pct(c.estimate_percentage)}</span></div>`).join('')||'<div class="muted">Sem dados disponíveis.</div>';
  openFull('#analysisModal');
}

function openCandidate(c,context={office:state.office,scope:state.scope}){
  if(!c)return;state.selectedCandidate=c;state.detailContext=context;
  qs('#detailOffice').textContent=officeLabels[context.office]||c.office||'Candidato';qs('#detailPhotoWrap').innerHTML=avatarHtml(c.ballot_name,c.photo,'detail-photo');qs('#detailNumber').textContent=c.number||'—';qs('#detailName').textContent=c.ballot_name||'—';qs('#detailParty').textContent=[c.party,c.party_name].filter(Boolean).join(' · ')||'Partido não informado';qs('#detailPlace').textContent=states[context.scope]||String(context.scope||'').toUpperCase();qs('#detailHeart').classList.toggle('active',isFavorite(c));qs('#detailHeart').textContent=isFavorite(c)?'♥':'♡';qs('#detailStatus').style.display='none';
  const estimate=pct(Number(c.estimate_percentage||0));
  qs('#detailResult').innerHTML=`<div class="info-stat"><small>Pesquisa</small><b>${esc(estimate)}</b></div><div class="info-stat"><small>Número</small><b>${esc(c.number||'—')}</b></div><div class="info-stat"><small>Partido</small><b>${esc(c.party||'—')}</b></div><div class="info-stat"><small>Cargo</small><b>${esc(officeLabels[context.office]||c.office||'—')}</b></div>`;qs('#viceSection').style.display='none';fillCandidateDetail(c);openFull('#candidateModal');loadCandidateDetail(c,context);
}
function renderFinance(c){
  const card=qs('#financeCard');if(!card)return;
  if(c.finance===undefined){card.innerHTML='<div class="empty-detail">Carregando prestação de contas…</div>';return}
  const f=c.finance||{},hasMovement=Number(f.receipts_count||0)>0||Number(f.expenses_count||0)>0;
  if(!hasMovement){card.innerHTML='<div class="empty-detail">Nenhuma receita ou despesa encontrada para esta candidatura na base pública até a última atualização.</div>';return}
  const donors=(f.top_donors||[]),suppliers=(f.top_suppliers||[]);
  card.innerHTML=`
    <div class="finance-grid">
      <div class="finance-stat finance-income"><small>Receitas</small><strong>${money(f.receipts_total)}</strong><span>${fmt(f.receipts_count)} lançamentos</span></div>
      <div class="finance-stat finance-expense"><small>Despesas contratadas</small><strong>${money(f.expenses_total)}</strong><span>${fmt(f.expenses_count)} lançamentos</span></div>
      <div class="finance-stat"><small>Teto de gastos</small><strong>${Number(f.spending_limit||0)>0?money(f.spending_limit):'—'}</strong><span>Limite informado</span></div>
    </div>
    ${donors.length?`<div class="finance-subtitle">Principais doadores</div><div class="finance-list">${donors.map(x=>`<div class="finance-row"><span>${esc(x.name)}</span><b>${money(x.value)}</b></div>`).join('')}</div>`:''}
    ${suppliers.length?`<div class="finance-subtitle">Principais fornecedores</div><div class="finance-list">${suppliers.map(x=>`<div class="finance-row"><span>${esc(x.name)}</span><b>${money(x.value)}</b></div>`).join('')}</div>`:''}
    <div class="finance-note">Valores de prestação de contas eleitoral publicados na base pública consultada.</div>
  `;
}
function fillCandidateDetail(c){
  qs('#personalFullName').textContent=c.name||'—';qs('#personalBirth').textContent=c.birth_date||'—';qs('#personalBirthplace').textContent=[c.birth_city,c.birth_state].filter(Boolean).join(' - ')||'—';qs('#personalEducation').textContent=c.education||'—';qs('#personalOccupation').textContent=c.occupation||'—';qs('#personalCivil').textContent=c.civil_status||'—';qs('#personalGender').textContent=c.gender||'—';qs('#personalRace').textContent=c.race||'—';qs('#detailCoalition').innerHTML=c.coalition||c.coalition_composition?`<b>${esc(c.coalition||'Composição')}</b><br><span class="muted">${esc(c.coalition_composition||'')}</span>`:'Sem coligação/federação informada.';
  if(c.assets===undefined){qs('#assetsCard').innerHTML='<div class="empty-detail">Carregando patrimônio declarado…</div>'}
  else{
    const assets=c.assets||[];
    qs('#assetsCard').innerHTML=assets.length?`<div class="asset-total"><small>Patrimônio total declarado</small><strong>${money(c.assets_total)}</strong><span class="muted">${assets.length} bens declarados</span></div><div class="asset-list">${assets.slice(0,18).map(a=>{const title=a.type||a.description||'Bem declarado',subtitle=a.type&&a.description&&a.type!==a.description?a.description:'';return `<div class="asset-item"><b><span>${esc(title)}</span><span>${money(a.value)}</span></b>${subtitle?`<small>${esc(subtitle)}</small>`:''}</div>`}).join('')}</div>`:'<div class="empty-detail">Nenhum bem declarado encontrado na base pública.</div>';
  }
  renderFinance(c);
}

async function loadCandidateDetail(c,context){
  try{const d=await requestJson(`/api/candidates/${encodeURIComponent(context.office)}/${encodeURIComponent(context.scope)}/${encodeURIComponent(c.id)}?v=3`,{cache:'no-store',timeout:8000});if(state.selectedCandidate?.id!==c.id)return;state.selectedCandidate={...c,...d,_office:context.office,_scope:context.scope};fillCandidateDetail(state.selectedCandidate)}catch(e){qs('#assetsCard').innerHTML='<div class="empty-detail">Não foi possível carregar os detalhes agora.</div>';qs('#financeCard').innerHTML='<div class="empty-detail">Não foi possível carregar a prestação de contas agora.</div>'}
}

function renderFavorites(){
  const values=Object.values(getFavorites());
  if(!values.length){qs('#favoritesList').innerHTML='<div class="favorites-empty"><div style="font-size:44px;margin-bottom:10px">♡</div>Nenhum candidato salvo ainda.<br>Abra um candidato e toque no coração para favoritar.</div>';return}
  qs('#favoritesList').innerHTML=values.map((c,i)=>`<article class="card candidate-card candidate-hero" data-favorite="${i}"><div class="candidate-main">${avatarHtml(c.ballot_name||c.name,c.photo||'')}<div><div class="cand-name">${esc(c.ballot_name||c.name)}</div><div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div><div class="muted" style="margin-top:7px">${esc(officeLabels[c._office]||c._office||'')}</div></div><div class="cand-pct">${c.estimate_percentage==null?'—':pct(c.estimate_percentage)}</div></div></article>`).join('');
  qsa('[data-favorite]').forEach(el=>el.onclick=()=>{const c=values[Number(el.dataset.favorite)];openCandidate(c,{office:c._office||'presidente',scope:c._scope||'br'})});
}
function showView(name){
  qs('#resultsView').hidden=name!=='results';qs('#pollsView').hidden=name!=='polls';qs('#favoritesView').hidden=name!=='favorites';qsa('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.view===name));
  if(name==='polls'){if(state.scope==='br')qs('#pollOffice').value='presidente';loadPoll(true)}else if(name==='favorites')renderFavorites();else if(name==='results'&&state.directoryContext!==`${state.office}:${state.scope}`)loadResults();
  window.scrollTo({top:0,behavior:'smooth'});
}

async function loadCatalog(){try{state.catalog=await requestJson('/api/g1/catalog',{cache:'force-cache',timeout:6000})}catch(e){state.catalog=null}}
function availableInstitutes(office=qs('#pollOffice').value,scope=state.scope){
  const label={presidente:'Presidente',governador:'Governador',senador:'Senador'}[office],cargo=state.catalog?.cargos?.find(x=>x.nome===label),loc=cargo?.localidades?.find(x=>String(x.uf).toLowerCase()===scope);
  return loc?.turnos?.['1-turno']||[];
}
function syncInstitutes(){const sel=qs('#pollInstitute'),cur=sel.value,available=availableInstitutes();sel.innerHTML='<option value="">Automático</option>'+available.map(x=>`<option value="${esc(String(x.nome).toLowerCase())}">${esc(x.nome)}</option>`).join('');if([...sel.options].some(o=>o.value===cur))sel.value=cur;else if(qs('#pollOffice').value==='presidente'&&state.scope==='br'&&available.some(x=>String(x.nome).toLowerCase()==='datafolha'))sel.value='datafolha';else sel.value=''}
function fillPollFilters(d){const q=qs('#pollQuestion'),qc=q.value;q.innerHTML=(d.available_questions||[]).map(x=>`<option value="${esc(x.code)}">${esc(x.label)}</option>`).join('')||`<option value="${esc(d.question_code||'')}">${esc(d.question||'Pergunta')}</option>`;q.value=[...q.options].some(o=>o.value===qc)?qc:(d.question_code||q.options[0]?.value||'');const s=qs('#pollStratum'),sv=s.value,groups={};(d.strata||[]).forEach(x=>(groups[x.group||'Outros']??=[]).push(x));s.innerHTML='<option value="">Total</option>'+Object.entries(groups).map(([g,items])=>`<optgroup label="${esc(g)}">${items.map(x=>`<option value="${esc((x.group_slug||g)+'|'+x.label)}">${esc(x.label)}</option>`).join('')}</optgroup>`).join('');if([...s.options].some(o=>o.value===sv))s.value=sv}
async function highQualityDirectoryForPoll(office,scope){
  const key=directoryKey(office,scope),cached=cacheRead(key,DIRECTORY_TTL);if(cached)return cached;
  try{const d=await requestJson(`/api/candidates?office=${encodeURIComponent(office)}&scope=${encodeURIComponent(scope)}&include_poll=false`,{cache:'force-cache',timeout:7000});cacheWrite(key,d);return d}catch(e){}return null;
}
function mergeHighQualityPollPhotos(poll,directory){
  if(!directory)return poll;(poll.choices||[]).forEach(choice=>{const c=matchPollChoice({ballot_name:choice.name,name:choice.name},directory.candidates||[]);if(c?.photo)choice.hq_photo=c.photo});return poll;
}
async function loadPoll(reset=false){
  const office=qs('#pollOffice').value;if(requireStateForOffice(office,'polls')){qs('#pollOffice').value='presidente';return}
  const scope=state.scope;syncInstitutes();
  const inst=qs('#pollInstitute').value,qcode=reset?'':qs('#pollQuestion').value;
  const cacheKey=pollKey(office,scope,inst,qcode);
  const cached=cacheRead(cacheKey,POLL_TTL);if(cached){state.poll=cached;state.pollContext=`${office}:${scope}`;fillPollFilters(cached);renderPoll()}
  if(!cached)qs('#pollList').innerHTML='<div class="card coming skeleton" style="height:150px"></div>';
  let url=`/api/g1/poll?office=${encodeURIComponent(office)}&scope=${encodeURIComponent(scope)}&round=1`;if(inst)url+=`&institute=${encodeURIComponent(inst)}`;if(qcode)url+=`&question=${encodeURIComponent(qcode)}`;
  try{
    const [d,dir]=await Promise.all([requestJson(url,{cache:'no-cache',timeout:9000}),highQualityDirectoryForPoll(office,scope)]);mergeHighQualityPollPhotos(d,dir);state.poll=d;state.pollContext=`${office}:${scope}`;cacheWrite(cacheKey,d);fillPollFilters(d);renderPoll();
  }catch(e){if(!cached){qs('#pollMeta').textContent='';qs('#pollList').innerHTML=`<div class="card coming"><b>Pesquisa indisponível</b><br>${esc(e.message)}</div>`;qs('#methodology').style.display='none'}}
}
function renderPoll(){
  const d=state.poll;if(!d)return;let choices=d.choices||[],margin=d.margin_error_points;const sv=qs('#pollStratum').value;if(sv){const st=(d.strata||[]).find(x=>(x.group_slug||x.group)+'|'+x.label===sv);if(st){choices=st.choices;margin=st.margin_error_points}}
  qs('#pollMeta').textContent=`${states[d.scope]||d.scope.toUpperCase()} · ${d.institute} · ${d.question} · ${d.latest_date||''}`;
  qs('#pollList').innerHTML=choices.map(c=>`<article class="card poll-card"><div class="poll-candidate">${avatarHtml(c.name,c.hq_photo||c.photo,'mini-avatar')}<div><div class="cand-name">${esc(c.name)}</div><div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div>${pollDelta(c)?`<div class="delta">${esc(pollDelta(c))}</div>`:''}</div><div class="poll-pct">${pct(c.percentage)}</div></div></article>`).join('');
  const lines=[];if(d.sample_size)lines.push(`<div><b>Amostra:</b> ${fmt(d.sample_size)} entrevistas</div>`);if(d.field_period)lines.push(`<div><b>Campo:</b> ${esc(d.field_period)}</div>`);if(margin!=null)lines.push(`<div><b>Margem de erro:</b> ±${esc(margin)} p.p.</div>`);if((d.registrations||[]).length)lines.push(`<div><b>Registro no TSE:</b> ${esc(d.registrations.join(', '))}</div>`);qs('#methodLines').innerHTML=lines.join('');qs('#methodology').style.display='block';
}
function pollDelta(c){const h=(c.history||[]).slice().sort((a,b)=>a.date.localeCompare(b.date));if(h.length<2)return'';const x=Number(h.at(-1).percentage)-Number(h.at(-2).percentage);if(Math.abs(x)<.05)return'Sem variação na rodada anterior';return`${x>0?'+':''}${x.toFixed(1).replace('.',',')} p.p. vs. rodada anterior`}

function renderStates(filter=''){
  const f=norm(filter),forbidBrazil=(state.pendingOffice&&isStateOffice(state.pendingOffice))||(state.pendingPollOffice&&isStateOffice(state.pendingPollOffice))||(!state.pendingOffice&&!state.pendingPollOffice&&isStateOffice(state.office));
  qs('#stateList').innerHTML=Object.entries(states).filter(([k])=>!(forbidBrazil&&k==='br')).filter(([,v])=>!f||norm(v).includes(f)).map(([k,v])=>`<div class="state-item ${k===state.scope?'selected':''}" data-state="${k}">${flagBadgeHtml(k)}<span class="state-name">${v}</span>${k===state.scope?'<span class="saved-mark">Selecionado</span>':''}<span class="chev">›</span></div>`).join('');
}
function selectState(scope,source='manual'){
  if(!states[scope])return;state.scope=scope;persistScope(scope,source);updateLocationUI();updateGeoStatus();
  const pendingOffice=state.pendingOffice,pendingPoll=state.pendingPollOffice;state.pendingOffice=null;state.pendingPollOffice=null;closeSheets();
  if(pendingOffice){setActiveOffice(pendingOffice);showView('results');loadResults();return}
  if(pendingPoll){qs('#pollOffice').value=pendingPoll;showView('polls');loadPoll(true);return}
  if(!qs('#resultsView').hidden){loadResults()}else if(!qs('#pollsView').hidden){loadPoll(true)}
}
async function detectLocation(){
  const btn=qs('#geoBtn'),title=qs('#geoTitle'),small=qs('#geoStatus');
  if(!navigator.geolocation){updateGeoStatus('Localização não disponível neste navegador');return}
  btn.classList.remove('saved');btn.classList.add('detecting');title.textContent='Usar minha localização';small.textContent='Detectando…';
  navigator.geolocation.getCurrentPosition(async p=>{try{
    const d=await requestJson(`/api/location/reverse?lat=${encodeURIComponent(p.coords.latitude)}&lon=${encodeURIComponent(p.coords.longitude)}`,{cache:'force-cache',timeout:7000}),code=String(d.uf||'').toLowerCase();
    if(states[code]){btn.classList.remove('detecting');btn.classList.add('saved');title.textContent='Localização salva';small.textContent=states[code];selectState(code,'detected');return}
    updateGeoStatus('Estado não identificado');
  }catch(e){updateGeoStatus('Não foi possível identificar o estado')}},()=>updateGeoStatus('Permissão de localização não concedida'),{enableHighAccuracy:false,timeout:7000,maximumAge:3600000});
}
function openSheet(id){qs('#overlay').classList.add('show');qs(id).classList.add('show');document.body.style.overflow='hidden'}
function closeSheets(){qs('#overlay').classList.remove('show');qsa('.sheet.show').forEach(x=>x.classList.remove('show'));document.body.style.overflow='';state.pendingOffice=null;state.pendingPollOffice=null;const title=qs('#locationSheet .sheet-head h2');if(title)title.textContent='Selecionar local'}
function openFull(id){qs(id).classList.add('show');document.body.style.overflow='hidden'}
function closeFull(el){el.closest('.fullscreen').classList.remove('show');document.body.style.overflow=''}

qs('#locationTrigger').onclick=()=>{const title=qs('#locationSheet .sheet-head h2');if(title)title.textContent='Selecionar local';updateGeoStatus();renderStates();openSheet('#locationSheet')};
qs('#overlay').onclick=closeSheets;qsa('[data-close]').forEach(x=>x.onclick=closeSheets);qs('#stateList').addEventListener('click',e=>{const item=e.target.closest('[data-state]');if(!item)return;e.preventDefault();selectState(item.dataset.state,'manual')});qs('#stateSearch').oninput=e=>renderStates(e.target.value);qs('#geoBtn').onclick=detectLocation;qs('#analysisBtn').onclick=openAnalysis;qsa('[data-full-close]').forEach(x=>x.onclick=()=>closeFull(x));
qs('#detailHeart').onclick=()=>{if(!state.selectedCandidate)return;const on=saveFavorite(state.selectedCandidate);qs('#detailHeart').classList.toggle('active',on);qs('#detailHeart').textContent=on?'♥':'♡'};
qsa('.nav-btn').forEach(b=>b.onclick=()=>showView(b.dataset.view));
qsa('.chip').forEach(b=>b.onclick=()=>{const office=b.dataset.office;if(requireStateForOffice(office,'results'))return;setActiveOffice(office);showView('results');loadResults()});
qs('#refreshBtn').onclick=()=>{const b=qs('#refreshBtn');b.classList.add('refreshing');setTimeout(()=>b.classList.remove('refreshing'),360);if(!qs('#resultsView').hidden)loadResults();else if(!qs('#pollsView').hidden)loadPoll(false);else renderFavorites()};
qs('#candidateSearch').oninput=e=>{state.candidateQuery=e.target.value;state.candidateLimit=24;renderRealCandidates()};qs('#candidateMore').onclick=()=>{state.candidateLimit+=24;renderRealCandidates()};
qs('#pollOffice').onchange=e=>{const office=e.target.value;if(requireStateForOffice(office,'polls')){e.target.value='presidente';return}qs('#pollQuestion').value='';qs('#pollStratum').value='';loadPoll(true)};
qs('#pollInstitute').onchange=()=>loadPoll(true);qs('#pollQuestion').onchange=()=>loadPoll(false);qs('#pollStratum').onchange=renderPoll;

updateLocationUI();updateGeoStatus();setActiveOffice('presidente');loadCatalog().then(syncInstitutes);loadResults();restoreCloudLocation();
setInterval(()=>{if(!document.hidden&&!qs('#resultsView').hidden&&state.office==='presidente')loadOfficialResultInBackground(state.requestSeq)},30000);
