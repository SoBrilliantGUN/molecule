"""预生成示例缓存：无 API Key 也能跑通「一句话 → 应用」的演示兜底。

每个示例是一份完整、自包含的单文件 HTML（内联 CSS/JS + localStorage）。它们**不**预置到
「我的项目」——只有当用户用对应提示词生成时，`match_mock()` 命中并直接返回缓存 HTML，
做到「相同提示词 → 相同应用」，且不消耗模型额度。
"""

_READING_NOTES = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>读书笔记</title>
<style>
  :root { --bg:#f6f3ee; --ink:#2a2622; --line:#e3ddd2; --accent:#b06a3a; --card:#fffdf8; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--ink); font-family:'Noto Serif SC',Georgia,serif; padding:24px; line-height:1.6; }
  .wrap { max-width:640px; margin:0 auto; }
  h1 { font-size:26px; letter-spacing:.02em; margin-bottom:4px; }
  .sub { color:#8a8175; font-size:13px; margin-bottom:20px; }
  form { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px; }
  input, select, button { padding:9px 12px; border:1px solid var(--line); border-radius:8px; font-size:14px; background:var(--card); }
  input[type=text] { flex:1; min-width:140px; }
  button { background:var(--accent); color:#fff; border:none; cursor:pointer; }
  ul { list-style:none; display:flex; flex-direction:column; gap:10px; }
  li { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:flex; align-items:center; gap:10px; }
  .meta { flex:1; }
  .title { font-weight:600; }
  .detail { color:#8a8175; font-size:12px; }
  .stars { color:#c9a04b; letter-spacing:1px; }
  .del { background:none; border:none; color:#b0554a; cursor:pointer; font-size:16px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>读书笔记</h1>
  <p class="sub">记录书名、作者、阅读状态与评分（数据保存在本地）</p>
  <form id="f">
    <input id="title" placeholder="书名" required>
    <input id="author" placeholder="作者">
    <select id="status"><option>未读</option><option>在读</option><option>读完</option></select>
    <select id="rating"><option value="0">☆</option><option value="1">★</option><option value="2">★★</option><option value="3">★★★</option><option value="4">★★★★</option><option value="5">★★★★★</option></select>
    <button>添加</button>
  </form>
  <ul id="list"></ul>
</div>
<script>
  const K='reading_notes';
  const load=()=>JSON.parse(localStorage.getItem(K)||'[]');
  const save=(d)=>localStorage.setItem(K,JSON.stringify(d));
  function render(){
    const d=load();
    document.getElementById('list').innerHTML=d.map((b,i)=>
      `<li><div class="meta"><div class="title">${esc(b.title)}</div><div class="detail">${esc(b.author||'佚名')} · ${esc(b.status)}</div></div><span class="stars">${'★'.repeat(b.rating)}${'☆'.repeat(5-b.rating)}</span><button class="del" data-i="${i}">×</button></li>`
    ).join('');
  }
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  document.getElementById('f').addEventListener('submit',e=>{
    e.preventDefault();
    const d=load();
    d.push({title:document.getElementById('title').value,author:document.getElementById('author').value,status:document.getElementById('status').value,rating:+document.getElementById('rating').value});
    save(d); e.target.reset(); render();
  });
  document.getElementById('list').addEventListener('click',e=>{
    if(e.target.classList.contains('del')){const d=load();d.splice(+e.target.dataset.i,1);save(d);render();}
  });
  render();
</script>
</body>
</html>"""

_FITNESS_LOG = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>健身打卡</title>
<style>
  :root { --bg:#0f1115; --ink:#e8ecf1; --line:#23262e; --accent:#4dd0a1; --card:#171a20; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--ink); font-family:'Inter','PingFang SC',system-ui,sans-serif; padding:24px; line-height:1.6; }
  .wrap { max-width:640px; margin:0 auto; }
  h1 { font-size:24px; font-weight:700; }
  .sub { color:#7f8792; font-size:13px; margin-bottom:20px; }
  form { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px; }
  input, button { padding:10px 12px; border:1px solid var(--line); border-radius:8px; font-size:14px; background:var(--card); color:var(--ink); }
  input[type=text], input[type=number] { flex:1; min-width:110px; }
  button { background:var(--accent); color:#08130d; font-weight:600; border:none; cursor:pointer; }
  ul { list-style:none; display:flex; flex-direction:column; gap:10px; }
  li { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:flex; align-items:center; gap:10px; }
  .date { font-family:monospace; color:var(--accent); font-size:12px; }
  .meta { flex:1; }
  .ex { font-weight:600; }
  .detail { color:#7f8792; font-size:12px; }
  .del { background:none; border:none; color:#e06c6c; cursor:pointer; font-size:16px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>健身打卡</h1>
  <p class="sub">记录日期、动作、组数与重量（数据保存在本地）</p>
  <form id="f">
    <input id="date" type="date" required>
    <input id="ex" placeholder="动作（如卧推）" required>
    <input id="sets" type="number" placeholder="组数" min="1">
    <input id="weight" type="number" placeholder="重量kg">
    <button>记录</button>
  </form>
  <ul id="list"></ul>
</div>
<script>
  const K='fitness_log';
  const load=()=>JSON.parse(localStorage.getItem(K)||'[]');
  const save=(d)=>localStorage.setItem(K,JSON.stringify(d));
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function render(){
    const d=load();
    document.getElementById('list').innerHTML=d.map((r,i)=>
      `<li><span class="date">${esc(r.date)}</span><div class="meta"><div class="ex">${esc(r.ex)}</div><div class="detail">${r.sets||'—'} 组${r.weight?' · '+r.weight+'kg':''}</div></div><button class="del" data-i="${i}">×</button></li>`
    ).join('');
  }
  document.getElementById('f').addEventListener('submit',e=>{
    e.preventDefault();
    const d=load();
    d.push({date:document.getElementById('date').value,ex:document.getElementById('ex').value,sets:document.getElementById('sets').value,weight:document.getElementById('weight').value});
    save(d); e.target.reset(); document.getElementById('date').value=new Date().toISOString().slice(0,10); render();
  });
  document.getElementById('list').addEventListener('click',e=>{
    if(e.target.classList.contains('del')){const d=load();d.splice(+e.target.dataset.i,1);save(d);render();}
  });
  document.getElementById('date').value=new Date().toISOString().slice(0,10);
  render();
</script>
</body>
</html>"""

_RECRUITMENT = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>招聘跟进</title>
<style>
  :root { --bg:#fbf9f4; --ink:#1f2430; --line:#e5e0d6; --accent:#3b6ea5; --card:#ffffff; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--ink); font-family:'Inter','PingFang SC',system-ui,sans-serif; padding:24px; line-height:1.6; }
  .wrap { max-width:680px; margin:0 auto; }
  h1 { font-size:24px; font-weight:700; }
  .sub { color:#8b8a86; font-size:13px; margin-bottom:20px; }
  form { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:20px; }
  input, select, button { padding:10px 12px; border:1px solid var(--line); border-radius:8px; font-size:14px; background:var(--card); }
  input[type=text] { flex:1; min-width:130px; }
  button { background:var(--accent); color:#fff; border:none; cursor:pointer; }
  table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  th, td { text-align:left; padding:11px 14px; border-bottom:1px solid var(--line); font-size:14px; }
  th { background:#f1eee7; font-weight:600; }
  tr:last-child td { border-bottom:none; }
  .stage { font-size:12px; padding:2px 8px; border-radius:12px; background:#eef3f8; color:var(--accent); }
  .stage.offer { background:#e8f5ec; color:#2f8a54; }
  .stage.reject { background:#fbeaea; color:#b0554a; }
  .del { background:none; border:none; color:#b0554a; cursor:pointer; }
</style>
</head>
<body>
<div class="wrap">
  <h1>招聘跟进</h1>
  <p class="sub">跟踪候选人、岗位与面试阶段（数据保存在本地）</p>
  <form id="f">
    <input id="name" placeholder="候选人姓名" required>
    <input id="role" placeholder="岗位">
    <select id="stage"><option>筛选</option><option>面试</option><option>Offer</option><option>拒绝</option></select>
    <button>添加</button>
  </form>
  <table>
    <thead><tr><th>候选人</th><th>岗位</th><th>阶段</th><th></th></tr></thead>
    <tbody id="list"></tbody>
  </table>
</div>
<script>
  const K='recruitment';
  const load=()=>JSON.parse(localStorage.getItem(K)||'[]');
  const save=(d)=>localStorage.setItem(K,JSON.stringify(d));
  const esc=(s)=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cls={'Offer':'offer','拒绝':'reject'};
  function render(){
    const d=load();
    document.getElementById('list').innerHTML=d.map((r,i)=>
      `<tr><td>${esc(r.name)}</td><td>${esc(r.role||'—')}</td><td><span class="stage ${cls[r.stage]||''}">${esc(r.stage)}</span></td><td><button class="del" data-i="${i}">删除</button></td></tr>`
    ).join('');
  }
  document.getElementById('f').addEventListener('submit',e=>{
    e.preventDefault();
    const d=load();
    d.push({name:document.getElementById('name').value,role:document.getElementById('role').value,stage:document.getElementById('stage').value});
    save(d); e.target.reset(); render();
  });
  document.getElementById('list').addEventListener('click',e=>{
    if(e.target.classList.contains('del')){const d=load();d.splice(+e.target.dataset.i,1);save(d);render();}
  });
  render();
</script>
</body>
</html>"""

EXAMPLES = [
    {"name": "读书笔记", "prompt": "做一个读书笔记管理，记录书名、作者、阅读状态和评分", "html": _READING_NOTES},
    {"name": "健身打卡", "prompt": "做一个健身打卡应用，记录日期、动作、组数、重量", "html": _FITNESS_LOG},
    {"name": "招聘跟进", "prompt": "做一个招聘跟进表，包含候选人、岗位、面试阶段", "html": _RECRUITMENT},
]


def match_mock(prompt: str) -> dict | None:
    """命中预生成示例则返回 {name, html}，否则 None。按示例名在提示词中出现做宽松匹配。"""
    p = prompt or ""
    for ex in EXAMPLES:
        if ex["name"] in p:
            return {"name": ex["name"], "html": ex["html"]}
    return None
