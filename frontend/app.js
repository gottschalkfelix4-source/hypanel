import { Chart } from './charts.js';
import { mountHytaleAuth } from './hytale-auth.js';
let authCleanup=()=>{};
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc = encodeURIComponent;
const bytes = value => value >= 1024**3 ? `${(value/1024**3).toFixed(2)} GiB` : value >= 1024**2 ? `${(value/1024**2).toFixed(1)} MiB` : `${(value/1024).toFixed(1)} KiB`;
const date = value => value ? new Date(value*1000).toLocaleString('de-DE') : '—';
const labels = {running:'Läuft',stopped:'Gestoppt',exited:'Beendet',created:'Erstellt',restarting:'Neustart',unavailable:'Docker nicht erreichbar',paused:'Pausiert',starting:'Wird gestartet'};
let charts=[], overviewTimer, jobTimer, commandHistory=[], historyIndex=0;
let panelVersion = '—';
let user, servers = [], selected, section = 'servers', tab = 'console', filePath = 'Server', source, metricsTimer, noticeTimer;

function busy(button, active) {
  if (button?.tagName !== 'BUTTON') return;
  button.disabled = active;
  if (active) button.setAttribute('aria-busy', 'true');
  else button.removeAttribute('aria-busy');
}
function reveal(node) {
  if (!node) return;
  node.classList.remove('view-enter');
  void node.offsetWidth;
  node.classList.add('view-enter');
}

function notify(message, error = false) {
  const node = $('#notice'); node.textContent = message; node.className = error ? 'error' : ''; node.hidden = false;
  clearTimeout(noticeTimer); noticeTimer = setTimeout(() => node.hidden = true, error ? 10000 : 4500);
}
async function api(path, method = 'GET', body) {
  const headers = {};
  if (user?.csrf) headers['X-CSRF-Token'] = user.csrf;
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const r = await fetch('/api' + path, {method, headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined});
  if (!r.ok) {
    let data; try { data = await r.json(); } catch { data = {detail: 'Anfrage fehlgeschlagen'}; }
    if (r.status === 401 && user) { user = null; disconnect(); await auth(); }
    throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  }
  return r.json();
}
function bind(selector, event, fn, root = document) {
  $$(selector, root).forEach(node => node.addEventListener(event, async e => {
    if (event === 'submit') e.preventDefault();
    const button = event === 'submit' ? $('button[type=submit]', node) : node;
    busy(button, true);
    try { await fn(e, node); } catch (error) { notify(error.message, true); }
    finally { busy(button, false); applyRights(); }
  }));
}
function formValues(form) { return Object.fromEntries(new FormData(form)); }
function disconnect() { authCleanup(); source?.close(); source = null; clearInterval(metricsTimer); clearInterval(overviewTimer); clearInterval(jobTimer); charts.forEach(c=>c.destroy()); charts=[]; }
function modal(title, content, submit, label = 'Speichern') {
  const dialog = $('#dialog');
  $('#dialog-content').innerHTML = `<h2>${escape(title)}</h2><form id="modal-form">${content}<div class="error" id="modal-error" hidden></div><div class="form-actions"><button type="button" id="cancel">Abbrechen</button><button type="submit" class="primary">${escape(label)}</button></div></form>`;
  $('#cancel').onclick = () => dialog.close();
  $('#modal-form').onsubmit = async e => {
    e.preventDefault(); const b = $('button[type=submit]', e.target); busy(b, true);
    try { const close = await submit(formValues(e.target), e.target); if (close !== false) dialog.close(); }
    catch (error) { $('#modal-error').textContent = error.message; $('#modal-error').hidden = false; }
    finally { busy(b, false); }
  };
  dialog.showModal();
}
function confirmAction(title, text, action, label = 'Bestätigen') {
  modal(title, `<p>${escape(text)}</p>`, action, label);
}
function status(value) { return `<span class="status ${escape(value)}">${escape(labels[value] || value)}</span>`; }
function table(headers, rows, empty = 'Keine Einträge vorhanden.') {
  return rows.length ? `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${escape(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : `<div class="empty">${escape(empty)}</div>`;
}
function serverUrl(suffix='') { return `/servers/${selected.id}${suffix}`; }

async function auth() {
  const setup = await api('/setup');
  $('#app').innerHTML = `<main class="auth-page"><div class="auth-box">${brand()}<h1>${setup.required ? 'Panel einrichten' : 'Anmelden'}</h1><p class="muted">Deine Hytale-Server auf Docker und Unraid.</p><form id="auth-form">${setup.required ? '<label>Einrichtungsschlüssel<input name="setup_key" type="password" required autocomplete="off"></label><p class="hint">Der einmalige Schlüssel liegt im Container unter <code>/data/setup.key</code>. Er schützt die erste Einrichtung.</p>' : ''}<label>Benutzername<input name="username" required minlength="3" maxlength="64" autocomplete="username"></label><label>Passwort<input name="password" type="password" required ${setup.required ? 'minlength="12"' : ''} autocomplete="${setup.required ? 'new-password' : 'current-password'}"></label><div id="auth-error" class="error" hidden></div><button type="submit" class="primary">${setup.required ? 'Administratorkonto erstellen' : 'Anmelden'}</button></form></div></main>`;
  $('#auth-form').onsubmit = async e => {
    e.preventDefault(); const b = $('button', e.target); busy(b, true);
    try {
      const values = formValues(e.target);
      if (setup.required) await api('/setup', 'POST', values);
      user = await api('/login', 'POST', {username:values.username,password:values.password});
      await navigate('servers');
    } catch (error) { $('#auth-error').textContent = error.message; $('#auth-error').hidden = false; }
    finally { busy(b, false); }
  };
}
const svgIcon=(path)=>`<span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${path}</svg></span>`;
const icons={servers:svgIcon('<rect x="3" y="3" width="18" height="7"/><rect x="3" y="14" width="18" height="7"/><path d="M6 6h2m-2 11h2"/>'),users:svgIcon('<circle cx="9" cy="7" r="4"/><path d="M2 21v-3a7 7 0 0 1 14 0v3M17 3a4 4 0 0 1 0 8m2 3a6 6 0 0 1 3 7"/>'),system:svgIcon('<path d="M12 3v4m0 10v4M3 12h4m10 0h4M5 5l3 3m8 8 3 3M5 19l3-3M16 8l3-3"/><circle cx="12" cy="12" r="5"/>'),jobs:svgIcon('<path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"/>')};
function brand(){return '<div class="brand"><img src="/icon.svg" alt=""><div><strong>HyPanel</strong><small>Hytale Server Hosting</small></div></div>';}
function shell() {
  if ($('.shell')) {
    $$('.site-header [data-nav]').forEach(button => {
      const active = button.dataset.nav === section;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    $('.account-menu').open = false;
    return;
  }
  const navigation={servers:'Server',...(user.role==='admin'?{users:'Benutzer',jobs:'Aufträge',audit:'Aktivitäten',system:'Panel'}:{})};
  $('#app').innerHTML=`<div class="shell"><header class="site-header"><div class="grass-strip"></div><div class="header-inner">${brand()}<nav aria-label="Hauptnavigation">${Object.entries(navigation).map(([key,label])=>`<button data-nav="${key}" class="${section===key?'active':''}">${icons[key]||icons.jobs}${label}</button>`).join('')}</nav><details class="account-menu"><summary aria-label="Benutzermenü"><span class="avatar">${escape(user.username[0].toUpperCase())}</span><span>${escape(user.username)}</span>⌄</summary><div class="account-dropdown"><strong>${escape(user.username)}</strong><p class="small muted">${user.role==='admin'?'Administrator':'Mitglied'}</p><button data-nav="account">Mein Konto</button><button id="logout">Abmelden</button></div></details></div></header><main class="content" id="content"></main><footer class="site-footer">HyPanel · selbstgehostet · Hytale auf Docker & Unraid · ${escape(panelVersion)}</footer></div>`;
  bind('[data-nav]','click',(_,n)=>navigate(n.dataset.nav));
  bind('#logout','click',async()=>{await api('/logout','POST');user=null;disconnect();await auth();});
}
async function navigate(next) {
  disconnect(); section = next; selected = null; shell();
  $('#content').innerHTML = '<div class="loading" role="status">Wird geladen …</div>';
  const render = {servers:renderServers,users:renderUsers,audit:renderAudit,system:renderSystem,jobs:renderJobs,account:renderAccount}[next];
  await render();
  reveal($('#content'));
}
function bar(percent,cls=''){return `<progress class="pixel-track ${cls}" max="100" value="${Math.min(100,Math.max(0,percent||0))}"></progress>`;}
function serverCard(s){const sample=s.last_metric;const cpu=sample?.cpu;const ram=sample?.memory;return `<article class="server-card" data-card="${s.id}"><div class="card-head"><img src="/icon.svg" alt=""><div class="card-title"><h2><button class="name-button" data-open="${s.id}">${escape(s.name)}</button></h2><p class="muted">Hytale · ${s.patchline==='release'?'Release':'Pre-Release'}${s.installed?'':' · Installation ausstehend'}</p><span class="card-address">${escape(location.hostname)}:${s.port} / UDP</span></div>${status(s.status==='running'&&!s.ready?'starting':s.status)}</div><div class="card-body"><div><div class="resource-line"><span>CPU</span><strong>${cpu==null?'—':cpu.toFixed(1)+' %'} · ${s.cpus} Kerne</strong></div>${bar(cpu,'cpu')}</div><div><div class="resource-line"><span>Arbeitsspeicher</span><strong>${ram==null?'—':bytes(ram*1024**2)} / ${bytes(s.memory_mb*1024**2)}</strong></div>${bar(ram/(s.memory_mb||1)*100)}</div><div class="resource-line"><span>Spieler</span><strong>${sample?.players??'Nicht verfügbar'}</strong><span>TPS</span><strong>${sample?.tps==null?'—':sample.tps.toFixed(1)}</strong></div></div><div class="card-foot"><div class="actions">${(s.rights||[]).includes('server.power')?`<button data-card-power="start" data-sid="${s.id}" ${s.status==='running'?'disabled':''}>▶ Start</button><button data-card-power="stop" data-sid="${s.id}" ${s.status!=='running'?'disabled':''}>■ Stop</button>`:''}</div><button data-open="${s.id}">Öffnen →</button></div></article>`;}
async function renderServers() {
  servers=await api('/servers');
  $('#content').innerHTML=`<div class="titlebar"><div><h1>Deine Server</h1><p class="muted">${servers.length} Server · ${servers.filter(s=>s.status==='running').length} Container aktiv</p></div><div class="actions"><button id="refresh">Aktualisieren</button>${user.role==='admin'?'<button id="create-server" class="primary">+ Server erstellen</button>':''}</div></div>${user.role==='admin'?'<div id="host-stats"></div>':''}<div class="server-grid">${servers.map(serverCard).join('')}</div>${servers.length?'':'<div class="empty">Noch keine Server. Erstelle deinen ersten Hytale-Server.</div>'}`;
  if(user.role==='admin'){const hostNode=$('#host-stats');api('/host').then(h=>{if(!hostNode.isConnected)return;hostNode.innerHTML=`<div class="stat-grid"><div class="stat-card"><span class="caption">Host CPU</span><div class="value">${h.cpu_percent.toFixed(1)} %</div>${bar(h.cpu_percent,'cpu')}</div><div class="stat-card"><span class="caption">Host RAM</span><div class="value">${bytes(h.memory_used)}</div><span class="caption">von ${bytes(h.memory_total)}</span></div><div class="stat-card"><span class="caption">Host-Laufzeit</span><div class="value">${Math.floor(h.uptime/86400)} Tage</div><span class="caption">${Math.floor(h.uptime/3600)%24} Stunden</span></div><div class="stat-card"><span class="caption">Spielserver</span><div class="value">${servers.filter(s=>s.ready).length} bereit</div><span class="caption">${servers.length} eingerichtet</span></div></div>`;}).catch(()=>{if(hostNode.isConnected)hostNode.textContent='Host-Messwerte derzeit nicht verfügbar.';});}
  bind('#refresh','click',renderServers);bind('#create-server','click',()=>serverForm());bind('[data-open]','click',(_,n)=>openServer(n.dataset.open));
  bind('[data-card-power]','click',async(_,n)=>{await api(`/servers/${n.dataset.sid}/power/${n.dataset.cardPower}`,'POST');await renderServers();});
  clearInterval(metricsTimer);metricsTimer=setInterval(async()=>{if(section!=='servers'||selected)return;try{const list=await api('/servers');for(const s of list){const card=$(`[data-card="${s.id}"]`);if(card){const temp=document.createElement('template');temp.innerHTML=serverCard(s);card.replaceWith(temp.content.firstElementChild);}}bind('[data-open]','click',(_,n)=>openServer(n.dataset.open));bind('[data-card-power]','click',async(_,n)=>{await api(`/servers/${n.dataset.sid}/power/${n.dataset.cardPower}`,'POST');await renderServers();});}catch{}},10000);
}
function serverForm(existing, clone = false) {
  const s = existing || {name:'',port:5520,memory_mb:4096,cpus:2,patchline:'release'};
  const title = clone?'Server klonen':existing?'Server bearbeiten':'Server erstellen';
  modal(title, `<label>Name<input name="name" required maxlength="80" value="${escape(clone?s.name+' Kopie':s.name)}"></label><div class="form-grid"><label>UDP-Port<input name="port" type="number" min="1024" max="65535" required value="${clone?s.port+1:s.port}"></label><label>RAM-Limit (MiB)<input name="memory_mb" type="number" min="4096" max="262144" step="256" value="${s.memory_mb}" required></label><label>CPU-Limit (Kerne)<input name="cpus" type="number" min="0.5" max="128" step="0.5" value="${s.cpus}" required></label><label>Update-Kanal<select name="patchline"><option value="release">Release</option><option value="pre-release" ${s.patchline==='pre-release'?'selected':''}>Pre-Release</option></select></label></div><label class="checkbox"><input type="checkbox" name="autostart" ${s.autostart?'checked':''}>Beim Panelstart starten</label><label class="checkbox"><input type="checkbox" name="autorestart" ${s.autorestart?'checked':''}>Nach Absturz neu starten (maximal 3 Versuche)</label><p class="hint">1 GiB des RAM-Limits bleibt für JVM und native Bibliotheken reserviert. Jeder Server läuft in einem eigenen Container.</p>`, async(v,f)=>{
    const body = {...v,port:Number(v.port??s.port),memory_mb:Number(v.memory_mb??s.memory_mb),cpus:Number(v.cpus??s.cpus),patchline:v.patchline??s.patchline,autostart:f.elements.autostart.checked,autorestart:f.elements.autorestart.checked};
    if (clone) await api(`/servers/${existing.id}/clone`,'POST',body);
    else if (existing) await api(`/servers/${existing.id}`,'PUT',body);
    else await api('/servers','POST',body);
    notify('Server gespeichert'); await navigate('servers');
  },clone?'Klonen':existing?'Speichern':'Erstellen');
  if(!can('settings.write'))$$('#edit-server').forEach(n=>n.disabled=true);
  if(!can('files.read'))$$('[data-tab=files]').forEach(n=>n.disabled=true);
  if(!can('console.read'))$$('[data-tab=console]').forEach(n=>n.disabled=true);
  if(!can('auth.manage'))$$('[data-tab=hytale],[data-open-hytale]').forEach(n=>n.disabled=true);
  if(user.role!=='admin')$$('#modal-form [name=port],#modal-form [name=memory_mb],#modal-form [name=cpus],#modal-form [name=patchline]').forEach(n=>n.disabled=true);
}
async function openServer(id) {
  disconnect(); selected = await api(`/servers/${id}`); tab='overview'; filePath='Server'; await renderServer();
  metricsTimer = setInterval(async()=>{if(selected?.id!==id)return; try {const current=await api(`/servers/${id}`);if(selected?.id===id){selected=current;updateMetrics();}}catch{}},5000);
}
function updateMetrics() {
  if (!$('#server-meta') || !selected) return;
  $('#server-meta').innerHTML = `${status(selected.status==='running'&&!selected.ready?'starting':selected.status)}<span>UDP <strong>${escape(location.hostname)}:${selected.port}</strong></span><span>RAM <strong>${bytes(selected.memory_bytes)} / ${bytes(selected.memory_mb*1024**2)}</strong></span><span>CPU <strong>${(selected.cpu_percent/selected.cpus).toFixed(1)}% / ${selected.cpus} Kerne</strong></span>${selected.exit_code?`<span>Exit-Code ${selected.exit_code}</span>`:''}`;
}
async function renderServer() {
  const previousTabs = $('.tabs');
  const scrollLeft = previousTabs?.scrollLeft || 0;
  const focusedTab = document.activeElement?.dataset.tab;
  authCleanup();
  source?.close(); source = null;
  clearInterval(overviewTimer);charts.forEach(c=>c.destroy());charts=[];
  const tabs = {overview:'Übersicht',hytale:'Hytale-Account',console:'Konsole',files:'Dateien',config:'Konfiguration',mods:'Mods / Plugins',catalog:'Mod-Browser',worlds:'Welten',players:'Spieler',backups:'Backups',schedules:'Zeitpläne',access:'Zugriff',settings:'Einstellungen'};
  $('#content').innerHTML = `<button id="back" class="name-button">← Alle Server</button><div class="titlebar spaced"><div><h1>${escape(selected.name)}</h1><div class="server-meta" id="server-meta"></div></div><div class="actions"><button data-power="start" class="primary">Starten</button><button data-power="restart">Neustart</button><button data-power="stop">Stoppen</button><button data-power="kill" class="danger">Erzwingen</button></div></div><nav class="tabs" aria-label="Serverbereiche">${Object.entries(tabs).map(([key,label])=>`<button data-tab="${key}" class="${tab===key?'active':''}">${label}</button>`).join('')}</nav><div id="tab-content"></div>`;
  updateMetrics();
  $('.tabs').scrollLeft = scrollLeft;
  $$('[data-tab]').forEach(button => {
    if (button.dataset.tab === tab) button.setAttribute('aria-current', 'page');
    if (button.dataset.tab === focusedTab) button.focus({preventScroll:true});
  });
  bind('#back','click',()=>navigate('servers'));
  bind('[data-power]','click',async(_,n)=>{
    const action=async()=>{selected=await api(serverUrl(`/power/${n.dataset.power}`),'POST'); updateMetrics(); notify('Serveraktion ausgeführt');};
    if(n.dataset.power==='kill') confirmAction('Server sofort beenden','Nicht gespeicherte Daten können verloren gehen.',action,'Sofort beenden'); else await action();
  });
  bind('[data-tab]','click',async(_,n)=>{tab=n.dataset.tab; await renderServer();});
  await ({overview:renderOverview,hytale:renderHytaleAuth,catalog:renderCatalog,access:renderAccess,console:renderConsole,files:renderFiles,config:renderConfig,mods:renderMods,worlds:renderWorlds,players:renderPlayers,backups:renderBackups,schedules:renderSchedules,settings:renderServerSettings}[tab])();
  applyRights();
  reveal($('#tab-content'));
}
function applyRights(){
  if(!selected||!user)return;
  const selectors={'server.power':'[data-power]','console.write':'[data-command],#command-form button,#apply-update','files.write':'#editor button[type=submit],#mkdir,#new-file,#upload,#unpack,[data-rename],[data-delete]','config.write':'#config-form button','mods.write':'#upload-mod,[data-toggle],[data-remove],[data-mod-update],#check-mod-updates','players.write':'#player-action button','backups.create':'[data-backup]','backups.restore':'[data-restore]','automation.write':'#new-schedule,[data-edit-job],[data-run-job],[data-toggle-job],[data-delete-job]'};
  for(const [right,selector] of Object.entries(selectors)){if(!can(right))$$(selector).forEach(n=>{n.disabled=true;n.title='Keine Berechtigung: '+rightLabels[right];});}
  if(!can('settings.write'))$$('#edit-server').forEach(n=>n.disabled=true);
  if(!can('files.read'))$$('[data-tab=files]').forEach(n=>n.disabled=true);
  if(!can('console.read'))$$('[data-tab=console]').forEach(n=>n.disabled=true);
  if(!can('auth.manage'))$$('[data-tab=hytale],[data-open-hytale]').forEach(n=>n.disabled=true);
  if(user.role!=='admin')$$('#install,#import,#clone-server,[data-delete-backup],#backup-policy button').forEach(n=>n.disabled=true);
}
async function renderConsole(){
  if(!can('console.read')){$('#tab-content').innerHTML='<div class="empty">Keine Berechtigung zum Lesen der Konsole.</div>';return;}
  $('#tab-content').innerHTML=`${!selected.installed?'<div class="hint">Hytale zuerst unter Einstellungen installieren oder importieren.</div>':''}<div class="toolbar"><div class="actions"><button data-open-hytale>Hytale-Anmeldeassistent</button><button data-command="help">Hilfe</button></div><a href="/api${serverUrl('/logs?download=true')}">Protokoll herunterladen</a></div><div class="console-tools"><label>Filter<input id="console-filter" type="search" placeholder="Protokoll durchsuchen"></label><label class="checkbox"><input id="console-follow" type="checkbox" checked>Automatisch scrollen</label><button id="console-clear">Anzeige leeren</button></div><pre class="console spaced" id="console" aria-label="Serverkonsole"></pre><form class="command-form" id="command-form"><input name="command" required maxlength="2048" placeholder="Konsolenbefehl, z. B. help" aria-label="Konsolenbefehl" autocomplete="off"><button type="submit">Senden</button></form><p class="muted small">↑ / ↓ ruft den Befehlsverlauf dieser Sitzung auf. Geräteanmeldung im offiziellen Hytale-Fenster bestätigen.</p>`;
  let output='',skip=0;
  const show=logs=>{output=logs;const node=$('#console');if(!node)return;const filter=$('#console-filter').value.toLowerCase();const lines=logs.split('\n').slice(skip).filter(line=>line.toLowerCase().includes(filter));node.innerHTML=lines.map(line=>`<span class="${/error|exception|severe/i.test(line)?'console-error':/warn/i.test(line)?'console-warn':''}">${escape(line)}</span>`).join('\n')||'Noch keine passende Serverausgabe.';if($('#console-follow').checked)node.scrollTop=node.scrollHeight;};
  show((await api(serverUrl('/logs'))).logs);source=new EventSource('/api'+serverUrl('/events'));source.onmessage=e=>{try{show(JSON.parse(e.data).logs);}catch{}};source.addEventListener('closed',()=>source?.close());
  bind('#console-filter','input',()=>show(output));bind('#console-clear','click',()=>{skip=output.split('\n').length;show(output);});
  bind('#command-form','submit',async(_,n)=>{const value=formValues(n).command;await api(serverUrl('/command'),'POST',{command:value});commandHistory.push(value);if(commandHistory.length>100)commandHistory.shift();historyIndex=commandHistory.length;n.reset();});
  $('#command-form input').addEventListener('keydown',e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();historyIndex=Math.min(commandHistory.length,Math.max(0,historyIndex+(e.key==='ArrowUp'?-1:1)));e.target.value=commandHistory[historyIndex]||'';}});
  bind('[data-command]','click',(_,n)=>sendCommand(n.dataset.command));
  bind('[data-open-hytale]','click',async()=>{tab='hytale';await renderServer();});
}
async function sendCommand(command) { await api(serverUrl('/command'),'POST',{command}); notify('Befehl gesendet. Ergebnis in der Konsole.'); }
async function editText(path, title=path) {
  let content=''; try{content=(await api(serverUrl('/files/content?path='+enc(path)))).content;}catch(e){if(!e.message.includes('nicht gefunden'))throw e;}
  $('#tab-content').innerHTML = `<h2>${escape(title)}</h2><p class="hint">Der Server muss zum Speichern gestoppt sein. JSON wird vor dem Schreiben geprüft.</p><form id="editor"><label>Dateiinhalt<textarea name="content" spellcheck="false">${escape(content||'{}')}</textarea></label><div class="editor-actions"><button type="submit" class="primary">Speichern</button><button type="button" id="editor-back">Zurück</button><a href="/api${serverUrl('/files/download?path='+enc(path))}">Herunterladen</a></div></form>`;
  bind('#editor','submit',async(e,n)=>{const back=$('#editor-back',n);back.disabled=true;try{await api(serverUrl('/files/content'),'PUT',{path,...formValues(n)});notify('Datei gespeichert');}finally{back.disabled=false;}});
  bind('#editor-back','click',renderServer);
}
async function renderFiles() {
  const rows=await api(serverUrl('/files?path='+enc(filePath)));
  const parts=filePath.split('/').filter(Boolean);
  $('#tab-content').innerHTML=`<div class="toolbar"><div class="breadcrumbs"><button data-directory="">Serverwurzel</button>${parts.map((p,i)=>`<span>/</span><button data-directory="${escape(parts.slice(0,i+1).join('/'))}">${escape(p)}</button>`).join('')}</div><div class="actions"><button id="mkdir">Ordner erstellen</button><button id="new-file">Datei erstellen</button><button id="upload">Hochladen</button><button id="sftp-info">SFTP-Zugang</button><button id="unpack">ZIP entpacken</button></div></div>${table(['Name','Größe','Geändert',''],rows.map(f=>`<tr><td><button class="name-button" ${f.directory?'data-directory':'data-edit'}="${escape(f.path)}">${f.directory?'▸ ':''}${escape(f.name)}</button></td><td>${f.directory?'—':bytes(f.size)}</td><td>${date(f.modified)}</td><td><div class="actions">${!f.directory?`<a href="/api${serverUrl('/files/download?path='+enc(f.path))}">Download</a>`:''}<button data-rename="${escape(f.path)}">Umbenennen</button><button data-delete="${escape(f.path)}" class="danger">Löschen</button></div></td></tr>`),'Dieses Verzeichnis ist leer.')}<p class="small muted">Änderungen und Uploads sind bei gestopptem Server möglich. Anmeldedaten und symbolische Links sind geschützt.</p>`;
  bind('[data-directory]','click',async(_,n)=>{filePath=n.dataset.directory;await renderFiles();});
  bind('[data-edit]','click',(_,n)=>editText(n.dataset.edit));
  bind('#mkdir','click',()=>modal('Ordner erstellen','<label>Name<input name="name" required></label>',async v=>{await api(serverUrl('/files/mkdir'),'POST',{path:[filePath,v.name].filter(Boolean).join('/')});await renderFiles();}));
  bind('#new-file','click',()=>modal('Datei erstellen','<label>Dateiname<input name="name" required placeholder="config.json"></label>',async v=>{await editText([filePath,v.name].filter(Boolean).join('/'));}));
  bind('#upload','click',()=>uploadModal(filePath,renderFiles));
  bind('#sftp-info','click',async()=>{const info=await api(serverUrl('/sftp'));modal('SFTP-Zugang',`<p>Mit WinSCP oder FileZilla verbinden. Passwort: dein Panel-Passwort.</p><dl class="definition"><dt>Host</dt><dd>${escape(location.hostname)}</dd><dt>Port</dt><dd>${info.port}</dd><dt>Benutzername</dt><dd><input readonly aria-label="SFTP-Benutzername" value="${escape(info.username)}"></dd><dt>Protokoll</dt><dd>SFTP</dd></dl><p class="hint">Nur dieses Serververzeichnis ist erreichbar. Schreibzugriff benötigt die Dateiberechtigung und einen gestoppten Server.</p>`,()=>{} ,'Schließen');});
  bind('#unpack','click',()=>modal('ZIP entpacken',`<label>ZIP-Pfad ab Serverwurzel<input name="path" required value="${escape(filePath)}/paket.zip"></label><label>Neues Zielverzeichnis<input name="destination" required value="${escape(filePath)}/entpackt"></label>`,async v=>{await api(serverUrl('/archive/extract'),'POST',v);await renderFiles();}));
  bind('[data-rename]','click',(_,n)=>modal('Umbenennen oder verschieben',`<label>Neuer Pfad ab Serverwurzel<input name="destination" required value="${escape(n.dataset.rename)}"></label>`,async v=>{await api(serverUrl('/files/rename'),'POST',{path:n.dataset.rename,...v});await renderFiles();}));
  bind('[data-delete]','click',(_,n)=>confirmAction('Datei löschen',n.dataset.delete+' wird dauerhaft entfernt.',async()=>{await api(serverUrl('/files/delete'),'POST',{path:n.dataset.delete});await renderFiles();},'Löschen'));
}
function uploadModal(path, refresh, importing=false) {
  modal(importing?'Hytale-Paket importieren':'Datei hochladen',`<label>${importing?'ZIP mit Server/HytaleServer.jar und Assets.zip':'Datei'}<input name="file" type="file" required ${importing?'accept=".zip"':''}></label>${importing?'':'<label class="checkbox"><input name="overwrite" type="checkbox">Vorhandene Datei ersetzen</label>'}<p class="hint">Der Server muss gestoppt sein. Große Pakete brauchen entsprechend freien Speicher.</p><progress id="upload-progress" hidden max="100"></progress>`, async(v,f)=>{
    const body=new FormData();body.append('file',f.elements.file.files[0]);
    if(!importing){body.append('path',path);body.append('overwrite',f.elements.overwrite.checked);}
    const progress=$('#upload-progress');progress.hidden=false;
    await new Promise((resolve,reject)=>{const x=new XMLHttpRequest();x.open('POST','/api'+serverUrl(importing?'/import':'/files/upload'));x.setRequestHeader('X-CSRF-Token',user.csrf);x.upload.onprogress=e=>{if(e.lengthComputable)progress.value=e.loaded/e.total*100;};x.onload=()=>{let d;try{d=JSON.parse(x.responseText);}catch{}if(x.status>=200&&x.status<300)resolve(d);else reject(Error(d?.detail||'Upload fehlgeschlagen'));};x.onerror=()=>reject(Error('Upload-Verbindung unterbrochen'));x.send(body);});
    notify('Upload abgeschlossen');selected=await api(serverUrl());await refresh();
  },'Hochladen');
}
async function renderConfig() {
  const configs=[['Server/config.json','Serverkonfiguration'],['Server/permissions.json','Berechtigungen'],['Server/whitelist.json','Whitelist'],['Server/bans.json','Sperrliste']];
  $('#tab-content').innerHTML=`<h2>Konfiguration</h2><p class="muted">Bearbeite die Originaldateien deiner Hytale-Version. Alle vorhandenen Einstellungen bleiben zugänglich.</p>${table(['Datei',''],configs.map(([path,name])=>`<tr><td>${name}<div class="small muted">${path}</div></td><td><button data-config="${path}">Bearbeiten</button></td></tr>`))}<div class="section"><h3>Hytale-Updates</h3><div class="actions"><button data-command="update check">Prüfen</button><button data-command="update status">Status</button><button data-command="update download">Vorbereiten</button><button id="apply-update">Update anwenden</button></div><p class="small muted spaced">Die integrierte Updatefunktion benötigt die offiziellen Launcher-Dateien. Der HyPanel-Runner übernimmt den Neustart bei Exit-Code 8.</p></div>`;
  const {fields}=await api(serverUrl('/config-form'));
  $('#tab-content').insertAdjacentHTML('afterbegin',`<div class="section"><h3>Servereinstellungen</h3><form id="config-form"><div class="form-grid">${fields.map(f=>`<label>${escape(f.label)}<input name="${f.key}" type="${f.type}" value="${escape(f.value)}" ${f.min!==null?`min="${f.min}" max="${f.max}"`:''} placeholder="${f.present?'':'Hytale-Standard'}"></label>`).join('')}</div><p class="small muted">Unbekannte Einstellungen bleiben erhalten. Leere Felder werden nicht geändert. Server zum Speichern stoppen.</p><button type="submit" class="primary">Speichern</button></form></div>`);
  bind('#config-form','submit',async(_,n)=>{const values=formValues(n);const result={};for(const f of fields){if(values[f.key]!=='')result[f.key]=f.type==='number'?Number(values[f.key]):values[f.key];}await api(serverUrl('/config-form'),'PUT',{values:result});notify('Konfiguration gespeichert');});
  bind('[data-config]','click',(_,n)=>editText(n.dataset.config));bind('[data-command]' ,'click',(_,n)=>sendCommand(n.dataset.command));
  bind('#apply-update','click',()=>confirmAction('Hytale aktualisieren','HyPanel erstellt zuerst ein vollständiges Backup. Danach wird das vorbereitete Update über die Hytale-Konsole angewendet.',async()=>{await api(serverUrl('/backups?full=true'),'POST');await sendCommand('update apply --confirm');}));
}
async function renderMods() {
  const rows=await api(serverUrl('/mods'));
  $('#tab-content').innerHTML=`<div class="titlebar"><div><h2>Mods</h2><p class="muted">JAR- und ZIP-Mods aus vertrauenswürdigen Quellen hochladen.</p></div><button id="upload-mod" class="primary">Mod hochladen</button></div>${table(['Mod','Status','Größe',''],rows.map(m=>`<tr><td>${escape(m.name)}</td><td>${m.enabled?'Aktiviert':'Deaktiviert'}</td><td>${bytes(m.size)}</td><td><button data-toggle="${escape(m.name)}">${m.enabled?'Deaktivieren':'Aktivieren'}</button> <button class="danger" data-remove="${escape(m.path)}">Entfernen</button></td></tr>`),'Keine Mods installiert.')}<p class="hint">Mods werden beim nächsten Start geladen. Zum Ändern den Server stoppen.</p>`;
  bind('#upload-mod','click',()=>uploadModal('Server/mods',renderMods));
  bind('[data-toggle]','click',async(_,n)=>{await api(serverUrl('/mods/toggle'),'POST',{path:n.dataset.toggle});await renderMods();});
  bind('[data-remove]','click',(_,n)=>confirmAction('Mod entfernen',n.dataset.remove,async()=>{await api(serverUrl('/files/delete'),'POST',{path:n.dataset.remove});await renderMods();},'Entfernen'));
}
async function renderWorlds() {
  const rows=await api(serverUrl('/worlds'));
  $('#tab-content').innerHTML=`<h2>Welten</h2>${table(['Welt',''],rows.map(w=>`<tr><td>${escape(w.name)}</td><td><button data-world="${escape(w.config_path)}">Konfiguration bearbeiten</button> <button data-folder="${escape('Server/universe/worlds/'+w.name)}">Dateien öffnen</button></td></tr>`),'Hytale erstellt die erste Welt beim Start.')}<p class="hint">Weltkonfigurationen enthalten unter anderem Seed, PvP, Spielzeit und Speichereinstellungen. Vor Änderungen ein Backup erstellen.</p>`;
  bind('[data-world]','click',(_,n)=>editText(n.dataset.world));
  bind('[data-folder]','click',async(_,n)=>{filePath=n.dataset.folder;tab='files';await renderServer();});
}
async function renderPlayers() {
  const players=await api(serverUrl('/players'));
  $('#tab-content').innerHTML=`<h2>Spielerverwaltung</h2><p class="muted">Befehle werden an den echten Server gesendet. Die Antworten erscheinen in der Konsole.</p><div class="actions"><button data-command="who">Spielerliste abfragen</button><button data-command="whitelist list">Whitelist anzeigen</button><button data-command="whitelist enable">Whitelist aktivieren</button><button data-command="whitelist disable">Whitelist deaktivieren</button><button data-player-file="Server/whitelist.json">Whitelist-Datei</button><button data-player-file="Server/bans.json">Sperrliste</button><button data-player-file="Server/permissions.json">Berechtigungen</button></div><div class="section"><form id="player-action"><div class="form-grid"><label>Spielername oder UUID<input name="player" required pattern="[A-Za-z0-9_-]+" maxlength="64"></label><label>Aktion<select name="action"><option value="kick">Kicken</option><option value="ban">Sperren</option><option value="unban">Entsperren</option><option value="whitelist add">Zur Whitelist hinzufügen</option><option value="whitelist remove">Von Whitelist entfernen</option><option value="op add">Operator hinzufügen</option><option value="op remove">Operator entfernen</option></select></label></div><button type="submit">Ausführen</button></form></div><p class="small muted">Verfügbare Befehle und erforderliche Parameter können sich mit Hytale-Versionen ändern. Nutze <code>help</code> in der Konsole für die aktuelle Syntax.</p>`;
  $('#tab-content').insertAdjacentHTML('afterbegin',`<div class="section"><h3>Aktive Spieler</h3>${players.available?table(['Name','UUID'],players.online.map(p=>`<tr><td>${escape(p.name)}</td><td>${escape(p.uuid)}</td></tr>`),'Keine Spieler online.'):'<p class="muted">Telemetrie beim nächsten Serverstart verfügbar.</p>'}<h3 class="spaced">Bekannte Spieler</h3>${table(['Name','Zuletzt gesehen'],players.known.map(p=>`<tr><td>${escape(p.name)}</td><td>${date(p.last_seen)}</td></tr>`),'Noch keine Spieler erfasst.')}</div>`);
  bind('[data-command]','click',(_,n)=>sendCommand(n.dataset.command));
  bind('[data-open-hytale]','click',async()=>{tab='hytale';await renderServer();});
  bind('[data-player-file]','click',(_,n)=>editText(n.dataset.playerFile));
  bind('#player-action','submit',async(e,n)=>{const v=formValues(n);confirmAction('Spieleraktion ausführen',v.action+' '+v.player,()=>api(serverUrl('/players/action'),'POST',{action:v.action,player:v.player}));});
}
async function renderBackups(){
  const [rows,policy]=await Promise.all([api(serverUrl('/backups')),api(serverUrl('/backup-policy'))]);
  $('#tab-content').innerHTML=`<div class="titlebar"><div><h2>Backups</h2><p class="muted">${bytes(policy.used_bytes)} von ${bytes(policy.quota_bytes)} · maximal ${policy.retention} Sicherungen</p></div><div class="actions"><button data-backup="false" class="primary">Daten sichern</button><button data-backup="true">Vollständig sichern</button></div></div>${table(['Backup','Größe','Erstellt',''],rows.map(b=>`<tr><td>${escape(b.name)}</td><td>${bytes(b.size)}</td><td>${date(b.created)}</td><td><div class="actions"><a href="/api${serverUrl('/backups/'+enc(b.name))}">Herunterladen</a><button data-restore="${escape(b.name)}">Wiederherstellen</button><button data-delete-backup="${escape(b.name)}" class="danger">Löschen</button></div></td></tr>`),'Noch keine Backups.')}<p class="hint">Ein laufender Server wird für eine konsistente Sicherung gestoppt und anschließend gestartet. Vollständige Backups enthalten auch Assets und Serverdateien. Hytale-Anmeldedaten bleiben geschützt.</p>${user.role==='admin'?`<div class="section"><h3>Aufbewahrung und Speicherlimit</h3><form id="backup-policy"><div class="form-grid"><label>Anzahl Backups<input name="retention" type="number" min="2" max="365" required value="${policy.retention}"></label><label>Backup-Quote (GiB)<input name="quota_gb" type="number" min="1" max="100000" required value="${policy.quota_bytes/1024**3}"></label></div><p class="small muted">Globales Limit: ${bytes(policy.global_quota_bytes)}. Quoten verhindern auch bei parallelen Aufträgen eine Überschreitung.</p><button type="submit">Speichern</button></form></div>`:''}`;
  bind('[data-backup]','click',(_,n)=>startTask({action:'backup',full:n.dataset.backup==='true'},()=>tab==='backups'?renderBackups():null));
  bind('[data-restore]','click',(_,n)=>confirmAction('Backup wiederherstellen','Server zuerst stoppen. Vor dem Einspielen wird eine vollständige Sicherung erstellt.',()=>startTask({action:'restore',name:n.dataset.restore},()=>tab==='backups'?renderBackups():null),'Wiederherstellen'));
  bind('[data-delete-backup]','click',(_,n)=>confirmAction('Backup löschen',n.dataset.deleteBackup,async()=>{await api(serverUrl('/backups/'+enc(n.dataset.deleteBackup)),'DELETE');await renderBackups();},'Löschen'));
  bind('#backup-policy','submit',async(_,n)=>{const v=formValues(n);await api(serverUrl('/backup-policy'),'PUT',{retention:Number(v.retention),quota_gb:Number(v.quota_gb)});await renderBackups();});applyRights();
}
async function renderSchedules() {
  const rows=await api(serverUrl('/schedules'));
  $('#tab-content').innerHTML=`<div class="titlebar"><h2>Zeitpläne</h2><button id="new-schedule" class="primary">Zeitplan erstellen</button></div>${table(['Name','Aktion','Cron / Zeitzone','Nächster Lauf','Letztes Ergebnis',''],rows.map(j=>`<tr><td>${escape(j.name)}<div class="small muted">${j.enabled?'Aktiv':'Pausiert'}</div></td><td>${escape(j.action)}</td><td>${escape(j.cron)}<div class="small muted">${escape(j.timezone)}</div></td><td>${j.enabled?date(j.next_run):'—'}</td><td class="wrap">${escape(j.last_result||'Noch nicht ausgeführt')}</td><td><div class="actions"><button data-edit-job="${j.id}">Bearbeiten</button><button data-run-job="${j.id}">Jetzt ausführen</button><button data-toggle-job="${j.id}">${j.enabled?'Pausieren':'Aktivieren'}</button><button class="danger" data-delete-job="${j.id}">Löschen</button></div></td></tr>`),'Noch keine Zeitpläne.')}<p class="small muted">Verpasste Termine werden nach einem Panelneustart einmal nachgeholt. Updates per Downloader setzen einen gestoppten Server voraus.</p>`;
  bind('#new-schedule','click',()=>scheduleForm());
  bind('[data-edit-job]','click',(_,n)=>scheduleForm(rows.find(j=>j.id===n.dataset.editJob)));
  bind('[data-run-job]','click',(_,n)=>confirmAction('Zeitplan jetzt ausführen','Die gespeicherte Aktion wird sofort ausgeführt.',async()=>{await api(serverUrl('/schedules/'+n.dataset.runJob+'/run'),'POST');await renderSchedules();}));
  bind('[data-toggle-job]','click',async(_,n)=>{const j=rows.find(j=>j.id===n.dataset.toggleJob);await api(serverUrl('/schedules/'+j.id),'PUT',{...j,enabled:!j.enabled});await renderSchedules();});
  bind('[data-delete-job]','click',(_,n)=>confirmAction('Zeitplan löschen','Dieser Zeitplan wird entfernt.',async()=>{await api(serverUrl('/schedules/'+n.dataset.deleteJob),'DELETE');await renderSchedules();},'Löschen'));
}
function scheduleForm(existing) {
  const j=existing||{name:'Tägliches Backup',cron:'0 4 * * *',timezone:'Europe/Berlin',action:'backup',command:'',enabled:true};
  const actions={backup:'Backup',restart:'Neustart',start:'Starten',stop:'Stoppen',command:'Konsolenbefehl',update:'Downloader-Update (gestoppt)'};
  modal(existing?'Zeitplan bearbeiten':'Zeitplan erstellen',`<label>Name<input name="name" required value="${escape(j.name)}"></label><div class="form-grid"><label>Cron (Minute Stunde Tag Monat Wochentag)<input name="cron" required value="${escape(j.cron)}"></label><label>Zeitzone<input name="timezone" required value="${escape(j.timezone)}"></label></div><label>Aktion<select name="action">${Object.entries(actions).map(([value,label])=>`<option value="${value}" ${j.action===value?'selected':''}>${label}</option>`).join('')}</select></label><label>Konsolenbefehl (nur für diese Aktion)<input name="command" maxlength="2048" value="${escape(j.command)}"></label><label class="checkbox"><input name="enabled" type="checkbox" ${j.enabled?'checked':''}>Aktiviert</label>`,async(v,f)=>{await api(serverUrl('/schedules'+(existing?'/'+existing.id:'')),existing?'PUT':'POST',{...v,enabled:f.elements.enabled.checked});await renderSchedules();});
}
async function renderServerSettings() {
  $('#tab-content').innerHTML=`<div class="split"><div class="section"><h2>Installation</h2><p>${selected.installed?'Hytale-Serverdateien vorhanden.':'Hytale ist noch nicht installiert.'}</p><div class="actions"><button id="install" class="primary">${selected.installed?'Mit Downloader aktualisieren':'Hytale herunterladen'}</button><button id="import">ZIP importieren</button></div><p class="small muted spaced">Der offizielle Downloader benötigt dein Hytale-Konto. Sein Gerätecode erscheint im Installationsprotokoll.</p><button id="install-log">Installationsprotokoll anzeigen</button><pre id="installation-console" class="console spaced" hidden></pre></div><div class="section"><h2>Ressourcen und Start</h2><dl class="definition"><dt>RAM-Limit</dt><dd>${bytes(selected.memory_mb*1024**2)}</dd><dt>CPU-Limit</dt><dd>${selected.cpus} Kerne</dd><dt>UDP-Port</dt><dd>${selected.port}</dd><dt>Autostart</dt><dd>${selected.autostart?'Aktiviert':'Deaktiviert'}</dd><dt>Absturz-Neustart</dt><dd>${selected.autorestart?'Aktiviert':'Deaktiviert'}</dd><dt>Update-Kanal</dt><dd>${selected.patchline}</dd></dl><div class="actions spaced"><button id="edit-server">Bearbeiten</button><button id="clone-server">Klonen</button></div></div></div>${user.role==='admin'?'<div class="section"><h2>Serverzugriff</h2><div id="access-table"></div><button id="assign-access" class="spaced">Zugriff vergeben</button></div><div class="section danger-zone"><h2>Server löschen</h2><p>Ein vollständiges Backup wird aufbewahrt. Serverdaten und Container werden entfernt.</p><button id="delete-server" class="danger">Server löschen</button></div>':''}`;
  bind('#install','click',()=>confirmAction('Hytale herunterladen',selected.installed?'Ein vollständiges Backup wird erstellt. Der Server muss gestoppt sein.':'Starte den offiziellen Downloader. Bestätige anschließend seinen Gerätecode.',async()=>{await startTask({action:'install'},()=>tab==='settings'?renderServerSettings():null);await showInstallLog();},'Download starten'));
  bind('#import','click',()=>uploadModal('',renderServerSettings,true));
  bind('#install-log','click',showInstallLog);bind('#edit-server','click',()=>serverForm(selected));bind('#clone-server','click',()=>serverForm(selected,true));
  bind('#delete-server','click',()=>modal('Server löschen',`<p>Zum Bestätigen <strong>${escape(selected.name)}</strong> eingeben.</p><label>Servername<input name="confirmation" required autocomplete="off"></label>`,async v=>{await api(serverUrl('?confirmation='+enc(v.confirmation)),'DELETE');notify('Server entfernt. Backup aufbewahrt.');await navigate('servers');},'Server löschen'));
  if(user.role==='admin'){
    const permissions=await api(serverUrl('/access'));
    $('#access-table').innerHTML=table(['Benutzer','Recht'],permissions.map(p=>`<tr><td>${escape(p.username)}</td><td>${p.permission==='operator'?'Operator':'Lesen'}</td></tr>`),'Administratoren haben immer Zugriff. Noch keine Mitglieder zugewiesen.');
    bind('#assign-access','click',async()=>{const users=await api('/users');modal('Serverzugriff vergeben',`<label>Benutzer<select name="user_id">${users.map(u=>`<option value="${u.id}">${escape(u.username)}</option>`).join('')}</select></label><label>Recht<select name="permission"><option value="viewer">Lesen</option><option value="operator">Operator: Konsole, Start/Stop, Dateien, Backups</option><option value="none">Zugriff entfernen</option></select></label>`,async v=>{await api(serverUrl('/access'),'PUT',v);await renderServerSettings();});});
  }
}
async function showInstallLog() {
  const sid=selected.id;const node=$('#installation-console');node.hidden=false;
  async function refresh(){if(selected?.id!==sid||tab!=='settings'||!node.isConnected)return;try{node.textContent=(await api(`/servers/${sid}/logs?install=true`)).logs||'Noch keine Downloader-Ausgabe.';node.scrollTop=node.scrollHeight;}catch(e){node.textContent=e.message;}setTimeout(refresh,3000);}
  await refresh();
}
async function renderUsers() {
  const users=await api('/users');
  $('#content').innerHTML=`<div class="titlebar"><h1>Benutzer</h1><button id="new-user" class="primary">Benutzer erstellen</button></div>${table(['Benutzername','Rolle','Status',''],users.map(u=>`<tr><td>${escape(u.username)}</td><td>${u.role==='admin'?'Administrator':'Mitglied'}</td><td>${u.disabled?'Deaktiviert':'Aktiv'}</td><td><button data-user="${u.id}">Bearbeiten</button></td></tr>`))}<p class="hint">Mitglieder erhalten Zugriff pro Server unter Einstellungen. Operatoren dürfen ihren Server steuern und Dateien ändern; Leser können Daten einsehen.</p>`;
  bind('#new-user','click',()=>modal('Benutzer erstellen','<label>Benutzername<input name="username" required minlength="3" maxlength="64" pattern="[a-zA-Z0-9_.-]+"></label><label>Passwort<input name="password" required type="password" minlength="12" autocomplete="new-password"></label><label>Rolle<select name="role"><option value="member">Mitglied</option><option value="admin">Administrator</option></select></label>',async v=>{await api('/users','POST',v);await renderUsers();}));
  bind('[data-user]','click',(_,n)=>{const u=users.find(u=>u.id===n.dataset.user);modal('Benutzer bearbeiten: '+u.username,`<label>Rolle<select name="role"><option value="member" ${u.role==='member'?'selected':''}>Mitglied</option><option value="admin" ${u.role==='admin'?'selected':''}>Administrator</option></select></label><label>Neues Passwort (leer lassen zum Beibehalten)<input name="password" type="password" minlength="12" autocomplete="new-password"></label><label class="checkbox"><input name="disabled" type="checkbox" ${u.disabled?'checked':''}>Benutzer deaktivieren</label>`,async(v,f)=>{await api('/users/'+u.id,'PUT',{...v,disabled:f.elements.disabled.checked});await renderUsers();});});
}
async function renderAudit() {
  const rows=await api('/audit');$('#content').innerHTML=`<div class="titlebar"><h1>Aktivitäten</h1><button id="refresh-audit">Aktualisieren</button></div>${table(['Zeit','Benutzer','Aktion','Server','Details'],rows.map(a=>`<tr><td>${date(a.at)}</td><td>${escape(a.user)}</td><td>${escape(a.action)}</td><td>${escape(a.server_id?.slice(0,8)||'—')}</td><td class="wrap">${escape(a.detail)}</td></tr>`))}`;bind('#refresh-audit','click',renderAudit);
}
async function renderSystem() {
  const s=await api('/system');const provider=await api('/providers/curseforge');$('#content').innerHTML=`<div class="titlebar"><h1>System</h1><button id="refresh-system">Aktualisieren</button></div><div class="section"><h2>Docker und Speicher</h2><dl class="definition"><dt>Docker</dt><dd>${s.docker.connected?'Verbunden · '+escape(s.docker.version):'Nicht erreichbar'}</dd><dt>Host-RAM</dt><dd>${bytes(s.docker.memory_bytes||0)}</dd><dt>Host-CPUs</dt><dd>${s.docker.cpus||'—'}</dd><dt>Freier Speicher</dt><dd>${bytes(s.disk.free)} von ${bytes(s.disk.total)}</dd><dt>Runner-Image</dt><dd>${escape(s.runner_image)}</dd><dt>Host-Datenpfad</dt><dd>${escape(s.host_data)}</dd><dt>Upload-Limit</dt><dd>${bytes(s.upload_limit_bytes)}</dd><dt>Öffentliche URL</dt><dd>${escape(s.public_url||'LAN-Betrieb')}</dd></dl></div><div class="section"><h2>Backup-Aufbewahrung</h2><form id="system-settings"><label>Anzahl Backups pro Server<input name="backup_retention" type="number" min="2" max="365" value="${s.backup_retention}" required></label><label>Globales Backup-Limit (GiB)<input name="backup_global_quota_gb" type="number" min="1" max="100000" required value="${s.backup_global_quota_gb}"></label><button type="submit" class="primary">Speichern</button></form></div><div class="section"><h2>CurseForge</h2><p class="muted">${provider.configured?'API-Schlüssel hinterlegt':'Noch kein API-Schlüssel hinterlegt'}</p><form id="provider-key"><label>Neuer API-Schlüssel<input name="key" type="password" minlength="10" required autocomplete="off"></label><div class="actions"><button type="submit" class="primary">Prüfen und speichern</button><button type="button" id="delete-provider-key" class="danger">Schlüssel entfernen</button></div></form><p class="small muted">Der Schlüssel wird vor dem Speichern geprüft und nie zurückgegeben. Die Suche verwendet ausschließlich Hytale-Projekte.</p></div><div class="section"><h2>Unraid und Netzwerk</h2><ul class="checklist"><li>Panel: HTTP-Port 8095 im Unraid-Template</li><li>Spielserver: individuelle UDP-Ports, standardmäßig ab 5520</li><li>Internetfreigabe: UDP-Port des Spielservers im Router weiterleiten</li><li>HTTPS: vorhandenen Reverse Proxy mit diesem Panel verbinden</li><li>Bei Reverse Proxy <code>HYPANEL_PUBLIC_URL</code> setzen und sichere Cookies aktivieren</li></ul><a href="https://github.com/gottschalkfelix4-source/hypanel" target="_blank" rel="noopener">Repository und Installationsanleitung</a></div>`;
  await renderPanelBackups();
  bind('#refresh-system','click',renderSystem);bind('#system-settings','submit',async(e,n)=>{await api('/system','PUT',{backup_retention:Number(formValues(n).backup_retention),backup_global_quota_gb:Number(formValues(n).backup_global_quota_gb)});notify('Aufbewahrung gespeichert');});bind('#provider-key','submit',async(_,n)=>{await api('/providers/curseforge','PUT',formValues(n));notify('Schlüssel geprüft und gespeichert');await renderSystem();});bind('#delete-provider-key','click',()=>confirmAction('API-Schlüssel entfernen','Der gespeicherte CurseForge-Schlüssel wird entfernt.',async()=>{await api('/providers/curseforge','DELETE');await renderSystem();},'Entfernen'));
}
async function renderAccount() {
  const tokens=await api('/me/tokens');
  $('#content').innerHTML=`<h1>Mein Konto</h1><div class="section"><h2>Passwort ändern</h2><form id="password-form"><label>Aktuelles Passwort<input name="current" type="password" required autocomplete="current-password"></label><label>Neues Passwort<input name="password" type="password" required minlength="12" autocomplete="new-password"></label><button type="submit" class="primary">Passwort ändern</button></form></div><div class="section"><div class="titlebar"><h2>API-Schlüssel</h2><button id="new-token">Schlüssel erstellen</button></div>${table(['Name','Gültig bis',''],tokens.map(t=>`<tr><td>${escape(t.name)}</td><td>${date(t.expires)}</td><td><button data-revoke-token="${t.id}" class="danger">Widerrufen</button></td></tr>`),'Keine API-Schlüssel.')}<p class="small muted">REST-Aufrufe mit Authorization: Bearer. Schlüssel erhalten deine Benutzerrechte und werden beim Passwortwechsel widerrufen.</p></div>`;
  bind('#password-form','submit',async(e,n)=>{await api('/me/password','POST',formValues(n));user=null;disconnect();notify('Passwort geändert. Bitte erneut anmelden.');await auth();});
  bind('#new-token','click',()=>modal('API-Schlüssel erstellen','<label>Name<input name="name" required maxlength="80"></label><label>Gültigkeit (Tage)<input name="days" type="number" min="1" max="365" value="90" required></label>',async v=>{const result=await api('/me/tokens','POST',{name:v.name,days:Number(v.days)});await renderAccount();$('#dialog-content').innerHTML=`<h2>API-Schlüssel erstellt</h2><p>Jetzt sicher kopieren. Der Schlüssel wird nur einmal angezeigt.</p><label>Schlüssel<input id="token-value" readonly value="${escape(result.token)}"></label><div class="form-actions"><button id="token-close">Schließen</button></div>`;$('#token-close').onclick=()=>$('#dialog').close();$('#token-value').select();return false;}));
  bind('[data-revoke-token]','click',(_,n)=>confirmAction('API-Schlüssel widerrufen','Dieser Schlüssel verliert sofort den Zugriff.',async()=>{await api('/me/tokens/'+n.dataset.revokeToken,'DELETE');await renderAccount();},'Widerrufen'));
}

const rightLabels={'console.read':'Konsole lesen','console.write':'Befehle senden','server.power':'Starten / Stoppen','files.read':'Dateien lesen','files.write':'Dateien bearbeiten','config.write':'Konfiguration ändern','mods.write':'Mods verwalten','backups.create':'Backups erstellen','backups.restore':'Backups einspielen','automation.write':'Automation verwalten','access.write':'Mitglieder verwalten','settings.write':'Servereinstellungen ändern','players.write':'Spieler verwalten','auth.manage':'Hytale-Account verwalten'};
const jobLabels={queued:'Wartet',running:'Läuft',done:'Abgeschlossen',failed:'Fehlgeschlagen',cancelled:'Abgebrochen'};
function can(right){return user.role==='admin'||(selected?.rights||[]).includes(right);}
function stat(label,value,caption='',percent=null,cls=''){return `<div class="stat-card"><span class="caption">${escape(label)}</span><div class="value">${escape(value)}</div><span class="caption">${escape(caption)}</span>${percent===null?'':bar(percent,cls)}</div>`;}
async function renderOverview(){
  const sid=selected.id;
  $('#tab-content').innerHTML=`<div id="overview-stats" class="stat-grid"></div><div class="section"><div class="titlebar"><h2>Auslastung</h2><label>Messzeitraum<select id="history-hours"><option value="1">Letzte Stunde</option><option value="6">Letzte 6 Stunden</option><option value="24">Letzte 24 Stunden</option><option value="72">Letzte 3 Tage</option></select></label></div><div class="chart-grid">${[['cpu','CPU · % des CPU-Limits'],['memory','Arbeitsspeicher · MiB'],['tps','TPS · langsamste Welt'],['players','Aktive Spieler']].map(([key,label])=>`<div class="chart-card"><header>${label}</header><canvas id="chart-${key}" aria-label="${label}" role="img"></canvas></div>`).join('')}</div><p class="small muted spaced">Messwerte bleiben 72 Stunden gespeichert. TPS werden aus tatsächlich fortgeschrittenen Welt-Ticks gemessen. Fehlende Werte bleiben als Lücken sichtbar.</p></div><div class="split"><div class="section"><h2>Aktive Spieler</h2><div id="overview-players"></div></div><div class="section"><h2>Letzte Aufträge</h2><div id="overview-jobs"></div></div></div><div class="section"><h2>Letzte Aktivitäten</h2><div id="overview-activity"></div></div><div class="section"><h2>Startdiagnose</h2><div id="diagnosis"></div><button id="refresh-diagnosis">Erneut prüfen</button></div>`;
  const options={cpu:{color:'#4aedd9',unit:'%',max:100},memory:{color:'#7fb238',unit:'MiB',max:selected.memory_mb},tps:{color:'#ffaa00',unit:'TPS'},players:{color:'#8ea0ff',unit:'Spieler'}};
  charts=Object.entries(options).map(([key,opts])=>new Chart($('#chart-'+key),{key,...opts}));
  async function refresh(){
    if(selected?.id!==sid||tab!=='overview')return;
    const [data,history]=await Promise.all([api(`/servers/${sid}/overview`),api(`/servers/${sid}/metrics/history?hours=`+$('#history-hours').value)]);
    if(selected?.id!==sid||tab!=='overview')return;
    const t=data.telemetry;const cpu=selected.cpu_percent/selected.cpus,ram=selected.memory_bytes;
    $('#overview-stats').innerHTML=stat('CPU',cpu.toFixed(1)+' %',selected.cpus+' zugewiesene Kerne',cpu,'cpu')+stat('Arbeitsspeicher',bytes(ram),'von '+bytes(selected.memory_mb*1024**2),ram/(selected.memory_mb*1024**2)*100)+stat('TPS',t.tps===null?'—':t.tps.toFixed(1),t.available?'Welt-Ticks / Sekunde':'Telemetrie beim nächsten Serverstart')+stat('Spieler',t.available?t.players.length:'—',t.available?'Live aus Hytale':'Nicht verfügbar');
    charts.forEach(c=>c.set(history));
    $('#overview-players').innerHTML=t.available?table(['Name','UUID'],t.players.map(p=>`<tr><td>${escape(p.name)}</td><td class="small">${escape(p.uuid)}</td></tr>`),'Keine Spieler online.'):'<p class="muted">Noch keine frischen Hytale-Messwerte.</p>';
    $('#overview-jobs').innerHTML=jobTable(data.jobs,false);
    $('#overview-activity').innerHTML=table(['Zeit','Aktion','Benutzer'],data.activity.map(a=>`<tr><td>${date(a.at)}</td><td>${escape(a.action)}</td><td>${escape(a.user)}</td></tr>`));
  }
  async function diagnosis(){const d=await api(`/servers/${sid}/diagnosis`);if($('#diagnosis'))$('#diagnosis').innerHTML=d.findings.length?d.findings.map(f=>`<p><strong>${escape(f.title)}</strong><br><span class="muted">${escape(f.hint)}</span></p>`).join(''):'<p class="muted">Keine bekannten Fehlermuster in den letzten 300 Protokollzeilen.</p>';}
  bind('#history-hours','change',refresh);bind('#refresh-diagnosis','click',diagnosis);await Promise.all([refresh(),diagnosis()]);overviewTimer=setInterval(()=>refresh().catch(()=>{}),10000);
}
function jobTable(rows,controls=true){return table(['Auftrag','Status','Fortschritt',...(controls?['']:[])],rows.map(j=>`<tr><td>${escape(j.kind)}<div class="small muted">${date(j.created)}${j.server_name?' · '+escape(j.server_name):''}</div></td><td><span class="job-state ${escape(j.state)}">${escape(jobLabels[j.state]||j.state)}</span></td><td class="wrap">${escape(j.message)}<progress max="100" value="${j.progress}"></progress></td>${controls?`<td>${['queued','running'].includes(j.state)&&j.cancellable?`<button data-cancel="${j.id}" class="danger" ${j.cancel?'disabled':''}>${j.cancel?'Abbruch angefordert':'Abbrechen'}</button>`:''}</td>`:''}</tr>`),'Noch keine Aufträge.');}
async function renderJobs(){clearInterval(jobTimer);async function refresh(){if(section!=='jobs')return;const rows=await api('/jobs');$('#content').innerHTML=`<div class="titlebar"><div><h1>Aufträge</h1><p class="muted">Installationen, Backups, Wiederherstellungen und Mod-Updates.</p></div></div>${jobTable(rows)}`;bind('[data-cancel]','click',async(_,n)=>{await api('/jobs/'+n.dataset.cancel+'/cancel','POST');await refresh();});}await refresh();jobTimer=setInterval(()=>refresh().catch(()=>{}),3000);}
async function startTask(body,refresh){
  const sid=selected.id;const {job_id}=await api(serverUrl('/tasks'),'POST',body);
  const dialog=$('#dialog');if(dialog.open)dialog.close();
  $('#dialog-content').innerHTML=`<h2>Auftrag läuft</h2><p id="job-message">Vorbereitung …</p><progress id="job-progress" data-job="${job_id}" max="100" value="0"></progress><div class="form-actions"><button id="job-hide">Im Hintergrund fortsetzen</button><button id="job-cancel" class="danger">Abbrechen</button></div>`;dialog.showModal();$('#job-hide').onclick=()=>dialog.close();
  $('#job-cancel').onclick=async()=>{try{await api('/jobs/'+job_id+'/cancel','POST');$('#job-cancel').disabled=true;}catch(e){notify(e.message,true);}};
  async function poll(){try{const j=await api('/jobs/'+job_id);if($('#job-progress')?.dataset.job===job_id){$('#job-progress').value=j.progress;$('#job-message').textContent=j.message;$('#job-cancel').disabled=!j.cancellable||Boolean(j.cancel);}if(['done','failed','cancelled'].includes(j.state)){if($('#job-progress')?.dataset.job===job_id)dialog.close();notify(j.state==='done'?'Auftrag abgeschlossen':j.message,j.state==='failed');if(selected?.id===sid)await refresh?.();return;}setTimeout(poll,1500);}catch(e){notify(e.message,true);}}
  await poll();
}
async function renderAccess(){
  if(user.role!=='admin'&&!can('access.write')){$('#tab-content').innerHTML='<div class="empty">Keine Berechtigung zur Verwaltung der Mitglieder.</div>';return;}
  const [members,users]=await Promise.all([api(serverUrl('/access')),api(serverUrl('/access/candidates'))]);
  $('#tab-content').innerHTML=`<div class="titlebar"><div><h2>Serverzugriff</h2><p class="muted">14 einzeln vergebbare Rechte. Administratoren haben immer Zugriff.</p></div><button id="access-new" class="primary">Mitglied hinzufügen</button></div>${table(['Mitglied','Berechtigungen',''],members.map(m=>`<tr><td>${escape(m.username)}</td><td class="wrap">${m.rights.map(r=>escape(rightLabels[r])).join(', ')}</td><td><button data-access="${m.user_id}">Bearbeiten</button></td></tr>`),'Noch keine Mitglieder zugewiesen.')}`;
  const edit=(existing)=>modal('Serverberechtigungen',`<label>Mitglied<select name="user_id" ${existing?'disabled':''}>${users.filter(u=>u.role!=='admin').map(u=>`<option value="${u.id}" ${u.id===existing?.user_id?'selected':''}>${escape(u.username)}</option>`).join('')}</select></label><div class="form-grid">${Object.entries(rightLabels).map(([r,l])=>`<label class="checkbox"><input type="checkbox" name="rights" value="${r}" ${existing?.rights?.includes(r)?'checked':''}>${l}</label>`).join('')}</div>${existing?'<label class="checkbox"><input name="remove" type="checkbox">Mitglied entfernen</label>':''}`,async(v,f)=>{await api(serverUrl('/access'),'PUT',{user_id:existing?.user_id||v.user_id,permission:f.elements.remove?.checked?'none':'custom',rights:[...f.querySelectorAll('[name=rights]:checked')].map(n=>n.value)});await renderAccess();});
  bind('#access-new','click',()=>edit());bind('[data-access]','click',(_,n)=>edit(members.find(m=>m.user_id===n.dataset.access)));
}
async function renderCatalog(){
  $('#tab-content').innerHTML=`<h2>Hytale-Mod-Browser</h2><p class="muted">Plugins und Asset-Packs von CurseForge. Versionen gezielt wählen und mit Sicherheitsbackup installieren.</p><form id="catalog-search" class="command-form"><input name="q" placeholder="Name einer Mod" aria-label="Mods suchen"><button type="submit" class="primary">Suchen</button></form><form id="catalog-id" class="command-form"><input name="pid" type="number" min="1" required placeholder="CurseForge-Projekt-ID" aria-label="CurseForge-Projekt-ID"><button type="submit">Projekt öffnen</button></form><div id="catalog-results" class="spaced"></div><div class="section"><div class="titlebar"><h3>Verwaltete Mods</h3><button id="check-mod-updates">Updates prüfen</button></div><div id="managed-mods"></div></div>`;
  async function managed(){const rows=await api(serverUrl('/mod-updates'));$('#managed-mods').innerHTML=table(['Mod','Installierte Datei','Letzte Prüfung',''],rows.map(m=>`<tr><td>${escape(m.name)}</td><td>${escape(m.filename)}</td><td>${date(m.checked)}</td><td>${m.latest_id&&m.latest_id!==m.file_id?`<button data-mod-update="${m.project_id}" data-file="${m.latest_id}" class="primary">Update verfügbar</button>`:`<button data-project="${m.project_id}">Version wählen</button>`}</td></tr>`),'Noch keine Mods über den Browser installiert.');bind('[data-project]','click',(_,n)=>chooseVersion(Number(n.dataset.project)));bind('[data-mod-update]','click',(_,n)=>confirmAction('Mod aktualisieren','Ein Backup wird erstellt; Server muss gestoppt sein.',()=>startTask({action:'mod',project_id:Number(n.dataset.modUpdate),file_id:Number(n.dataset.file)},renderCatalog),'Aktualisieren'));applyRights();}
  async function search(q){try{const r=await api(serverUrl('/catalog?q='+enc(q)));$('#catalog-results').innerHTML=`<div class="catalog-grid">${r.items.map(p=>`<article class="catalog-item"><h3>${escape(p.name)}</h3><p>${escape(p.summary)}</p><span class="small muted">${Number(p.downloads).toLocaleString('de-DE')} Downloads</span><button data-project="${p.id}">Versionen anzeigen</button></article>`).join('')}</div>${r.items.length?'':'<div class="empty">Keine Projekte gefunden.</div>'}`;bind('[data-project]','click',(_,n)=>chooseVersion(Number(n.dataset.project)));}catch(e){$('#catalog-results').innerHTML=`<div class="hint">${escape(e.message)}</div>`;}}
  bind('#catalog-search','submit',(_,n)=>search(formValues(n).q));bind('#catalog-id','submit',(_,n)=>chooseVersion(Number(formValues(n).pid)));bind('#check-mod-updates','click',async()=>{await api(serverUrl('/mod-updates/check'),'POST');await managed();});await managed();await search('');
}
async function chooseVersion(pid){const versions=await api(serverUrl('/catalog/'+pid+'/versions'));modal('Mod-Version wählen',`<label>Version<select name="fid">${versions.map(v=>`<option value="${v.id}">${escape(v.name)} · ${escape(v.versions.join(', '))}</option>`).join('')}</select></label><p class="hint">Server zuerst stoppen. Der Download wird geprüft und ein Backup erstellt. Minecraft-Pakete und fehlende Pflichtabhängigkeiten werden abgelehnt.</p>`,async v=>{if(!can('mods.write'))throw Error('Keine Berechtigung zur Mod-Verwaltung');await startTask({action:'mod',project_id:pid,file_id:Number(v.fid)},renderCatalog);return false;},'Installieren');}

async function renderPanelBackups(){
  const rows=await api('/panel-backups');$('#content').insertAdjacentHTML('beforeend',`<div class="section"><div class="titlebar"><h2>Panel-Sicherungen</h2><button id="panel-backup-create">Panel sichern</button></div><p class="hint">Enthält Datenbank, Einstellungen, Identitäten und SFTP-Hostschlüssel. Archive enthalten Zugangsdaten und gehören in eine geschützte Ablage. Spielwelten werden separat gesichert.</p>${table(['Sicherung','Größe',''],rows.map(b=>`<tr><td>${escape(b.name)}</td><td>${bytes(b.size)}</td><td><a href="/api/panel-backups/${enc(b.name)}">Download</a> <button data-panel-backup-delete="${escape(b.name)}" class="danger">Löschen</button></td></tr>`),'Noch keine Panel-Sicherungen.')}</div>`);bind('#panel-backup-create','click',async()=>{await api('/panel-backups','POST');await renderSystem();});bind('[data-panel-backup-delete]','click',(_,n)=>confirmAction('Panel-Sicherung löschen',n.dataset.panelBackupDelete,async()=>{await api('/panel-backups/'+enc(n.dataset.panelBackupDelete),'DELETE');await renderSystem();},'Löschen'));
}
try {panelVersion=(await api('/health')).version;user=await api('/me');await navigate('servers');}catch{await auth();}

function renderHytaleAuth(){
  if(!can('auth.manage')){$('#tab-content').textContent='Keine Berechtigung zum Verwalten des Hytale-Accounts.';return;}
  authCleanup=mountHytaleAuth({element:$('#tab-content'),sid:selected.id,name:selected.name,api,escape,can});
}
