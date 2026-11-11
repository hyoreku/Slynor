// Regression tests with a mocked HTTP API/PyWebView bridge; no network or audio.
// Requires Node + Playwright + Chromium. Run: node tools/test_ui.cjs
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const tracks=['C:/Music/first.wav','C:/Music/second.wav'];
const playlists=[{id:'first',name:'Мой <любимый> плейлист',cover:null,tracks}, {id:'second',name:'Другой плейлист',cover:null,tracks:[tracks[1]]}];
const settings={version:1,accent:'#8655eb',theme:'dark',language:'ru',developer_mode:false,playlist_shape:'square',wallpaper:null,wallpaper_dim:65,wallpaper_blur:0,cover_background:true,spectrum_enabled:false,sidebar_auto_collapse:true};
const defaultEQ={enabled:false,preamp:0,bands:Array(10).fill(0),speed_mode:'standard'};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||chromium.executablePath(),args:['--no-sandbox']});
 const failures=[];let checks=0;
 for(const [width,height] of [[1920,1080],[1280,800],[1000,800],[900,740]]) {
  const page=await browser.newPage({viewport:{width,height},reducedMotion:'reduce'});
  page.on('pageerror',err=>failures.push(`${width}: ${err.message}`));
  let eq=structuredClone(defaultEQ), saved=structuredClone(defaultEQ);
  let state={state:'idle',filename:null,file_path:null,time_ms:0,duration_ms:0,can_seek:false,volume:50,queue_index:null,queue_size:0,shuffle_enabled:false,rate:1,speed_mode:'standard'};
  const requests=[];
  await page.route('http://ownplayer.test/**',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.pathname.startsWith('/api/')) {
    let data={message:'Готово'};const body=req.postDataJSON();requests.push(url.pathname);
    if(url.pathname==='/api/player/state') data=state;
    else if(url.pathname==='/api/library/metadata')data={tracks:body.paths.map((p,i)=>({path:p,title:i===0?'Песня <один>':'Песня два',artist:'Артист',album:'Альбом',duration_ms:180000,cover_url:null}))};
    else if(url.pathname==='/api/player/equalizer') data={available:true,writable:true,current:eq,saved,frequencies:[31.25,62.5,125,250,500,1000,2000,4000,8000,16000],presets:[{id:'flat',name:'Ровный',preamp:0,bands:Array(10).fill(0)}]};
    else if(url.pathname==='/api/player/equalizer/apply') {eq=body;state.rate={standard:1,speedup:1.25,slowed:.8}[eq.speed_mode];state.speed_mode=eq.speed_mode;data={current:eq};}
    else if(url.pathname==='/api/player/equalizer/save'){saved=body;data={saved};}
    else if(url.pathname==='/api/player/queue/play'){
     const p=body.paths[body.index];state={...state,state:'playing',file_path:p,filename:path.basename(p),queue_index:body.index,queue_size:body.paths.length,duration_ms:180000,time_ms:10000,can_seek:true};
    } else if(url.pathname==='/api/player/pause')state.state='paused';
    else if(url.pathname==='/api/player/resume')state.state='playing';
    else if(url.pathname==='/api/player/seek'){state.time_ms=body.position_ms;data={position_ms:state.time_ms};}
    else if(url.pathname==='/api/player/spectrum')data={available:false,bands:[],status:'disabled'};
    else if(url.pathname==='/api/player/queue')data={paths:tracks,index:0,shuffle_enabled:false};
    else if(url.pathname==='/api/desktop/status')data={tray_available:true,close_to_tray:true};
    else if(url.pathname==='/api/online/status')data={sources:[{id:'soundcloud',configured:false,message:'SoundCloud не настроен'},{id:'vk',configured:false,message:'ВК недоступен'}]};
    else if(url.pathname==='/api/online/playlists')data={playlists:[]};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});return;
   }
   const file=url.pathname==='/'?path.join(root,'frontend/index.html'):path.join(root,'frontend',url.pathname.replace(/^\/static\//,''));
   try{await route.fulfill({status:200,contentType:file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':file.endsWith('.js')?'application/javascript':'text/html',body:await fs.readFile(file)});}catch(e){await route.fulfill({status:404,body:'not found'});}
  });
  await page.addInitScript(({tracks,playlists,settings})=>{
   window.testBridgeWrites=[];window.failSidebarSave=false;
   const api={load_library:async()=>tracks,load_playlists:async()=>playlists,load_settings:async()=>settings,
    save_sidebar_preferences:async value=>{window.testBridgeWrites.push(value);if(window.failSidebarSave)throw new Error('test failure');settings.sidebar_auto_collapse=value;return settings;},
    save_preferences:async(language,developer_mode)=>Object.assign(settings,{language,developer_mode}),
    save_theme:async theme=>Object.assign(settings,{theme}),save_visual_effects:async()=>settings};
   // Native readiness is delivered after all deferred JS has initialized.
   addEventListener('load',()=>{window.pywebview={api};dispatchEvent(new Event('pywebviewready'));});
  },{tracks,playlists,settings:structuredClone(settings)});
  await page.goto('http://ownplayer.test/');
  await page.waitForFunction(()=>document.querySelectorAll('.sidebar-playlist-button').length===2&&!document.getElementById('sidebar-auto-collapse').disabled);
  await page.locator('.sidebar-playlist-button').first().click();
  await page.waitForFunction(()=>document.querySelectorAll('#library-list li').length===2);
  assert.equal(await page.locator('#playlist-summary-name').textContent(),playlists[0].name);checks++;
  assert.equal(await page.locator('#playlist-summary-name *').count(),0);checks++;
  const layout=await page.evaluate(()=>{
   const summary=document.getElementById('playlist-summary').getBoundingClientRect(),content=document.querySelector('.playlist-library-content').getBoundingClientRect();
   return {left:summary.right<=content.left,above:summary.bottom<=content.top,overflow:document.documentElement.scrollWidth>innerWidth,album:getComputedStyle(document.querySelector('.library-item .track-album')).display};
  });
  assert.ok(width>1150?layout.left:layout.above,`${width}: summary breakpoint`);checks++;
  assert.ok(!layout.overflow,`${width}: overflow`);checks++;
  assert.notEqual(layout.album,'none');checks++;
  await page.locator('#library-list [data-play-track]').first().click();
  await page.waitForFunction(()=>!document.getElementById('screen-playing').hidden);
  assert.equal(await page.locator('#back-to-tracks').evaluate(el=>el.parentElement.tagName),'HEADER');checks++;
  await page.locator('#equalizer-button').click();
  const geometry=await page.evaluate(()=>{
   const speed=document.getElementById('eq-speed').getBoundingClientRect(),preamp=document.getElementById('eq-preamp').getBoundingClientRect();
   const panel=document.getElementById('eq-panel');
   return {orientation:preamp.width>preamp.height,right:preamp.left>=speed.right,bands:document.querySelectorAll('#eq-bands input').length,
    overflow:panel.scrollHeight>panel.clientHeight, panelHeight:panel.clientHeight,
    outside:[...panel.querySelectorAll('input,select,button')].filter(el=>!el.hidden&&getComputedStyle(el).display!=='none').some(el=>el.getBoundingClientRect().bottom>innerHeight)};
  });
  assert.ok(geometry.orientation,`${width}: preamp horizontal`);checks++;
  assert.ok(geometry.right,`${width}: preamp after speed`);checks++;
  assert.equal(geometry.bands,10);checks++;
  assert.ok(!geometry.overflow,`${width}: EQ panel overflow ${JSON.stringify(geometry)}`);checks++;
  assert.ok(!geometry.outside,`${width}: EQ controls outside viewport ${JSON.stringify(geometry)}`);checks++;
  await page.locator('#eq-speed').selectOption('speedup');
  await page.waitForFunction(()=>document.getElementById('eq-message').textContent.includes('Настройки переданы VLC'));
  assert.equal(eq.speed_mode,'speedup');checks++;
  await page.locator('#eq-preamp').evaluate(el=>{el.value='-5';el.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.waitForFunction(()=>document.getElementById('eq-preamp-value').textContent.includes('-5.0'));
  await page.locator('#eq-close').click();
  // Browse another playlist while the first remains the actual queue source.
  await page.locator('.sidebar-playlist-button').nth(1).click();
  await page.locator('#player-cover').click();
  await page.locator('#back-to-tracks').click();
  assert.equal(await page.locator('#playlist-view').inputValue(),'first');checks++;
  assert.equal(await page.locator('#playlist-summary-name').textContent(),playlists[0].name);checks++;
  await page.locator('#sidebar-toggle').click();
  assert.equal(Math.round((await page.locator('#sidebar').boundingBox()).width),80);checks++;
  assert.equal(await page.locator('.sidebar-playlist-button').count(),2);checks++;
  assert.ok(await page.locator('.sidebar-playlist-symbol').first().isVisible());checks++;
  await page.locator('#player-cover').click();
  await page.locator('#equalizer-button').click();
  assert.equal(Math.round((await page.locator('#eq-panel').boundingBox()).x),80);checks++;
  await page.locator('#eq-close').click();
  await page.locator('#back-to-tracks').click();
  await page.locator('#sidebar-toggle').click();
  // RU/EN remains functional and does not translate playlist names.
  await page.locator('[data-screen="settings"]').click();
  await page.locator('#language-choice').selectOption('en');
  await page.waitForFunction(()=>document.documentElement.lang==='en');
  assert.equal(await page.locator('#sidebar-toggle').getAttribute('title'),'Collapse sidebar');checks++;
  await page.locator('.sidebar-playlist-button').first().click();
  assert.equal(await page.locator('#playlist-summary-name').textContent(),playlists[0].name);checks++;
  // Accelerate only test clock. Timer remains exactly 60 seconds in production.
  await page.clock.install();
  await page.locator('#player-cover').click();
  await page.mouse.move(width-20,20);
  await page.evaluate(()=>document.activeElement.blur());
  await page.clock.fastForward(59000);
  assert.ok(!await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  await page.clock.fastForward(2000);
  assert.ok(await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  await page.locator('#sidebar-toggle').click();
  await page.locator('#sidebar-toggle').focus();
  await page.mouse.move(width-20,20);
  await page.clock.fastForward(65000);
  assert.ok(!await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  // Modal playlist editor prevents collapse even with pointer/focus outside sidebar.
  await page.locator('.sidebar-playlist-button').first().click();
  await page.locator('#player-cover').click();
  await page.evaluate(()=>openPlaylistEditor('first'));
  await page.clock.fastForward(65000);
  assert.ok(!await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  await page.locator('#close-playlist-editor').click();
  await page.mouse.move(width-20,20);await page.evaluate(()=>document.activeElement.blur());
  await page.clock.fastForward(59000);
  assert.ok(!await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  // Pause suspends the timer.
  state.state='paused';await page.evaluate(()=>refreshPlayerState());
  await page.waitForFunction(()=>currentPlaybackState==='paused');
  await page.clock.fastForward(65000);
  assert.ok(!await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  // Native minimize may not set document.hidden on WebView2.
  state.state='playing';await page.evaluate(()=>{dispatchEvent(new CustomEvent('native-window-state',{detail:{minimized:true}}));refreshPlayerState();});
  await page.clock.fastForward(65000);
  assert.ok(!await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  // Disabling auto-collapse writes a boolean; failed save restores last confirmed value.
  await page.locator('[data-screen="settings"]').click();
  await page.locator('#sidebar-auto-collapse').uncheck();
  await page.waitForFunction(()=>testBridgeWrites.includes(false));
  await page.evaluate(()=>window.failSidebarSave=true);
  await page.locator('#sidebar-auto-collapse').click();
  await page.waitForFunction(()=>!document.getElementById('sidebar-auto-collapse').checked);
  checks++;
  await page.evaluate(()=>window.failSidebarSave=false);
  await page.locator('#player-cover').click();
  state.state='playing';
  await page.evaluate(()=>{dispatchEvent(new CustomEvent('native-window-state',{detail:{minimized:false}}));refreshPlayerState();});
  await page.mouse.move(width-20,20);await page.evaluate(()=>document.activeElement.blur());
  await page.clock.fastForward(65000);
  assert.ok(!await page.evaluate(()=>document.documentElement.classList.contains('sidebar-collapsed')));checks++;
  // Screenshot normal playlist and the current track/EQ at this size.
  await page.locator('.sidebar-playlist-button').first().click();
  await page.screenshot({path:path.join(root,'tests',`ui-playlist-${width}.png`)});
  await page.locator('#player-cover').click();await page.locator('#equalizer-button').click();
  await page.screenshot({path:path.join(root,'tests',`ui-eq-${width}.png`)});
  console.log(`PASS ${width}x${height}`);await page.close();
 }
 assert.deepEqual(failures,[],'Browser JS errors');
 console.log(`PASS ${checks} assertions; no browser JS errors. API and native bridge are mocked.`);
 await browser.close();
})().catch(err=>{console.error(err);process.exit(1);});
