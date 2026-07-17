#!/usr/bin/env python3
"""Live browser view of a uvradio MCP session log (Flask + SSE).

Tails the newest logs/session-*.jsonl and streams it to the browser, rendering a
timeline of every tool call, its result, and — the useful part — the radio screen
image captured at each step.

    python3 tools/mcp/logviewer.py            # http://127.0.0.1:8090
    python3 tools/mcp/logviewer.py --port 9000 --log-dir /path/to/logs

Deliberately a SEPARATE process from the MCP server: the viewer then survives MCP
restarts, never fights for a port across sessions, and can open old sessions.
SSE (not websockets) because the stream is one-way — `EventSource` is three lines.
"""
import argparse
import json
import time
from pathlib import Path

from flask import Flask, Response, jsonify, send_from_directory

app = Flask(__name__)
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def newest_log():
    logs = sorted(LOG_DIR.glob("session-*.jsonl"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


PAGE = """<!doctype html><meta charset=utf-8><title>uvradio session log</title>
<style>
 :root{--bg:#0e1116;--fg:#d7dce3;--dim:#7d8794;--line:#232a34;--ok:#3fb950;--err:#f85149;--acc:#58a6ff;
       --build:#d29922;--sim:#58a6ff;--screen:#3fb950;--input:#bc8cff;--flash:#f85149;--debug:#39c5cf;--log:#7d8794}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
   font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
 header{position:sticky;top:0;background:#11161d;border-bottom:1px solid var(--line);
   padding:8px 16px;display:flex;gap:14px;align-items:center;z-index:9;flex-wrap:wrap}
 h1{font-size:14px;margin:0;font-weight:600} .meta{color:var(--dim);font-size:12px}
 #dot{width:8px;height:8px;border-radius:50%;background:var(--err)} #dot.on{background:var(--ok)}
 .stat{color:var(--dim);font-size:12px} .stat b{color:var(--fg)} .stat.bad b{color:var(--err)}
 #film{display:flex;gap:6px;overflow-x:auto;padding:8px 16px;background:#0b0f14;
   border-bottom:1px solid var(--line);position:sticky;top:41px;z-index:8;min-height:64px}
 #film img{height:48px;width:96px;cursor:pointer;opacity:.65;flex:none;margin:0}
 #film img:hover,#film img.cur{opacity:1;outline:1px solid var(--acc)}
 .wrap{display:grid;grid-template-columns:1fr 300px;gap:16px;padding:16px;max-width:1300px;margin:0 auto}
 @media(max-width:900px){.wrap{grid-template-columns:1fr}}
 aside{position:sticky;top:120px;align-self:start}
 .now{border:1px solid var(--line);border-radius:6px;background:#131922;padding:10px}
 .now h2{margin:0 0 8px;font-size:12px;color:var(--dim);font-weight:600;letter-spacing:.05em}
 .now img{width:100%;margin:0}
 .live{margin-top:8px;font-size:12px;color:var(--dim)} .live b{color:var(--acc)}
 .row{border:1px solid var(--line);border-left:3px solid var(--line);border-radius:6px;
   margin:6px 0;background:#131922;overflow:hidden}
 .hd{padding:6px 12px;display:flex;gap:10px;align-items:center;cursor:pointer}
 .tool{font-weight:600} .id,.t{color:var(--dim);font-size:12px} .dur{margin-left:auto;font-size:12px;color:var(--dim)}
 .dur.warn{color:var(--build)} .dur.slow{color:var(--err)}
 .bd{padding:0 12px 10px;display:none} .row.open .bd{display:block}
 pre{margin:6px 0;padding:8px;background:#0b0f14;border:1px solid var(--line);border-radius:4px;
   white-space:pre-wrap;word-break:break-word;color:#aab3bf;max-height:340px;overflow:auto;font-size:12px}
 img{image-rendering:pixelated;border:1px solid var(--line);border-radius:4px;background:#000;
   width:512px;max-width:100%;display:block;margin:6px 0}
 .bad{color:var(--err)} .row.pending{opacity:.75} .row.pending .tool::after{content:' …';color:var(--acc)}
 .row.err{border-left-color:var(--err)}
 .sess{border-left:3px solid var(--acc);padding-left:10px;color:var(--dim);margin:10px 0;font-size:12px}
 .note{margin:14px 0 6px;padding:6px 10px;border-radius:6px;background:#1b2430;
   border-left:3px solid var(--acc);color:var(--fg);font-weight:600}
 .same{color:var(--dim);font-style:italic;font-size:12px;margin:6px 0}
 #follow{margin-left:auto;background:#1b2430;color:var(--dim);border:1px solid var(--line);
   border-radius:5px;padding:3px 9px;font:inherit;font-size:12px;cursor:pointer}
 #follow.on{color:var(--ok);border-color:var(--ok)}
</style>
<header>
  <span id=dot></span><h1>uvradio</h1>
  <span class=meta id=meta>connecting…</span>
  <span class=stat>calls <b id=ncalls>0</b></span>
  <span class="stat" id=errbox>errors <b id=nerrs>0</b></span>
  <span class=stat>elapsed <b id=elapsed>0s</b></span>
  <button id=follow class=on title="auto-scroll to newest (pauses when you scroll up)">⇊ following</button>
</header>
<div id=film></div>
<div class=wrap>
  <main id=out></main>
  <aside>
    <div class=now>
      <h2>NOW</h2>
      <img id=nowimg alt="latest screen">
      <div class=live id=live>idle</div>
    </div>
  </aside>
</div>
<script>
const $ = i => document.getElementById(i);
const out=$('out'), dot=$('dot'), meta=$('meta'), film=$('film'), nowimg=$('nowimg'), live=$('live');
const rows={}, ICON={build:'⚙',sim:'▣',screen:'▦',input:'⌨',flash:'⚡',debug:'✱',log:'✎',other:'•'};
let ncalls=0, nerrs=0, t0=Date.now(), lastSha=null, inflight=new Set();
const esc = s => (s??'').toString().replace(/[<&>]/g,c=>({'<':'&lt;','&':'&amp;','>':'&gt;'}[c]));

// Follow-tail: stick to the newest entry, but STOP the moment the reader scrolls up —
// an auto-scroll that fights you while you're reading is worse than none.
const btn=$('follow'); let follow=true;
const atBottom=()=> (innerHeight+scrollY) >= (document.body.scrollHeight-80);
function paintFollow(){ btn.classList.toggle('on',follow); btn.textContent = follow?'⇊ following':'⇊ paused'; }
function tail(){ if(follow) scrollTo({top:document.body.scrollHeight,behavior:'smooth'}); }
addEventListener('scroll',()=>{ const f=atBottom(); if(f!==follow){ follow=f; paintFollow(); } },{passive:true});
btn.onclick=()=>{ follow=!follow; paintFollow(); tail(); };

setInterval(()=>{ $('elapsed').textContent = Math.floor((Date.now()-t0)/1000)+'s'; }, 1000);
function stats(){ $('ncalls').textContent=ncalls; $('nerrs').textContent=nerrs;
  $('errbox').className = 'stat' + (nerrs?' bad':'');
  live.innerHTML = inflight.size ? 'running <b>'+[...inflight].join(', ')+'</b>' : 'idle'; }

function row(e){
  if(rows[e.id]) return rows[e.id];
  const el=document.createElement('div'); el.className='row open pending'; el.id='c'+e.id;
  el.style.borderLeftColor = `var(--${e.cat||'other'})`;
  el.innerHTML = `<div class=hd><span class=id>#${e.id}</span>
    <span class=tool style="color:var(--${e.cat||'other'})">${ICON[e.cat]||'•'} ${esc(e.tool)}</span>
    <span class=t>+${((e.t_ms||0)/1000).toFixed(1)}s</span><span class=dur></span></div><div class=bd></div>`;
  el.querySelector('.hd').onclick=()=>el.classList.toggle('open');
  out.appendChild(el); rows[e.id]=el; return el;
}

function onEvent(e){
  if(e.type==='session'){
    const g=e.git||{}, f=e.firmware||{};
    meta.textContent = `${e.session} · ${g.branch||'?'}@${g.sha||'?'}${g.dirty?'*':''}`+(f.built?` · fw ${f.built}`:'');
    t0 = Date.now();
    const d=document.createElement('div'); d.className='sess';
    d.textContent=`session ${e.session} · ${e.ts} · ${e.cwd||''}`; out.appendChild(d); return;
  }
  if(e.type==='note'){
    const d=document.createElement('div'); d.className='note';
    d.textContent='— '+e.text+' —'; out.appendChild(d); tail(); return;
  }
  const el=row(e);
  if(e.type==='call'){
    ncalls++; inflight.add(e.tool); stats();
    el.querySelector('.bd').innerHTML=`<pre>args: ${esc(JSON.stringify(e.args))}</pre>`;
    tail(); return;
  }
  el.classList.remove('pending'); inflight.delete(e.tool);
  const d=el.querySelector('.dur');
  d.textContent=(e.dur_ms??'')+' ms';
  d.className='dur'+(e.dur_ms>5000?' slow':e.dur_ms>1000?' warn':'');
  const bd=el.querySelector('.bd');
  if(e.type==='error'){ nerrs++; el.classList.add('err'); bd.innerHTML+=`<pre class=bad>${esc(e.error)}</pre>`; stats(); return; }
  if(e.text) bd.innerHTML+=`<pre>${esc(e.text)}</pre>`;
  (e.images||[]).forEach(im=>{
    const src='/'+im.path+'?h='+im.sha256;
    if(im.sha256===lastSha){                        // dedupe: identical screen
      const s=document.createElement('div'); s.className='same';
      s.textContent='screen unchanged'; bd.appendChild(s);
    } else {
      const i=document.createElement('img'); i.src=src; i.title=`${im.path} (${im.bytes}B)`; bd.appendChild(i);
      const t=document.createElement('img'); t.src=src; t.title='#'+e.id+' '+e.tool;
      t.onclick=()=>{ document.getElementById('c'+e.id).scrollIntoView({block:'center'}); };
      film.appendChild(t); film.scrollLeft=film.scrollWidth;
      [...film.children].forEach(c=>c.classList.remove('cur')); t.classList.add('cur');
    }
    lastSha=im.sha256; nowimg.src=src;               // NOW pane always shows the latest
  });
  stats(); tail();
}
const src=new EventSource('/stream');
src.onopen=()=>dot.classList.add('on');
src.onerror=()=>dot.classList.remove('on');
src.onmessage=m=>{ try{ onEvent(JSON.parse(m.data)); }catch(err){} };
</script>
"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/artifacts/<path:name>")
def artifacts(name):
    return send_from_directory(LOG_DIR / "artifacts", name)


@app.route("/sessions")
def sessions():
    return jsonify([p.name for p in sorted(LOG_DIR.glob("session-*.jsonl"))])


@app.route("/stream")
def stream():
    """Replay the current log, then tail it — new lines pushed as they land."""
    def gen():
        path = newest_log()
        while path is None:                       # no session yet: wait for one
            yield ": waiting for a session log\n\n"
            time.sleep(1.0)
            path = newest_log()
        with open(path, "r", encoding="utf-8") as f:
            idle = 0
            while True:
                line = f.readline()
                if line:
                    idle = 0
                    if line.strip():
                        yield f"data: {line.strip()}\n\n"
                else:
                    time.sleep(0.3)
                    idle += 1
                    if idle % 50 == 0:            # keep the connection alive
                        yield ": keepalive\n\n"
                    # a newer session started -> follow it
                    nxt = newest_log()
                    if nxt and nxt != path:
                        path = nxt
                        f.close()
                        f = open(path, "r", encoding="utf-8")
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
