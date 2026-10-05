let data=null;
let marketData=null;
let availableDates=[];
let selectedDate='';
let loadVersion=0;
let activeRequest=null;
let marketState='loading';
function fmtMcap(o,f){if(o==null)return '—';return Number(o).toLocaleString('ja-JP')+esc(f||'');}
function fmtPct(p){return p==null?'—':'+'+Number(p).toFixed(2)+'%';}
function fmtPct5(p){return p==null?'':'('+(p>0?'+':'')+Number(p).toFixed(2)+'%)';}
function fmtNum(x){return x==null?'—':Number(x).toLocaleString('ja-JP');}
function fmtTurnover(t){return t==null?'—':Math.round(Number(t)).toLocaleString('ja-JP');}
function fmtTurnoverOku(r){let v=(r.turnover_yen!=null)?Number(r.turnover_yen)/1e8:(r.turnover_m!=null?Number(r.turnover_m)/100:null);if(v==null||!Number.isFinite(v))return '—';return v<1?(v*10000).toLocaleString('ja-JP',{maximumFractionDigits:1})+'万円':v.toLocaleString('ja-JP',{maximumFractionDigits:1})+'億円';}
function fmtMarket(m){m=m||'';if(m.indexOf('プライム')>=0)return 'Prime';if(m.indexOf('スタンダード')>=0)return 'Standard';if(m.indexOf('グロース')>=0)return 'Growth';return m;}
function fmtCode(c){c=(c==null?'':String(c));return (c.length===5&&c.endsWith('0'))?c.slice(0,4):c;}
function fmtMcapCell(o,f){if(o==null)return '—';return Math.round(Number(o)).toLocaleString('ja-JP')+'億円'+esc(f||'');}
function mcapNote(r){return (r.mcap_source==='yahoo'?'（Yahoo参照）':'')+(r.mcap_date&&r.mcap_date!==data.session_date?'（'+esc(r.mcap_date)+'時点）':'');}
function changeYen(r){if(r==null||r.close==null||r.adj_close==null||r.prev_adj_close==null)return null;var a=Number(r.adj_close);if(!a)return null;return Math.round(Number(r.close)-Number(r.prev_adj_close)*Number(r.close)/a);}
function fmtSigned(v){if(v==null)return '—';var n=Number(v);return (n>=0?'+':'')+n.toLocaleString('ja-JP');}
function fmtCloseCell(r){if(r.close==null)return '—';var c=changeYen(r);return fmtNum(r.close)+'円'+(c!=null?'<span class="chg">'+fmtSigned(c)+'円</span>':'');}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function safeUrl(s){try{if(typeof s!=='string'||/[\s<>"'\\]/.test(s))return '';const u=new URL(s);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?s:'';}catch(e){return '';}}
function kindBadge(k){k=String(k||'').replace(/[\[\]]/g,'');if(!['開示','報道','テーマ'].includes(k))return '';return '<span class="kind k'+k+'">'+k+'</span>';}
function openInfo(){var d=document.getElementById('infoModal');if(d&&d.showModal)d.showModal();}
function closeInfoOnBackdrop(e){if(e.target===e.currentTarget)e.currentTarget.close();}
async function init(){
  try{
    const response=await fetch('data/manifest.json?'+Date.now());
    if(!response.ok)throw new Error('manifest');
    const m=await response.json();
    const sel=document.getElementById('dateSelect');sel.innerHTML='';
    if(!Array.isArray(m.dates))throw new Error('manifest dates');
    if(!m.dates.length){sel.innerHTML='<option>データなし</option>';document.getElementById('tableArea').innerHTML='<div class="empty">まだデータがありません。</div>';marketState='missing';renderMarket();applyHash();return;}
    availableDates=m.dates.filter(d=>/^\d{4}-\d{2}-\d{2}$/.test(d));
    availableDates.forEach((d,i)=>{const o=document.createElement('option');o.value=d;const dt=new Date(d+'T00:00:00');o.textContent=d+' ('+['日','月','火','水','木','金','土'][dt.getDay()]+')';if(i===0)o.selected=true;sel.appendChild(o);});
    const requested=new URL(location.href).searchParams.get('date')||availableDates[0];
    if(!availableDates.includes(requested)){const o=document.createElement('option');o.value=requested;o.textContent=requested+'（公開データなし）';sel.appendChild(o);}
    await loadDate(requested,false);
  }catch(e){document.getElementById('tableArea').innerHTML='<div class="empty">日付一覧を読み込めませんでした。<button type="button" onclick="init()">再読み込み</button></div>';}
}
async function loadDate(d,updateUrl=true){
  if(!d)return;
  selectedDate=d;
  const version=++loadVersion;
  if(activeRequest)activeRequest.abort();
  const controller=new AbortController();activeRequest=controller;
  const signal=controller.signal;
  const selector=document.getElementById('dateSelect');
  if(!Array.from(selector.options).some(o=>o.value===d)){const o=new Option(d+'（公開データなし）',d);selector.add(o);}
  selector.value=d;
  if(updateUrl){const url=new URL(location.href);url.searchParams.set('date',d);history.pushState(null,'',url);}
  data=null;marketData=null;marketState='loading';
  document.getElementById('summary').replaceChildren();
  document.getElementById('droppedArea').replaceChildren();
  document.getElementById('infoBody').replaceChildren();
  if(!availableDates.includes(d)){
    document.getElementById('tableArea').innerHTML='<div class="empty">この日付の公開データはありません。保存期間を過ぎたか、未公開の日付です。</div>';
    marketState='missing';renderMarket();applyHash();return;
  }
  document.getElementById('tableArea').innerHTML='<div class="loading">読み込み中…</div>';
  renderMarket();applyHash();
  async function request(suffix,optional){const r=await fetch('data/'+d+suffix+'.json?'+Date.now(),{signal});if(optional&&r.status===404)return null;if(!r.ok)throw new Error('HTTP '+r.status);const value=await r.json();if(value.session_date!==d)throw new Error('session mismatch');return value;}
  const timeout=setTimeout(()=>controller.abort(),20000);
  const rankingTask=request('',false).then(value=>{
    if(version!==loadVersion)return;
    data=value;render();
  }).catch(()=>{
    if(version!==loadVersion)return;
    data=null;document.getElementById('summary').replaceChildren();document.getElementById('droppedArea').replaceChildren();document.getElementById('infoBody').replaceChildren();
    document.getElementById('tableArea').innerHTML='<div class="empty">ランキングを読み込めませんでした。<button type="button" onclick="retryDate()">再読み込み</button></div>';
  });
  const marketTask=request('_market',true).then(value=>{
    if(version!==loadVersion)return;
    marketData=value;marketState=value?'ready':'missing';renderMarket();
  }).catch(()=>{
    if(version!==loadVersion)return;
    marketData=null;marketState='error';renderMarket();
  });
  await Promise.allSettled([rankingTask,marketTask]);clearTimeout(timeout);
}
function retryDate(){return loadDate(selectedDate,false);}
function render(){
  const rows=data.rows||data.items||[];   /* data.items は旧形式 JSON 後方互換 */
  const cnt=data.counts||{};
  let total=cnt.qualifying;
  if(total==null) total=(data.count_total!=null?data.count_total:rows.length);
  const cntChip = data.capped
    ? '<div class="chip"><span class="num">'+esc(total)+'</span> 社該当（上位 '+rows.length+' 社を掲載）</div>'
    : '<div class="chip"><span class="num">'+rows.length+'</span> 社該当</div>';
  document.getElementById('summary').innerHTML=
    cntChip+
    '<button type="button" class="infobtn" onclick="openInfo()"><span class="i">i</span>データ情報</button>';
  const c=data.criteria||{};
  document.getElementById('infoBody').innerHTML=
    '<div class="k">データ対象日時</div><div class="v">'+esc(data.session_window||'—')+'</div>'+
    '<div class="k">生成日時</div><div class="v">'+esc(data.generated_at||'—')+'</div>'+
    '<div class="k">抽出条件</div><div class="v">'+esc('値上がり率≥+'+(c.min_pct??5)+'% かつ 売買代金≥'+((c.min_turnover_yen??1e7)/1e6)+'百万円／東証個別株のみ・時価総額≥'+(c.min_mcap_oku??100)+'億円'+(c.max_rank?'・上昇率上位'+c.max_rank+'社':'')+'。時価総額は J-Quants の当日終値×自己株式控除後株式数（億円・四捨五入）。†（2026-09-24 以前のデータのみ）は旧方式で増資・自己株により株探最新株数と>1%乖離。')+'</div>';
  let h='<table><thead><tr><th class="r">#</th><th>コード</th><th>銘柄</th><th>市場</th><th class="r">前日比%<br>(5営業日)</th><th class="r">終値<br>(前日比)</th><th class="r">売買代金</th><th>変動要因</th></tr></thead><tbody>';
  rows.forEach(r=>{
    let factor=mdInline(r.factor||'（材料未確認）');
    const fk=(r.factor_kind||'').replace(/[\[\]]/g,'');
    if(fk==='開示'&&r.disclosures&&r.disclosures.length&&safeUrl(r.disclosures[0].pdf_url)){factor=factor+' <a href="'+esc(safeUrl(r.disclosures[0].pdf_url))+'" target="_blank" rel="noopener">[開示PDF]</a>';}
    const code=fmtCode(r.code);
    h+='<tr>'+
      '<td class="rank">'+esc(r.rank||'')+'</td>'+
      '<td class="code rankcode" data-rank="'+esc(r.rank||'')+'"><a href="https://kabutan.jp/stock/?code='+encodeURIComponent(code)+'" target="_blank" rel="noopener">'+esc(code)+'</a></td>'+
      '<td class="name" data-code="'+esc(code)+'"><a class="stock-link" href="https://kabutan.jp/stock/?code='+encodeURIComponent(code)+'" target="_blank" rel="noopener">'+esc(r.name)+'<span class="code-inline">（'+esc(code)+'）</span></a><span class="mcap">'+fmtMcapCell(r.mcap_oku,r.mcap_flag)+mcapNote(r)+'</span></td>'+
      '<td class="mkt">'+esc(fmtMarket(r.market))+'</td>'+
      '<td class="pct" data-label="前日比%(5営業日)">'+fmtPct(r.pct)+(r.pct5!=null?'<span class="pct5">'+fmtPct5(r.pct5)+'</span>':'')+'</td>'+
      '<td class="num" data-label="終値(前日比)">'+fmtCloseCell(r)+'</td>'+
      '<td class="num" data-label="売買代金">'+fmtTurnoverOku(r)+'</td>'+
      '<td class="factor">'+kindBadge(r.factor_kind)+factor+'</td>'+
    '</tr>';
  });
  h+='</tbody></table>';
  if(!rows.length)h='<div class="empty">この日は掲載条件を満たす銘柄がありません。</div>';
  document.getElementById('tableArea').innerHTML=h;
  // 除外（薄商い／時価総額<100億）を折りたたみで
  const c2=data.criteria||{};
  const tmM=((c2.min_turnover_yen??1e7)/1e6);
  const mcO=(c2.min_mcap_oku??100);
  const pctMin=esc(c2.min_pct??5);
  let dh='';
  const dt=data.dropped_turnover||[];
  if(dt.length){
    dh+='<details class="dropped card" style="padding:0 14px 10px;"><summary>参考：値上がり率≥+'+pctMin+'% だが売買代金&lt;'+tmM+'百万円 で除外（薄商い '+dt.length+'件）</summary><table><thead><tr><th>コード</th><th>銘柄</th><th class="r">前日比%<br>(5営業日)</th><th class="r">売買代金<br>(百万円)</th></tr></thead><tbody>';
    dt.forEach(r=>{dh+='<tr><td class="code">'+esc(fmtCode(r.code))+'</td><td class="name">'+esc(r.name)+'</td><td class="pct" data-label="前日比%(5営業日)">'+fmtPct(r.pct)+(r.pct5!=null?'<span class="pct5">'+fmtPct5(r.pct5)+'</span>':'')+'</td><td class="num" data-label="売買代金(百万円)">'+fmtTurnover(r.turnover_m)+'</td></tr>';});
    dh+='</tbody></table></details>';
  }
  const dm=data.dropped_mcap||[];
  if(dm.length){
    dh+='<details class="dropped card" style="padding:0 14px 10px;margin-top:12px;"><summary>参考：値上がり率・売買代金は満たすが時価総額&lt;'+esc(mcO)+'億円 で除外（'+dm.length+'件）</summary><table><thead><tr><th>コード</th><th>銘柄</th><th class="r">前日比%<br>(5営業日)</th><th class="r">時価総額<br>(億円)</th></tr></thead><tbody>';
    dm.forEach(r=>{dh+='<tr><td class="code">'+esc(fmtCode(r.code))+'</td><td class="name">'+esc(r.name)+'</td><td class="pct" data-label="前日比%(5営業日)">'+fmtPct(r.pct)+(r.pct5!=null?'<span class="pct5">'+fmtPct5(r.pct5)+'</span>':'')+'</td><td class="num" data-label="時価総額(億円)">'+fmtMcap(r.mcap_oku)+'</td></tr>';});
    dh+='</tbody></table></details>';
  }
  document.getElementById('droppedArea').innerHTML=dh;
}
/* ===================== 市場分析ビュー ===================== */
function mdInline(s){
  const plain=t=>esc(t).replace(/\[\[([^\]]+)\]\]/g,'<span class="stk">$1</span>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  s=String(s==null?'':s);let out='',offset=0;
  for(const match of s.matchAll(/\[([^\]]+)\]\(([^)]+)\)/g)){
    out+=plain(s.slice(offset,match.index));
    const url=safeUrl(match[2]);
    out+=url?'<a href="'+esc(url)+'" target="_blank" rel="noopener">'+esc(match[1])+'</a>':plain(match[0]);
    offset=match.index+match[0].length;
  }
  return out+plain(s.slice(offset));
}
/* 配列でない値（オブジェクト等）が来ても .forEach で例外を投げず空配列に退避する防御ヘルパ。
   フラグメントの型崩れ（例: theme_matrix.rows をオブジェクトにする）で市場分析タブ全体が
   空になる事故を防ぐ（結合器 build_market_json.validate_market が本来は弾くが二重の安全網）。*/
function asArr(x){return Array.isArray(x)?x:[];}
function pctClass(v){return (Number(v)>=0)?'pct':'pdown';}
function pctStr(v){if(v==null)return '—';var n=Number(v);return (n>=0?'+':'')+n.toFixed(2)+'%';}
function applyHash(){
  var market=(location.hash==='#market');
  var vr=document.getElementById('viewRanking'),vm=document.getElementById('viewMarket');
  if(vr)vr.style.display=market?'none':'';
  if(vm)vm.style.display=market?'':'none';
  var tr=document.getElementById('tabRanking'),tm=document.getElementById('tabMarket');
  if(tr){tr.classList.toggle('active',!market);tr.setAttribute('aria-current',market?'false':'page');}
  if(tm){tm.classList.toggle('active',market);tm.setAttribute('aria-current',market?'page':'false');}
}
function sectorBars(sectors){
  var maxAbs=1;asArr(sectors).forEach(function(s){var a=Math.abs(Number(s.w_pct)||0);if(a>maxAbs)maxAbs=a;});
  var h='<p class="scroll-hint">表が収まらない場合は左右にスクロールできます。</p><div class="tscroll"><table class="sec33"><thead><tr><th>33業種</th><th>銘柄</th><th class="r">騰落率</th><th class="barcell"></th><th class="r">上昇/下落</th><th class="r">売買代金<br>(億円)</th></tr></thead><tbody>';
  asArr(sectors).forEach(function(s){
    var w=Number(s.w_pct)||0,width=Math.abs(w)/maxAbs*50;
    var bar='<div class="barwrap">'+(w>=0?'<div class="barpos" style="width:'+width+'%"></div>':'<div class="barneg" style="width:'+width+'%"></div>')+'</div>';
    /* 銘柄＝そのセクターの騰落を主導した銘柄（売買代金加重寄与の上位1〜2件。
       build_market_stats.py sector_drivers）。複数該当時は1銘柄=1行で併記し、
       drivers の無い過去データは「—」表示（後方互換）。 */
    var ds=asArr(s.drivers);
    var drv=ds.length?ds.map(function(d){return '<div class="drvrow"><a class="drvname" href="https://kabutan.jp/stock/?code='+esc(fmtCode(d.code))+'" target="_blank" rel="noopener">'+esc(d.name)+'</a><span class="drvpct '+pctClass(d.pct)+'">（'+pctStr(d.pct)+'）</span></div>';}).join(''):'—';
    h+='<tr><td>'+esc(s.name)+'</td>'+
      '<td class="drv">'+drv+'</td>'+
      '<td class="'+pctClass(w)+'">'+pctStr(w)+'</td>'+
      '<td class="barcell">'+bar+'</td>'+
      '<td class="num">'+esc(s.up)+' / '+esc(s.down)+'</td>'+
      '<td class="num">'+fmtTurnover(s.turnover_oku)+'</td></tr>';
  });
  return h+'</tbody></table></div>';
}
function themeSection(tm){
  tm=tm||{};
  var rows=asArr(tm.rows);
  if(!rows.length)return '';
  /* 新形式（買い/売り・テーマ・銘柄・背景）を優先。旧形式（theme/bought/sold の2列）は後方互換で描画。 */
  var isNew=rows.some(function(r){return r&&(r.side!=null||r.stocks!=null||r.background!=null);});
  var h='<div class="msec"><h2>テーマ別の資金フロー</h2><div class="tscroll"><table class="mmatrix">';
  if(isNew){
    h+='<thead><tr><th>方向</th><th class="thmcol">テーマ</th><th>主な銘柄</th><th class="bgcol">背景</th></tr></thead><tbody>';
    rows.forEach(function(r){
      var sell=(r.side==='sell'||r.side==='売り');
      var badge=sell?'<span class="sidebadge sell">売り</span>':'<span class="sidebadge buy">買い</span>';
      /* theme はワンフレーズが原則。「A→B」形式が来た場合は「→」の直前で改行し2行表示する。 */
      h+='<tr class="'+(sell?'rsell':'rbuy')+'"><td>'+badge+'</td>'+
        '<td class="thead-note thmcol">'+esc(r.theme).replace(/→/g,'<br>→')+'</td>'+
        '<td class="stkcol">'+mdInline(r.stocks||'')+'</td>'+
        '<td class="factor">'+mdInline(r.background||'')+'</td></tr>';
    });
  }else{
    h+='<thead><tr><th></th><th><span class="mk mk-buy"></span>買われた</th><th><span class="mk mk-sell"></span>売られた</th></tr></thead><tbody>';
    rows.forEach(function(r){h+='<tr><td class="thead-note">'+esc(r.theme)+'</td><td class="mbuy">'+mdInline(r.bought||'')+'</td><td class="msell">'+mdInline(r.sold||'')+'</td></tr>';});
  }
  h+='</tbody></table></div>';
  if(tm.character)h+='<div class="mnote" style="margin-top:8px">'+mdInline(tm.character)+'</div>';
  return h+'</div>';
}
function renderMarket(){
  var el=document.getElementById('marketArea');if(!el)return;
  if(!marketData){el.innerHTML=marketState==='loading'?'<div class="loading">市場分析を読み込み中…</div>':marketState==='error'?'<div class="empty">市場分析を読み込めませんでした。<button type="button" onclick="retryDate()">再読み込み</button></div>':'<div class="empty">この日付の市場分析は公開されていません。</div>';return;}
  try{
  var d=marketData,u=d.universe||{},h='';
  h+='<div class="msec mhead"><div class="kick">市場分析｜MARKET ANALYSIS</div><h2>'+esc(d.title||'市場分析')+'</h2>';
  h+='<div class="lead">対象日 '+esc(d.session_date||'')+(d.prev_date?'（前営業日 '+esc(d.prev_date)+'）':'')+(u.description?'　／　'+esc(u.description)+(u.n_liquid?'（'+Number(u.n_liquid).toLocaleString('ja-JP')+'銘柄）':''):'')+'</div>';
  if(d.thesis){
    if(Array.isArray(d.thesis)){h+='<blockquote class="mthesis"><ul>';asArr(d.thesis).forEach(function(t){h+='<li>'+mdInline(t)+'</li>';});h+='</ul></blockquote>';}
    else h+='<blockquote class="mthesis">'+mdInline(d.thesis)+'</blockquote>';
  }
  h+='</div>';
  var ov=d.overview||{};
  if(ov.snapshot&&ov.snapshot.length){
    h+='<div class="msec"><h2>市場概況</h2><table class="kv"><tbody>';
    ov.snapshot.forEach(function(r){h+='<tr><td class="k">'+esc(r.label)+'</td><td class="v">'+esc(r.value)+'</td><td class="n">'+mdInline(r.note||'')+'</td></tr>';});
    h+='</tbody></table>';
    if(ov.points&&ov.points.length){h+='<ul>';ov.points.forEach(function(p){h+='<li>'+mdInline(p)+'</li>';});h+='</ul>';}
    if(ov.flow&&ov.flow.length){h+='<div class="lead" style="margin-top:6px">一日の構図</div><ol>';ov.flow.forEach(function(p){h+='<li>'+mdInline(p)+'</li>';});h+='</ol>';}
    if(ov.flow_conclusion){
      if(Array.isArray(ov.flow_conclusion)){h+='<div class="flowc"><ul>';asArr(ov.flow_conclusion).forEach(function(p){h+='<li>'+mdInline(p)+'</li>';});h+='</ul></div>';}
      else h+='<div class="flowc">'+mdInline(ov.flow_conclusion)+'</div>';
    }
    h+='</div>';
  }
  h+=themeSection(d.theme_matrix);
  if(d.sectors33&&d.sectors33.length){
    h+='<div class="msec"><h2>セクター騰落率（東証33業種・売買代金加重）</h2>'+sectorBars(d.sectors33)+'</div>';
  }
  h+='<div class="msec"><details><summary class="msum">データ・手法・出典</summary>';
  var me=d.methodology||{};
  if(me.lines&&me.lines.length){h+='<ul style="margin-top:10px">';me.lines.forEach(function(l){h+='<li>'+mdInline(l)+'</li>';});h+='</ul>';}
  if(d.news_sources&&d.news_sources.length){
    h+='<div class="mnote" style="margin-top:8px"><span class="thead-note">ニュース・個別材料の出典'+(d.sources_accessed?'（アクセス: '+esc(d.sources_accessed)+'）':'')+'</span></div><ul>';
    asArr(d.news_sources).forEach(function(ns){var ls=asArr(ns.links).map(function(l){const url=safeUrl(l.url);return url?'<a href="'+esc(url)+'" target="_blank" rel="noopener">'+esc(l.label)+'</a>':esc(l.label);}).join('／');h+='<li>'+esc(ns.topic)+'：'+ls+'</li>';});
    h+='</ul>';
  }
  h+='</details>';
  if(d.disclaimer&&d.disclaimer.length){h+='<div class="mfoot" style="margin-top:10px">';d.disclaimer.forEach(function(l){h+='<div>・'+mdInline(l)+'</div>';});h+='</div>';}
  h+='</div>';
  el.innerHTML=h;
  }catch(e){el.innerHTML='<div class="empty">市場分析の表示中にエラーが発生しました（データ形式の可能性）。</div>';if(window.console&&console.error)console.error(e);}
}
window.addEventListener('hashchange',applyHash);
window.addEventListener('popstate',()=>loadDate(new URL(location.href).searchParams.get('date')||availableDates[0],false));
init();
