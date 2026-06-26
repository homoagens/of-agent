#!/usr/bin/env python3
# app.py — minimal web UI for OF-Agent.
# Chat with the agent over an OpenFOAM case (local path or user@host:/path).
# Mirrors the CLI REPL (main.py) in the browser: set a case, then ask questions.
#
# Run:  ./start.sh   (or: python app.py)   ->   http://localhost:7862

import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from agent import run_agent
from context import LocalContext, SSHContext
from session import SessionMemory
from main import _make_cfg, _parse_ssh_target, _is_ssh_target

app = FastAPI(title="OF-Agent")
_state = {"ctx": None, "mem": None, "cfg": None, "label": None}
_lock = threading.Lock()


class CaseReq(BaseModel):
    case: str
    key: str | None = None
    port: int | None = None
    password: str | None = None


class AskReq(BaseModel):
    message: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.post("/set-case")
def set_case(r: CaseReq):
    target = r.case.strip()
    if not target:
        return JSONResponse({"error": "empty case"}, status_code=400)
    try:
        if _is_ssh_target(target):
            user, host, rpath = _parse_ssh_target(target)
            ctx = SSHContext(hostname=host, remote_root=rpath, user=user,
                             port=r.port or 22, key_filename=r.key, password=r.password)
        else:
            p = Path(target).expanduser()
            if not p.is_dir():
                return JSONResponse({"error": f"not a directory: {target}"}, status_code=400)
            ctx = LocalContext(p)
        label = ctx.resolve_str()
        cfg = _make_cfg(ctx)
        mem = SessionMemory(context=ctx, case_label=label)
        with _lock:
            _state.update(ctx=ctx, mem=mem, cfg=cfg, label=label)
        return {"ok": True, "label": label}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/ask")
def ask(r: AskReq):
    with _lock:
        cfg, mem = _state["cfg"], _state["mem"]
    if cfg is None:
        return JSONResponse({"error": "set a case first"}, status_code=400)
    msg = r.message.strip()
    if not msg:
        return JSONResponse({"error": "empty message"}, status_code=400)
    try:
        result = run_agent(cfg, user_task=msg, initial_messages=mem.as_messages())
        reply = (result.get("reply") if result else None) or "(empty response)"
        mem.add(msg, reply)
        mem.save()
        return {"reply": reply}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OF-Agent</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 0; background:#0e1116; color:#e6e6e6; }
  header { padding:12px 16px; background:#161b22; border-bottom:1px solid #30363d; }
  header b { color:#58a6ff; }
  #setup { padding:12px 16px; border-bottom:1px solid #30363d; display:flex; gap:8px; flex-wrap:wrap; }
  input[type=text]{ flex:1; min-width:280px; padding:8px; background:#0e1116; color:#e6e6e6;
                    border:1px solid #30363d; border-radius:6px; }
  button{ padding:8px 14px; background:#238636; color:#fff; border:0; border-radius:6px; cursor:pointer; }
  button:disabled{ opacity:.5; cursor:default; }
  #label{ padding:6px 16px; font-size:13px; color:#8b949e; }
  #chat{ padding:16px; max-width:900px; margin:0 auto; }
  .msg{ margin:10px 0; padding:10px 12px; border-radius:8px; white-space:pre-wrap; }
  .user{ background:#1f2937; }
  .bot{ background:#161b22; border:1px solid #30363d; }
  .err{ background:#3d1f1f; border:1px solid #6e2b2b; }
  #ask{ position:sticky; bottom:0; background:#0e1116; padding:12px 16px;
        border-top:1px solid #30363d; display:flex; gap:8px; max-width:900px; margin:0 auto; }
</style></head>
<body>
<header><b>OF-Agent</b> — OpenFOAM case assistant</header>
<div id="setup">
  <input id="case" type="text" placeholder="OpenFOAM case: /path/to/case  or  user@host:/path">
  <button id="setBtn" onclick="setCase()">Set case</button>
</div>
<div id="label">No case set.</div>
<div id="chat"></div>
<div id="ask">
  <input id="q" type="text" placeholder="Ask about the case..." disabled
         onkeydown="if(event.key==='Enter')send()">
  <button id="sendBtn" onclick="send()" disabled>Send</button>
</div>
<script>
const chat=document.getElementById('chat');
function add(text, cls){ const d=document.createElement('div'); d.className='msg '+cls; d.textContent=text; chat.appendChild(d); window.scrollTo(0,document.body.scrollHeight); }
async function setCase(){
  const c=document.getElementById('case').value.trim(); if(!c) return;
  const b=document.getElementById('setBtn'); b.disabled=true; b.textContent='...';
  try{
    const r=await fetch('/set-case',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:c})});
    const j=await r.json();
    if(j.error){ add('Error: '+j.error,'err'); }
    else{ document.getElementById('label').textContent='Monitoring: '+j.label;
          document.getElementById('q').disabled=false; document.getElementById('sendBtn').disabled=false; }
  }catch(e){ add('Error: '+e,'err'); }
  b.disabled=false; b.textContent='Set case';
}
async function send(){
  const q=document.getElementById('q'); const m=q.value.trim(); if(!m) return;
  add(m,'user'); q.value=''; const sb=document.getElementById('sendBtn'); sb.disabled=true;
  add('thinking...','bot'); const ph=chat.lastChild;
  try{
    const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m})});
    const j=await r.json();
    ph.textContent = j.error ? ('Error: '+j.error) : j.reply;
    if(j.error) ph.className='msg err';
  }catch(e){ ph.textContent='Error: '+e; ph.className='msg err'; }
  sb.disabled=false; q.focus();
}
</script>
</body></html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7862)
