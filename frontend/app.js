const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc = encodeURIComponent;
const bytes = value => value >= 1024**3 ? `${(value/1024**3).toFixed(2)} GiB` : value >= 1024**2 ? `${(value/1024**2).toFixed(1)} MiB` : `${(value/1024).toFixed(1)} KiB`;
const date = value => value ? new Date(value*1000).toLocaleString('de-DE') : '—';
const labels = {running:'Läuft',stopped:'Gestoppt',exited:'Beendet',created:'Erstellt',restarting:'Neustart',unavailable:'Docker nicht erreichbar',paused:'Pausiert'};
let user, servers = [], selected, section = 'servers', tab = 'console', filePath = 'Server', source, metricsTimer, noticeTimer;

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
    if (button?.tagName === 'BUTTON') button.disabled = true;
    try { await fn(e, node); } catch (error) { notify(error.message, true); }
    finally { if (button?.tagName === 'BUTTON') button.disabled = false; applyRights(); }
  }));
}
function formValues(form) { return Object.fromEntries(new FormData(form)); }
function disconnect() { source?.close(); source = null; clearInterval(metricsTimer); }
function modal(title, content, submit, label = 'Speichern') {
  const dialog = $('#dialog');
  $('#dialog-content').innerHTML = `<h2>${escape(title)}</h2><form id="modal-form">${content}<div class="error" id="modal-error" hidden></div><div class="form-actions"><button type="button" id="cancel">Abbrechen</button><button type="submit" class="primary">${escape(label)}</button></div></form>`;
  $('#cancel').onclick = () => dialog.close();
  $('#modal-form').onsubmit = async e => {
    e.preventDefault(); const b = $('button[type=submit]', e.target); b.disabled = true;
    try { const close = await submit(formValues(e.target), e.target); if (close !== false) dialog.close(); }
    catch (error) { $('#modal-error').textContent = error.message; $('#modal-error').hidden = false; }
    finally { b.disabled = false; }
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
  $('#app').innerHTML = `<main class="auth-page"><div class="auth-box"><div class="brand"><img src="/icon.svg" alt="">HyPanel</div><h1>${setup.required ? 'Panel einrichten' : 'Anmelden'}</h1><p class="muted">Deine Hytale-Server auf Docker und Unraid.</p><form id="auth-form">${setup.required ? '<label>Einrichtungsschlüssel<input name="setup_key" type="password" required autocomplete="off"></label><p class="hint">Der einmalige Schlüssel liegt im Container unter <code>/data/setup.key</code>. Er schützt die erste Einrichtung.</p>' : ''}<label>Benutzername<input name="username" required minlength="3" maxlength="64" autocomplete="username"></label><label>Passwort<input name="password" type="password" required ${setup.required ? 'minlength="12"' : ''} autocomplete="${setup.required ? 'new-password' : 'current-password'}"></label><div id="auth-error" class="error" hidden></div><button type="submit" class="primary">${setup.required ? 'Administratorkonto erstellen' : 'Anmelden'}</button></form></div></main>`;
  $('#auth-form').onsubmit = async e => {
    e.preventDefault(); const b = $('button', e.target); b.disabled = true;
    try {
      const values = formValues(e.target);
      if (setup.required) await api('/setup', 'POST', values);
      user = await api('/login', 'POST', {username:values.username,password:values.password});
      await navigate('servers');
    } catch (error) { $('#auth-error').textContent = error.message; $('#auth-error').hidden = false; }
    finally { b.disabled = false; }
  };
}
function shell() {
  $('#app').innerHTML = `<div class="shell"><aside class="sidebar"><div class="brand"><img src="/icon.svg" alt="">HyPanel</div><nav aria-label="Hauptnavigation"><button data-nav="servers" class="${section==='servers'?'active':''}">Server</button>${user.role==='admin'?`<button data-nav="users" class="${section==='users'?'active':''}">Benutzer</button><button data-nav="audit" class="${section==='audit'?'active':''}">Aktivitäten</button><button data-nav="system" class="${section==='system'?'active':''}">System</button>`:''}<button data-nav="account" class="${section==='account'?'active':''}">Mein Konto</button></nav><div class="account"><div>${escape(user.username)}<br><span class="muted">${user.role==='admin'?'Administrator':'Mitglied'}</span></div><button id="logout">Abmelden</button></div></aside><div class="workspace"><header class="topbar">Hytale Server Hosting<span>HyPanel 0.1.0</span></header><main class="content" id="content"></main></div></div>`;
  bind('[data-nav]', 'click', (_, n)=>navigate(n.dataset.nav));
  bind('#logout','click',async()=>{await api('/logout','POST'); user=null; disconnect(); await auth();});
}
async function navigate(next) {
  disconnect(); section = next; selected = null; shell();
  const render = {servers:renderServers,users:renderUsers,audit:renderAudit,system:renderSystem,account:renderAccount}[next];
  await render();
}
async function renderServers() {
  servers = await api('/servers');
  $('#content').innerHTML = `<div class="titlebar"><div><h1>Server</h1><p class="muted">${servers.length} Server eingerichtet</p></div><div class="actions"><button id="refresh">Aktualisieren</button>${user.role==='admin'?'<button id="create-server" class="primary">Server erstellen</button>':''}</div></div>${table(['Name','Status','Verbindung (UDP)','RAM-Limit','CPU-Limit',''],servers.map(s=>`<tr><td><button class="name-button" data-open="${s.id}">${escape(s.name)}</button><div class="small muted">${s.installed?'Hytale installiert':'Installation ausstehend'}</div></td><td>${status(s.status)}${s.installation==='running'?'<div class="small">Download läuft</div>':''}</td><td>${escape(location.hostname)}:${s.port}</td><td>${bytes(s.memory_mb*1024**2)}</td><td>${s.cpus} Kerne</td><td><button data-open="${s.id}">Öffnen</button></td></tr>`),'Noch keine Server. Erstelle einen Server und installiere Hytale über den offiziellen Downloader.')}`;
  bind('#refresh','click',renderServers);
  bind('#create-server','click',()=>serverForm());
  bind('[data-open]','click',(_, n)=>openServer(n.dataset.open));
}
function serverForm(existing, clone = false) {
  const s = existing || {name:'',port:5520,memory_mb:4096,cpus:2,patchline:'release'};
  const title = clone?'Server klonen':existing?'Server bearbeiten':'Server erstellen';
  modal(title, `<label>Name<input name="name" required maxlength="80" value="${escape(clone?s.name+' Kopie':s.name)}"></label><div class="form-grid"><label>UDP-Port<input name="port" type="number" min="1024" max="65535" required value="${clone?s.port+1:s.port}"></label><label>RAM-Limit (MiB)<input name="memory_mb" type="number" min="4096" max="262144" step="256" value="${s.memory_mb}" required></label><label>CPU-Limit (Kerne)<input name="cpus" type="number" min="0.5" max="128" step="0.5" value="${s.cpus}" required></label><label>Update-Kanal<select name="patchline"><option value="release">Release</option><option value="pre-release" ${s.patchline==='pre-release'?'selected':''}>Pre-Release</option></select></label></div><label class="checkbox"><input type="checkbox" name="autostart" ${s.autostart?'checked':''}>Beim Panelstart starten</label><label class="checkbox"><input type="checkbox" name="autorestart" ${s.autorestart?'checked':''}>Nach Absturz neu starten (maximal 3 Versuche)</label><p class="hint">1 GiB des RAM-Limits bleibt für JVM und native Bibliotheken reserviert. Jeder Server läuft in einem eigenen Container.</p>`, async(v,f)=>{
    const body = {...v,port:Number(v.port),memory_mb:Number(v.memory_mb),cpus:Number(v.cpus),autostart:f.elements.autostart.checked,autorestart:f.elements.autorestart.checked};
    if (clone) await api(`/servers/${existing.id}/clone`,'POST',body);
    else if (existing) await api(`/servers/${existing.id}`,'PUT',body);
    else await api('/servers','POST',body);
    notify('Server gespeichert'); await navigate('servers');
  },clone?'Klonen':existing?'Speichern':'Erstellen');
}
async function openServer(id) {
  disconnect(); selected = await api(`/servers/${id}`); tab='console'; filePath='Server'; await renderServer();
  metricsTimer = setInterval(async()=>{if(!selected)return; try {selected=await api(`/servers/${id}`); updateMetrics();}catch{}},5000);
}
function updateMetrics() {
  if (!$('#server-meta') || !selected) return;
  $('#server-meta').innerHTML = `${status(selected.status)}<span>UDP <strong>${escape(location.hostname)}:${selected.port}</strong></span><span>RAM <strong>${bytes(selected.memory_bytes)} / ${bytes(selected.memory_mb*1024**2)}</strong></span><span>CPU <strong>${selected.cpu_percent}% / ${selected.cpus} Kerne</strong></span>${selected.exit_code?`<span>Exit-Code ${selected.exit_code}</span>`:''}`;
}
async function renderServer() {
  source?.close(); source = null;
  const tabs = {console:'Konsole',files:'Dateien',config:'Konfiguration',mods:'Mods',worlds:'Welten',players:'Spieler',backups:'Backups',schedules:'Zeitpläne',settings:'Einstellungen'};
  $('#content').innerHTML = `<button id="back" class="name-button">← Alle Server</button><div class="titlebar spaced"><div><h1>${escape(selected.name)}</h1><div class="server-meta" id="server-meta"></div></div><div class="actions"><button data-power="start" class="primary">Starten</button><button data-power="restart">Neustart</button><button data-power="stop">Stoppen</button><button data-power="kill" class="danger">Erzwingen</button></div></div><nav class="tabs" aria-label="Serverbereiche">${Object.entries(tabs).map(([key,label])=>`<button data-tab="${key}" class="${tab===key?'active':''}">${label}</button>`).join('')}</nav><div id="tab-content"></div>`;
  updateMetrics();
  bind('#back','click',()=>navigate('servers'));
  bind('[data-power]','click',async(_,n)=>{
    const action=async()=>{selected=await api(serverUrl(`/power/${n.dataset.power}`),'POST'); updateMetrics(); notify('Serveraktion ausgeführt');};
    if(n.dataset.power==='kill') confirmAction('Server sofort beenden','Nicht gespeicherte Daten können verloren gehen.',action,'Sofort beenden'); else await action();
  });
  bind('[data-tab]','click',async(_,n)=>{tab=n.dataset.tab; await renderServer();});
  await ({console:renderConsole,files:renderFiles,config:renderConfig,mods:renderMods,worlds:renderWorlds,players:renderPlayers,backups:renderBackups,schedules:renderSchedules,settings:renderServerSettings}[tab])();
  applyRights();
}
function applyRights() {
  if(!selected||!user)return;
  const manage=user.role==='admin';
  const write=manage||selected.permission==='operator';
  const lock=(selector)=>$$(selector).forEach(n=>{n.disabled=true;n.title='Dein Benutzerkonto hat dafür keine Berechtigung.';});
  if(!write){lock('[data-power],[data-command],#command-form button,#editor button[type=submit],#player-action button,#mkdir,#new-file,#upload,[data-rename],[data-delete],#upload-mod,[data-toggle],[data-remove],[data-backup]');}
  if(!manage){lock('#install,#import,#install-log,#edit-server,#clone-server,#new-schedule,[data-edit-job],[data-run-job],[data-toggle-job],[data-delete-job],[data-restore],[data-delete-backup]');}
}
async function renderConsole() {
  $('#tab-content').innerHTML = `${!selected.installed?'<div class="hint">Hytale ist noch nicht installiert. Unter Einstellungen kannst du den offiziellen Downloader starten oder Serverdateien importieren.</div>':''}<div class="toolbar"><div class="actions"><button data-command="auth login device">Hytale anmelden</button><button data-command="auth persistence Encrypted">Anmeldung speichern</button><button data-command="auth status">Auth-Status</button><button data-command="help">Hilfe</button></div><a href="/api${serverUrl('/logs?download=true')}">Protokoll herunterladen</a></div><pre class="console" id="console" aria-label="Serverkonsole"></pre><form class="command-form" id="command-form"><input name="command" required maxlength="2048" placeholder="Konsolenbefehl, z. B. help" aria-label="Konsolenbefehl" autocomplete="off"><button type="submit">Senden</button></form><p class="muted small">Die Konsole überträgt Serverausgaben live. Geräteanmeldung im offiziellen Hytale-Browserfenster bestätigen.</p>`;
  const show = logs => {const node=$('#console'); if(!node)return; const bottom=node.scrollHeight-node.scrollTop-node.clientHeight<60; node.textContent=logs||'Noch keine Serverausgabe. Starte den Server.'; if(bottom)node.scrollTop=node.scrollHeight;};
  show((await api(serverUrl('/logs'))).logs);
  source = new EventSource('/api'+serverUrl('/events'));
  source.onmessage=e=>{try{show(JSON.parse(e.data).logs);}catch{}};
  source.addEventListener('closed',()=>source?.close());
  bind('#command-form','submit',async(e,n)=>{await api(serverUrl('/command'),'POST',formValues(n));n.reset();});
  bind('[data-command]','click',(_,n)=>sendCommand(n.dataset.command));
}
async function sendCommand(command) { await api(serverUrl('/command'),'POST',{command}); notify('Befehl gesendet. Ergebnis in der Konsole.'); }
async function editText(path, title=path) {
  let content=''; try{content=(await api(serverUrl('/files/content?path='+enc(path)))).content;}catch(e){if(!e.message.includes('nicht gefunden'))throw e;}
  $('#tab-content').innerHTML = `<h2>${escape(title)}</h2><p class="hint">Der Server muss zum Speichern gestoppt sein. JSON wird vor dem Schreiben geprüft.</p><form id="editor"><label>Dateiinhalt<textarea name="content" spellcheck="false">${escape(content||'{}')}</textarea></label><div class="editor-actions"><button type="submit" class="primary">Speichern</button><button type="button" id="editor-back">Zurück</button><a href="/api${serverUrl('/files/download?path='+enc(path))}">Herunterladen</a></div></form>`;
  bind('#editor','submit',async(e,n)=>{await api(serverUrl('/files/content'),'PUT',{path,...formValues(n)});notify('Datei gespeichert');});
  bind('#editor-back','click',renderServer);
}
async function renderFiles() {
  const rows=await api(serverUrl('/files?path='+enc(filePath)));
  const parts=filePath.split('/').filter(Boolean);
  $('#tab-content').innerHTML=`<div class="toolbar"><div class="breadcrumbs"><button data-directory="">Serverwurzel</button>${parts.map((p,i)=>`<span>/</span><button data-directory="${escape(parts.slice(0,i+1).join('/'))}">${escape(p)}</button>`).join('')}</div><div class="actions"><button id="mkdir">Ordner erstellen</button><button id="new-file">Datei erstellen</button><button id="upload">Hochladen</button></div></div>${table(['Name','Größe','Geändert',''],rows.map(f=>`<tr><td><button class="name-button" ${f.directory?'data-directory':'data-edit'}="${escape(f.path)}">${f.directory?'▸ ':''}${escape(f.name)}</button></td><td>${f.directory?'—':bytes(f.size)}</td><td>${date(f.modified)}</td><td><div class="actions">${!f.directory?`<a href="/api${serverUrl('/files/download?path='+enc(f.path))}">Download</a>`:''}<button data-rename="${escape(f.path)}">Umbenennen</button><button data-delete="${escape(f.path)}" class="danger">Löschen</button></div></td></tr>`),'Dieses Verzeichnis ist leer.')}<p class="small muted">Änderungen und Uploads sind bei gestopptem Server möglich. Anmeldedaten und symbolische Links sind geschützt.</p>`;
  bind('[data-directory]','click',async(_,n)=>{filePath=n.dataset.directory;await renderFiles();});
  bind('[data-edit]','click',(_,n)=>editText(n.dataset.edit));
  bind('#mkdir','click',()=>modal('Ordner erstellen','<label>Name<input name="name" required></label>',async v=>{await api(serverUrl('/files/mkdir'),'POST',{path:[filePath,v.name].filter(Boolean).join('/')});await renderFiles();}));
  bind('#new-file','click',()=>modal('Datei erstellen','<label>Dateiname<input name="name" required placeholder="config.json"></label>',async v=>{await editText([filePath,v.name].filter(Boolean).join('/'));}));
  bind('#upload','click',()=>uploadModal(filePath,renderFiles));
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
  bind('[data-config]','click',(_,n)=>editText(n.dataset.config));bind('[data-command]','click',(_,n)=>sendCommand(n.dataset.command));
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
  $('#tab-content').innerHTML=`<h2>Spielerverwaltung</h2><p class="muted">Befehle werden an den echten Server gesendet. Die Antworten erscheinen in der Konsole.</p><div class="actions"><button data-command="list">Spielerliste abfragen</button><button data-command="whitelist list">Whitelist anzeigen</button><button data-player-file="Server/whitelist.json">Whitelist-Datei</button><button data-player-file="Server/bans.json">Sperrliste</button><button data-player-file="Server/permissions.json">Berechtigungen</button></div><div class="section"><form id="player-action"><div class="form-grid"><label>Spielername oder UUID<input name="player" required pattern="[A-Za-z0-9_-]+" maxlength="64"></label><label>Aktion<select name="action"><option value="kick">Kicken</option><option value="ban">Sperren</option><option value="unban">Entsperren</option><option value="whitelist add">Zur Whitelist hinzufügen</option><option value="whitelist remove">Von Whitelist entfernen</option><option value="op add">Operator hinzufügen</option><option value="op remove">Operator entfernen</option></select></label></div><button type="submit">Ausführen</button></form></div><p class="small muted">Verfügbare Befehle und erforderliche Parameter können sich mit Hytale-Versionen ändern. Nutze <code>help</code> in der Konsole für die aktuelle Syntax.</p>`;
  bind('[data-command]','click',(_,n)=>sendCommand(n.dataset.command));
  bind('[data-player-file]','click',(_,n)=>editText(n.dataset.playerFile));
  bind('#player-action','submit',async(e,n)=>{const v=formValues(n);confirmAction('Spieleraktion ausführen',v.action+' '+v.player,()=>sendCommand(v.action+' '+v.player));});
}
async function renderBackups() {
  const rows=await api(serverUrl('/backups'));
  $('#tab-content').innerHTML=`<div class="titlebar"><h2>Backups</h2><div class="actions"><button data-backup="false" class="primary">Daten sichern</button><button data-backup="true">Vollständig sichern</button></div></div><p class="hint">Für konsistente Backups wird ein laufender Server kurz gestoppt und danach gestartet. Anmeldedaten werden nicht ins Archiv aufgenommen. Vor einer Wiederherstellung entsteht eine vollständige Sicherheitskopie.</p>${table(['Archiv','Erstellt','Größe',''],rows.map(b=>`<tr><td>${escape(b.name)}</td><td>${date(b.created)}</td><td>${bytes(b.size)}</td><td><div class="actions"><a href="/api${serverUrl('/backups/'+enc(b.name))}">Download</a><button data-restore="${escape(b.name)}">Wiederherstellen</button><button data-delete-backup="${escape(b.name)}" class="danger">Löschen</button></div></td></tr>`),'Noch keine Backups. Datenbackups enthalten Welten, Konfiguration und Mods. Vollständige Backups enthalten zusätzlich Server und Assets.')}`;
  bind('[data-backup]','click',async(_,n)=>{await api(serverUrl('/backups?full='+n.dataset.backup),'POST');notify('Backup erstellt');await renderBackups();});
  bind('[data-restore]','click',(_,n)=>confirmAction('Backup wiederherstellen','Server zuerst stoppen. Gesicherte Welten und Konfiguration ersetzen den aktuellen Stand.',async()=>{await api(serverUrl('/backups/'+enc(n.dataset.restore)+'/restore'),'POST');notify('Backup wiederhergestellt');await renderBackups();},'Wiederherstellen'));
  bind('[data-delete-backup]','click',(_,n)=>confirmAction('Backup löschen',n.dataset.deleteBackup,async()=>{await api(serverUrl('/backups/'+enc(n.dataset.deleteBackup)),'DELETE');await renderBackups();},'Löschen'));
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
  bind('#install','click',()=>confirmAction('Hytale herunterladen',selected.installed?'Ein vollständiges Backup wird erstellt. Der Server muss gestoppt sein.':'Starte den offiziellen Downloader. Bestätige anschließend seinen Gerätecode.',async()=>{await api(serverUrl('/install'),'POST');notify('Downloader gestartet. Installationsprotokoll öffnen.');await showInstallLog();},'Download starten'));
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
  const s=await api('/system');$('#content').innerHTML=`<div class="titlebar"><h1>System</h1><button id="refresh-system">Aktualisieren</button></div><div class="section"><h2>Docker und Speicher</h2><dl class="definition"><dt>Docker</dt><dd>${s.docker.connected?'Verbunden · '+escape(s.docker.version):'Nicht erreichbar'}</dd><dt>Host-RAM</dt><dd>${bytes(s.docker.memory_bytes||0)}</dd><dt>Host-CPUs</dt><dd>${s.docker.cpus||'—'}</dd><dt>Freier Speicher</dt><dd>${bytes(s.disk.free)} von ${bytes(s.disk.total)}</dd><dt>Runner-Image</dt><dd>${escape(s.runner_image)}</dd><dt>Host-Datenpfad</dt><dd>${escape(s.host_data)}</dd><dt>Upload-Limit</dt><dd>${bytes(s.upload_limit_bytes)}</dd><dt>Öffentliche URL</dt><dd>${escape(s.public_url||'LAN-Betrieb')}</dd></dl></div><div class="section"><h2>Backup-Aufbewahrung</h2><form id="system-settings"><label>Anzahl Backups pro Server<input name="backup_retention" type="number" min="2" max="365" value="${s.backup_retention}" required></label><button type="submit" class="primary">Speichern</button></form></div><div class="section"><h2>Unraid und Netzwerk</h2><ul class="checklist"><li>Panel: HTTP-Port 8095 im Unraid-Template</li><li>Spielserver: individuelle UDP-Ports, standardmäßig ab 5520</li><li>Internetfreigabe: UDP-Port des Spielservers im Router weiterleiten</li><li>HTTPS: vorhandenen Reverse Proxy mit diesem Panel verbinden</li><li>Bei Reverse Proxy <code>HYPANEL_PUBLIC_URL</code> setzen und sichere Cookies aktivieren</li></ul><a href="https://github.com/gottschalkfelix4-source/hypanel" target="_blank" rel="noopener">Repository und Installationsanleitung</a></div>`;
  bind('#refresh-system','click',renderSystem);bind('#system-settings','submit',async(e,n)=>{await api('/system','PUT',{backup_retention:Number(formValues(n).backup_retention)});notify('Aufbewahrung gespeichert');});
}
async function renderAccount() {
  const tokens=await api('/me/tokens');
  $('#content').innerHTML=`<h1>Mein Konto</h1><div class="section"><h2>Passwort ändern</h2><form id="password-form"><label>Aktuelles Passwort<input name="current" type="password" required autocomplete="current-password"></label><label>Neues Passwort<input name="password" type="password" required minlength="12" autocomplete="new-password"></label><button type="submit" class="primary">Passwort ändern</button></form></div><div class="section"><div class="titlebar"><h2>API-Schlüssel</h2><button id="new-token">Schlüssel erstellen</button></div>${table(['Name','Gültig bis',''],tokens.map(t=>`<tr><td>${escape(t.name)}</td><td>${date(t.expires)}</td><td><button data-revoke-token="${t.id}" class="danger">Widerrufen</button></td></tr>`),'Keine API-Schlüssel.')}<p class="small muted">REST-Aufrufe mit Authorization: Bearer. Schlüssel erhalten deine Benutzerrechte und werden beim Passwortwechsel widerrufen.</p></div>`;
  bind('#password-form','submit',async(e,n)=>{await api('/me/password','POST',formValues(n));user=null;disconnect();notify('Passwort geändert. Bitte erneut anmelden.');await auth();});
  bind('#new-token','click',()=>modal('API-Schlüssel erstellen','<label>Name<input name="name" required maxlength="80"></label><label>Gültigkeit (Tage)<input name="days" type="number" min="1" max="365" value="90" required></label>',async v=>{const result=await api('/me/tokens','POST',{name:v.name,days:Number(v.days)});await renderAccount();$('#dialog-content').innerHTML=`<h2>API-Schlüssel erstellt</h2><p>Jetzt sicher kopieren. Der Schlüssel wird nur einmal angezeigt.</p><label>Schlüssel<input id="token-value" readonly value="${escape(result.token)}"></label><div class="form-actions"><button id="token-close">Schließen</button></div>`;$('#token-close').onclick=()=>$('#dialog').close();$('#token-value').select();return false;}));
  bind('[data-revoke-token]','click',(_,n)=>confirmAction('API-Schlüssel widerrufen','Dieser Schlüssel verliert sofort den Zugriff.',async()=>{await api('/me/tokens/'+n.dataset.revokeToken,'DELETE');await renderAccount();},'Widerrufen'));
}
try {user=await api('/me');await navigate('servers');}catch{await auth();}
