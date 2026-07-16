let data=null;
let marketData=null;
function fmtMcap(o,f){if(o==null)return '—';return o.toLocaleString('ja-JP')+(f||'');}
function fmtPct(p){return p==null?'—':'+'+Number(p).toFixed(2)+'%';}
function fmtPct5(p){return p==null?'':'('+(p>0?'+':'')+Number(p).toFixed(2)+'%)';}
function fmtNum(x){return x==null?'—':Number(x).toLocaleString('ja-JP');}
function fmtTurnover(t){return t==null?'—':Math.round(Number(t)).toLocaleString('ja-JP');}
function fmtTurnoverOku(r){let v=(r.turnover_yen!=null)?Number(r.turnover_yen)/1e8:(r.turnover_m!=null?Number(r.turnover_m)/100:null);return v==null?'—':Math.round(v).toLocaleString('ja-JP');}
function fmtMarket(m){m=m||'';if(m.indexOf('プライム')>=0)return 'Prime';if(m.indexOf('スタンダード')>=0)return 'Standard';if(m.indexOf('グロース')>=0)return 'Growth';return m;}
function fmtCode(c){c=(c==null?'':String(c));return (c.length===5&&c.endsWith('0'))?c.slice(0,4):c;}
function fmtMcapCell(o,f){if(o==null)return '—';o=Number(o);var s=o>=10000?(o/10000).toFixed(1)+'兆円':Math.round(o).toLocaleString('ja-JP')+'億円';return s+(f||'');}
function changeYen(r){if(r==null||r.close==null||r.adj_close==null||r.prev_adj_close==null)return null;var a=Number(r.adj_close);if(!a)return null;return Math.round(Number(r.close)-Number(r.prev_adj_close)*Number(r.close)/a);}
function fmtSigned(v){if(v==null)return '—';var n=Number(v);return (n>=0?'+':'')+n.toLocaleString('ja-JP');}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function kindBadge(k){k=(k||'').replace(/[\[\]]/g,'');if(!k)return '';return '<span class="kind k'+k+'">'+k+'</span>';}
function openInfo(){var d=document.getElementById('infoModal');if(d&&d.showModal)d.showModal();}
function closeInfoOnBackdrop(e){if(e.target===e.currentTarget)e.currentTarget.close();}
async function init(){
  try{
    const m=await (await fetch('data/manifest.json?'+Date.now())).json();
    const sel=document.getElementById('dateSelect');sel.innerHTML='';
    if(!m.dates||!m.dates.length){sel.innerHTML='<option>データなし</option>';document.getElementById('tableArea').innerHTML='<div class="empty">まだデータがありません。</div>';return;}
    m.dates.forEach((d,i)=>{const o=document.createElement('option');o.value=d;const dt=new Date(d+'T00:00:00');o.textContent=d+' ('+['日','月','火','水','木','金','土'][dt.getDay()]+')';if(i===0)o.selected=true;sel.appendChild(o);});
    window.addEventListener('hashchange',applyHash);
    loadDate(m.dates[0]);
  }catch(e){document.getElementById('tableArea').innerHTML='<div class="empty">データの読み込みに失敗しました。</div>';}
}
async function loadDate(d){
  if(!d)return;
  document.getElementById('tableArea').innerHTML='<div class="loading">読み込み中…</div>';
  const marketReq=fetch('data/'+d+'_market.json?'+Date.now()).then(r=>{if(!r.ok)throw 0;return r.json();}).catch(()=>null);
  try{data=await (await fetch('data/'+d+'.json?'+Date.now())).json();render();}
  catch(e){document.getElementById('tableArea').innerHTML='<div class="empty">この日付のデータを読み込めませんでした。</div>';}
  try{marketData=await marketReq;}catch(e){marketData=null;}
  renderMarket();applyHash();
}
function render(){
  const rows=data.rows||data.items||[];   /* data.items は旧形式 JSON 後方互換 */
  const cnt=data.counts||{};
  let total=cnt.qualifying;
  if(total==null) total=(data.count_total!=null?data.count_total:rows.length);
  const cntChip = data.capped
    ? '<div class="chip"><span class="num">'+total+'</span> 社該当（上位 '+rows.length+' 社を掲載）</div>'
    : '<div class="chip"><span class="num">'+rows.length+'</span> 社該当</div>';
  document.getElementById('summary').innerHTML=
    cntChip+
    '<button type="button" class="infobtn" onclick="openInfo()"><span class="i">i</span>データ情報</button>';
  const c=data.criteria||{};
  document.getElementById('infoBody').innerHTML=
    '<div class="k">データ対象日時</div><div class="v">'+esc(data.session_window||'—')+'</div>'+
    '<div class="k">生成日時</div><div class="v">'+esc(data.generated_at||'—')+'</div>'+
    '<div class="k">抽出条件</div><div class="v">'+esc('値上がり率≥+'+(c.min_pct??5)+'% かつ 売買代金≥'+((c.min_turnover_yen??1e7)/1e6)+'百万円／東証個別株のみ・時価総額≥'+(c.min_mcap_oku??100)+'億円'+(c.max_rank?'・上昇率上位'+c.max_rank+'社':'')+'。時価総額は当日終値×発行済株式数（億円・四捨五入）。† は増資・自己株で株探最新株数と>1%乖離。')+'</div>';
  let h='<table><thead><tr><th class="r">#</th><th>コード</th><th>銘柄</th><th>市場</th><th class="r">前日比%<br>(5営業日)</th><th class="r">前日比<br>(円)</th><th class="r">終値<br>(円)</th><th class="r">売買代金<br>(億円)</th><th>変動要因</th></tr></thead><tbody>';
  rows.forEach(r=>{
    let factor=mdInline(r.factor||'（材料未確認）');
    const fk=(r.factor_kind||'').replace(/[\[\]]/g,'');
    if(fk==='開示'&&r.disclosures&&r.disclosures.length&&r.disclosures[0].pdf_url){factor=factor+' <a href="'+esc(r.disclosures[0].pdf_url)+'" target="_blank">[開示PDF]</a>';}
    const code=fmtCode(r.code);
    h+='<tr>'+
      '<td class="rank">'+(r.rank||'')+'</td>'+
      '<td class="code rankcode" data-rank="'+(r.rank||'')+'"><a href="https://kabutan.jp/stock/?code='+esc(code)+'" target="_blank">'+esc(code)+'</a></td>'+
      '<td class="name" data-code="'+esc(code)+'">'+esc(r.name)+'<span class="code-inline">（'+esc(code)+'）</span><span class="mcap">'+fmtMcapCell(r.mcap_oku,r.mcap_flag)+'</span></td>'+
      '<td class="mkt">'+esc(fmtMarket(r.market))+'</td>'+
      '<td class="pct" data-label="前日比%(5営業日)">'+fmtPct(r.pct)+(r.pct5!=null?'<span class="pct5">'+fmtPct5(r.pct5)+'</span>':'')+'</td>'+
      '<td class="num" data-label="前日比(円)">'+fmtSigned(changeYen(r))+'</td>'+
      '<td class="num" data-label="終値(円)">'+fmtNum(r.close)+'</td>'+
      '<td class="num" data-label="売買代金(億円)">'+fmtTurnoverOku(r)+'</td>'+
      '<td class="factor">'+kindBadge(r.factor_kind)+factor+'</td>'+
    '</tr>';
  });
  h+='</tbody></table>';
  document.getElementById('tableArea').innerHTML=h;
  // 除外（薄商い／時価総額<100億）を折りたたみで
  const c2=data.criteria||{};
  const tmM=((c2.min_turnover_yen??1e7)/1e6);
  const mcO=(c2.min_mcap_oku??100);
  const pctMin=(c2.min_pct??5);
  let dh='';
  const dt=data.dropped_turnover||[];
  if(dt.length){
    dh+='<details class="dropped card" style="padding:0 14px 10px;"><summary>参考：値上がり率≥+'+pctMin+'% だが売買代金&lt;'+tmM+'百万円 で除外（薄商い '+dt.length+'件）</summary><table><thead><tr><th>コード</th><th>銘柄</th><th class="r">前日比%<br>(5営業日)</th><th class="r">売買代金<br>(百万円)</th></tr></thead><tbody>';
    dt.forEach(r=>{dh+='<tr><td class="code">'+esc(fmtCode(r.code))+'</td><td class="name">'+esc(r.name)+'</td><td class="pct" data-label="前日比%(5営業日)">'+fmtPct(r.pct)+(r.pct5!=null?'<span class="pct5">'+fmtPct5(r.pct5)+'</span>':'')+'</td><td class="num" data-label="売買代金(百万円)">'+fmtTurnover(r.turnover_m)+'</td></tr>';});
    dh+='</tbody></table></details>';
  }
  const dm=data.dropped_mcap||[];
  if(dm.length){
    dh+='<details class="dropped card" style="padding:0 14px 10px;margin-top:12px;"><summary>参考：値上がり率・売買代金は満たすが時価総額&lt;'+mcO+'億円 で除外（'+dm.length+'件）</summary><table><thead><tr><th>コード</th><th>銘柄</th><th class="r">前日比%<br>(5営業日)</th><th class="r">時価総額<br>(億円)</th></tr></thead><tbody>';
    dm.forEach(r=>{dh+='<tr><td class="code">'+esc(fmtCode(r.code))+'</td><td class="name">'+esc(r.name)+'</td><td class="pct" data-label="前日比%(5営業日)">'+fmtPct(r.pct)+(r.pct5!=null?'<span class="pct5">'+fmtPct5(r.pct5)+'</span>':'')+'</td><td class="num" data-label="時価総額(億円)">'+fmtMcap(r.mcap_oku)+'</td></tr>';});
    dh+='</tbody></table></details>';
  }
  document.getElementById('droppedArea').innerHTML=dh;
}
/* ===================== 市場分析ビュー ===================== */
function mdInline(s){
  s=esc(s==null?'':s);
  s=s.replace(/\[\[([^\]]+)\]\]/g,'<span class="stk">$1</span>');
  s=s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  return s;
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
  if(tr)tr.classList.toggle('active',!market);
  if(tm)tm.classList.toggle('active',market);
}
function sectorBars(sectors){
  var maxAbs=1;asArr(sectors).forEach(function(s){var a=Math.abs(Number(s.w_pct)||0);if(a>maxAbs)maxAbs=a;});
  var h='<div class="tscroll"><table class="sec33"><thead><tr><th>33業種</th><th>銘柄</th><th class="r">騰落率</th><th class="barcell"></th><th class="r">上昇/下落</th><th class="r">売買代金<br>(億円)</th></tr></thead><tbody>';
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
      '<td class="num">'+s.up+' / '+s.down+'</td>'+
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
  if(!marketData){el.innerHTML='<div class="empty">この日付の市場分析データはありません。</div>';return;}
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
    asArr(d.news_sources).forEach(function(ns){var ls=asArr(ns.links).map(function(l){return '<a href="'+esc(l.url)+'" target="_blank" rel="noopener">'+esc(l.label)+'</a>';}).join('／');h+='<li>'+esc(ns.topic)+'：'+ls+'</li>';});
    h+='</ul>';
  }
  h+='</details>';
  if(d.disclaimer&&d.disclaimer.length){h+='<div class="mfoot" style="margin-top:10px">';d.disclaimer.forEach(function(l){h+='<div>・'+mdInline(l)+'</div>';});h+='</div>';}
  h+='</div>';
  el.innerHTML=h;
  }catch(e){el.innerHTML='<div class="empty">市場分析の表示中にエラーが発生しました（データ形式の可能性）。</div>';if(window.console&&console.error)console.error(e);}
}
init();
