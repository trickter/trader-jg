const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const money=value=>value==null?'—':'$'+Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:2}).format(value);
const price=value=>value==null?'—':value>=1?'$'+Number(value).toFixed(4):'$'+Number(value).toPrecision(5);
const percent=value=>value==null?'—':(Number(value)*100).toFixed(1)+'%';
const chainIcon={bsc:'/static/icons/bsc.svg',solana:'/static/icons/solana.svg',robinhood:'/static/icons/robinhood.jpg'};
const qualificationLabel={VERIFIED:'已验证',PENDING:'待核验',EXCLUDED:'已排除'};
const outcomeLabel={OPEN:'观察中',WIN:'成功',LOSS:'失败',TIMEOUT:'到期',AMBIGUOUS:'顺序不明',UNCONFIRMED:'未确认'};
const ratioLabel=value=>value==null?'—':Math.abs(value-2/3)<.01?'2/3':Math.abs(value-1/3)<.01?'1/3':'1/6';
let items=[];

async function load(){
  const query=new URLSearchParams(),chain=$('#researchChain').value,qualification=$('#qualification').value,signal=$('#signal').value,search=$('#researchSearch').value.trim();
  if(chain)query.set('chain',chain);if(qualification)query.set('qualification',qualification);if(signal)query.set('signal',signal);if(search)query.set('search',search);
  $('#researchRows').innerHTML='<tr><td colspan="8" class="empty">正在加载…</td></tr>';
  try{
    const response=await fetch('/api/strategy/candidates?'+query);
    if(!response.ok)throw new Error('HTTP '+response.status);
    items=(await response.json()).items;render();
  }catch(error){
    $('#researchRows').innerHTML='<tr><td colspan="8" class="empty">加载失败，请稍后重试</td></tr>';console.error(error);
  }
}

function render(){
  const body=$('#researchRows');
  if(!items.length){body.innerHTML='<tr><td colspan="8" class="empty">当前没有符合筛选条件的数据</td></tr>';return}
  body.innerHTML=items.map(item=>{
    const q=item.qualification_status, outcome=item.signal_outcome;
    const coverage=item.bar_count?`${item.bar_count} 根<br><span class="state">${esc((item.bar_start||'').slice(0,10))} 至 ${(item.bar_end||'').slice(0,10)}</span>`:'待回填';
    return `<tr data-id="${item.id}"><td><span class="token-heading"><span class="chain-mark"><img src="${chainIcon[item.chain]||''}" alt="${esc(item.chain)}"></span><b>${esc(item.symbol||'未知')}</b></span><span class="state">${esc(item.address)}</span></td><td><span class="status ${q}">${qualificationLabel[q]||q}</span><br><span class="state">${esc(item.qualification_reason||'')}</span></td><td>${money(item.peak_market_cap_usd)}<br><span class="state">${esc((item.evidence_at||'').slice(0,10))}</span></td><td>${price(item.price_usd)}<br><span class="state">${percent(item.price_to_high)} of high</span></td><td>${item.level_ratio==null?'—':ratioLabel(item.level_ratio)+' · '+esc(item.entry_type)}<br><span class="state">${price(item.level_price)}</span></td><td>${outcome?`<span class="status ${outcome}">${outcomeLabel[outcome]||outcome}</span><br><span class="state">${percent(item.net_return)}</span>`:'尚无信号'}</td><td>${money(item.liquidity_usd)}</td><td>${coverage}</td></tr>`;
  }).join('');
  body.querySelectorAll('tr[data-id]').forEach(row=>row.onclick=()=>showDetail(Number(row.dataset.id)));
}

async function health(){
  try{
    const data=await fetch('/api/strategy/health').then(response=>response.json());
    const counts=Object.fromEntries(data.qualifications.map(row=>[row.status,row.count]));
    const bars=data.ohlcv.reduce((sum,row)=>sum+row.bars,0),tokens=data.ohlcv.reduce((sum,row)=>sum+row.tokens,0);
    const jobs=Object.fromEntries(data.backfill_jobs.map(row=>[row.status,row.count]));
    const stats=[['已验证',counts.VERIFIED||0],['待核验',counts.PENDING||0],['小时 K 线',bars],['最新回放',data.latest_run?.id?'#'+data.latest_run.id:'—']];
    $('#researchStats').innerHTML=stats.map(([label,value])=>`<div class="panel stat"><strong>${esc(value)}</strong><span>${label}</span></div>`).join('');
    const badge=$('#researchHealth'),failed=jobs.failed||0;badge.textContent=failed?`${failed} 个回填任务失败`:`${tokens} 个币已有小时线`;badge.className='badge '+(failed?'bad':'');
  }catch(error){$('#researchHealth').textContent='研究数据检查失败';$('#researchHealth').className='badge bad';console.error(error)}
}

async function showDetail(id){
  const [detail,candles]=await Promise.all([
    fetch('/api/strategy/candidates/'+id).then(response=>response.json()),
    fetch('/api/strategy/candidates/'+id+'/candles?limit=1000').then(response=>response.json()),
  ]),token=detail.token,qualification=detail.qualification,signals=detail.signals;
  $('#researchDetailBody').innerHTML=`<p class="eyebrow">${esc(token.chain)} · ${esc(token.address)}</p><h2>${esc(token.symbol||token.name||'未知 Token')}</h2><div class="detail-grid"><div><label>研究资格</label><strong>${qualificationLabel[qualification?.status]||'未建立'}</strong></div><div><label>峰值市值证据</label><strong>${money(qualification?.peak_market_cap_usd)}</strong></div><div><label>小时线</label><strong>${detail.coverage.count||0} 根</strong></div></div><canvas id="researchChart" width="820" height="280"></canvas><h3>回放记录</h3><div class="events">${signals.length?`<table class="signal-table"><thead><tr><th>触发</th><th>层级</th><th>入场</th><th>结果</th><th>净收益</th></tr></thead><tbody>${signals.map(signal=>`<tr><td>${esc((signal.triggered_at||'').replace('T',' ').slice(0,16))}</td><td>${ratioLabel(signal.level_ratio)} · ${esc(signal.entry_type)}</td><td>${price(signal.entry_price)}</td><td>${outcomeLabel[signal.outcome]||signal.outcome}</td><td>${percent(signal.net_return)}</td></tr>`).join('')}</tbody></table>`:'暂无回放信号'}</div>`;
  $('#researchDetail').showModal();draw(candles,signals[0]);
}

function draw(candles,signal){
  const canvas=$('#researchChart'),ctx=canvas.getContext('2d'),data=candles.filter(bar=>bar.close>0);ctx.clearRect(0,0,canvas.width,canvas.height);
  if(data.length<2){ctx.fillStyle='#667085';ctx.fillText('小时线不足，请先运行 backfill',20,30);return}
  const values=data.map(bar=>bar.close),high=Math.max(...values),low=Math.min(...values),span=high-low||high*.01,px=index=>24+index/(data.length-1)*(canvas.width-48),py=value=>18+(high-value)/span*(canvas.height-42);
  ctx.strokeStyle='#16855b';ctx.lineWidth=1.7;ctx.beginPath();data.forEach((bar,index)=>index?ctx.lineTo(px(index),py(bar.close)):ctx.moveTo(px(index),py(bar.close)));ctx.stroke();
  if(signal?.high_price){
    [2/3,1/3,1/6].forEach((ratio,index)=>{const value=signal.high_price*ratio;if(value<low||value>high)return;ctx.setLineDash([5,4]);ctx.strokeStyle=['#2563eb','#9a6700','#c93b4b'][index];ctx.beginPath();ctx.moveTo(24,py(value));ctx.lineTo(canvas.width-24,py(value));ctx.stroke();ctx.fillStyle=ctx.strokeStyle;ctx.fillText(ratioLabel(ratio),canvas.width-48,py(value)-4)});ctx.setLineDash([]);
  }
}

let timer;
$('#researchSearch').oninput=()=>{clearTimeout(timer);timer=setTimeout(load,250)};
['researchChain','qualification','signal'].forEach(id=>$('#'+id).onchange=load);
$('#researchRefresh').onclick=()=>{load();health()};
$('#researchDetail .close').onclick=()=>$('#researchDetail').close();
load();health();
