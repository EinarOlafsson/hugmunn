"""The page served to a phone. One file, no build step, no CDN.

Held as a string rather than a file so it cannot go missing from a wheel, and
written without dependencies because a page fetched over a tunnel on a train
should not also be fetching a framework.

Two things it does that a naive chat page does not:

* **The token moves out of the URL immediately.** It has to arrive in the
  query string — the first request is a link scanned on another device — but
  it is read into memory and the address bar rewritten before anything else,
  so it is not sitting in browser history or in the referrer of any later
  request.
* **Approvals interrupt.** A tool call waiting on a human is not a message in
  the transcript that can scroll away; it is a blocking card with the command
  in full, because approving something you cannot see is not approving.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>localagent</title>
<style>
:root {
  --bg:#16181d; --page:#1a1d23; --surface:#1e2127; --surface-alt:#242830;
  --border:#2f343d; --fg:#dde1e7; --dim:#8b93a1; --accent:#6aa6ff;
  --user:#2a3446; --ok:#5ec27a; --warn:#e0b341; --err:#e0685f; --code:#12141a;
  --on-accent:#10131a;
}
* { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html,body { height:100%; margin:0; }
body {
  background:var(--page); color:var(--fg);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  display:flex; flex-direction:column;
  padding-bottom:env(safe-area-inset-bottom);
}
header {
  display:flex; align-items:center; gap:10px; padding:10px 14px;
  background:var(--surface); border-bottom:1px solid var(--border);
  padding-top:calc(10px + env(safe-area-inset-top));
}
header b { font-size:15px; }
#model { color:var(--dim); font-size:12px; flex:1; overflow:hidden;
         text-overflow:ellipsis; white-space:nowrap; }
button {
  background:var(--surface-alt); color:var(--fg); border:1px solid var(--border);
  border-radius:8px; padding:8px 12px; font-size:14px; cursor:pointer;
}
button.primary { background:var(--accent); color:var(--on-accent); border:none;
                 font-weight:600; }
button.danger { border-color:var(--err); color:var(--err); }
button:disabled { opacity:.5; }

#log { flex:1; overflow-y:auto; padding:14px; display:flex;
       flex-direction:column; gap:10px; overscroll-behavior:contain; }
.msg { max-width:88%; padding:9px 13px; border-radius:12px; white-space:pre-wrap;
       word-wrap:break-word; }
.user { align-self:flex-end; background:var(--user); }
.bot  { align-self:flex-start; background:transparent; padding-left:0; }
.think { align-self:flex-start; color:var(--dim); font-size:13px;
         border-left:2px solid var(--border); padding-left:10px; }
.tool { align-self:stretch; background:var(--surface); border:1px solid var(--border);
        border-radius:10px; padding:9px 12px; font-size:13px; }
.tool .name { font-weight:600; }
.tool pre { margin:6px 0 0; padding:8px; background:var(--code); border-radius:6px;
            overflow-x:auto; font-size:12px; max-height:220px; }
.notice { align-self:center; color:var(--dim); font-size:12px; }
.err { color:var(--err); }
pre.code { background:var(--code); padding:10px; border-radius:8px; overflow-x:auto; }

#composer { display:flex; gap:8px; padding:10px 12px; background:var(--surface);
            border-top:1px solid var(--border); }
#text { flex:1; resize:none; background:var(--surface-alt); color:var(--fg);
        border:1px solid var(--border); border-radius:10px; padding:10px;
        font:inherit; max-height:130px; }

#approval { position:fixed; inset:0; background:rgba(0,0,0,.72);
            display:none; align-items:center; justify-content:center; padding:16px;
            z-index:20; }
#approval.on { display:flex; }
#approval .card { background:var(--surface); border:1px solid var(--border);
                  border-radius:14px; padding:16px; max-width:560px; width:100%;
                  max-height:82vh; overflow-y:auto; }
#approval h3 { margin:0 0 8px; font-size:16px; }
#approval pre { background:var(--code); padding:10px; border-radius:8px;
                overflow-x:auto; font-size:12px; max-height:40vh; }
#approval .row { display:flex; gap:8px; margin-top:12px; }
#approval .row button { flex:1; }

#panel { display:none; padding:12px 14px; background:var(--surface);
         border-bottom:1px solid var(--border); }
#panel.on { display:block; }
#panel label { display:block; margin:10px 0 4px; font-size:12px; color:var(--dim);
               text-transform:uppercase; letter-spacing:.5px; }
#panel select { width:100%; background:var(--surface-alt); color:var(--fg);
                border:1px solid var(--border); border-radius:8px; padding:9px; font:inherit; }
#meter { height:4px; background:var(--surface-alt); border-radius:2px; margin-top:10px; }
#meter div { height:100%; background:var(--accent); border-radius:2px; width:0; }
</style>
</head>
<body>

<header>
  <b>localagent</b>
  <span id="model">connecting…</span>
  <button id="gear" title="Settings">⚙</button>
</header>

<div id="panel">
  <label for="autonomy">Autonomy</label>
  <select id="autonomy"></select>
  <label for="effort">Effort</label>
  <select id="effort"></select>
  <label for="thinking">Reasoning</label>
  <select id="thinking">
    <option value="false">Off — answer directly</option>
    <option value="true">On — think first</option>
  </select>
  <div id="meter"><div></div></div>
  <div style="color:var(--dim);font-size:12px;margin-top:6px" id="ctx"></div>
</div>

<div id="log"></div>

<div id="composer">
  <textarea id="text" rows="1" placeholder="Message, or /help"></textarea>
  <button id="send" class="primary">Send</button>
  <button id="stop" class="danger" style="display:none">Stop</button>
</div>

<div id="approval"><div class="card">
  <h3 id="ap-title">Approve tool call</h3>
  <div id="ap-summary" style="color:var(--dim);font-size:13px"></div>
  <pre id="ap-args"></pre>
  <div class="row">
    <button id="ap-deny">Deny</button>
    <button id="ap-allow" class="primary">Allow</button>
  </div>
</div></div>

<script>
// The token arrives in the query because the first request is a link opened
// on another device. Take it into memory and clear the address bar before
// anything else, so it is not left in history or sent as a referrer.
const TOKEN = new URLSearchParams(location.search).get("t") || "";
history.replaceState(null, "", location.pathname);

const $ = id => document.getElementById(id);
const log = $("log");
let streaming = false;

const api = (path, body) => fetch(path, {
  method: body === undefined ? "GET" : "POST",
  headers: {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
  body: body === undefined ? undefined : JSON.stringify(body),
});

function atBottom() { return log.scrollHeight - log.scrollTop - log.clientHeight < 80; }
function follow(was) { if (was) log.scrollTop = log.scrollHeight; }

function add(cls, text) {
  const was = atBottom();
  const el = document.createElement("div");
  el.className = "msg " + cls;
  el.textContent = text || "";
  log.appendChild(el);
  follow(was);
  return el;
}

function toolCard(name, summary) {
  const was = atBottom();
  const el = document.createElement("div");
  el.className = "msg tool";
  el.innerHTML = '<div><span class="name"></span> <span class="sum"></span></div>';
  el.querySelector(".name").textContent = name;
  el.querySelector(".sum").textContent = summary || "";
  log.appendChild(el);
  follow(was);
  return el;
}

// ---- state ----

async function refresh() {
  try {
    const state = await (await api("/api/state")).json();
    $("model").textContent = state.model || "";
    fillSelect($("autonomy"), state.autonomy_options, state.autonomy);
    fillSelect($("effort"), state.effort_options, state.effort);
    $("thinking").value = String(!!state.thinking);
    if (state.context) {
      const pct = Math.min(100, Math.round(100 * state.context.used / Math.max(1, state.context.available)));
      $("meter").firstElementChild.style.width = pct + "%";
      $("ctx").textContent = state.context.used.toLocaleString() + " of " +
        state.context.available.toLocaleString() + " tokens used";
    }
    if (!log.childElementCount && state.messages) replay(state.messages);
    (state.pending_approvals || []).forEach(askApproval);
  } catch (e) { $("model").textContent = "disconnected"; }
}

function fillSelect(el, options, value) {
  if (!options || el.dataset.filled) { if (value != null) el.value = String(value); return; }
  el.innerHTML = "";
  options.forEach(o => {
    const opt = document.createElement("option");
    opt.value = String(o.value); opt.textContent = o.label;
    el.appendChild(opt);
  });
  el.dataset.filled = "1";
  if (value != null) el.value = String(value);
}

function replay(messages) {
  messages.forEach(m => {
    if (m.role === "user") add("user", m.content);
    else if (m.role === "assistant" && m.content) add("bot", m.content);
    else if (m.role === "tool") {
      const card = toolCard(m.name || "tool", "");
      const pre = document.createElement("pre");
      pre.textContent = (m.content || "").slice(0, 4000);
      card.appendChild(pre);
    }
  });
  log.scrollTop = log.scrollHeight;
}

// ---- sending ----

async function send() {
  const text = $("text").value.trim();
  if (!text || streaming) return;
  $("text").value = ""; $("text").style.height = "auto";
  add("user", text);
  streaming = true;
  $("send").style.display = "none"; $("stop").style.display = "";

  let bot = null, think = null;
  const cards = {};
  try {
    const response = await api("/api/send", {text});
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, cut); buffer = buffer.slice(cut + 2);
        const line = chunk.split("\n").find(l => l.startsWith("data: "));
        if (!line) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.kind === "reasoning") {
          if (!think) think = add("think", "");
          think.textContent += ev.text;
          follow(true);
        } else if (ev.kind === "content") {
          if (!bot) bot = add("bot", "");
          bot.textContent += ev.text;
          follow(true);
        } else if (ev.kind === "tool_start") {
          cards[ev.tool_id || ev.tool_name] = toolCard(ev.tool_name, ev.tool_summary);
        } else if (ev.kind === "tool_result") {
          const card = cards[ev.tool_id || ev.tool_name];
          if (card) {
            const pre = document.createElement("pre");
            pre.textContent = (ev.text || "").slice(0, 4000);
            card.appendChild(pre);
          }
        } else if (ev.kind === "approval") {
          askApproval(ev);
        } else if (ev.kind === "denied") {
          add("notice", "denied: " + ev.tool_name);
        } else if (ev.kind === "error") {
          add("notice err", ev.text);
        } else if (ev.kind === "notice") {
          add("notice", ev.text);
        }
      }
    }
  } catch (e) {
    add("notice err", "connection lost: " + e);
  }
  streaming = false;
  $("send").style.display = ""; $("stop").style.display = "none";
  refresh();
}

// ---- approvals ----

let pendingId = null;
function askApproval(ev) {
  pendingId = ev.id || ev.tool_id;
  $("ap-title").textContent = "Allow " + (ev.name || ev.tool_name) + "?";
  $("ap-summary").textContent = ev.summary || ev.tool_summary || "";
  $("ap-args").textContent = JSON.stringify(ev.arguments || {}, null, 2);
  $("approval").classList.add("on");
}
function answer(allowed) {
  if (!pendingId) return;
  api("/api/approve", {id: pendingId, allowed});
  pendingId = null;
  $("approval").classList.remove("on");
}
$("ap-allow").onclick = () => answer(true);
$("ap-deny").onclick  = () => answer(false);

// ---- wiring ----

$("send").onclick = send;
$("stop").onclick = () => api("/api/cancel", {});
$("gear").onclick = () => $("panel").classList.toggle("on");
$("text").addEventListener("input", e => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(130, e.target.scrollHeight) + "px";
});
$("text").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) {
    e.preventDefault(); send();
  }
});
["autonomy", "effort", "thinking"].forEach(key => {
  $(key).onchange = e => api("/api/setting", {key, value: e.target.value}).then(refresh);
});

refresh();
setInterval(() => { if (!streaming) refresh(); }, 8000);
</script>
</body>
</html>
"""
