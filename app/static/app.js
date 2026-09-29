const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
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
let state={
  scope:savedScope,office:'presidente',result:null,poll:null,selectedCandidate:null,catalog:null,
  directory:null,directoryContext:null,candidateLimit:24,candidateQuery:'',pendingOffice:null,pendingPollOffice:null,
  detailContext:null,requestSeq:0,pollContext:null
};

try{if(window.Telegram?.WebApp){Telegram.WebApp.ready();Telegram.WebApp.expand();Telegram.WebApp.setHeaderColor('#080c12');Telegram.WebApp.setBackgroundColor('#080c12')}}catch(e){}

function isStateOffice(office){return office!=='presidente'}
function stateFlagUrl(scope){if(scope==='br')return'https://flagcdn.com/br.svg';const slug=stateFlagSlugs[scope];return slug?`${STATE_FLAG_BASE}/${slug}.svg`:''}
function flagBadgeHtml(scope,cls='state-flag'){const url=stateFlagUrl(scope);return url?`<span class="${cls}"><img src="${esc(url)}" alt="" loading="lazy" decoding="async"></span>`:`<span class="${cls}"></span>`}
function cloudGet(key){return new Promise(resolve=>{try{const cloud=window.Telegram?.WebApp?.CloudStorage;if(!cloud?.getItem)return resolve('');cloud.getItem(key,(err,value)=>resolve(err?'':String(value||'')))}catch(e){resolve('')}})}
function cloudSet(key,value){try{const cloud=window.Telegram?.WebApp?.CloudStorage;if(cloud?.setItem)cloud.setItem(key,String(value||''),()=>{})}catch(e){}}
function persistScope(scope,source='manual'){localStorage.setItem('election_scope',scope);localStorage.setItem('election_scope_source',source);savedScopeSource=source;cloudSet('election_scope',scope);cloudSet('election_scope_source',source)}
function updateLocationUI(){const name=states[state.scope]||state.scope.toUpperCase();$('#locationName').textContent=name;const target=$('#locationFlag');if(target)target.innerHTML=`<img src="${esc(stateFlagUrl(state.scope))}" alt="" decoding="async">`}
function updateGeoStatus(message=''){const btn=$('#geoBtn'),title=$('#geoTitle'),small=$('#geoStatus');if(!btn||!title||!small)return;btn.classList.remove('detecting','saved');if(message){small.textContent=message;return}const source=localStorage.getItem('election_scope_source')||savedScopeSource;if(state.scope!=='br'&&(source==='detected'||source==='manual')){btn.classList.add('saved');title.textContent=source==='detected'?'Localização salva':'Estado salvo';small.textContent=`${states[state.scope]} · toque para alterar`;return}title.textContent='Usar minha localização';small.textContent='Detectar estado automaticamente'}
async function restoreCloudLocation(){if(localStorage.getItem('election_scope'))return;const cloudScope=await cloudGet('election_scope');if(!states[cloudScope]||cloudScope==='br')return;const source=await cloudGet('election_scope_source');state.scope=cloudScope;savedScopeSource=source||'manual';localStorage.setItem('election_scope',cloudScope);localStorage.setItem('election_scope_source',savedScopeSource);updateLocationUI();updateGeoStatus();if(!$('#resultsView').hidden)loadResults()}
function cacheRead(key,ttl){try{const raw=localStorage.getItem(CACHE_PREFIX+key);if(!raw)return null;const item=JSON.parse(raw);if(Date.now()-item.at>ttl){localStorage.removeItem(CACHE_PREFIX+key);return null}return item.data}catch(e){return null}}
function cacheWrite(key,data){try{localStorage.setItem(CACHE_PREFIX+key,JSON.stringify({at:Date.now(),data}));const keys=[];for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k&&k.startsWith(CACHE_PREFIX+'dir:')){try{keys.push([k,JSON.parse(localStorage.getItem(k)).at||0])}catch(e){}}}keys.sort((a,b)=>b[1]-a[1]);keys.slice(6).forEach(([k])=>localStorage.removeItem(k))}catch(e){}}
function directoryKey(office=state.office,scope=state.scope){return `dir:${office}:${office==='presidente'?'br':scope}:v5`}
function pollKey(office,scope){return `poll:${office}:${scope}:v3`}

function setGauge(valid,voidPct,subPct,nullPct,blankPct){
  const vals=[valid,voidPct,subPct,nullPct,blankPct].map(x=>Math.max(0,Number(x||0)));
  const sum=vals.reduce((a,b)=>a+b,0)||1,scaled=sum>100?vals.map(x=>x/sum*100):vals;
  const ids=['#gaugeValid','#gaugeVoid','#gaugeSubJudice','#gaugeNull','#gaugeBlank'];let offset=0;
  scaled.forEach((value,i)=>{const v=Math.max(0,Math.min(100,value));const el=$(ids[i]);if(el){el.style.strokeDasharray=`${v} ${100-v}`;el.style.strokeDashoffset=`-${offset}`}offset+=v});
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
  $$('.chip').forEach(x=>x.classList.toggle('active',x.dataset.office===office));
  $('#officeTitle').textContent=officeLabels[office];
  $('#candidateSearch').value='';state.candidateQuery='';state.candidateLimit=24;
}
function requireStateForOffice(office,kind='results'){
  if(!isStateOffice(office)||state.scope!=='br')return false;
  if(kind==='polls')state.pendingPollOffice=office;else state.pendingOffice=office;
  const title=$('#locationSheet .sheet-head h2');if(title)title.textContent=`Selecionar estado para ${officeLabels[office]||office}`;
  renderStates();openSheet('#locationSheet');return true;
}

function applyDirectory(directory,fromCache=false){
  state.directory=directory;
  state.directoryContext=`${state.office}:${state.scope}`;
  $('#locationName').textContent=states[state.scope]||state.scope.toUpperCase();
  $('#officeTitle').textContent=officeLabels[state.office];
  $('#sectionsInfo').textContent=`${directory.count||0} candidaturas`;
  const est=directory.estimate||{};
  $('#estimateMeta').textContent=est.available
    ?`${est.institute} · ${est.date||'última rodada'} · pesquisa de intenção de voto`
    :fromCache?'Candidaturas reais · atualizando…':'Candidaturas registradas no TSE';
  $('#candidateSearchWrap').style.display=(directory.count||0)>20?'flex':'none';
  renderRealCandidates();
}
function matchPollChoice(candidate,choices){
  const names=[norm(candidate.ballot_name),norm(candidate.name)].filter(Boolean);
  const optionNames=x=>[norm(x.name),norm(x.ballot_name)].filter(Boolean);
  return choices.find(x=>optionNames(x).some(v=>names.includes(v)))||choices.find(x=>optionNames(x).some(p=>p&&names.some(n=>p.length>=5&&n.length>=5&&(p.includes(n)||n.includes(p)))));
}
function mergePollIntoDirectory(poll){
  if(!state.directory)return;
  const choices=poll.choices||[];
  state.directory.candidates.forEach(c=>{const m=matchPollChoice(c,choices);c.estimate_percentage=m?Number(m.percentage):null;c.estimate_history=m?(m.history||[]):[]});
  state.directory.estimate={available:true,source:'G1',institute:poll.institute,question:poll.question,date:poll.latest_date,margin_error_points:poll.margin_error_points,sample_size:poll.sample_size,field_period:poll.field_period,registrations:poll.registrations,source_url:poll.source_url};
  state.directory.candidates.sort((a,b)=>(a.estimate_percentage==null)-(b.estimate_percentage==null)||(Number(b.estimate_percentage||0)-Number(a.estimate_percentage||0))||String(a.ballot_name).localeCompare(String(b.ballot_name),'pt-BR'));
  $('#estimateMeta').textContent=`${poll.institute} · ${poll.latest_date||'última rodada'} · pesquisa de intenção de voto`;
  renderRealCandidates();
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
    const r=await fetch(url,{cache:'no-cache'}),poll=await r.json();
    if(!r.ok)throw new Error(poll.detail||'Pesquisa indisponível');
    if(seq!==state.requestSeq||state.office!==office||state.scope!==scope)return;
    state.poll=poll;state.pollContext=context;cacheWrite(pollKey(office,scope),poll);mergePollIntoDirectory(poll);
  }catch(e){
    if(!cached&&seq===state.requestSeq&&state.directory){state.directory.estimate={available:false};$('#estimateMeta').textContent='Candidaturas registradas no TSE · sem pesquisa comparável disponível';renderRealCandidates()}
  }
}
async function loadOfficialResultInBackground(seq){
  if(state.office!=='presidente'){state.result=null;$('#summaryCard').style.display='none';return}
  const scope=state.scope;
  try{
    const r=await fetch(`/api/result?scope=${encodeURIComponent(scope)}`,{cache:'no-cache'}),d=await r.json();
    if(seq!==state.requestSeq||state.office!=='presidente'||state.scope!==scope)return;
    if(r.ok&&!d.simulation){state.result=d;$('#summaryCard').style.display='block';renderSummary()}else{state.result=null;$('#summaryCard').style.display='none'}
  }catch(e){state.result=null;$('#summaryCard').style.display='none'}
}
async function loadResults(){
  if(requireStateForOffice(state.office,'results'))return;
  const seq=++state.requestSeq,office=state.office,scope=state.scope;
  $('#summaryCard').style.display='none';
  const key=directoryKey(office,scope),cached=cacheRead(key,DIRECTORY_TTL);
  if(cached){applyDirectory(cached,true)}else{$('#candidateArea').innerHTML='<div class="card coming skeleton" style="height:180px"></div>';$('#estimateMeta').textContent='Carregando candidaturas…'}
  loadOfficialResultInBackground(seq);
  loadEstimateInBackground(seq);
  try{
    const r=await fetch(`/api/candidates?office=${encodeURIComponent(office)}&scope=${encodeURIComponent(scope)}&include_poll=false`,{cache:'no-cache'}),directory=await r.json();
    if(!r.ok)throw new Error(directory.detail||'Falha ao carregar candidaturas');
    if(seq!==state.requestSeq||state.office!==office||state.scope!==scope)return;
    cacheWrite(key,directory);
    applyDirectory(directory,false);
    if(state.poll&&state.pollContext===`${office}:${scope}`)mergePollIntoDirectory(state.poll);
  }catch(e){
    if(!cached&&seq===state.requestSeq){$('#candidateArea').innerHTML=`<div class="card coming"><b>Não foi possível carregar as candidaturas</b><br>${esc(e.message)}</div>`;$('#estimateMeta').textContent='Fonte temporariamente indisponível'}
  }
}

function candidateEstimateText(c){return c.estimate_percentage==null?'—':pct(c.estimate_percentage)}
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
    html=visible.map((c,i)=>`<article class="candidate-row real animate" data-real-candidate="${i}">${avatarHtml(c.ballot_name,c.photo,'mini-avatar')}<div><div class="row-name">${esc(c.ballot_name)}</div><div class="row-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div><div class="no-estimate">${esc(c.occupation||c.party_name||'Candidatura registrada')}</div></div><div class="row-pct">—</div></article>`).join('');
  }
  $('#candidateArea').innerHTML=html||'<div class="card coming"><b>Nenhuma candidatura encontrada</b><br>Tente outro termo de busca.</div>';
  $$('[data-real-candidate]').forEach(el=>el.onclick=()=>openCandidate(visible[Number(el.dataset.realCandidate)],{office:state.office,scope:state.scope}));
  const more=$('#candidateMore');more.style.display=arr.length>state.candidateLimit?'block':'none';more.textContent=`Mostrar mais (${Math.max(0,arr.length-state.candidateLimit)})`;
}

function renderSummary(){
  const d=state.result;if(!d)return;const total=Number(d.total_votes||0);
  const validPct=total?Number(d.valid_votes||0)/total*100:0,voidPct=total?Number(d.void_votes||0)/total*100:0,subPct=total?Number(d.void_sub_judice_votes||0)/total*100:0,nullPct=total?Number(d.null_votes||0)/total*100:0,blankPct=total?Number(d.blank_votes||0)/total*100:0;
  setGauge(validPct,voidPct,subPct,nullPct,blankPct);$('#totalVotes').textContent=`${fmt(total)} votos`;$('#validShare').textContent=pct(validPct);$('#validVotes').textContent=fmt(d.valid_votes);$('#voidVotes').textContent=fmt(d.void_votes);$('#subJudiceVotes').textContent=fmt(d.void_sub_judice_votes);$('#nullVotes').innerHTML=`${fmt(d.null_votes)} · <span class="muted">${pct(nullPct)}</span>`;$('#blankVotes').innerHTML=`${fmt(d.blank_votes)} · <span class="muted">${pct(blankPct)}</span>`;
}
function openAnalysis(){
  const d=state.result,directory=state.directory,real=(directory?.candidates||[]).filter(c=>c.estimate_percentage!=null).slice(0,6);
  if(!d&&!real.length)return;
  $('#analysisPlace').textContent=`● ${states[state.scope]||state.scope.toUpperCase()}`;
  if(d){$('#eligibleText').textContent=`${fmt(d.electorate_total)} eleitores aptos`;$('#participationLabel').textContent=`Participação: ${pct(d.turnout_pct)}`;$('#participationBar').style.width=Math.min(100,Number(d.turnout_pct||0))+'%';const total=Number(d.total_votes||0)||1;const stats=[['Votos válidos',d.valid_votes,Number(d.valid_votes||0)/total*100,'#45a84d'],['Votos brancos',d.blank_votes,Number(d.blank_votes||0)/total*100,'#848d99'],['Votos nulos',d.null_votes,Number(d.null_votes||0)/total*100,'#ef5045'],['Abstenções',d.abstention,d.abstention_pct,'#f59c32']];$('#analysisStats').innerHTML=stats.map(s=>`<div class="statbox"><small><i class="legend-dot" style="background:${s[3]}"></i>${s[0]}</small><strong>${fmt(s[1])}</strong><em>${pct(s[2])}</em></div>`).join('')}
  const arr=real.length?real:((d?.candidates||[]).slice(0,6).map(c=>({...c,estimate_percentage:c.percentage}))),sum=arr.reduce((a,c)=>a+Number(c.estimate_percentage||0),0)||1;let cursor=0,parts=[];arr.forEach((c,i)=>{const share=Number(c.estimate_percentage||0)/sum*100;parts.push(`${palette[i]} ${cursor}% ${cursor+share}%`);cursor+=share});$('#candidateDonut').style.background=`conic-gradient(${parts.join(',')})`;$('#donutLegend').innerHTML=arr.map((c,i)=>`<div class="legend-item"><b><i class="legend-dot" style="background:${palette[i]}"></i>${esc(c.ballot_name)}</b><span>${pct(c.estimate_percentage)}</span></div>`).join('');openFull('#analysisModal');
}

function openCandidate(c,context={office:state.office,scope:state.scope}){
  if(!c)return;state.selectedCandidate=c;state.detailContext=context;
  $('#detailOffice').textContent=officeLabels[context.office]||c.office||'Candidato';$('#detailPhotoWrap').innerHTML=avatarHtml(c.ballot_name,c.photo,'detail-photo');$('#detailNumber').textContent=c.number||'—';$('#detailName').textContent=c.ballot_name||'—';$('#detailParty').textContent=[c.party,c.party_name].filter(Boolean).join(' · ')||'Partido não informado';$('#detailPlace').textContent=states[context.scope]||String(context.scope||'').toUpperCase();$('#detailHeart').classList.toggle('active',isFavorite(c));$('#detailHeart').textContent=isFavorite(c)?'♥':'♡';$('#detailStatus').style.display='none';
  const estimate=c.estimate_percentage==null?'Sem pesquisa disponível':pct(c.estimate_percentage);
  $('#detailResult').innerHTML=`<div class="info-stat"><small>Pesquisa</small><b>${esc(estimate)}</b></div><div class="info-stat"><small>Número</small><b>${esc(c.number||'—')}</b></div><div class="info-stat"><small>Partido</small><b>${esc(c.party||'—')}</b></div><div class="info-stat"><small>Cargo</small><b>${esc(officeLabels[context.office]||c.office||'—')}</b></div>`;$('#viceSection').style.display='none';fillCandidateDetail(c);openFull('#candidateModal');loadCandidateDetail(c,context);
}
function fillCandidateDetail(c){
  $('#personalFullName').textContent=c.name||'—';$('#personalBirth').textContent=c.birth_date||'—';$('#personalBirthplace').textContent=[c.birth_city,c.birth_state].filter(Boolean).join(' - ')||'—';$('#personalEducation').textContent=c.education||'—';$('#personalOccupation').textContent=c.occupation||'—';$('#personalCivil').textContent=c.civil_status||'—';$('#personalGender').textContent=c.gender||'—';$('#personalRace').textContent=c.race||'—';$('#detailCoalition').innerHTML=c.coalition||c.coalition_composition?`<b>${esc(c.coalition||'Composição')}</b><br><span class="muted">${esc(c.coalition_composition||'')}</span>`:'Sem coligação/federação informada.';
  const assets=c.assets||[];$('#assetsCard').innerHTML=assets.length?`<div class="asset-total"><small>Patrimônio total declarado</small><strong>${money(c.assets_total)}</strong><span class="muted">${assets.length} bens declarados</span></div><div class="asset-list">${assets.slice(0,12).map(a=>`<div class="asset-item"><b><span>${esc(a.type||'Bem')}</span><span>${money(a.value)}</span></b><small>${esc(a.description||'')}</small></div>`).join('')}</div>`:'<div class="empty-detail">Detalhes patrimoniais carregam quando disponíveis na base pública.</div>';
}
async function loadCandidateDetail(c,context){
  try{const r=await fetch(`/api/candidates/${encodeURIComponent(context.office)}/${encodeURIComponent(context.scope)}/${encodeURIComponent(c.id)}`,{cache:'force-cache'}),d=await r.json();if(!r.ok)return;if(state.selectedCandidate?.id!==c.id)return;state.selectedCandidate={...c,...d,_office:context.office,_scope:context.scope};fillCandidateDetail(state.selectedCandidate)}catch(e){}
}

function renderFavorites(){
  const values=Object.values(getFavorites());
  if(!values.length){$('#favoritesList').innerHTML='<div class="favorites-empty"><div style="font-size:44px;margin-bottom:10px">♡</div>Nenhum candidato salvo ainda.<br>Abra um candidato e toque no coração para favoritar.</div>';return}
  $('#favoritesList').innerHTML=values.map((c,i)=>`<article class="card candidate-card candidate-hero" data-favorite="${i}"><div class="candidate-main">${avatarHtml(c.ballot_name||c.name,c.photo||'')}<div><div class="cand-name">${esc(c.ballot_name||c.name)}</div><div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div><div class="muted" style="margin-top:7px">${esc(officeLabels[c._office]||c._office||'')}</div></div><div class="cand-pct">${c.estimate_percentage==null?'—':pct(c.estimate_percentage)}</div></div></article>`).join('');
  $$('[data-favorite]').forEach(el=>el.onclick=()=>{const c=values[Number(el.dataset.favorite)];openCandidate(c,{office:c._office||'presidente',scope:c._scope||'br'})});
}
function showView(name){
  $('#resultsView').hidden=name!=='results';$('#pollsView').hidden=name!=='polls';$('#favoritesView').hidden=name!=='favorites';$$('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.view===name));
  if(name==='polls'){if(state.scope==='br')$('#pollOffice').value='presidente';loadPoll(true)}else if(name==='favorites')renderFavorites();else if(name==='results'&&state.directoryContext!==`${state.office}:${state.scope}`)loadResults();
  window.scrollTo({top:0,behavior:'smooth'});
}

async function loadCatalog(){try{const r=await fetch('/api/g1/catalog',{cache:'force-cache'});if(r.ok)state.catalog=await r.json()}catch(e){}}
function availableInstitutes(office=$('#pollOffice').value,scope=state.scope){
  const label={presidente:'Presidente',governador:'Governador',senador:'Senador'}[office],cargo=state.catalog?.cargos?.find(x=>x.nome===label),loc=cargo?.localidades?.find(x=>String(x.uf).toLowerCase()===scope);
  return loc?.turnos?.['1-turno']||[];
}
function syncInstitutes(){const sel=$('#pollInstitute'),cur=sel.value,available=availableInstitutes();sel.innerHTML='<option value="">Automático</option>'+available.map(x=>`<option value="${esc(String(x.nome).toLowerCase())}">${esc(x.nome)}</option>`).join('');if([...sel.options].some(o=>o.value===cur))sel.value=cur;else if($('#pollOffice').value==='presidente'&&state.scope==='br'&&available.some(x=>String(x.nome).toLowerCase()==='datafolha'))sel.value='datafolha';else sel.value=''}
function fillPollFilters(d){const q=$('#pollQuestion'),qc=q.value;q.innerHTML=(d.available_questions||[]).map(x=>`<option value="${esc(x.code)}">${esc(x.label)}</option>`).join('')||`<option value="${esc(d.question_code||'')}">${esc(d.question||'Pergunta')}</option>`;q.value=[...q.options].some(o=>o.value===qc)?qc:(d.question_code||q.options[0]?.value||'');const s=$('#pollStratum'),sv=s.value,groups={};(d.strata||[]).forEach(x=>(groups[x.group||'Outros']??=[]).push(x));s.innerHTML='<option value="">Total</option>'+Object.entries(groups).map(([g,items])=>`<optgroup label="${esc(g)}">${items.map(x=>`<option value="${esc((x.group_slug||g)+'|'+x.label)}">${esc(x.label)}</option>`).join('')}</optgroup>`).join('');if([...s.options].some(o=>o.value===sv))s.value=sv}
async function highQualityDirectoryForPoll(office,scope){
  const key=directoryKey(office,scope),cached=cacheRead(key,DIRECTORY_TTL);if(cached)return cached;
  try{const r=await fetch(`/api/candidates?office=${encodeURIComponent(office)}&scope=${encodeURIComponent(scope)}&include_poll=false`,{cache:'force-cache'}),d=await r.json();if(r.ok){cacheWrite(key,d);return d}}catch(e){}return null;
}
function mergeHighQualityPollPhotos(poll,directory){
  if(!directory)return poll;(poll.choices||[]).forEach(choice=>{const c=matchPollChoice({ballot_name:choice.name,name:choice.name},directory.candidates||[]);if(c?.photo)choice.hq_photo=c.photo});return poll;
}
async function loadPoll(reset=false){
  const office=$('#pollOffice').value;if(requireStateForOffice(office,'polls')){$('#pollOffice').value='presidente';return}
  const scope=state.scope,inst=$('#pollInstitute').value,qcode=reset?'':$('#pollQuestion').value;syncInstitutes();
  const cached=cacheRead(pollKey(office,scope),POLL_TTL);if(cached){state.poll=cached;fillPollFilters(cached);renderPoll()}
  if(!cached)$('#pollList').innerHTML='<div class="card coming skeleton" style="height:150px"></div>';
  let url=`/api/g1/poll?office=${encodeURIComponent(office)}&scope=${encodeURIComponent(scope)}&round=1`;if(inst)url+=`&institute=${encodeURIComponent(inst)}`;if(qcode)url+=`&question=${encodeURIComponent(qcode)}`;
  try{
    const [pr,dir]=await Promise.all([fetch(url,{cache:'no-cache'}),highQualityDirectoryForPoll(office,scope)]),d=await pr.json();if(!pr.ok)throw new Error(d.detail||'Pesquisa indisponível');mergeHighQualityPollPhotos(d,dir);state.poll=d;cacheWrite(pollKey(office,scope),d);fillPollFilters(d);renderPoll();
  }catch(e){if(!cached){$('#pollMeta').textContent='';$('#pollList').innerHTML=`<div class="card coming"><b>Pesquisa indisponível</b><br>${esc(e.message)}</div>`;$('#methodology').style.display='none'}}
}
function renderPoll(){
  const d=state.poll;if(!d)return;let choices=d.choices||[],margin=d.margin_error_points;const sv=$('#pollStratum').value;if(sv){const st=(d.strata||[]).find(x=>(x.group_slug||x.group)+'|'+x.label===sv);if(st){choices=st.choices;margin=st.margin_error_points}}
  $('#pollMeta').textContent=`${states[d.scope]||d.scope.toUpperCase()} · ${d.institute} · ${d.question} · ${d.latest_date||''}`;
  $('#pollList').innerHTML=choices.map(c=>`<article class="card poll-card"><div class="poll-candidate">${avatarHtml(c.name,c.hq_photo||c.photo,'mini-avatar')}<div><div class="cand-name">${esc(c.name)}</div><div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div>${pollDelta(c)?`<div class="delta">${esc(pollDelta(c))}</div>`:''}</div><div class="poll-pct">${pct(c.percentage)}</div></div></article>`).join('');
  const lines=[];if(d.sample_size)lines.push(`<div><b>Amostra:</b> ${fmt(d.sample_size)} entrevistas</div>`);if(d.field_period)lines.push(`<div><b>Campo:</b> ${esc(d.field_period)}</div>`);if(margin!=null)lines.push(`<div><b>Margem de erro:</b> ±${esc(margin)} p.p.</div>`);if((d.registrations||[]).length)lines.push(`<div><b>Registro no TSE:</b> ${esc(d.registrations.join(', '))}</div>`);$('#methodLines').innerHTML=lines.join('');$('#methodology').style.display='block';
}
function pollDelta(c){const h=(c.history||[]).slice().sort((a,b)=>a.date.localeCompare(b.date));if(h.length<2)return'';const x=Number(h.at(-1).percentage)-Number(h.at(-2).percentage);if(Math.abs(x)<.05)return'Sem variação na rodada anterior';return`${x>0?'+':''}${x.toFixed(1).replace('.',',')} p.p. vs. rodada anterior`}

function renderStates(filter=''){
  const f=norm(filter),forbidBrazil=(state.pendingOffice&&isStateOffice(state.pendingOffice))||(state.pendingPollOffice&&isStateOffice(state.pendingPollOffice))||(!state.pendingOffice&&!state.pendingPollOffice&&isStateOffice(state.office));
  $('#stateList').innerHTML=Object.entries(states).filter(([k])=>!(forbidBrazil&&k==='br')).filter(([,v])=>!f||norm(v).includes(f)).map(([k,v])=>`<div class="state-item ${k===state.scope?'selected':''}" data-state="${k}">${flagBadgeHtml(k)}<span class="state-name">${v}</span>${k===state.scope?'<span class="saved-mark">Selecionado</span>':''}<span class="chev">›</span></div>`).join('');
  $('[data-state]').forEach(el=>el.onclick=()=>selectState(el.dataset.state,'manual'));
}
function selectState(scope,source='manual'){
  if(!states[scope])return;state.scope=scope;persistScope(scope,source);updateLocationUI();updateGeoStatus();
  const pendingOffice=state.pendingOffice,pendingPoll=state.pendingPollOffice;state.pendingOffice=null;state.pendingPollOffice=null;closeSheets();
  if(pendingOffice){setActiveOffice(pendingOffice);showView('results');loadResults();return}
  if(pendingPoll){$('#pollOffice').value=pendingPoll;showView('polls');loadPoll(true);return}
  if(!$('#resultsView').hidden){loadResults()}else if(!$('#pollsView').hidden){loadPoll(true)}
}
async function detectLocation(){
  const btn=$('#geoBtn'),title=$('#geoTitle'),small=$('#geoStatus');
  if(!navigator.geolocation){updateGeoStatus('Localização não disponível neste navegador');return}
  btn.classList.remove('saved');btn.classList.add('detecting');title.textContent='Usar minha localização';small.textContent='Detectando…';
  navigator.geolocation.getCurrentPosition(async p=>{try{
    const r=await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${p.coords.latitude}&lon=${p.coords.longitude}`,{headers:{'Accept-Language':'pt-BR'}}),d=await r.json(),code=String(d.address?.['ISO3166-2-lvl4']||d.address?.['ISO3166-2-lvl3']||'').split('-').pop().toLowerCase();
    if(states[code]){btn.classList.remove('detecting');btn.classList.add('saved');title.textContent='Localização salva';small.textContent=states[code];selectState(code,'detected');return}
    updateGeoStatus('Estado não identificado');
  }catch(e){updateGeoStatus('Não foi possível identificar o estado')}},()=>updateGeoStatus('Permissão de localização não concedida'),{enableHighAccuracy:false,timeout:7000,maximumAge:3600000});
}
function openSheet(id){$('#overlay').classList.add('show');$(id).classList.add('show');document.body.style.overflow='hidden'}
function closeSheets(){$('#overlay').classList.remove('show');$$('.sheet.show').forEach(x=>x.classList.remove('show'));document.body.style.overflow='';state.pendingOffice=null;state.pendingPollOffice=null;const title=$('#locationSheet .sheet-head h2');if(title)title.textContent='Selecionar local'}
function openFull(id){$(id).classList.add('show');document.body.style.overflow='hidden'}
function closeFull(el){el.closest('.fullscreen').classList.remove('show');document.body.style.overflow=''}

$('#locationTrigger').onclick=()=>{const title=$('#locationSheet .sheet-head h2');if(title)title.textContent='Selecionar local';updateGeoStatus();renderStates();openSheet('#locationSheet')};
$('#overlay').onclick=closeSheets;$$('[data-close]').forEach(x=>x.onclick=closeSheets);$('#stateSearch').oninput=e=>renderStates(e.target.value);$('#geoBtn').onclick=detectLocation;$('#analysisBtn').onclick=openAnalysis;$$('[data-full-close]').forEach(x=>x.onclick=()=>closeFull(x));
$('#detailHeart').onclick=()=>{if(!state.selectedCandidate)return;const on=saveFavorite(state.selectedCandidate);$('#detailHeart').classList.toggle('active',on);$('#detailHeart').textContent=on?'♥':'♡'};
$$('.nav-btn').forEach(b=>b.onclick=()=>showView(b.dataset.view));
$$('.chip').forEach(b=>b.onclick=()=>{const office=b.dataset.office;if(requireStateForOffice(office,'results'))return;setActiveOffice(office);showView('results');loadResults()});
$('#refreshBtn').onclick=()=>{const b=$('#refreshBtn');b.classList.add('refreshing');setTimeout(()=>b.classList.remove('refreshing'),360);if(!$('#resultsView').hidden)loadResults();else if(!$('#pollsView').hidden)loadPoll(false);else renderFavorites()};
$('#candidateSearch').oninput=e=>{state.candidateQuery=e.target.value;state.candidateLimit=24;renderRealCandidates()};$('#candidateMore').onclick=()=>{state.candidateLimit+=24;renderRealCandidates()};
$('#pollOffice').onchange=e=>{const office=e.target.value;if(requireStateForOffice(office,'polls')){e.target.value='presidente';return}$('#pollQuestion').value='';$('#pollStratum').value='';loadPoll(true)};
$('#pollInstitute').onchange=()=>loadPoll(true);$('#pollQuestion').onchange=()=>loadPoll(false);$('#pollStratum').onchange=renderPoll;

updateLocationUI();updateGeoStatus();setActiveOffice('presidente');loadCatalog().then(syncInstitutes);loadResults();restoreCloudLocation();
setInterval(()=>{if(!document.hidden&&!$('#resultsView').hidden&&state.office==='presidente')loadOfficialResultInBackground(state.requestSeq)},30000);
