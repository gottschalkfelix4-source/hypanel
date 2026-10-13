// Der Assistent bedient ausschließlich die explizit ausgewählte Server-ID.
export function mountHytaleAuth({element, sid, name, api, escape, can}) {
  let alive=true, timer, busy=false, previous='';
  const endpoint=`/servers/${encodeURIComponent(sid)}/hytale-auth`;
  const button=(action,label,extra='')=>`<button data-auth-action="${action}" ${busy?'disabled':''} ${extra}>${label}</button>`;
  const device=(value,target)=>value?`<div class="auth-device"><p>Öffne Hytale und bestätige diesen Code:</p><code class="device-code">${escape(value.code)}</code><div class="actions"><a class="auth-link" href="${escape(value.url)}" target="_blank" rel="noopener noreferrer">Bei Hytale anmelden ↗</a>${button('cancel','Anmeldung abbrechen',`data-target="${target}"`)}</div><p class="small muted" data-auth-expiry="${value.expires_at}"></p><p class="small">Wähle im Hytale-Fenster deinen eigenen Account. Bei einem anderen angemeldeten Account dort zuerst abmelden oder den Link in einem privaten Browser öffnen.</p></div>`:'';
  function draw(data) {
    const d=data.download,s=data.server;
    let download=d.phase==='waiting'?device(d.device,'download'):
      ['starting','downloading'].includes(d.phase)?`<p>${d.phase==='starting'?'Download-Anmeldung wird vorbereitet …':'Hytale wird heruntergeladen und installiert …'}</p>${button('cancel','Download abbrechen','data-target="download"')}`:
      data.installed?`<p>Serverdateien sind installiert.</p>${!d.saved?`<p>Du kannst deinen Download-Account verbinden, ohne Serverdateien erneut herunterzuladen.${data.running?' Stoppe dafür zuerst diesen Server.':''}</p>${button('download-login','Download-Account verbinden',can('server.power')&&!data.running?'':'disabled')}`:''}`:
      `<p>${d.phase==='failed'?'Der Download ist fehlgeschlagen. Du kannst die Anmeldung erneut beginnen.':d.phase==='expired'?'Der Gerätecode ist abgelaufen. Starte eine neue Anmeldung.':'Installiere Hytale mit deinem Account für diesen Server.'}</p>${button('install','Download-Anmeldung starten',can('server.power')?'':'disabled')}`;
    download+=`<p class="small muted">Download-Anmeldung: ${d.saved?'nur für diesen Server gespeichert':'nicht gespeichert'}. Hytale verwendet dafür einen eigenen Anmeldeschritt.</p>`;
    if(d.saved)download+=button('forget-download','Download-Anmeldung entfernen');
    let login;
    if(!data.installed)login='<p>Zuerst die Serverdateien installieren.</p>';
    else if(!data.running)login=`<p>Starte diesen Server, um seinen Account zu prüfen oder anzumelden.${s.saved?' Eine lokale Anmeldedatei ist vorhanden; ihre Gültigkeit wird beim Start geprüft.':''}</p>${button('start','Server starten',can('server.power')?'':'disabled')}${s.saved?button('logout','Gespeicherte Server-Anmeldung entfernen'):''}`;
    else if(s.authenticated)login=`<p>Angemeldet als <strong>${escape(s.profile||'Hytale-Account')}</strong>.</p>${button('logout','Server abmelden / Account wechseln')}`;
    else if(s.profiles.length)login=`<p>Wähle das Profil für diesen Server.</p><label>Hytale-Profil<select id="hytale-profile">${s.profiles.map(p=>`<option value="${p.index}">${escape(p.name)}</option>`).join('')}</select></label>${button('select','Profil verwenden')}`;
    else if(s.device)login=device(s.device,'server');
    else if(s.phase==='pending')login=`<p>Hytale-Anmeldung wird vorbereitet oder bestätigt …</p>${button('cancel','Anmeldung abbrechen')}`;
    else if(!s.available)login=`<p>Warte auf den Server und seinen Anmeldestatus. Nach einem Panel-Update kann ein Server-Neustart nötig sein.</p>${button('restart','Server neu starten',can('server.power')?'':'disabled')}`;
    else login=`<p>${s.phase==='expired'?'Die Anmeldung ist abgelaufen. Fordere einen neuen Code an.':s.phase==='failed'?'Hytale konnte die Anmeldung nicht abschließen. Du kannst einen neuen Versuch starten.':'Dieser Server ist noch nicht bei Hytale angemeldet.'}</p>${button('login','Server-Anmeldung starten','class="primary"')}`;
    const stored=s.authenticated&&s.encrypted&&s.saved;
    const save=stored?'<p class="auth-complete">Anmeldung abgeschlossen und verschlüsselt für diesen Server gespeichert.</p>':s.authenticated?
      `<p>Die Anmeldung ist aktiv. Die verschlüsselte Speicherung wird geprüft.</p>${button('save','Verschlüsselt speichern')}`:
      '<p>Nach erfolgreicher Anmeldung werden die Zugangsdaten automatisch verschlüsselt für diesen Server gespeichert. Bei Neustarts verwendet Hytale diese Anmeldung erneut.</p>';
    element.innerHTML=`<h2>Hytale-Anmeldeassistent</h2><p>Dieser Assistent gilt ausschließlich für <strong>${escape(name)}</strong>. Jeder Server kann einen anderen Hytale-Account verwenden.</p><p class="small muted">Dein Hytale-Passwort gibst du nur auf der offiziellen Hytale-Seite ein.</p><div id="hytale-auth-error" class="error" role="alert" hidden></div><section class="section"><h3>1. Serverdateien und Download-Anmeldung</h3>${download}</section><section class="section"><h3>2. Hytale-Account für diesen Server</h3>${login}</section><section class="section"><h3>3. Anmeldung für Neustarts speichern</h3>${save}</section><p class="small muted">Die Account-Zuordnung wird weder auf andere Server noch auf Server-Klone übertragen.</p>`;
    for(const node of element.querySelectorAll('[data-auth-action]'))node.onclick=()=>action(node.dataset.authAction,node.dataset.target||'server');
  }
  async function action(action,target) {
    if(busy||!alive)return;
    const confirmations={logout:`Die Hytale-Anmeldung ausschließlich für „${name}“ entfernen? Aktive Spieler können die Verbindung verlieren.`,'forget-download':`Die gespeicherte Download-Anmeldung ausschließlich für „${name}“ entfernen?`,restart:`„${name}“ neu starten?`};
    if(confirmations[action]&&!window.confirm(confirmations[action]))return;
    busy=true;clearTimeout(timer);
    const profile=Number(element.querySelector('#hytale-profile')?.value||1);
    element.querySelectorAll('button').forEach(n=>n.disabled=true);
    try {await api(endpoint,'POST',{action,target,profile});previous='';}
    catch(error){if(alive){const box=element.querySelector('#hytale-auth-error');box.hidden=false;box.textContent=error.message;}}
    finally {busy=false;if(alive){element.querySelectorAll('button').forEach(n=>n.disabled=['start','restart','install','download-login'].includes(n.dataset.authAction)&&!can('server.power'));await refresh();}}
  }
  async function refresh() {
    if(!alive||busy)return;
    try {
      const data=await api(endpoint);
      if(!alive)return;
      const signature=JSON.stringify(data);
      if(signature!==previous){draw(data);previous=signature;}
      for(const node of element.querySelectorAll('[data-auth-expiry]'))node.textContent=`Code gültig für noch ${Math.max(0,Math.ceil((Number(node.dataset.authExpiry)-Date.now()/1000)/60))} Minuten. Bestätigung wird automatisch geprüft.`;
    }catch(error){if(alive){const box=element.querySelector('#hytale-auth-error');if(box){box.hidden=false;box.textContent=error.message;}else element.textContent=error.message;}}
    if(alive)timer=setTimeout(refresh,2000);
  }
  element.textContent='Anmeldestatus wird geladen …';
  refresh();
  return ()=>{alive=false;clearTimeout(timer);};
}
