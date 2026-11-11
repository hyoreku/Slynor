// Deterministic API/native fixtures. Real animations use Chromium, not WebView2.
const {chromium}=require('playwright');const fs=require('node:fs/promises');const path=require('node:path');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');const check=(value,label)=>{assert.ok(value,label);checks++;};let checks=0;
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAADwAAAA8CAIAAAC1nk4lAAAAbklEQVR4nO3OsRFAQAAAQUTKkIoVoRBFKFcp9PDBSm7zm7n5Pbdp1H48w+163cPtMlz+qGmlaaVppWmlaaVppWmlaaVppWmlaaVppWmlaaVppWmlaaVppWmlaaVppWmlaaVppWmlaaVppWmlaeUDYzID+w+E0fUAAAAASUVORK5CYII=','base64');
const local=['C:/Music/one.wav','C:/Music/two.wav','C:/Music/three.wav'];
const cover='/api/assets/track-covers/'+'a'.repeat(64)+'.jpg';
const remote=[1,2,3].map((id,i)=>({path:'online:soundcloud:'+id,title:'Online '+id,artist:'Artist',source:'soundcloud',access:['playable','preview','blocked'][i],duration_ms:180000,cover_url:'https://i1.sndcdn.com/art.jpg',permalink_url:'https://soundcloud.com/artist/song'}));
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||chromium.executablePath(),headless:true,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:1280,height:800},reducedMotion:'no-preference'});
 let state={state:'playing',filename:'one.wav',file_path:local[0],volume:50,time_ms:2000,duration_ms:180000,can_seek:true,queue_size:3,queue_index:0,rate:1,speed_mode:'standard'};
 let eq={enabled:false,preamp:0,bands:Array(10).fill(0),speed_mode:'standard'};const calls=[];const errors=[];
 let holdState=false,releaseState=null,holdMetadata=false,releaseMetadata=null;
 let network18={mode:'system',url:'',search_first:'auto',proxy_detected:false,credentials_present:false};
 let fixtureTracks=remote,fastCalls=0,coverAttempts=0;const fastCover='/api/assets/online-covers/'+'d'.repeat(64)+'.jpg';const fastTrack={path:'online:catalog:'+'d'.repeat(32),title:'Fast YouTube track',artist:'Artist',source:'youtube',access:'prepare',cover_url:fastCover};
 const settings={version:1,accent:'#8655eb',language:'ru',theme:'dark',spectrum_enabled:false,cover_background:false,close_to_tray:true,sidebar_auto_collapse:true};
 await context.addInitScript(({local,settings})=>{
  window.desktopWrites=[];addEventListener('load',()=>{window.pywebview={api:{load_library:async()=>local,load_playlists:async()=>[],load_settings:async()=>settings,save_theme:async value=>({...settings,theme:value}),save_playing_screen:async value=>{window.savedPlayingScreen=value;return{...settings,playing_screen_enabled:value};},save_app_icon:async value=>{window.savedIcon=value;return{...settings,app_icon:value};},save_mini_background:async value=>({...settings,mini_background:value}),save_audio_output:async value=>({...settings,audio_output:value}),save_desktop_preferences:async value=>{desktopWrites.push(value);return{...settings,close_to_tray:value};}}};dispatchEvent(new Event('pywebviewready'));});
 },{local,settings});
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());if(url.pathname===fastCover){coverAttempts++;return route.fulfill({status:coverAttempts===1?503:200,contentType:'image/png',body:coverAttempts===1?Buffer.alloc(0):png});}if(url.hostname==='i1.sndcdn.com'||url.pathname.startsWith('/api/assets/'))return route.fulfill({contentType:'image/png',body:png});
  if(url.pathname.startsWith('/api/')){
   const body=req.postDataJSON();calls.push({path:url.pathname,body});let data={message:'Готово'},status=200;
   if(url.pathname==='/api/network/settings'){
    if(req.method()==='POST')network18={mode:body.mode,url:body.url,search_first:body.search_first,proxy_detected:body.mode==='manual',effective:body.mode==='manual'?body.url:null,credentials_present:!!body.password};
    data=network18;
   }else if(url.pathname==='/api/network/check')data={network:{mode:network18.mode,proxy_detected:true},checks:[{service:'youtube',code:'http_200',elapsed_ms:12}]};
   else if(url.pathname==='/api/player/state'){data=structuredClone(state);if(holdState){holdState=false;await new Promise(resolve=>releaseState=resolve);}}
   else if(url.pathname==='/api/appearance'){state.appearance={...body};data=body;}
   else if(url.pathname==='/api/player/output-devices')data={devices:[{id:'',name:'По умолчанию в Windows'},{id:'usb',name:'Наушники USB'}],selected:'',available:true};
   else if(url.pathname==='/api/player/output-device')data={selected:body.device,message:'Команда выбора передана VLC.'};
   else if(url.pathname==='/api/library/metadata'){if(holdMetadata&&body.paths.includes('C:/Music/delayed.wav')){holdMetadata=false;await new Promise(resolve=>releaseMetadata=resolve);}data={tracks:body.paths.map(p=>p===fastTrack.path?fastTrack:p.startsWith('online:')?remote.find(t=>t.path===p):{path:p,title:path.basename(p),artist:'Artist',duration_ms:180000,cover_url:cover})};}
   else if(url.pathname==='/api/player/equalizer')data={available:true,writable:true,current:eq,saved:eq,frequencies:[31,62,125,250,500,1000,2000,4000,8000,16000],presets:[]};
   else if(url.pathname==='/api/player/queue')data={paths:local,index:2,shuffle_enabled:false};
   else if(url.pathname==='/api/player/pause')state={...state,state:'paused'};
   else if(url.pathname==='/api/player/resume')state={...state,state:'playing'};
   else if(url.pathname==='/api/player/volume')state={...state,volume:body.volume};
   else if(url.pathname==='/api/desktop/status')data={tray_available:true,close_to_tray:true};
   else if(url.pathname==='/api/online/status')data={sources:[{id:'soundcloud',configured:true,message:'SoundCloud: тестовое подключение'},{id:'vk',configured:false,message:'ВК: официальный доступ не подтверждён'}]};
   else if(url.pathname==='/api/online/playlists')data={playlists:[]};
   else if(url.pathname==='/api/online/search-jobs'){
    data={id:body.source==='vk'?'vk':'search',status:'queued'};
   }else if(url.pathname==='/api/online/jobs/search')data={status:'done',tracks:fixtureTracks,warnings:[]};
   else if(url.pathname==='/api/online/jobs/vk')data={status:'failed',tracks:[],error:'ВК Музыка не подключена: официальный доступ не подтверждён.'};
   else if(url.pathname==='/api/online/search'){
    if(body.source==='vk'){status=503;data={detail:'ВК Музыка не подключена: официальный доступ не подтверждён.'};}
    else data={tracks:remote};
   }else if(url.pathname==='/api/online/stream-jobs')data={id:'fast16',status:'queued'};
   else if(url.pathname==='/api/online/jobs/fast16'){fastCalls++;data=fastCalls<3?{status:'running',stage:'Test stream resolution'}:{status:'done',ref:fastTrack.path};}
   else if(url.pathname==='/api/online/diagnostics')data={events:[{provider:'youtube',operation:'stream',code:'auth_required',elapsed_ms:1200}]};
   else if(url.pathname==='/api/online/import')data={playlist:{id:'imported',source:'soundcloud',name:'Imported playlist',tracks:remote}};
   else if(url.pathname==='/api/player/queue/play'){state={...state,file_path:body.paths[body.index],filename:'Online track',state:body.paths[0]===fastTrack.path?'opening':'playing'};if(body.paths[0]===fastTrack.path)setTimeout(()=>{state={...state,state:'playing',current_metadata:fastTrack};},650);}
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=url.pathname==='/'?path.join(root,'frontend/index.html'):url.pathname==='/mini'?path.join(root,'frontend/mini.html'):path.join(root,'frontend',url.pathname.replace(/^\/static\//,''));
  try{return route.fulfill({contentType:file.endsWith('.svg')?'image/svg+xml':file.endsWith('.webp')?'image/webp':file.endsWith('.png')?'image/png':file.endsWith('.ico')?'image/x-icon':file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html',body:await fs.readFile(file)});}catch{return route.fulfill({status:404,body:'not found'});}
 });

 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:8765/');
 await page.waitForFunction(()=>document.querySelectorAll('#library-list li').length===3);
 await page.evaluate(()=>dispatchEvent(new CustomEvent('visual-settings-loaded',{detail:{...window.visualSettingsSnapshot,cover_background:true}})));
 for(const width of [1280,1000,900,390]){
  const height=width===390?844:800;await page.setViewportSize({width,height});
  for(const theme of ['dark','light','transparent']){
   await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;applyAccent(currentAccent);},theme);
   for(const screen of ['home','music','settings','playing']){
    await page.evaluate(screen=>showScreen(screen),screen);await page.waitForTimeout(screen==='playing'?410:200);
    check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'document fits '+width+' '+theme+' '+screen);
    const overflow=await page.locator('button,input,select').evaluateAll(es=>es.filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&(r.left< -1||r.right>innerWidth+1)&&!e.closest('[hidden]');}).map(e=>e.id||e.getAttribute('aria-label')||e.textContent.slice(0,40)));
    check(overflow.length===0,'interactive controls fit '+width+' '+theme+' '+screen+' '+JSON.stringify(overflow));
    if(screen==='playing'){
     check(await page.locator('#sidebar').isHidden(),'playing focus '+width+' '+theme);
     check(await page.locator('#large-track-cover').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<innerHeight;}),'cover fits '+width+' '+theme);
    }
    if(screen==='settings'){
     for(const tab of ['Общие','Оформление','Технические']){
      await page.getByRole('tab',{name:tab,exact:true}).click();await page.waitForTimeout(240);
      const overflow=await page.locator('#screen-settings button,#screen-settings input,#screen-settings select').evaluateAll(es=>es.filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&(r.left< -1||r.right>innerWidth+1)&&!e.closest('[hidden]');}).map(e=>e.id||e.textContent.slice(0,30)));
      check(overflow.length===0,'settings tab fits '+width+' '+theme+' '+tab+' '+JSON.stringify(overflow));
      if(width===1000&&theme==='dark'&&tab==='Общие')await page.screenshot({path:path.join(root,'tests/stage22-general.png')});
      if(width===1280&&theme==='dark'&&tab==='Технические'){await page.screenshot({path:path.join(root,'tests/stage22-technical.png')});await page.locator('.startup-diagnostics').scrollIntoViewIfNeeded();await page.waitForTimeout(240);await page.screenshot({path:path.join(root,'tests/stage22-startup.png')});}
     }
     await page.getByRole('tab',{name:'Оформление',exact:true}).click();await page.waitForTimeout(240);
    }
    if(width===1280&&theme==='dark'&&screen==='home')await page.screenshot({path:path.join(root,'tests/stage22-home.png')});
    if(width===1000&&theme==='light'&&screen==='settings')await page.screenshot({path:path.join(root,'tests/stage22-light.png')});
    if(width===390&&theme==='dark'&&screen==='settings')await page.screenshot({path:path.join(root,'tests/stage22-narrow-settings.png')});
    if(width===390&&theme==='dark'&&screen==='playing')await page.screenshot({path:path.join(root,'tests/stage22-narrow-playing.png')});
   }
   await page.evaluate(()=>showScreen('settings'));await page.getByRole('tab',{name:'Оформление',exact:true}).click();
   await page.locator('#app-icon-options').scrollIntoViewIfNeeded();await page.waitForTimeout(200);
   check(await page.locator('#app-icon-options').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}),'brand grid fits '+width+' '+theme);
   if(width===1000&&theme==='dark')await page.screenshot({path:path.join(root,'tests/stage22-brand-settings.png')});
   if(width===390&&theme==='dark')await page.screenshot({path:path.join(root,'tests/stage22-narrow-icons.png')});
  }
 }
 await page.setViewportSize({width:900,height:740});await page.evaluate(()=>{document.documentElement.dataset.theme='dark';showScreen('playing');window.equalizerPanel.open();});await page.waitForTimeout(420);
 await page.screenshot({path:path.join(root,'tests/stage22-eq-900.png')});
 check(errors.length===0,'no JavaScript errors '+JSON.stringify(errors));
 await browser.close();console.log('PASS '+checks+' layout assertions, 4 widths and 3 themes; native/API mocked.');
})().catch(e=>{console.error(e);process.exit(1)});
