const $=s=>document.querySelector(s);let state={view:'active',data:[],search:'',chain:'',marketCapSort:null,page:1,pageSize:50,total:0,pages:1,statusCounts:{PASS:0,UNKNOWN:0,REJECT:0},loaded:false},loadRequest=0;
const money=v=>v==null?'—':'$'+Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:2}).format(v);
const price=v=>v==null?'—':v>=1?'$'+Number(v).toFixed(4):'$'+Number(v).toPrecision(5);
const ago=s=>{if(!s)return'—';const n=(Date.now()-new Date(s))/1000;if(n<60)return Math.max(0,Math.floor(n))+'秒前';if(n<3600)return Math.floor(n/60)+'分前';if(n<86400)return Math.floor(n/3600)+'时前';return Math.floor(n/86400)+'天前'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sourceLabel=s=>{
  if(s.startsWith('binance_alpha'))return 'Alpha 1H';
  if(s.startsWith('okx_trending'))return s.endsWith('_4h')?'OKX 4H':'';
  if(s.startsWith('gmgn_trending'))return s.endsWith('_6h')?'GMGN 6H':'';
  return s.startsWith('dex_boost')?'DEX Boost（历史）':s;
};
const okxChainSlug={bsc:'bsc',solana:'solana',robinhood:'robinhood-chain'};
const chainMeta={bsc:{label:'BSC',icon:'/static/icons/bsc.svg'},solana:{label:'Solana',icon:'/static/icons/solana.svg'},robinhood:{label:'Robinhood Chain',icon:'/static/icons/robinhood.jpg'}};
const okxTokenUrl=(chain,address)=>`https://web3.okx.com/zh-hans/token/${okxChainSlug[chain]||encodeURIComponent(chain)}/${encodeURIComponent(address)}`;
async function copyContract(button,address){
  try{
    if(navigator.clipboard?.writeText){
      await navigator.clipboard.writeText(address);
    }else{
      const input=document.createElement('textarea');input.value=address;input.style.position='fixed';input.style.opacity='0';document.body.appendChild(input);input.select();document.execCommand('copy');input.remove();
    }
    button.classList.add('copied');button.nextElementSibling.textContent='已复制';
  }catch{
    button.nextElementSibling.textContent='复制失败，请手动复制';
  }
  setTimeout(()=>{button.classList.remove('copied');button.nextElementSibling.textContent=''},1600);
}
async function load(){
  const request=++loadRequest;
  const body=$('#rows');if(!state.loaded)body.innerHTML='<tr><td colspan="9" class="empty">正在加载…</td></tr>';body.closest('table').setAttribute('aria-busy','true');
  const q=new URLSearchParams({view:state.view,page:String(state.page),page_size:String(state.pageSize)}),status=$('#status').value,chain=state.chain,source=$('#source').value;
  if(status)q.set('status',status);if(chain)q.set('chain',chain);if(source)q.set('source',source);if(status==='REJECT')q.set('include_reject','true');
  if(state.search)q.set('search',state.search);if(state.marketCapSort)q.set('sort',state.marketCapSort);
  try{
    const res=await fetch('/api/candidates?'+q),payload=await res.json();if(request!==loadRequest)return;state.data=payload.items;state.page=payload.page;state.pageSize=payload.page_size;state.total=payload.total;state.pages=payload.pages;state.statusCounts=payload.status_counts;state.loaded=true;render();
  }catch(error){
    if(request===loadRequest&&!state.loaded)body.innerHTML='<tr><td colspan="9" class="empty">加载失败，请稍后重试</td></tr>';console.error(error);
  }finally{
    if(request===loadRequest)body.closest('table').setAttribute('aria-busy','false');
  }
}
const setText=(node,value,highlight=false)=>{const next=String(value);if(node.textContent===next)return;const hadValue=node.textContent!==''&&node.textContent!=='—';node.textContent=next;if(highlight&&hadValue&&!matchMedia('(prefers-reduced-motion: reduce)').matches)node.animate([{backgroundColor:'rgba(22,133,91,.14)'},{backgroundColor:'transparent'}],{duration:650,easing:'ease-out'})};
function createRow(){
  const tr=document.createElement('tr');tr.className='row-enter';tr.innerHTML='<td><div class="token"><span class="token-heading"><a class="token-name" target="_blank" rel="noopener noreferrer" title="在欧易查看 K 线"></a><span class="chain-mark"><img alt=""></span></span><span class="contract-line"><button type="button" class="contract-copy" title="点击复制合约地址"></button><span class="copy-feedback" aria-live="polite"></span></span></div></td><td class="price live-value"></td><td class="market-cap live-value"></td><td class="liquidity live-value"></td><td class="volume live-value"></td><td class="tx live-value"></td><td class="risk"></td><td><span class="status"></span><br><span class="state"></span></td><td class="sources"></td>';
  tr.onclick=()=>detail(tr.dataset.id);tr.querySelector('.token-name').onclick=e=>e.stopPropagation();const copy=tr.querySelector('.contract-copy');copy.onclick=e=>{e.stopPropagation();copyContract(copy,copy.dataset.address)};
  requestAnimationFrame(()=>tr.classList.remove('row-enter'));return tr;
}
function updateRow(tr,x){
  tr.dataset.id=x.id;
  const name=tr.querySelector('.token-name');name.href=okxTokenUrl(x.chain,x.address);setText(name,x.symbol||'未知');
  const chain=chainMeta[x.chain]||{label:x.chain,icon:''},chainMark=tr.querySelector('.chain-mark'),chainIcon=chainMark.querySelector('img');chainMark.title=chain.label;chainIcon.src=chain.icon;chainIcon.alt=chain.label;
  const contract=tr.querySelector('.contract-copy');contract.dataset.address=x.address;setText(contract,x.address);
  setText(tr.querySelector('.sources'),(x.sources||'').split(',').map(sourceLabel).filter((v,i,a)=>v&&a.indexOf(v)===i).join(' / '));
  setText(tr.querySelector('.price'),price(x.price_usd),true);setText(tr.querySelector('.market-cap'),money(x.market_cap_usd),true);setText(tr.querySelector('.liquidity'),money(x.liquidity_usd),true);setText(tr.querySelector('.volume'),money(x.volume_h1_usd),true);setText(tr.querySelector('.tx'),x.tx_h1??'—',true);
  setText(tr.querySelector('.risk'),x.risk_coverage||'missing');const status=x.status||'UNKNOWN',statusNode=tr.querySelector('.status');statusNode.className='status '+status;setText(statusNode,status);setText(tr.querySelector('.state'),x.view_state);
}
function render(){
  const counts=state.statusCounts;
  const rows=[...state.data];
  if(state.marketCapSort){
    const direction=state.marketCapSort==='market_cap_desc'?-1:1;
    rows.sort((a,b)=>a.market_cap_usd==null?b.market_cap_usd==null?0:1:b.market_cap_usd==null?-1:(Number(a.market_cap_usd)-Number(b.market_cap_usd))*direction);
  }
  const direction=state.marketCapSort==='market_cap_desc'?'descending':state.marketCapSort==='market_cap_asc'?'ascending':'none';
  $('#marketCapHeader').setAttribute('aria-sort',direction);$('#sortMarketCap .sort-icon').textContent=direction==='descending'?'↓':direction==='ascending'?'↑':'↕';
  const statItems=[['候选币',state.total],['基础达标',counts.PASS],['数据待补',counts.UNKNOWN],['明确过滤',counts.REJECT]],stats=$('#stats');
  if(stats.children.length!==statItems.length)stats.innerHTML=statItems.map(x=>`<div class="panel stat"><strong></strong><span>${x[0]}</span></div>`).join('');statItems.forEach((x,i)=>setText(stats.children[i].querySelector('strong'),x[1]));
  $('#pageSummary').textContent=`共 ${state.total} 条 · 第 ${state.page} / ${state.pages} 页`;$('#prevPage').disabled=state.page<=1;$('#nextPage').disabled=state.page>=state.pages;
  const body=$('#rows');
  if(!rows.length){body.innerHTML='<tr><td colspan="9" class="empty">当前筛选下没有数据</td></tr>';return}
  body.querySelector('.empty')?.closest('tr').remove();const existing=new Map([...body.querySelectorAll('tr[data-id]')].map(tr=>[tr.dataset.id,tr])),active=new Set(rows.map(x=>String(x.id)));
  existing.forEach((tr,id)=>{if(!active.has(id))tr.remove()});rows.forEach(x=>{const id=String(x.id),tr=existing.get(id)||createRow();updateRow(tr,x);body.appendChild(tr)});
}
async function detail(id){
  const [info,shots]=await Promise.all([fetch('/api/candidates/'+id).then(r=>r.json()),fetch('/api/candidates/'+id+'/snapshots').then(r=>r.json())]);const t=info.token;
  $('#detailBody').innerHTML=`<p class="eyebrow">${esc(t.chain)} · ${esc(t.address)}</p><h2>${esc(t.symbol||'未知 Token')}</h2><div class="detail-grid"><div><label>首次发现</label><strong>${new Date(t.first_discovered_at).toLocaleString()}</strong></div><div><label>最后上榜</label><strong>${ago(t.last_seen_at)}</strong></div><div><label>采样点</label><strong>${shots.length}</strong></div></div><canvas id="chart" width="820" height="210"></canvas><h3>最新判断</h3><p class="reasons">${esc((info.evaluations[0]?.reasons||[]).join(' · ')||'无过滤原因')}</p><h3>来源记录</h3><ul class="events">${info.listings.slice(0,80).map(x=>`<li><b>${esc(x.source)}</b> ${esc(x.timeframe||'')} #${x.rank} · ${new Date(x.observed_at).toLocaleString()}</li>`).join('')}</ul><h3>主池切换</h3><ul class="events">${info.pair_switches.length?info.pair_switches.map(x=>`<li>${esc(x.from_pair)} → <b>${esc(x.to_pair)}</b> · ${new Date(x.observed_at).toLocaleString()}</li>`).join(''):'<li>暂无切换</li>'}</ul>`;
  $('#detail').showModal();draw(shots);
}
function draw(shots){const c=$('#chart'),x=c.getContext('2d'),d=shots.filter(s=>s.price_usd!=null);x.clearRect(0,0,c.width,c.height);if(d.length<2){x.fillStyle='#667085';x.fillText('价格采样不足',20,30);return}const vals=d.map(s=>s.price_usd),min=Math.min(...vals),max=Math.max(...vals),span=max-min||max*.01||1;x.strokeStyle='#16855b';x.lineWidth=2;x.beginPath();d.forEach((s,i)=>{const px=18+i/(d.length-1)*(c.width-36),py=18+(max-s.price_usd)/span*(c.height-36);i?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()}
async function health(){const h=await fetch('/api/health/sources').then(r=>r.json()),failed=h.latest.filter(x=>x.status==='failed').length,b=$('#healthBadge');b.textContent=failed?`${failed} 个来源异常`:'数据源正常';b.className='badge '+(failed?'bad':'');b.title=h.okx_configured?'OKX 已配置':'OKX 未配置'}
$('#views').onclick=e=>{if(!e.target.dataset.view)return;document.querySelectorAll('#views button').forEach(b=>b.classList.remove('active'));e.target.classList.add('active');state.view=e.target.dataset.view;state.page=1;load()};
let searchTimer;
$('#search').oninput=e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.search=e.target.value.trim();state.page=1;load()},250)};
$('#search').onkeydown=e=>{if(e.key==='Enter'){clearTimeout(searchTimer);state.search=e.currentTarget.value.trim();state.page=1;load()}if(e.key==='Escape'){clearTimeout(searchTimer);e.currentTarget.value='';state.search='';state.page=1;load()}};
$('#sortMarketCap').onclick=()=>{state.marketCapSort=state.marketCapSort==='market_cap_desc'?'market_cap_asc':'market_cap_desc';state.page=1;load()};
$('#chainFilter').onclick=e=>{const button=e.target.closest('button[data-chain]');if(!button)return;document.querySelectorAll('#chainFilter button').forEach(x=>x.classList.remove('active'));button.classList.add('active');state.chain=button.dataset.chain;state.page=1;load()};
['status','source'].forEach(id=>$('#'+id).onchange=()=>{state.page=1;load()});$('#pageSize').onchange=e=>{state.pageSize=Number(e.target.value);state.page=1;load()};$('#prevPage').onclick=()=>{if(state.page>1){state.page--;load()}};$('#nextPage').onclick=()=>{if(state.page<state.pages){state.page++;load()}};$('#refresh').onclick=()=>{load();health()};$('#detail .close').onclick=()=>$('#detail').close();load();health();setInterval(load,30000);
