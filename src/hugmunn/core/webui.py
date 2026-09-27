"""Self-contained remote dashboard. No CDN, build step, or browser-stored passwords."""
from ..branding import svg

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><meta name="referrer" content="no-referrer">
<title>hugmunn · Remote</title>
<style>
:root{color-scheme:dark;--page:#16181d;--surface:#1e2127;--surface-alt:#242830;--fg:#dde1e7;--dim:#a8b0bd;--border:#39414e;--accent:#6aa6ff;--on-accent:#10131a;--error:#ff9a95;--user:#2a3446;--alpha:.92}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top right,#303d52,transparent 60%),var(--page);color:var(--fg);font:14px/1.55 system-ui,sans-serif;min-height:100dvh}
[hidden]{display:none!important}button,input,select,textarea{font:inherit;color:var(--fg);background:var(--surface-alt);border:1px solid var(--border);border-radius:10px;padding:9px 12px}button{cursor:pointer}button:hover{border-color:var(--accent)}button:disabled{opacity:.5;cursor:default}.primary{background:var(--accent);color:var(--on-accent);font-weight:650;border-color:transparent}.danger{color:var(--error)}input,select,textarea{width:100%}input[type=checkbox]{width:auto}input[type=range]{padding:0}label{display:block;margin:12px 0 5px;color:var(--dim);font-size:12px}h1,h2,p{margin:0 0 12px}h1{font-size:24px}h2{font-size:15px}small,.muted{color:var(--dim)}.card{background:color-mix(in srgb,var(--surface) calc(var(--alpha)*100%),transparent);border:1px solid var(--border);border-radius:20px;padding:20px;box-shadow:0 16px 45px #0002;backdrop-filter:blur(16px)}
#login{width:min(440px,calc(100% - 32px));margin:12vh auto}#login .primary{width:100%;margin-top:20px}.brand{display:flex;align-items:center;gap:10px}.brand svg{width:34px;height:34px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--dim)}
header{display:flex;align-items:center;gap:18px;padding:18px 26px}header .brand{font-size:19px;font-weight:650}#model-label{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--dim)}#workspace{display:grid;grid-template-columns:320px 1fr;gap:16px;padding:0 20px 20px;height:calc(100dvh - 78px)}aside{overflow:auto;padding:4px}aside .card{padding:16px;margin-bottom:12px}details summary{cursor:pointer;font-weight:600}details[open] summary{margin-bottom:10px}.row{display:flex;gap:8px;align-items:center}.row>*{flex:1}.row input[type=checkbox]{flex:0}#conversation{display:flex;flex-direction:column;padding:0;overflow:hidden;min-height:0}#log{flex:1;overflow:auto;padding:24px;display:flex;flex-direction:column;gap:16px}.msg{white-space:pre-wrap;overflow-wrap:anywhere;max-width:95%}.user{align-self:flex-end;background:var(--user);border-radius:16px;padding:12px 16px}.bot{align-self:flex-start}.tool,.think{font-size:12px;color:var(--dim);border-left:2px solid var(--border);padding-left:12px}.notice{font-size:12px;color:var(--dim)}.error{color:var(--error)}#composer{padding:16px;border-top:1px solid var(--border);display:flex;align-items:flex-end;gap:10px}#text{resize:vertical;min-height:65px;max-height:220px}#context-label{font-size:11px;padding:0 18px 12px;color:var(--dim)}#banner{padding:8px 20px;color:var(--error)}#skills-list{max-height:300px;overflow:auto}.skill{display:block;padding:8px 0;margin:0;font-size:13px}.skill small{display:block;padding-left:22px;font-size:11px}.skill input{margin-right:7px}#gpu-output{white-space:pre-wrap;word-break:break-word;max-height:200px;overflow:auto;font-size:11px}#approval{position:fixed;inset:0;z-index:10;background:#000a;display:grid;place-items:center;padding:20px}#approval .card{width:min(580px,100%);max-height:85vh;overflow:auto}#ap-args{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--surface-alt);padding:12px;border-radius:10px}#mobile-controls{display:none}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(max-width:800px){header{padding:12px;gap:10px}#workspace{grid-template-columns:1fr;height:calc(100dvh - 68px);padding:0 10px 10px}aside{display:none;position:absolute;inset:65px 10px 10px;z-index:5;background:var(--page);border-radius:20px}aside.open{display:block}#mobile-controls{display:block}#log{padding:16px}.msg{max-width:100%}#composer{flex-wrap:wrap;padding:12px}#text{flex-basis:100%}#model-label{font-size:11px}}
</style></head><body>
<form id="login" class="card" hidden>
<div class="brand">__MARK__<div><div class="eyebrow">Your desktop, anywhere</div><h1>Hugmunn</h1></div></div>
<p class="muted">Sign in to control your running application.</p>
<label for="username">Username</label><input id="username" autocomplete="username" required>
<label for="password">Password</label><input id="password" type="password" autocomplete="current-password" required>
<p id="login-error" class="error" role="alert"></p><button class="primary" type="submit">Sign in</button>
</form>
<div id="app" hidden><header><div class="brand">__MARK__ Hugmunn</div><span id="model-label">Connecting…</span><button id="mobile-controls">Controls</button><button id="logout">Sign out</button></header>
<div id="banner" role="alert" hidden></div>
<div id="workspace"><aside id="controls">
<section class="card"><h2>Model & conversation</h2>
<label for="provider">Provider</label><select id="provider"></select><label for="model">Model</label><select id="model"></select>
<p id="server-status" class="muted"></p><div class="row"><button id="start-model">Start model</button><button id="stop-model">Unload</button></div>
<label for="sessions">Saved conversations</label><div class="row"><select id="sessions" aria-label="Saved conversations"></select><button id="restore">Open</button></div>
<div class="row" style="margin-top:10px"><button id="new">New chat</button><button id="save">Save</button></div></section>
<section class="card"><details open><summary>Agent controls</summary>
<label for="autonomy">Autonomy</label><select id="autonomy"></select><label for="effort">Effort</label><select id="effort"></select><label for="persistence">Persistence</label><select id="persistence"></select>
<label><input id="thinking" type="checkbox"> Reasoning</label><label><input id="tools_enabled" type="checkbox"> Enable tools</label>
<label for="goal">Goal</label><textarea id="goal" rows="2"></textarea><button id="set-goal">Set goal</button>
<label for="workdir">Working directory</label><input id="workdir"><button id="set-workdir">Set directory</button>
<label for="context">Context tokens</label><input id="context" type="number" min="1"><button id="set-context">Apply context</button>
<label for="system_prompt">System prompt</label><textarea id="system_prompt" rows="4"></textarea><button id="set-prompt">Save prompt</button>
</details></section>
<section class="card"><details><summary>Skills</summary><input id="skills-search" type="search" placeholder="Search skills" aria-label="Search skills"><p id="skills-count" class="muted"></p><div id="skills-list"></div><button id="import-skills">Import installed Codex skills</button></details></section>
<section class="card"><details><summary>Custom tools</summary><div id="plugins-list"></div></details></section>
<section class="card"><details><summary>Appearance</summary><label for="theme">Theme</label><select id="theme"></select><label for="panel_opacity">Panel opacity</label><input id="panel_opacity" type="range" min="50" max="100"><label for="window_opacity">Desktop window opacity</label><input id="window_opacity" type="range" min="50" max="100"><label><input id="rounded_windows" type="checkbox"> Rounded desktop window</label></details></section>
<section class="card"><h2>GPU task</h2><p class="muted">Unload the local model, run your command, then reload it.</p><label for="gpu-command">Foreground command</label><textarea id="gpu-command" rows="3" placeholder="python my_gpu_task.py"></textarea><button id="run-gpu">Run GPU task</button><p id="gpu-status" class="muted"></p><pre id="gpu-output"></pre></section>
</aside><main id="conversation" class="card"><div id="log" role="log" aria-live="polite"></div><div id="composer"><textarea id="text" placeholder="Message Hugmunn, or /help" aria-label="Message"></textarea><button id="send" class="primary">Send</button><button id="stop" class="danger" hidden>Stop</button></div><div id="context-label"></div></main></div></div>
<div id="approval" hidden><div class="card"><h2 id="ap-title">Approve tool call</h2><p id="ap-summary"></p><pre id="ap-args"></pre><div class="row"><button id="deny">Deny</button><button id="allow" class="primary">Allow</button></div></div></div>
<script>
const $=id=>document.getElementById(id);
// Backward-compatible bearer links are memory-only; password sessions use HttpOnly cookies.
const token=new URLSearchParams(location.search).get('t')||'';
history.replaceState(null,'',location.pathname);
let streaming=false,state=null,pending=null,lastLog='',lastSkills='',polling=false;
async function api(path,body){
 const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Hugmunn-Request':'1',...(token?{Authorization:'Bearer '+token}:{})},body:body===undefined?undefined:JSON.stringify(body)});
 if(!response.ok){let data={};try{data=await response.json()}catch{}if(response.status===401&&path!='/api/login'){showLogin()}throw new Error(data.error||('Request failed: '+response.status))}return response;
}
function showLogin(){$('login').hidden=false;$('app').hidden=true;$('approval').hidden=true;state=null;$('log').replaceChildren();lastLog=''}
function error(e){$('banner').hidden=false;$('banner').textContent=e.message||String(e)}
async function act(name,value){try{await api('/api/action',{name,value});await refresh()}catch(e){error(e)}}
async function setting(key,value){try{await api('/api/setting',{key,value});await refresh()}catch(e){error(e);await refresh()}}
function select(id,options,value){const el=$(id);if(document.activeElement===el)return;const sig=JSON.stringify(options);if(el.dataset.options!==sig){el.replaceChildren();(options||[]).forEach(o=>{const option=document.createElement('option');option.value=o.value;option.textContent=o.label;el.append(option)});el.dataset.options=sig}if(value!=null)el.value=String(value)}
function field(id,value){if(document.activeElement===$(id))return;if($(id).type==='checkbox')$(id).checked=!!value;else $(id).value=value??''}
function add(cls,text){const el=document.createElement('div');el.className='msg '+cls;el.textContent=text||'';$('log').append(el);return el}
function replay(messages,live){const log=$('log'),near=log.scrollHeight-log.scrollTop-log.clientHeight<100,top=log.scrollTop;log.replaceChildren();if(!messages.length&&!live.length)add('notice','Ready when you are. Start a model, choose a skill, or send a message.');messages.forEach(m=>{if(m.role==='user')add('user',m.content);else if(m.role==='assistant'&&m.content)add('bot',m.content);else if(m.role==='tool')add('tool',(m.name||'Tool')+'\n'+String(m.content||'').slice(-8000))});live.forEach(e=>{if(e.kind==='content')add('bot',e.text);else if(e.kind==='reasoning')add('think',e.text);else if(e.kind==='tool_start')add('tool',e.tool_name+' · '+e.tool_summary)});log.scrollTop=near?log.scrollHeight:top}
function skills(){if(!state)return;const query=$('skills-search').value.toLowerCase();const entries=state.skills.filter(x=>(x.name+' '+x.category+' '+x.description).toLowerCase().includes(query));$('skills-list').replaceChildren();entries.forEach(x=>{const label=document.createElement('label');label.className='skill';const check=document.createElement('input');check.type='checkbox';check.checked=x.enabled;check.disabled=state.busy;check.onchange=()=>{const keys=state.skills.filter(s=>s.enabled&&s.key!==x.key).map(s=>s.key);if(check.checked)keys.push(x.key);setting('skills',keys)};label.append(check,document.createTextNode(x.name));const small=document.createElement('small');small.textContent=x.category+' · ~'+x.tokens+' tokens';label.append(small);label.title=x.description;$('skills-list').append(label)});$('skills-count').textContent=state.skills.filter(x=>x.enabled).length+' enabled / '+state.skills.length+' available'}
async function refresh(){if(polling)return;polling=true;try{const s=await(await api('/api/state')).json();state=s;$('login').hidden=true;$('app').hidden=false;$('model-label').textContent=s.model||'No model selected';$('server-status').textContent=s.server_status;$('gpu-status').textContent=s.gpu.status;$('gpu-output').textContent=s.gpu.output;
 ['provider','model','autonomy','effort','persistence','theme'].forEach(id=>select(id,s[id+'_options'],id==='model'?s.model_key:s[id]));select('sessions',(s.sessions||[]).map(x=>({value:x.id,label:x.title})),null);
 ['thinking','tools_enabled','goal','workdir','system_prompt','panel_opacity','window_opacity','rounded_windows'].forEach(id=>field(id,s[id]));field('context',s.context.limit);$('context-label').textContent=s.context.used.toLocaleString()+' / '+s.context.available.toLocaleString()+' usable context tokens';
 const p=s.palette;for(const [css,key] of Object.entries({'page':'page','surface':'surface','surface-alt':'surface_alt','fg':'fg','dim':'fg_muted','border':'border','accent':'accent','on-accent':'on_accent','error':'error','user':'user'}))document.documentElement.style.setProperty('--'+css,p[key]);document.documentElement.style.setProperty('--alpha',s.panel_opacity/100);document.documentElement.style.colorScheme=s.theme.includes('light')?'light':'dark';
 $('stop').hidden=!s.busy&&!streaming;$('send').disabled=s.busy||streaming;
 for(const el of $('controls').querySelectorAll('input,select,textarea,button'))el.disabled=s.busy;
 $('start-model').disabled=s.busy||s.server_running||s.provider!=='local';$('stop-model').disabled=s.busy||!s.server_running;
 const skillSig=JSON.stringify([s.skills,s.busy]);if(skillSig!==lastSkills){lastSkills=skillSig;skills()}
 const plugins=$('plugins-list');plugins.replaceChildren();for(const p of s.plugins){const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=p.enabled;check.disabled=s.busy;check.onchange=()=>setting('plugins',s.plugins.filter(x=>x.key===p.key?check.checked:x.enabled).map(x=>x.key));label.append(check,document.createTextNode(' '+p.key));plugins.append(label)}if(!s.plugins.length)plugins.textContent='No custom tools installed.';
 if(!streaming){const sig=JSON.stringify([s.session_id,s.messages,s.live_events]);if(sig!==lastLog){lastLog=sig;replay(s.messages,s.live_events)}}
 const ap=s.pending_approvals[0];pending=ap?ap.id:null;$('approval').hidden=!ap;if(ap){$('ap-title').textContent='Allow '+ap.name+'?';$('ap-summary').textContent=ap.summary;$('ap-args').textContent=JSON.stringify(ap.arguments,null,2)}
 }catch(e){if(state)error(e)}finally{polling=false}}
async function send(){const text=$('text').value.trim();if(!text||streaming)return;streaming=true;$('text').value='';add('user',text);$('send').disabled=true;$('stop').hidden=false;let bot=null,think=null;try{const response=await api('/api/send',{text});const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let cut;while((cut=buffer.indexOf('\n\n'))>=0){const chunk=buffer.slice(0,cut);buffer=buffer.slice(cut+2);const line=chunk.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;const ev=JSON.parse(line.slice(6));if(ev.kind==='content'){if(!bot)bot=add('bot','');bot.textContent+=ev.text}else if(ev.kind==='reasoning'){if(!think)think=add('think','');think.textContent+=ev.text}else if(ev.kind==='tool_start'){bot=think=null;add('tool',ev.tool_name+' · '+ev.tool_summary)}else if(ev.kind==='tool_result')add('tool',ev.text.slice(-8000));else if(ev.kind==='notice'||ev.kind==='error')add(ev.kind==='error'?'error':'notice',ev.text);$('log').scrollTop=$('log').scrollHeight}}}catch(e){error(e)}finally{streaming=false;lastLog='';await refresh()}}
$('login').onsubmit=async e=>{e.preventDefault();$('login-error').textContent='';try{await api('/api/login',{username:$('username').value,password:$('password').value});$('password').value='';await refresh()}catch(e){$('login-error').textContent=e.message}};
$('logout').onclick=async()=>{try{await api('/api/logout',{});showLogin()}catch(e){error(e)}};
$('send').onclick=send;$('stop').onclick=async()=>{try{await api('/api/cancel',{});await refresh()}catch(e){error(e)}};
$('mobile-controls').onclick=()=>$('controls').classList.toggle('open');$('skills-search').oninput=skills;
$('text').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!matchMedia('(pointer: coarse)').matches){e.preventDefault();send()}};
['autonomy','effort','persistence','panel_opacity','window_opacity'].forEach(id=>$(id).onchange=()=>setting(id,Number($(id).value)));
['provider','model','theme'].forEach(id=>$(id).onchange=()=>setting(id,$(id).value));
['thinking','tools_enabled','rounded_windows'].forEach(id=>$(id).onchange=()=>setting(id,$(id).checked));
for(const [button,key] of [['set-goal','goal'],['set-workdir','workdir'],['set-prompt','system_prompt'],['set-context','context']])$(button).onclick=()=>setting(key,key==='context'?Number($(key).value):$(key).value);
for(const [button,name] of [['start-model','start_model'],['stop-model','stop_model'],['new','new'],['save','save'],['import-skills','import_skills']])$(button).onclick=()=>act(name);
$('restore').onclick=()=>act('restore',$('sessions').value);$('run-gpu').onclick=()=>act('gpu',$('gpu-command').value);
async function approve(allowed){try{await api('/api/approve',{id:pending,allowed});await refresh()}catch(e){error(e)}}$('allow').onclick=()=>approve(true);$('deny').onclick=()=>approve(false);
refresh();setInterval(refresh,1500);
</script></body></html>'''
PAGE = PAGE.replace('__MARK__', svg().replace('width="512" height="512"', 'width="34" height="34" aria-hidden="true"').replace('fill="#ffffff"', 'fill="currentColor"').replace('stroke="#ffffff"', 'stroke="currentColor"'))
