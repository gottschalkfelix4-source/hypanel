// Zustandswechsel im Assistenten; Authentifizierungs- und Rechtegrenzen werden separat im Backend getestet.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {execFileSync}=require('node:child_process');
const {randomBytes}=require('node:crypto');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.HYPANEL_TEST_URL||'http://localhost:18095';
(async()=>{
  const password=randomBytes(24).toString('base64url');
  for(let attempt=0;attempt<60;attempt++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}await new Promise(resolve=>setTimeout(resolve,250));}
  const key=execFileSync('docker',['exec','hypanel-auth-qa','cat','/data/setup.key'],{encoding:'utf8'}).trim();
  const setup=await fetch(base+'/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'auth-qa',password,setup_key:key})});
  assert.equal(setup.status,200);
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.getByLabel('Benutzername',{exact:true}).fill('auth-qa');await page.getByLabel('Passwort',{exact:true}).fill(password);
  await page.getByRole('button',{name:'Anmelden',exact:true}).click();
  await page.getByRole('button',{name:/Server erstellen/}).click();await page.getByLabel('Name',{exact:true}).fill('Server für Freunde');
  await page.getByRole('button',{name:'Erstellen',exact:true}).click();await page.getByRole('button',{name:/Öffnen/}).click();
  await page.getByRole('button',{name:'Hytale-Account',exact:true}).click();
  await page.getByRole('heading',{name:'Hytale-Anmeldeassistent'}).waitFor();
  await page.getByRole('button',{name:'Download-Anmeldung starten',exact:true}).waitFor();
  const state={installed:false,running:false,download:{phase:'idle',saved:false,device:null},server:{phase:'stopped',available:false,authenticated:false,saved:false,profile:null,profiles:[],encrypted:false,device:null}};
  const device={code:'ABCD-1234',url:'https://accounts.hytale.com/device?user_code=ABCD-1234',expires_at:Date.now()/1000+900};
  const actions=[];let sid;
  await page.route('**/api/servers/*/hytale-auth',async route=>{
    const current=route.request().url().split('/servers/')[1].split('/')[0];sid=sid||current;assert.equal(current,sid);
    if(route.request().method()==='POST'){
      const body=route.request().postDataJSON();actions.push(body.action);
      if(body.action==='install'){state.download.phase='waiting';state.download.device=device;}
      if(body.action==='start'){state.running=true;state.server.available=true;state.server.phase='unauthenticated';}
      if(body.action==='login'){state.server.phase='waiting';state.server.device=device;}
      if(body.action==='select'){assert.equal(body.profile,2);Object.assign(state.server,{phase:'authenticated',profiles:[],device:null,authenticated:true,profile:'FriendAccount',encrypted:true,saved:true});}
      if(body.action==='logout')Object.assign(state.server,{phase:'unauthenticated',authenticated:false,profile:null,saved:false});
      if(body.action==='cancel'){state.server.device=null;state.server.phase='unauthenticated';}
    }
    await route.fulfill({json:{server_id:current,...state}});
  });
  await page.getByRole('button',{name:'Download-Anmeldung starten',exact:true}).click();await page.locator('.device-code').waitFor();
  assert.equal(await page.locator('.auth-link').getAttribute('href'),device.url);
  state.installed=true;state.download.phase='complete';state.download.saved=true;state.download.device=null;
  await page.getByRole('button',{name:'Server starten',exact:true}).click();
  await page.getByRole('button',{name:'Server-Anmeldung starten',exact:true}).click();await page.locator('.device-code').waitFor();
  fs.mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/auth-wizard-desktop.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/auth-wizard-mobile.png',fullPage:true,animations:'disabled'});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  state.server.profiles=[{index:1,name:'First'},{index:2,name:'FriendAccount'}];state.server.device=null;
  await page.getByLabel('Hytale-Profil').selectOption('2');await page.getByRole('button',{name:'Profil verwenden',exact:true}).click();
  await page.getByText('Anmeldung abgeschlossen und verschlüsselt für diesen Server gespeichert.',{exact:true}).waitFor();
  page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Server abmelden / Account wechseln',exact:true}).click();
  await page.getByRole('button',{name:'Server-Anmeldung starten',exact:true}).waitFor();
  assert.deepEqual(actions,['install','start','login','select','logout']);assert.deepEqual(errors,[]);
  await browser.close();console.log('Auth wizard browser QA passed: per-server endpoint, download, device link, start, profile selection, persistence, logout, mobile.');
})().catch(e=>{console.error(e.message);process.exit(1)});
