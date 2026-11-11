// Real browser decoding and loop continuity; provide a local short WebM or MP4.
// No audio engine, no external URLs, no alteration of the supplied file.
const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const file=process.argv[2];if(!file)throw Error('Usage: node tools/test_stage21_media.cjs short.webm');
 const bytes=await fs.readFile(file),source=await fs.readFile(path.join(__dirname,'../frontend/js/media-background.js'),'utf8');
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||chromium.executablePath(),headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage();
  await page.route('http://media.test/**',r=>r.request().url().endsWith('/loop.webm')?r.fulfill({contentType:file.endsWith('.mp4')?'video/mp4':'video/webm',body:bytes}):r.fulfill({contentType:'text/html',body:'<div id="wallpaper-layer"></div><p id="wallpaper-message"></p><script>let wallpaperFile="loop.webm";</script>'}));
  await page.goto('http://media.test/');await page.addScriptTag({content:source});
  await page.waitForFunction(()=>document.querySelector('video').currentTime>0);
  await page.evaluate(()=>{window.frames21=[];const video=document.querySelector('video');const frame=(now,meta)=>{frames21.push({now,time:meta.mediaTime});video.requestVideoFrameCallback(frame);};video.requestVideoFrameCallback(frame);});
  // Same URL notification must not rewind a running video.
  await page.waitForTimeout(180);
  const before=await page.locator('video').evaluate(v=>v.currentTime);
  await page.evaluate(()=>dispatchEvent(new CustomEvent('wallpaper-media',{detail:{url:'/api/assets/wallpapers/loop.webm'}})));
  const after=await page.locator('video').evaluate(v=>v.currentTime);
  assert.ok(after>=before,'same URL must not rewind');
  await page.waitForFunction(()=>frames21.filter((f,i)=>i&&f.time<frames21[i-1].time).length>=3,{timeout:15000});
  const result=await page.evaluate(()=>{const wraps=frames21.filter((f,i)=>i&&f.time<frames21[i-1].time);return{frames:frames21.length,loops:wraps.length,loopGapsMs:frames21.flatMap((f,i)=>i&&f.time<frames21[i-1].time?[Math.round(f.now-frames21[i-1].now)]:[])};});
  await page.evaluate(()=>dispatchEvent(new CustomEvent('native-window-state',{detail:{minimized:true}})));
  assert.ok(await page.locator('video').evaluate(v=>v.paused),'native minimize pauses');
  const paused=await page.locator('video').evaluate(v=>v.currentTime);
  await page.waitForTimeout(120);assert.ok(Math.abs(await page.locator('video').evaluate(v=>v.currentTime)-paused)<.02);
  await page.evaluate(()=>dispatchEvent(new CustomEvent('native-window-state',{detail:{minimized:false}})));
  await page.waitForFunction(()=>!document.querySelector('video').paused);
  await page.emulateMedia({reducedMotion:'reduce'});assert.ok(await page.locator('video').evaluate(v=>v.paused));
  console.log('PASS real local video, three loops, no same-URL reset, visibility/reduced-motion:',JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
