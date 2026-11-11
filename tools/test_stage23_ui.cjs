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
 let queueUpload=[],queueCommits=[];
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
   if(url.pathname==='/api/player/queue/uploads'){queueUpload=[];data={id:'large-queue'};}
   else if(url.pathname==='/api/player/queue/uploads/large-queue/chunks'){assert.equal(body.offset,queueUpload.length);assert.ok(body.paths.length<=1000);queueUpload.push(...body.paths);data={accepted:queueUpload.length};}
   else if(url.pathname==='/api/player/queue/uploads/large-queue/commit'){assert.equal(body.expected_count,queueUpload.length);queueCommits.push({paths:[...queueUpload],index:body.index,shuffle:body.shuffle});data={message:'Queue accepted'};}
   else if(url.pathname==='/api/network/settings'){
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

 try {
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:8765/');
 await page.waitForFunction(()=>document.querySelectorAll('#library-list li').length===3);
 await page.evaluate(()=>showScreen('online'));
 check(await page.locator('#online-audio-format').inputValue()==='original','original audio default');
 await page.locator('#online-audio-format').selectOption('mp3');
 check(await page.evaluate(()=>localStorage.getItem('ownplayer-audio-format'))==='mp3','MP3 choice persisted');
 await page.locator('#online-audio-format').selectOption('original');
 await page.setViewportSize({width:390,height:844});
 check(await page.locator('#online-audio-format').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}),'format selector fits narrow window');
 await page.setViewportSize({width:1280,height:800});
 await page.evaluate(()=>{
   libraryFiles.splice(0,libraryFiles.length);
   for(let i=0;i<25000;i++){const p=`C:/Music/song-${i}.opus`;libraryFiles.push(p);metadataMap.set(fileKey(p),{path:p,title:`Song ${i}`,artist:'Artist',duration_ms:180000});}
   showScreen('music');renderLibrary();
 });
 const rendered=await page.locator('#library-list .library-item').count();
 check(rendered>0&&rendered<80,'25,000 tracks use a small DOM window');
 check((await page.locator('#library-count').textContent()).includes('25000'),'total count is not truncated');
 await page.evaluate(()=>{document.querySelector('.workspace').scrollTop=1e9;});
 await page.waitForFunction(()=>[...document.querySelectorAll('#library-list .library-item')].some(e=>e.dataset.path==='C:/Music/song-24999.opus'));
 check(await page.locator('#library-list .library-item').count()<80,'last window is bounded');
 const beforeRefresh=await page.evaluate(()=>document.querySelector('.workspace').scrollTop);
 await page.evaluate(()=>renderLibrary());
 check(Math.abs((await page.evaluate(()=>document.querySelector('.workspace').scrollTop))-beforeRefresh)<2,'metadata rerender preserves scroll position');
 check(await page.locator('#library-list .library-item').last().getAttribute('data-path')==='C:/Music/song-24999.opus','last track reachable');
 const gaps=await page.locator('#library-list .library-item').evaluateAll(es=>es.slice(1).map((e,i)=>Math.round(e.getBoundingClientRect().top-es[i].getBoundingClientRect().bottom)));
 check(gaps.every(x=>x===8),'virtual desktop gaps remain 8px');
 await page.evaluate(()=>{librarySearch.value='Song 24999';renderLibrary();});
 check(await page.locator('#library-list .library-item').count()===1,'search filters the full library, not just the window');
 check(await page.locator('#library-list').evaluate(e=>e.style.paddingTop===''&&e.style.paddingBottom===''),'small filtered list clears spacers');
 await page.evaluate(()=>{librarySearch.value='';renderLibrary();document.querySelector('.workspace').scrollTop=0;});
 await page.waitForFunction(()=>document.querySelector('#library-list .library-item')?.dataset.path==='C:/Music/song-0.opus');
 await page.setViewportSize({width:390,height:844});
 await page.waitForTimeout(150);
 await page.evaluate(()=>{document.querySelector('.workspace').scrollTop=1e9;});
 await page.waitForFunction(()=>[...document.querySelectorAll('#library-list .library-item')].some(e=>e.dataset.path==='C:/Music/song-24999.opus'));
 check(await page.locator('#library-list .library-item').count()<80,'mobile window is bounded');
 check(await page.locator('#library-list .library-item').first().evaluate(e=>Math.round(e.getBoundingClientRect().height))===120,'mobile rows retain CSS height');
 await page.setViewportSize({width:1280,height:800});
 await page.evaluate(async()=>{
   const response=await uploadLargeQueue(libraryFiles,24999,false);if(!response.ok)throw Error('Queue upload failed');
 });
 check(queueCommits.length===1&&queueCommits[0].paths.length===25000,'large queue uploaded without truncation');
 check(queueCommits[0].index===24999&&!queueCommits[0].shuffle,'selected index retained');
 check(calls.filter(c=>c.path.endsWith('/chunks')).every(c=>c.body.paths.length<=1000),'each queue request is bounded');
 await page.evaluate(async()=>{await uploadLargeQueue(libraryFiles,0,true);});
 check(queueCommits.length===2&&queueCommits[1].shuffle,'large shuffle queue uses chunk protocol');
 // Exercise the actual play handler, not only the helper.
 await page.evaluate(()=>{librarySearch.value='Song 24999';renderLibrary();});
 await page.locator('#library-list [data-play-track]').click();
 await page.waitForFunction(()=>!commandBusy);
 check(queueCommits.length===3&&queueCommits[2].index===24999,'Play handler uploads complete queue from filtered view');
 check(errors.length===0,'no browser JS errors: '+errors.join(';'));
 console.log(`PASS ${checks} stage23 UI assertions; 25,000-track fixture, mocked API/audio, Chromium not WebView2.`);
 } finally {await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
