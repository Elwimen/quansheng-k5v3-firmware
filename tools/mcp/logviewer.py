#!/usr/bin/env python3
"""Live browser view of a uvradio MCP session log (Flask + SSE).

Tails a logs/session-*.jsonl and streams it to the browser, rendering a timeline of
every tool call, its result, and — the useful part — the radio screen at each step.

    python3 tools/mcp/logviewer.py            # http://127.0.0.1:8090
    python3 tools/mcp/logviewer.py --port 9000 --log-dir /path/to/logs

Deliberately a SEPARATE process from the MCP server: the viewer then survives MCP
restarts, never fights for a port across sessions, and can open old sessions.
SSE (not websockets) because the stream is one-way — `EventSource` is three lines.

UI: NOW pane (latest screen + in-flight), screen filmstrip, category colours,
duration heat, identical-screen dedupe, chapter notes, follow-tail, filter/search,
session picker, keyboard nav, image lightbox, and a screen diff (green = pixels
that turned on, red = turned off).
"""
import argparse
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

app = Flask(__name__)
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def _logs():
    return sorted(LOG_DIR.glob("session-*.jsonl"), key=lambda p: p.stat().st_mtime)


def newest_log():
    logs = _logs()
    return logs[-1] if logs else None


PAGE = r"""<!doctype html><meta charset=utf-8><title>uvradio session log</title>
<style>
 :root{--bg:#0e1116;--fg:#d7dce3;--dim:#7d8794;--line:#232a34;--ok:#3fb950;--err:#f85149;--acc:#58a6ff;
       --build:#d29922;--sim:#58a6ff;--screen:#3fb950;--input:#bc8cff;--flash:#f85149;--debug:#39c5cf;--log:#7d8794}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
   font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
 header{position:sticky;top:0;background:#11161d;border-bottom:1px solid var(--line);
   padding:8px 16px;display:flex;gap:14px;align-items:center;z-index:10;flex-wrap:wrap}
 h1{font-size:14px;margin:0;font-weight:600} .meta{color:var(--dim);font-size:12px}
 #dot{width:8px;height:8px;border-radius:50%;background:var(--err)} #dot.on{background:var(--ok)}
 .stat{color:var(--dim);font-size:12px} .stat b{color:var(--fg)} .stat.bad b{color:var(--err)}
 button,select,input{background:#1b2430;color:var(--fg);border:1px solid var(--line);
   border-radius:5px;padding:3px 8px;font:inherit;font-size:12px}
 button{cursor:pointer;color:var(--dim)} button.on{color:var(--ok);border-color:var(--ok)}
 #follow{margin-left:auto}
 #bar{position:sticky;top:41px;z-index:9;display:flex;gap:8px;align-items:center;flex-wrap:wrap;
   padding:6px 16px;background:#0d131a;border-bottom:1px solid var(--line)}
 #q{flex:1;min-width:140px;max-width:320px}
 .chip{padding:2px 8px;border-radius:99px;border:1px solid var(--line);cursor:pointer;
   font-size:11px;color:var(--dim);user-select:none}
 .chip.off{opacity:.35;text-decoration:line-through}
 #film{display:flex;gap:6px;overflow-x:auto;padding:8px 16px;background:#0b0f14;
   border-bottom:1px solid var(--line);position:sticky;top:76px;z-index:8;min-height:64px}
 #film img{height:48px;width:96px;cursor:pointer;opacity:.6;flex:none;margin:0;border:1px solid var(--line)}
 #film img:hover,#film img.cur{opacity:1;outline:1px solid var(--acc)}
 .wrap{display:grid;grid-template-columns:1fr 300px;gap:16px;padding:16px;max-width:1300px;margin:0 auto}
 @media(max-width:900px){.wrap{grid-template-columns:1fr}}
 aside{position:sticky;top:150px;align-self:start}
 .now{border:1px solid var(--line);border-radius:6px;background:#131922;padding:10px}
 .now h2{margin:0 0 8px;font-size:12px;color:var(--dim);font-weight:600;letter-spacing:.05em}
 .now img{width:100%;margin:0;cursor:zoom-in}
 .live{margin-top:8px;font-size:12px;color:var(--dim)} .live b{color:var(--acc)}
 #hist{margin-top:10px;font-size:11px;color:var(--dim)}
 #hist div{display:flex;align-items:center;gap:6px} #hist i{height:6px;border-radius:2px;display:block}
 .row{border:1px solid var(--line);border-left:3px solid var(--line);border-radius:6px;
   margin:6px 0;background:#131922;overflow:hidden;scroll-margin-top:160px}
 .row.hide{display:none} .row.sel{outline:1px solid var(--acc)}
 .hd{padding:6px 12px;display:flex;gap:10px;align-items:center;cursor:pointer}
 .tool{font-weight:600} .id,.t{color:var(--dim);font-size:12px}
 .dur{margin-left:auto;font-size:12px;color:var(--dim)}
 .dur.warn{color:var(--build)} .dur.slow{color:var(--err)}
 .bd{padding:0 12px 10px;display:none} .row.open .bd{display:block}
 pre{margin:6px 0;padding:8px;background:#0b0f14;border:1px solid var(--line);border-radius:4px;
   white-space:pre-wrap;word-break:break-word;color:#aab3bf;max-height:340px;overflow:auto;font-size:12px}
 .shot{position:relative;display:inline-block}
 .shot img,.shot canvas{image-rendering:pixelated;border:1px solid var(--line);border-radius:4px;
   background:#000;width:512px;max-width:100%;display:block;margin:6px 0;cursor:zoom-in}
 .shot button{position:absolute;right:6px;top:12px;opacity:.75}
 .bad{color:var(--err)} .row.pending{opacity:.75} .row.pending .tool::after{content:' …';color:var(--acc)}
 .row.err{border-left-color:var(--err)}
 .sess{border-left:3px solid var(--acc);padding-left:10px;color:var(--dim);margin:10px 0;font-size:12px}
 .note{margin:14px 0 6px;padding:6px 10px;border-radius:6px;background:#1b2430;
   border-left:3px solid var(--acc);color:var(--fg);font-weight:600}
 .same{color:var(--dim);font-style:italic;font-size:12px;margin:6px 0}
 #box{position:fixed;inset:0;background:#000c;display:none;z-index:99;cursor:zoom-out;
   align-items:center;justify-content:center;padding:24px}
 #box.on{display:flex} #box img{image-rendering:pixelated;max-width:98vw;max-height:94vh;
   border:1px solid var(--line);background:#000}
 kbd{background:#1b2430;border:1px solid var(--line);border-radius:3px;padding:0 4px;font-size:11px}
</style>
<header>
  <span id=dot></span><h1>uvradio</h1>
  <span class=meta id=meta>connecting…</span>
  <span class=stat>calls <b id=ncalls>0</b></span>
  <span class="stat" id=errbox>errors <b id=nerrs>0</b></span>
  <span class=stat>elapsed <b id=elapsed>0s</b></span>
  <button id=follow class=on title="auto-scroll to newest (pauses when you scroll up)">⇊ following</button>
</header>
<div id=bar>
  <select id=sess title="session"></select>
  <input id=q placeholder="search tool / text …  (press /)">
  <span id=chips></span>
  <button id=onlyerr title="errors only">! errors</button>
  <button id=diffbtn title="show what changed vs the previous screen">⧉ diff</button>
  <span class=meta id=shown></span>
</div>
<div id=film></div>
<div class=wrap>
  <main id=out></main>
  <aside>
    <div class=now>
      <h2>NOW</h2>
      <img id=nowimg alt="latest screen">
      <div class=live id=live>idle</div>
      <div id=hist></div>
    </div>
  </aside>
</div>
<div id=box><img id=boximg></div>
<script>
const $=i=>document.getElementById(i);
const out=$('out'),dot=$('dot'),meta=$('meta'),film=$('film'),nowimg=$('nowimg'),live=$('live');
const CATS=['build','sim','screen','input','flash','debug','log','other'];
const ICON={build:'⚙',sim:'▣',screen:'▦',input:'⌨',flash:'⚡',debug:'✱',log:'✎',other:'•'};
const esc=s=>(s??'').toString().replace(/[<&>]/g,c=>({'<':'&lt;','&':'&amp;','>':'&gt;'}[c]));
let rows={},ncalls=0,nerrs=0,t0=Date.now(),lastSha=null,inflight=new Set(),
    shots=[],counts={},es=null,sel=-1,offCats=new Set(),onlyErr=false,diffOn=false;

/* ---------- follow-tail: stick to newest, but never fight a reader ---------- */
const btn=$('follow'); let follow=true;
const atBottom=()=>(innerHeight+scrollY)>=(document.body.scrollHeight-80);
const paintFollow=()=>{btn.classList.toggle('on',follow);btn.textContent=follow?'⇊ following':'⇊ paused';};
const tail=()=>{ if(follow) scrollTo({top:document.body.scrollHeight,behavior:'smooth'}); };
addEventListener('scroll',()=>{const f=atBottom(); if(f!==follow){follow=f;paintFollow();}},{passive:true});
btn.onclick=()=>{follow=!follow;paintFollow();tail();};

/* ---------- filter ---------- */
CATS.forEach(c=>{const s=document.createElement('span');s.className='chip';s.dataset.c=c;
  s.style.color=`var(--${c})`;s.textContent=ICON[c]+' '+c;
  s.onclick=()=>{offCats.has(c)?offCats.delete(c):offCats.add(c);s.classList.toggle('off');filter();};
  $('chips').appendChild(s);});
$('q').oninput=filter;
$('onlyerr').onclick=()=>{onlyErr=!onlyErr;$('onlyerr').classList.toggle('on',onlyErr);filter();};
function filter(){
  const q=$('q').value.toLowerCase(); let n=0,tot=0;
  for(const el of out.querySelectorAll('.row')){
    tot++;
    const hit=(!q||(el.dataset.hay||'').includes(q))
          && !offCats.has(el.dataset.cat||'other')
          && (!onlyErr||el.classList.contains('err'));
    el.classList.toggle('hide',!hit); if(hit)n++;
  }
  $('shown').textContent = (q||offCats.size||onlyErr)?`${n}/${tot} shown`:'';
}

/* ---------- lightbox ---------- */
const box=$('box'),boximg=$('boximg');
box.onclick=()=>box.classList.remove('on');
const zoom=src=>{boximg.src=src;box.classList.add('on');};
nowimg.onclick=()=>nowimg.src&&zoom(nowimg.src);

/* ---------- screen diff: green = turned on, red = turned off ---------- */
const loadImg=s=>new Promise(r=>{const i=new Image();i.onload=()=>r(i);i.src=s;});
async function diffTo(el,prev,cur){
  const [a,b]=await Promise.all([loadImg(prev),loadImg(cur)]);
  const w=a.naturalWidth,h=a.naturalHeight,c=document.createElement('canvas');
  c.width=w;c.height=h; const x=c.getContext('2d',{willReadFrequently:true});
  x.drawImage(b,0,0); const B=x.getImageData(0,0,w,h);
  x.drawImage(a,0,0); const A=x.getImageData(0,0,w,h);
  const O=x.createImageData(w,h);
  for(let i=0;i<B.data.length;i+=4){
    const on=B.data[i]>127, was=A.data[i]>127;
    if(on===was){ const v=on?70:0; O.data[i]=O.data[i+1]=O.data[i+2]=v; }
    else if(on){ O.data[i]=0; O.data[i+1]=255; O.data[i+2]=0; }      // turned ON
    else       { O.data[i]=255; O.data[i+1]=0; O.data[i+2]=0; }      // turned OFF
    O.data[i+3]=255;
  }
  x.putImageData(O,0,0); c.onclick=()=>zoom(c.toDataURL());
  el.replaceWith(c); return c;
}
$('diffbtn').onclick=()=>{diffOn=!diffOn;$('diffbtn').classList.toggle('on',diffOn);
  document.querySelectorAll('.shot').forEach(s=>paintShot(s));};
async function paintShot(s){
  const cur=s.dataset.src, prev=s.dataset.prev;
  const node=s.firstElementChild;
  if(diffOn&&prev){ const c=await diffTo(node,prev,cur); c.className='dimg'; }
  else { const i=new Image(); i.src=cur; i.onclick=()=>zoom(cur); node.replaceWith(i); }
}

/* ---------- stats / histogram ---------- */
setInterval(()=>{$('elapsed').textContent=Math.floor((Date.now()-t0)/1000)+'s';},1000);
function stats(){
  $('ncalls').textContent=ncalls; $('nerrs').textContent=nerrs;
  $('errbox').className='stat'+(nerrs?' bad':'');
  live.innerHTML=inflight.size?'running <b>'+[...inflight].join(', ')+'</b>':'idle';
  const max=Math.max(1,...Object.values(counts));
  $('hist').innerHTML=Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([c,n])=>
    `<div><i style="width:${Math.round(n/max*90)}px;background:var(--${c})"></i>${ICON[c]||''} ${c} ${n}</div>`).join('');
}

/* ---------- rows ---------- */
function row(e){
  if(rows[e.id])return rows[e.id];
  const el=document.createElement('div'); el.className='row open pending'; el.id='c'+e.id;
  el.dataset.cat=e.cat||'other'; el.dataset.hay=(e.tool+' '+JSON.stringify(e.args||{})).toLowerCase();
  el.style.borderLeftColor=`var(--${e.cat||'other'})`;
  el.innerHTML=`<div class=hd><span class=id>#${e.id}</span>
    <span class=tool style="color:var(--${e.cat||'other'})">${ICON[e.cat]||'•'} ${esc(e.tool)}</span>
    <span class=t>+${((e.t_ms||0)/1000).toFixed(1)}s</span><span class=dur></span></div><div class=bd></div>`;
  el.querySelector('.hd').onclick=()=>el.classList.toggle('open');
  out.appendChild(el); rows[e.id]=el; return el;
}

function onEvent(e){
  if(e.type==='session'){
    const g=e.git||{},f=e.firmware||{};
    meta.textContent=`${e.session} · ${g.branch||'?'}@${g.sha||'?'}${g.dirty?'*':''}`+(f.built?` · fw ${f.built}`:'');
    t0=Date.now();
    const d=document.createElement('div');d.className='sess';
    d.textContent=`session ${e.session} · ${e.ts} · ${e.cwd||''}`;out.appendChild(d);return;
  }
  if(e.type==='note'){
    const d=document.createElement('div');d.className='note';d.textContent='— '+e.text+' —';
    out.appendChild(d);tail();return;
  }
  const el=row(e);
  if(e.type==='call'){ ncalls++;inflight.add(e.tool);stats();
    el.querySelector('.bd').innerHTML=`<pre>args: ${esc(JSON.stringify(e.args))}</pre>`;
    filter();tail();return; }
  el.classList.remove('pending'); inflight.delete(e.tool);
  counts[e.cat||'other']=(counts[e.cat||'other']||0)+1;
  const d=el.querySelector('.dur');
  d.textContent=(e.dur_ms??'')+' ms';
  d.className='dur'+(e.dur_ms>5000?' slow':e.dur_ms>1000?' warn':'');
  const bd=el.querySelector('.bd');
  if(e.type==='error'){ nerrs++;el.classList.add('err');
    bd.innerHTML+=`<pre class=bad>${esc(e.error)}</pre>`;
    el.dataset.hay+=' '+(e.error||'').toLowerCase(); stats();filter();return; }
  if(e.text){ bd.innerHTML+=`<pre>${esc(e.text)}</pre>`; el.dataset.hay+=' '+e.text.toLowerCase(); }
  (e.images||[]).forEach(im=>{
    const src='/'+im.path+'?h='+im.sha256;
    if(im.sha256===lastSha){
      const s=document.createElement('div');s.className='same';s.textContent='screen unchanged';bd.appendChild(s);
    }else{
      const prev=shots.length?shots[shots.length-1].src:null;
      const s=document.createElement('div');s.className='shot';s.dataset.src=src;if(prev)s.dataset.prev=prev;
      const i=new Image();i.src=src;i.onclick=()=>zoom(src);s.appendChild(i);
      const b=document.createElement('button');b.textContent='⧉';b.title='diff vs previous screen';
      b.onclick=ev=>{ev.stopPropagation();s.dataset.solo=s.dataset.solo?'':'1';paintShotSolo(s);};
      if(prev)s.appendChild(b);
      bd.appendChild(s); shots.push({id:e.id,src});
      if(diffOn)paintShot(s);
      const t=new Image();t.src=src;t.title='#'+e.id+' '+e.tool;
      t.onclick=()=>{follow=false;paintFollow();$('c'+e.id).scrollIntoView({block:'center'});};
      film.appendChild(t); film.scrollLeft=film.scrollWidth;
      [...film.children].forEach(c=>c.classList.remove('cur')); t.classList.add('cur');
    }
    lastSha=im.sha256; nowimg.src=src;
  });
  stats(); filter(); tail();
}
async function paintShotSolo(s){
  const node=s.firstElementChild;
  if(s.dataset.solo){ await diffTo(node,s.dataset.prev,s.dataset.src); }
  else { const i=new Image();i.src=s.dataset.src;i.onclick=()=>zoom(s.dataset.src);node.replaceWith(i); }
}

/* ---------- keyboard ---------- */
addEventListener('keydown',ev=>{
  if(ev.target.tagName==='INPUT'){ if(ev.key==='Escape')ev.target.blur(); return; }
  const vis=[...out.querySelectorAll('.row:not(.hide)')];
  const k=ev.key;
  if(k==='/'){ev.preventDefault();$('q').focus();}
  else if(k==='j'||k==='k'){ if(!vis.length)return; follow=false;paintFollow();
    sel=Math.max(0,Math.min(vis.length-1,(sel<0?vis.length-1:sel)+(k==='j'?1:-1)));
    vis.forEach(r=>r.classList.remove('sel')); vis[sel].classList.add('sel');
    vis[sel].scrollIntoView({block:'center'}); }
  else if(k==='e'){ if(sel>=0&&vis[sel])vis[sel].classList.toggle('open'); }
  else if(k==='f'){ follow=!follow;paintFollow();tail(); }
  else if(k==='g'){ follow=false;paintFollow();scrollTo({top:0}); }
  else if(k==='G'){ follow=true;paintFollow();tail(); }
  else if(k==='Escape'){ box.classList.remove('on'); }
});

/* ---------- session picker + stream ---------- */
function connect(name){
  if(es)es.close();
  out.innerHTML='';film.innerHTML='';rows={};shots=[];counts={};
  ncalls=0;nerrs=0;lastSha=null;inflight.clear();sel=-1;nowimg.removeAttribute('src');stats();
  es=new EventSource('/stream'+(name?('?session='+encodeURIComponent(name)):''));
  es.onopen=()=>dot.classList.add('on');
  es.onerror=()=>dot.classList.remove('on');
  es.onmessage=m=>{try{onEvent(JSON.parse(m.data));}catch(err){}};
}
fetch('/sessions').then(r=>r.json()).then(list=>{
  const s=$('sess');
  s.innerHTML='<option value="">▶ newest (live)</option>'+
    list.map(x=>`<option value="${x.name}">${x.name.replace('session-','').replace('.jsonl','')} · ${x.events}e</option>`).join('');
  s.onchange=()=>connect(s.value);
});
connect('');
</script>
"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/health")
def health():
    """Identity marker: lets open_log_viewer tell OUR viewer from some other app
    that merely happens to hold the port."""
    return jsonify({"app": "uvradio-logviewer", "log_dir": str(LOG_DIR),
                    "sessions": len(_logs())})


@app.route("/artifacts/<path:name>")
def artifacts(name):
    return send_from_directory(LOG_DIR / "artifacts", name)


@app.route("/sessions")
def sessions():
    out = []
    for p in reversed(_logs()):
        try:
            n = sum(1 for _ in open(p, encoding="utf-8"))
        except Exception:
            n = 0
        out.append({"name": p.name, "bytes": p.stat().st_size, "events": n})
    return jsonify(out)


@app.route("/stream")
def stream():
    """Replay a session then tail it.

    ?session=<file> pins a specific session (and never jumps away from it);
    without it, follow the newest log and switch when a newer one appears.
    """
    want = request.args.get("session")

    def gen():
        if want:
            path = LOG_DIR / Path(want).name          # basename only: no path traversal
            if not path.exists():
                yield "data: {}\n\n"
                return
        else:
            path = newest_log()
            while path is None:
                yield ": waiting for a session log\n\n"
                time.sleep(1.0)
                path = newest_log()
        f = open(path, "r", encoding="utf-8")
        idle = 0
        try:
            while True:
                line = f.readline()
                if line:
                    idle = 0
                    if line.strip():
                        yield f"data: {line.strip()}\n\n"
                else:
                    time.sleep(0.3)
                    idle += 1
                    if idle % 50 == 0:
                        yield ": keepalive\n\n"
                    if not want:                       # live mode: follow a newer session
                        nxt = newest_log()
                        if nxt and nxt != path:
                            path = nxt
                            f.close()
                            f = open(path, "r", encoding="utf-8")
        finally:
            f.close()

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log-dir", type=Path, default=LOG_DIR)
    a = ap.parse_args()
    LOG_DIR = a.log_dir
    app.run(host=a.host, port=a.port, threaded=True, debug=False)
