#!/usr/bin/env python3
# app.py — minimal web UI for OF-Agent.
# Chat with the agent over an OpenFOAM case (local path or user@host:/path).
# Mirrors the CLI REPL (main.py) in the browser: set a case, then ask questions.
#
# Run:  ./start.sh   (or: python app.py)   ->   http://localhost:7862

import json
import os
import queue
import signal
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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


@app.post("/ask-stream")
def ask_stream(r: AskReq):
    """
    Same as /ask but streams progress as Server-Sent Events so the UI can show
    the model thinking and acting live. Event payloads (one JSON per SSE line):
      {type:"step"}        a new reasoning step started
      {type:"token"}       channel ("thinking"/"answer") + text — live tokens
      {type:"thought"}     the parsed one-line thought for this step
      {type:"action"}      a skill is being called (label)
      {type:"observation"} the skill result (truncated)
      {type:"final"}       the final reply text
      {type:"done"}        stream finished (reply saved to memory)
      {type:"error"}       something went wrong (error message)
    """
    with _lock:
        cfg, mem = _state["cfg"], _state["mem"]
    if cfg is None:
        return JSONResponse({"error": "set a case first"}, status_code=400)
    msg = r.message.strip()
    if not msg:
        return JSONResponse({"error": "empty message"}, status_code=400)

    q: queue.Queue = queue.Queue()
    DONE = object()

    def worker():
        try:
            result = run_agent(cfg, user_task=msg,
                               initial_messages=mem.as_messages(),
                               on_event=q.put)
            reply = (result.get("reply") if result else None) or "(empty response)"
            mem.add(msg, reply)
            mem.save()
            q.put({"type": "done", "reply": reply})
        except Exception as e:
            q.put({"type": "error", "error": str(e)})
        finally:
            q.put(DONE)

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            ev = q.get()
            if ev is DONE:
                break
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/shutdown")
def shutdown():
    """Stop the server cleanly (the 'quit' button in the UI)."""
    # Close any open SSH connection before exiting.
    with _lock:
        ctx = _state.get("ctx")
    if ctx is not None and hasattr(ctx, "close"):
        try:
            ctx.close()
        except Exception:
            pass

    # Ask uvicorn to stop shortly after this response is sent, so the browser
    # still gets a clean 200 back.
    def _stop():
        os.kill(os.getpid(), signal.SIGINT)

    threading.Timer(0.5, _stop).start()
    return {"ok": True}


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
  header { padding:12px 16px; background:#161b22; border-bottom:1px solid #30363d;
           display:flex; align-items:center; justify-content:space-between; }
  header b { color:#58a6ff; }
  #quitBtn { background:#6e2b2b; padding:6px 12px; font-size:13px; }
  #quitBtn:hover { background:#8a3636; }
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
  .think{ margin:6px 0; border:1px solid #30363d; border-radius:8px; background:#0d1117; }
  .think summary{ cursor:pointer; padding:8px 12px; color:#8b949e; font-size:13px; user-select:none; }
  .think pre{ margin:0; padding:0 12px 10px; color:#7d8590; font-size:12.5px;
              white-space:pre-wrap; max-height:240px; overflow:auto; font-family:ui-monospace,monospace; }
  .activity{ color:#8b949e; font-size:13px; margin:3px 0; }
  .activity .dot{ color:#58a6ff; }
  .reply{ margin:8px 0 4px; }
  #ask{ position:sticky; bottom:0; background:#0e1116; padding:12px 16px;
        border-top:1px solid #30363d; display:flex; gap:8px; max-width:900px; margin:0 auto; }
</style></head>
<body>
<header>
  <span><b>OF-Agent</b> — OpenFOAM case assistant</span>
  <button id="quitBtn" onclick="quit()">Quit</button>
</header>
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
async function quit(){
  if(!confirm('Close OF-Agent? The server will stop.')) return;
  try{ await fetch('/shutdown',{method:'POST'}); }catch(e){}
  document.getElementById('q').disabled=true;
  document.getElementById('sendBtn').disabled=true;
  document.getElementById('quitBtn').disabled=true;
  document.getElementById('label').textContent='OF-Agent stopped. You can close this tab.';
  add('Server stopped. Goodbye.','bot');
}
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
function scrollDown(){ window.scrollTo(0,document.body.scrollHeight); }

// Pull the (possibly partial) value of the "reply" field out of a JSON string
// that is still being streamed. Returns null until the reply key appears.
function partialReply(buf){
  const k=buf.indexOf('"reply"'); if(k<0) return null;
  let i=buf.indexOf('"', k+7); if(i<0) return null;   // opening quote of value
  let out=''; i++;
  for(; i<buf.length; i++){
    const c=buf[i];
    if(c==='\\\\'){ const n=buf[i+1]; if(n===undefined) break;
      out += (n==='n'?'\\n':n==='t'?'\\t':n); i++; continue; }
    if(c==='"') break;            // closing quote -> reply complete
    out+=c;
  }
  return out;
}

async function send(){
  const q=document.getElementById('q'); const m=q.value.trim(); if(!m) return;
  add(m,'user'); q.value=''; const sb=document.getElementById('sendBtn'); sb.disabled=true;

  // Build the live bot container: a collapsible "thinking" box, an activity
  // log, and the reply paragraph (filled in once the answer is ready).
  const box=document.createElement('div'); box.className='msg bot';
  const think=document.createElement('details'); think.className='think'; think.open=true;
  const sum=document.createElement('summary'); sum.textContent='Thinking…'; think.appendChild(sum);
  const thinkPre=document.createElement('pre'); think.appendChild(thinkPre);
  const acts=document.createElement('div');
  const reply=document.createElement('div'); reply.className='reply';
  box.appendChild(think); box.appendChild(acts); box.appendChild(reply);
  chat.appendChild(box); scrollDown();

  let hasThinking=false, answerBuf='';
  function activity(text){ const d=document.createElement('div'); d.className='activity';
    d.innerHTML='<span class="dot">▸</span> '+text; acts.appendChild(d); scrollDown(); }
  function handle(ev){
    if(ev.type==='token' && ev.channel==='thinking'){ hasThinking=true; thinkPre.textContent+=ev.text; scrollDown(); }
    else if(ev.type==='token' && ev.channel==='answer'){
      // The answer channel streams raw JSON; show the reply field as it grows.
      answerBuf+=ev.text; const p=partialReply(answerBuf);
      if(p!==null){ reply.textContent=p; scrollDown(); }
    }
    else if(ev.type==='step'){ answerBuf=''; }   // new step -> fresh JSON buffer
    else if(ev.type==='thought'){ activity('<i>'+ev.text+'</i>'); }
    else if(ev.type==='action'){ if(ev.label) activity(ev.label); }
    else if(ev.type==='final'||ev.type==='done'){ if(ev.reply) reply.textContent=ev.reply; }
    else if(ev.type==='error'){ const d=document.createElement('div'); d.className='msg err'; d.textContent='Error: '+ev.error; box.appendChild(d); }
  }

  try{
    const r=await fetch('/ask-stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m})});
    if(!r.ok){ const j=await r.json().catch(()=>({error:r.statusText})); reply.textContent='Error: '+(j.error||r.statusText); reply.className='reply'; box.className='msg err'; }
    else{
      const reader=r.body.getReader(); const dec=new TextDecoder(); let buf='';
      while(true){
        const {value,done}=await reader.read(); if(done) break;
        buf+=dec.decode(value,{stream:true});
        let i;
        while((i=buf.indexOf('\\n\\n'))>=0){
          const line=buf.slice(0,i).trim(); buf=buf.slice(i+2);
          if(line.startsWith('data:')){ try{ handle(JSON.parse(line.slice(5).trim())); }catch(e){} }
        }
      }
    }
  }catch(e){ reply.textContent='Error: '+e; box.className='msg err'; }

  sum.textContent = hasThinking ? 'Thinking (click to toggle)' : 'No reasoning trace';
  if(!hasThinking) think.style.display='none';
  else think.open=false;
  sb.disabled=false; q.focus(); scrollDown();
}
</script>
</body></html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7862)
