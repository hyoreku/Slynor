// Local-only, bounded possible-stall telemetry. Never sends titles, paths, URLs or input text.
(() => {
 let last=performance.now(),lastReport=0,nativeHidden=false;
 window.addEventListener('native-window-state',e=>{nativeHidden=!!e.detail?.minimized;last=performance.now();});
 document.addEventListener('visibilitychange',()=>{last=performance.now();});
 setInterval(()=>{const now=performance.now(),gap=now-last;last=now;if(document.hidden||nativeHidden||gap<3500||now-lastReport<60000)return;lastReport=now;void fetch('/api/diagnostics/ui-stall',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({duration_ms:Math.min(120000,Math.round(gap)),screen:['home','music','playing','online','settings'].includes(activeScreen)?activeScreen:'unknown'}),signal:AbortSignal.timeout(3000)}).catch(()=>{});},1000);
})();
