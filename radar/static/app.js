const $=s=>document.querySelector(s);let state={view:'active',data:[]};
const money=v=>v==null?'—':Intl.NumberFormat('zh-CN',{notation:'compact',style:'currency',currency:'USD',maximumFractionDigits:2}).format(v);
const price=v=>v==null?'—':v>=1?'$'+Number(v).toFixed(4):'$'+Number(v).toPrecision(5);
const ago=s=>{if(!s)return'—';const n=(Date.now()-new Date(s))/1000;if(n<60)return Math.max(0,Math.floor(n))+'秒前';if(n<3600)return Math.floor(n/60)+'分前';if(n<86400)return Math.floor(n/3600)+'时前';return Math.floor(n/86400)+'天前'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sourceLabel=s=>s.startsWith('binance_alpha')?'Alpha 1H':s.startsWith('okx_trending')?'OKX '+(s.endsWith('_4h')?'4H':'1H'):s.startsWith('dex_boost')?'DEX Boost':s;
async function load(){
  $('#rows').innerHTML='<tr><td colspan="11" class="empty">正在加载…</td></tr>';
  const q=new URLSearchParams({view:state.view,limit:'500'}),status=$('#status').value,chain=$('#chain').value,source=$('#source').value;
  if(status)q.set('status',status);if(chain)q.set('chain',chain);if(source)q.set('source',source);if(status==='REJECT')q.set('include_reject','true');
  const res=await fetch('/api/candidates?'+q);state.data=await res.json();render();
}
function render(){
  const counts={PASS:0,UNKNOWN:0,REJECT:0};state.data.forEach(x=>counts[x.status||'UNKNOWN']++);
  $('#stats').innerHTML=[['候选币',state.data.length],['基础达标',counts.PASS],['数据待补',counts.UNKNOWN],['明确过滤',counts.REJECT]].map(x=>`<div class="panel stat"><strong>${x[1]}</strong><span>${x[0]}</span></div>`).join('');
  $('#rows').innerHTML=state.data.length?state.data.map(x=>`<tr data-id="${x.id}"><td><div class="token">${x.logo_url?`<img src="${esc(x.logo_url)}" alt="" onerror="this.style.display='none'">`:''}<span><b>${esc(x.symbol||'未知')}</b><small>${esc(x.address)}</small></span></div></td><td>${esc(x.chain)}</td><td>${esc((x.sources||'').split(',').map(sourceLabel).filter((v,i,a)=>a.indexOf(v)===i).join(' / '))}</td><td>${price(x.price_usd)}</td><td>${money(x.market_cap_usd)}</td><td>${money(x.liquidity_usd)}</td><td>${money(x.volume_h1_usd)}</td><td>${x.tx_h1??'—'}</td><td>${x.risk_coverage||'missing'}</td><td><span class="status ${x.status||'UNKNOWN'}">${x.status||'UNKNOWN'}</span><br><span class="state">${x.view_state}</span></td><td>${ago(x.observed_at)}</td></tr>`).join(''):'<tr><td colspan="11" class="empty">当前筛选下没有数据</td></tr>';
  document.querySelectorAll('#rows tr[data-id]').forEach(tr=>tr.onclick=()=>detail(tr.dataset.id));
}
async function detail(id){
  const [info,shots]=await Promise.all([fetch('/api/candidates/'+id).then(r=>r.json()),fetch('/api/candidates/'+id+'/snapshots').then(r=>r.json())]);const t=info.token;
  $('#detailBody').innerHTML=`<p class="eyebrow">${esc(t.chain)} · ${esc(t.address)}</p><h2>${esc(t.symbol||'未知 Token')}</h2><div class="detail-grid"><div><label>首次发现</label><strong>${new Date(t.first_discovered_at).toLocaleString()}</strong></div><div><label>最后上榜</label><strong>${ago(t.last_seen_at)}</strong></div><div><label>采样点</label><strong>${shots.length}</strong></div></div><canvas id="chart" width="820" height="210"></canvas><h3>最新判断</h3><p class="reasons">${esc((info.evaluations[0]?.reasons||[]).join(' · ')||'无过滤原因')}</p><h3>来源记录</h3><ul class="events">${info.listings.slice(0,80).map(x=>`<li><b>${esc(x.source)}</b> ${esc(x.timeframe||'')} #${x.rank} · ${new Date(x.observed_at).toLocaleString()}</li>`).join('')}</ul><h3>主池切换</h3><ul class="events">${info.pair_switches.length?info.pair_switches.map(x=>`<li>${esc(x.from_pair)} → <b>${esc(x.to_pair)}</b> · ${new Date(x.observed_at).toLocaleString()}</li>`).join(''):'<li>暂无切换</li>'}</ul>`;
  $('#detail').showModal();draw(shots);
}
function draw(shots){const c=$('#chart'),x=c.getContext('2d'),d=shots.filter(s=>s.price_usd!=null);x.clearRect(0,0,c.width,c.height);if(d.length<2){x.fillStyle='#859198';x.fillText('价格采样不足',20,30);return}const vals=d.map(s=>s.price_usd),min=Math.min(...vals),max=Math.max(...vals),span=max-min||max*.01||1;x.strokeStyle='#45d483';x.lineWidth=2;x.beginPath();d.forEach((s,i)=>{const px=18+i/(d.length-1)*(c.width-36),py=18+(max-s.price_usd)/span*(c.height-36);i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()}
async function health(){const h=await fetch('/api/health/sources').then(r=>r.json()),failed=h.latest.filter(x=>x.status==='failed').length,b=$('#healthBadge');b.textContent=failed?`${failed} 个来源异常`:'数据源正常';b.className='badge '+(failed?'bad':'');b.title=h.okx_configured?'OKX 已配置':'OKX 未配置'}
$('#views').onclick=e=>{if(!e.target.dataset.view)return;document.querySelectorAll('#views button').forEach(b=>b.classList.remove('active'));e.target.classList.add('active');state.view=e.target.dataset.view;load()};
['chain','status','source'].forEach(id=>$('#'+id).onchange=load);$('#refresh').onclick=()=>{load();health()};$('#detail .close').onclick=()=>$('#detail').close();load();health();setInterval(load,30000);
