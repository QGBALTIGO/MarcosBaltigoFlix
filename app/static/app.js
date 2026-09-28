const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const fmt=n=>new Intl.NumberFormat('pt-BR').format(Number(n||0));
const money=n=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(n||0));
const pct=n=>Number(n||0).toLocaleString('pt-BR',{minimumFractionDigits:0,maximumFractionDigits:2})+'%';
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const norm=s=>String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]/g,'');
const palette=['#3d7df6','#65c66b','#f59c32','#ef5045','#9a58dc','#61b7e7','#e593ae','#d7b859'];
const states={br:'Brasil',ac:'Acre',al:'Alagoas',ap:'Amapá',am:'Amazonas',ba:'Bahia',ce:'Ceará',df:'Distrito Federal',es:'Espírito Santo',go:'Goiás',ma:'Maranhão',mt:'Mato Grosso',ms:'Mato Grosso do Sul',mg:'Minas Gerais',pa:'Pará',pb:'Paraíba',pr:'Paraná',pe:'Pernambuco',pi:'Piauí',rj:'Rio de Janeiro',rn:'Rio Grande do Norte',rs:'Rio Grande do Sul',ro:'Rondônia',rr:'Roraima',sc:'Santa Catarina',sp:'São Paulo',se:'Sergipe',to:'Tocantins'};
const officeLabels={presidente:'Presidente',governador:'Governador',senador:'Senador',federal:'Deputado Federal',estadual:'Deputado Estadual/Distrital'};
let state={scope:localStorage.getItem('election_scope')||'br',office:'presidente',result:null,poll:null,pollPhotos:{},selectedCandidate:null,catalog:null,directory:null,candidateLimit:30,candidateQuery:''};

try{if(window.Telegram?.WebApp){Telegram.WebApp.ready();Telegram.WebApp.expand();Telegram.WebApp.setHeaderColor('#080c12');Telegram.WebApp.setBackgroundColor('#080c12')}}catch(e){}

function setGauge(valid,voidPct,subPct,nullPct,blankPct){const vals=[valid,voidPct,subPct,nullPct,blankPct].map(x=>Math.max(0,Number(x||0)));const sum=vals.reduce((a,b)=>a+b,0)||1;const scaled=sum>100?vals.map(x=>x/sum*100):vals;const ids=['#gaugeValid','#gaugeVoid','#gaugeSubJudice','#gaugeNull','#gaugeBlank'];let offset=0;scaled.forEach((value,i)=>{const v=Math.max(0,Math.min(100,value));$(ids[i]).style.strokeDasharray=`${v} ${100-v}`;$(ids[i]).style.strokeDashoffset=`-${offset}`;offset+=v})}
function avatarHtml(name,photo,cls='avatar'){return photo?`<img class="${cls}" src="${esc(photo)}" alt="" loading="lazy">`:`<div class="${cls} avatar-placeholder">${esc((name||'?').split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase())}</div>`}
function favoriteKey(c){return `${state.office}:${state.scope}:${c.id||c.candidate_id||c.number||''}:${norm(c.ballot_name||c.name)}`}
function getFavorites(){try{return JSON.parse(localStorage.getItem('election_favorites')||'{}')}catch(e){return {}}}
function saveFavorite(c){const f=getFavorites(),k=favoriteKey(c);if(f[k])delete f[k];else f[k]={...c,_office:state.office,_scope:state.scope};localStorage.setItem('election_favorites',JSON.stringify(f));return !!f[k]}
function isFavorite(c){return !!getFavorites()[favoriteKey(c)]}
function photoFor(c){return c.photo||state.pollPhotos[norm(c.ballot_name||c.name)]||''}

async function loadResults(){
  $('#candidateArea').innerHTML='<div class="card coming skeleton" style="height:220px"></div>';
  $('#estimateMeta').textContent='Carregando candidaturas reais…';
  try{
    const directoryRequest=fetch(`/api/candidates?office=${encodeURIComponent(state.office)}&scope=${encodeURIComponent(state.scope)}`,{cache:'no-store'});
    const resultRequest=state.office==='presidente'?fetch(`/api/result?scope=${state.scope}`,{cache:'no-store'}):Promise.resolve(null);
    const [directoryResponse,resultResponse]=await Promise.all([directoryRequest,resultRequest]);
    const directory=await directoryResponse.json();
    if(!directoryResponse.ok)throw new Error(directory.detail||'Falha ao carregar candidaturas');
    state.directory=directory;
    state.candidateLimit=30;
    state.candidateQuery=$('#candidateSearch')?.value||'';
    if(resultResponse){
      const result=await resultResponse.json();
      if(resultResponse.ok){
        if(result.simulation){
          state.result=null;
          $('#summaryCard').style.display='none';
        }else{
          state.result=result;
          $('#summaryCard').style.display='block';
          renderSummary();
        }
      }
    }else{
      state.result=null;
      $('#summaryCard').style.display='none';
    }
    $('#locationName').textContent=states[state.scope]||state.scope.toUpperCase();
    $('#officeTitle').textContent=officeLabels[state.office];
    $('#sectionsInfo').textContent=`${directory.count} candidaturas`;
    const est=directory.estimate||{};
    $('#estimateMeta').textContent=est.available
      ?`${est.institute} · ${est.date||'última rodada'} · pesquisa de intenção de voto`
      :'Candidaturas registradas no TSE · sem pesquisa comparável integrada';
    $('#candidateSearchWrap').style.display=directory.count>20?'flex':'none';
    renderRealCandidates();
  }catch(e){
    $('#candidateArea').innerHTML=`<div class="card coming"><b>Não foi possível carregar as candidaturas</b>${esc(e.message)}</div>`;
    $('#estimateMeta').textContent='Fonte temporariamente indisponível';
  }
}

function candidateEstimateText(c){
  return c.estimate_percentage==null?'—':pct(c.estimate_percentage);
}
function renderRealCandidates(){
  const directory=state.directory;
  if(!directory)return;
  const query=norm(state.candidateQuery);
  let arr=(directory.candidates||[]).filter(c=>{
    if(!query)return true;
    return [c.ballot_name,c.name,c.number,c.party,c.occupation].some(v=>norm(v).includes(query));
  });
  const visible=arr.slice(0,state.candidateLimit);
  const hasEstimate=!!directory.estimate?.available;
  const max=Math.max(...visible.map(c=>Number(c.estimate_percentage||0)),1);
  let html='';
  if(hasEstimate&&visible.length){
    const hero=visible.slice(0,2),rest=visible.slice(2);
    html+=hero.map((c,i)=>`<article class="card candidate-card candidate-hero animate" data-real-candidate="${i}">
      <div class="candidate-main">
        ${avatarHtml(c.ballot_name,c.photo)}
        <div>
          <div class="cand-name">${esc(c.ballot_name)}</div>
          <div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div>
          <div class="estimate-badge">${esc(directory.estimate.institute||'Pesquisa')} · ${esc(directory.estimate.date||'')}</div>
        </div>
        <div class="cand-pct">${candidateEstimateText(c)}</div>
      </div>
      <div class="progress"><i style="width:${Math.max(0,Math.min(100,Number(c.estimate_percentage||0)))}%"></i></div>
      <div class="cand-votes">Intenção de voto · não é apuração</div>
    </article>`).join('');
    html+=rest.map((c,idx)=>`<article class="candidate-row real animate" data-real-candidate="${idx+2}">
      ${avatarHtml(c.ballot_name,c.photo,'mini-avatar')}
      <div>
        <div class="row-name">${esc(c.ballot_name)}</div>
        <div class="row-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div>
        <div class="row-bar"><i style="width:${Math.max(0,Math.min(100,Number(c.estimate_percentage||0)))}%"></i></div>
      </div>
      <div class="row-pct">${candidateEstimateText(c)}</div>
    </article>`).join('');
  }else{
    html=visible.map((c,i)=>`<article class="candidate-row real animate" data-real-candidate="${i}">
      ${avatarHtml(c.ballot_name,c.photo,'mini-avatar')}
      <div>
        <div class="row-name">${esc(c.ballot_name)}</div>
        <div class="row-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div>
        <div class="no-estimate">${esc(c.occupation||c.party_name||'Candidatura registrada')}</div>
      </div>
      <div class="row-pct">—</div>
    </article>`).join('');
  }
  $('#candidateArea').innerHTML=html||'<div class="card coming"><b>Nenhuma candidatura encontrada</b>Tente outro termo de busca.</div>';
  $$('[data-real-candidate]').forEach(el=>el.onclick=()=>openCandidate(visible[Number(el.dataset.realCandidate)]));
  const more=$('#candidateMore');
  more.style.display=arr.length>state.candidateLimit?'block':'none';
  more.textContent=`Mostrar mais (${arr.length-state.candidateLimit})`;
}

function renderSummary(){const d=state.result;if(!d)return;const total=Number(d.total_votes||0);const validPct=total?Number(d.valid_votes||0)/total*100:0;const voidPct=total?Number(d.void_votes||0)/total*100:0;const subPct=total?Number(d.void_sub_judice_votes||0)/total*100:0;const nullPct=total?Number(d.null_votes||0)/total*100:0;const blankPct=total?Number(d.blank_votes||0)/total*100:0;setGauge(validPct,voidPct,subPct,nullPct,blankPct);$('#totalVotes').textContent=`${fmt(total)} votos`;$('#validShare').textContent=pct(validPct);$('#validVotes').textContent=fmt(d.valid_votes);$('#voidVotes').textContent=fmt(d.void_votes);$('#subJudiceVotes').textContent=fmt(d.void_sub_judice_votes);$('#nullVotes').innerHTML=`${fmt(d.null_votes)} · <span class="muted">${pct(nullPct)}</span>`;$('#blankVotes').innerHTML=`${fmt(d.blank_votes)} · <span class="muted">${pct(blankPct)}</span>`}

function openAnalysis(){
  const d=state.result;
  const directory=state.directory;
  const real=(directory?.candidates||[]).filter(c=>c.estimate_percentage!=null).slice(0,6);
  if(!d&&!real.length)return;
  $('#analysisPlace').textContent=`● ${states[state.scope]||state.scope.toUpperCase()}`;
  if(d){
    $('#eligibleText').textContent=`${fmt(d.electorate_total)} eleitores aptos`;
    $('#participationLabel').textContent=`Participação: ${pct(d.turnout_pct)}`;
    $('#participationBar').style.width=Math.min(100,Number(d.turnout_pct||0))+'%';
    const total=Number(d.total_votes||0)||1;
    const stats=[['Votos válidos',d.valid_votes,Number(d.valid_votes||0)/total*100,'#45a84d'],['Votos brancos',d.blank_votes,Number(d.blank_votes||0)/total*100,'#848d99'],['Votos nulos',d.null_votes,Number(d.null_votes||0)/total*100,'#ef5045'],['Abstenções',d.abstention,d.abstention_pct,'#f59c32']];
    $('#analysisStats').innerHTML=stats.map(s=>`<div class="statbox"><small><i class="legend-dot" style="background:${s[3]}"></i>${s[0]}</small><strong>${fmt(s[1])}</strong><em>${pct(s[2])}</em></div>`).join('');
  }
  const arr=real.length?real:((d?.candidates||[]).slice(0,6).map(c=>({...c,estimate_percentage:c.percentage})));
  const sum=arr.reduce((a,c)=>a+Number(c.estimate_percentage||0),0)||1;
  let cursor=0,parts=[];
  arr.forEach((c,i)=>{const share=Number(c.estimate_percentage||0)/sum*100;parts.push(`${palette[i]} ${cursor}% ${cursor+share}%`);cursor+=share});
  $('#candidateDonut').style.background=`conic-gradient(${parts.join(',')})`;
  $('#donutLegend').innerHTML=arr.map((c,i)=>`<div class="legend-item"><b><i class="legend-dot" style="background:${palette[i]}"></i>${esc(c.ballot_name)}</b><span>${pct(c.estimate_percentage)}</span></div>`).join('');
  openFull('#analysisModal');
}

function openCandidate(c){
  state.selectedCandidate=c;
  $('#detailOffice').textContent=officeLabels[state.office];
  $('#detailPhotoWrap').innerHTML=avatarHtml(c.ballot_name,c.photo,'detail-photo');
  $('#detailNumber').textContent=c.number||'—';
  $('#detailName').textContent=c.ballot_name||'—';
  $('#detailParty').textContent=[c.party,c.party_name].filter(Boolean).join(' · ')||'Partido não informado';
  $('#detailPlace').textContent=states[state.scope]||state.scope.toUpperCase();
  $('#detailHeart').classList.toggle('active',isFavorite(c));
  $('#detailHeart').textContent=isFavorite(c)?'♥':'♡';
  const st=$('#detailStatus');st.style.display='none';
  const estimate=c.estimate_percentage==null?'Sem pesquisa disponível':pct(c.estimate_percentage);
  $('#detailResult').innerHTML=`<div class="info-stat"><small>Pesquisa</small><b>${esc(estimate)}</b></div><div class="info-stat"><small>Número</small><b>${esc(c.number||'—')}</b></div><div class="info-stat"><small>Partido</small><b>${esc(c.party||'—')}</b></div><div class="info-stat"><small>Cargo</small><b>${esc(officeLabels[state.office])}</b></div>`;
  $('#viceSection').style.display='none';
  fillCandidateDetail(c);
  openFull('#candidateModal');
  loadCandidateDetail(c);
}
function fillCandidateDetail(c){
  $('#personalFullName').textContent=c.name||'—';
  $('#personalBirth').textContent=c.birth_date||'—';
  $('#personalBirthplace').textContent=[c.birth_city,c.birth_state].filter(Boolean).join(' - ')||'—';
  $('#personalEducation').textContent=c.education||'—';
  $('#personalOccupation').textContent=c.occupation||'—';
  $('#personalCivil').textContent=c.civil_status||'—';
  $('#personalGender').textContent=c.gender||'—';
  $('#personalRace').textContent=c.race||'—';
  $('#detailCoalition').innerHTML=c.coalition||c.coalition_composition
    ?`<b>${esc(c.coalition||'Composição')}</b><br><span class="muted">${esc(c.coalition_composition||'')}</span>`
    :'Sem coligação/federação informada.';
  const assets=c.assets||[];
  if(assets.length){
    $('#assetsCard').innerHTML=`<div class="asset-total"><small>Patrimônio total declarado</small><strong>${money(c.assets_total)}</strong><span class="muted">${assets.length} bens declarados</span></div><div class="asset-list">${assets.slice(0,12).map(a=>`<div class="asset-item"><b><span>${esc(a.type||'Bem')}</span><span>${money(a.value)}</span></b><small>${esc(a.description||'')}</small></div>`).join('')}</div>`;
  }else{
    $('#assetsCard').innerHTML='<div class="empty-detail">Nenhum bem encontrado na base espelhada ou declaração indisponível.</div>';
  }
}
async function loadCandidateDetail(c){
  try{
    const r=await fetch(`/api/candidates/${encodeURIComponent(state.office)}/${encodeURIComponent(state.scope)}/${encodeURIComponent(c.id)}`,{cache:'no-store'});
    const d=await r.json();
    if(!r.ok)return;
    state.selectedCandidate=d;
    fillCandidateDetail(d);
    // Situação processual muda ao longo da campanha; não exibimos dado potencialmente
    // defasado vindo do espelho até termos consulta direta atualizada do TSE.
  }catch(e){}
}

function renderFavorites(){const values=Object.values(getFavorites());if(!values.length){$('#favoritesList').innerHTML='<div class="favorites-empty"><div style="font-size:44px;margin-bottom:10px">♡</div>Nenhum candidato salvo ainda.<br>Abra um candidato e toque no coração para favoritar.</div>';return}$('#favoritesList').innerHTML=values.map(c=>`<article class="card candidate-card candidate-hero"><div class="candidate-main">${avatarHtml(c.ballot_name||c.name,c.photo||'')}<div><div class="cand-name">${esc(c.ballot_name||c.name)}</div><div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div><div class="muted" style="margin-top:7px">${esc(officeLabels[c._office]||c._office||'')}</div></div><div class="cand-pct">${c.estimate_percentage==null?'—':pct(c.estimate_percentage)}</div></div></article>`).join('')}

function showView(name){$('#resultsView').hidden=name!=='results';$('#pollsView').hidden=name!=='polls';$('#favoritesView').hidden=name!=='favorites';$$('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.view===name));if(name==='polls')loadPoll();if(name==='favorites')renderFavorites();window.scrollTo({top:0,behavior:'smooth'})}

async function loadCatalog(){try{const r=await fetch('/api/g1/catalog',{cache:'no-store'});if(r.ok)state.catalog=await r.json()}catch(e){}}
function availableInstitutes(){const label={presidente:'Presidente',governador:'Governador',senador:'Senador'}[$('#pollOffice').value];const cargo=state.catalog?.cargos?.find(x=>x.nome===label);const loc=cargo?.localidades?.find(x=>String(x.uf).toLowerCase()===state.scope);return loc?.turnos?.['1-turno']||[]}
function syncInstitutes(){const sel=$('#pollInstitute'),cur=sel.value,available=availableInstitutes();sel.innerHTML='<option value="">Automático</option>'+available.map(x=>`<option value="${esc(String(x.nome).toLowerCase())}">${esc(x.nome)}</option>`).join('');if([...sel.options].some(o=>o.value===cur))sel.value=cur;else if($('#pollOffice').value==='presidente'&&state.scope==='br'&&available.some(x=>String(x.nome).toLowerCase()==='datafolha'))sel.value='datafolha'}
function fillPollFilters(d){const q=$('#pollQuestion'),qc=q.value;q.innerHTML=(d.available_questions||[]).map(x=>`<option value="${esc(x.code)}">${esc(x.label)}</option>`).join('')||`<option value="${esc(d.question_code||'')}">${esc(d.question||'Pergunta')}</option>`;q.value=[...q.options].some(o=>o.value===qc)?qc:(d.question_code||q.options[0]?.value||'');const s=$('#pollStratum'),sv=s.value,groups={};(d.strata||[]).forEach(x=>(groups[x.group||'Outros']??=[]).push(x));s.innerHTML='<option value="">Total</option>'+Object.entries(groups).map(([g,items])=>`<optgroup label="${esc(g)}">${items.map(x=>`<option value="${esc((x.group_slug||g)+'|'+x.label)}">${esc(x.label)}</option>`).join('')}</optgroup>`).join('');if([...s.options].some(o=>o.value===sv))s.value=sv}
async function loadPoll(reset=false){syncInstitutes();const office=$('#pollOffice').value,inst=$('#pollInstitute').value,qcode=reset?'':$('#pollQuestion').value;$('#pollList').innerHTML='<div class="card coming skeleton" style="height:170px"></div>';let url=`/api/g1/poll?office=${office}&scope=${state.scope}&round=1`;if(inst)url+=`&institute=${encodeURIComponent(inst)}`;if(qcode)url+=`&question=${encodeURIComponent(qcode)}`;try{const r=await fetch(url,{cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Pesquisa indisponível');state.poll=d;fillPollFilters(d);renderPoll()}catch(e){$('#pollList').innerHTML=`<div class="card coming"><b>Pesquisa indisponível</b>${esc(e.message)}</div>`;$('#methodology').style.display='none'}}
function renderPoll(){const d=state.poll;if(!d)return;let choices=d.choices||[],margin=d.margin_error_points;const sv=$('#pollStratum').value;if(sv){const st=(d.strata||[]).find(x=>(x.group_slug||x.group)+'|'+x.label===sv);if(st){choices=st.choices;margin=st.margin_error_points}}$('#pollMeta').textContent=`${states[d.scope]||d.scope.toUpperCase()} · ${d.institute} · ${d.question} · ${d.latest_date||''}`;$('#pollList').innerHTML=choices.map(c=>`<article class="card poll-card"><div class="poll-candidate">${avatarHtml(c.name,c.photo,'mini-avatar')}<div><div class="cand-name">${esc(c.name)}</div><div class="cand-id">${[c.number,c.party].filter(Boolean).map(esc).join(' · ')}</div>${pollDelta(c)?`<div class="delta">${esc(pollDelta(c))}</div>`:''}</div><div class="poll-pct">${pct(c.percentage)}</div></div></article>`).join('');const lines=[];if(d.sample_size)lines.push(`<div><b>Amostra:</b> ${fmt(d.sample_size)} entrevistas</div>`);if(d.field_period)lines.push(`<div><b>Campo:</b> ${esc(d.field_period)}</div>`);if(margin!=null)lines.push(`<div><b>Margem de erro:</b> ±${esc(margin)} p.p.</div>`);if((d.registrations||[]).length)lines.push(`<div><b>Registro no TSE:</b> ${esc(d.registrations.join(', '))}</div>`);$('#methodLines').innerHTML=lines.join('');$('#methodology').style.display='block'}
function pollDelta(c){const h=(c.history||[]).slice().sort((a,b)=>a.date.localeCompare(b.date));if(h.length<2)return'';const x=Number(h.at(-1).percentage)-Number(h.at(-2).percentage);if(Math.abs(x)<.05)return'Sem variação na rodada anterior';return`${x>0?'+':''}${x.toFixed(1).replace('.',',')} p.p. vs. rodada anterior`}

function renderStates(filter=''){const f=norm(filter);$('#stateList').innerHTML=Object.entries(states).filter(([k])=>k!=='br').filter(([,v])=>!f||norm(v).includes(f)).map(([k,v])=>`<div class="state-item" data-state="${k}"><span class="uf-badge">${k.toUpperCase()}</span><span>${v}</span><span class="chev">›</span></div>`).join('');$$('[data-state]').forEach(el=>el.onclick=()=>selectState(el.dataset.state))}
function selectState(scope){state.scope=scope;localStorage.setItem('election_scope',scope);$('#locationName').textContent=states[scope];closeSheets();loadResults();if(!$('#pollsView').hidden)loadPoll(true)}
async function detectLocation(){const btn=$('#geoBtn');if(!navigator.geolocation){btn.querySelector('small').textContent='Localização não disponível neste navegador';return}btn.querySelector('small').textContent='Detectando…';navigator.geolocation.getCurrentPosition(async p=>{try{const r=await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${p.coords.latitude}&lon=${p.coords.longitude}`,{headers:{'Accept-Language':'pt-BR'}});const d=await r.json(),code=String(d.address?.['ISO3166-2-lvl4']||d.address?.['ISO3166-2-lvl3']||'').split('-').pop().toLowerCase();if(states[code]){selectState(code);return}btn.querySelector('small').textContent='Estado não identificado; selecione abaixo'}catch(e){btn.querySelector('small').textContent='Não foi possível identificar o estado'}},()=>btn.querySelector('small').textContent='Permissão de localização não concedida',{enableHighAccuracy:false,timeout:8000})}
function openSheet(id){$('#overlay').classList.add('show');$(id).classList.add('show');document.body.style.overflow='hidden'}
function closeSheets(){$('#overlay').classList.remove('show');$$('.sheet.show').forEach(x=>x.classList.remove('show'));document.body.style.overflow=''}
function openFull(id){$(id).classList.add('show');document.body.style.overflow='hidden'}
function closeFull(el){el.closest('.fullscreen').classList.remove('show');document.body.style.overflow=''}

$('#locationTrigger').onclick=()=>{renderStates();openSheet('#locationSheet')};$('#overlay').onclick=closeSheets;$$('[data-close]').forEach(x=>x.onclick=closeSheets);$('#stateSearch').oninput=e=>renderStates(e.target.value);$('#geoBtn').onclick=detectLocation;$('#analysisBtn').onclick=openAnalysis;$$('[data-full-close]').forEach(x=>x.onclick=()=>closeFull(x));$('#detailHeart').onclick=()=>{if(!state.selectedCandidate)return;const on=saveFavorite(state.selectedCandidate);$('#detailHeart').classList.toggle('active',on);$('#detailHeart').textContent=on?'♥':'♡'};
$('.nav-btn').forEach(b=>b.onclick=()=>showView(b.dataset.view));$('.chip').forEach(b=>b.onclick=()=>{$('.chip').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.office=b.dataset.office;$('#officeTitle').textContent=officeLabels[state.office];$('#candidateSearch').value='';state.candidateQuery='';loadResults()});$('#refreshBtn').onclick=()=>{const b=$('#refreshBtn');b.classList.add('refreshing');setTimeout(()=>b.classList.remove('refreshing'),360);if(!$('#resultsView').hidden)loadResults();else if(!$('#pollsView').hidden)loadPoll(false)};
$('#candidateSearch').oninput=e=>{state.candidateQuery=e.target.value;state.candidateLimit=30;renderRealCandidates()};$('#candidateMore').onclick=()=>{state.candidateLimit+=30;renderRealCandidates()};
$('#pollOffice').onchange=()=>loadPoll(true);$('#pollInstitute').onchange=()=>loadPoll(true);$('#pollQuestion').onchange=()=>loadPoll(false);$('#pollStratum').onchange=renderPoll;

$('#locationName').textContent=states[state.scope]||'Brasil';loadCatalog().then(()=>syncInstitutes());loadResults();setInterval(()=>{if(!document.hidden&&!$('#resultsView').hidden&&state.office==='presidente')loadResults()},20000);
