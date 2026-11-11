// Bounded palette cache; read artwork, never the audio file. CORS failures stay neutral.
(() => {
 const cache=new Map(),pending=new Map(),revision=new WeakMap();
 async function palette(url){
  if(cache.has(url))return cache.get(url);if(pending.has(url))return pending.get(url);
  const promise=new Promise(resolve=>{const image=new Image();image.crossOrigin='anonymous';
   image.onload=()=>{try{const c=document.createElement('canvas');c.width=c.height=48;const ctx=c.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0,48,48);const d=ctx.getImageData(0,0,48,48).data,b=new Map();
    for(let i=0;i<d.length;i+=4){if(d[i+3]<160||Math.max(d[i],d[i+1],d[i+2])<20)continue;const k=[d[i]>>5,d[i+1]>>5,d[i+2]>>5].join(',');const v=b.get(k)||{n:0,s:[0,0,0]};v.n++;v.s.forEach((_,j)=>v.s[j]+=d[i+j]);b.set(k,v);}
    const colors=[];for(const v of [...b.values()].sort((a,b)=>b.n-a.n)){const rgb=v.s.map(x=>Math.round(x/v.n));if(colors.every(c=>c.reduce((n,x,j)=>n+(x-rgb[j])**2,0)>1800))colors.push(rgb);if(colors.length===3)break;}
    if(!colors.length)colors.push([55,55,60]);while(colors.length<3)colors.push(colors[0]);if(cache.size>=128)cache.delete(cache.keys().next().value);cache.set(url,colors);resolve(colors);
   }catch{resolve(null);}};image.onerror=()=>resolve(null);image.src=url;
  });pending.set(url,promise);try{return await promise;}finally{pending.delete(url);}
 }
 window.coverPalette=palette;
 function paint(element){const image=element.querySelector('img');const url=image?.getAttribute('src')||'';
  if(revision.get(element)===url)return;revision.set(element,url);element.classList.remove('has-palette');if(!url)return;
  void palette(url).then(colors=>{if(!colors||revision.get(element)!==url)return;colors.forEach((c,i)=>element.style.setProperty('--glow-'+(i+1),c.join(',')));element.classList.add('has-palette');});
 }
 for(const id of ['player-cover','playlist-summary-cover']){const element=document.getElementById(id);if(!element)continue;new MutationObserver(()=>paint(element)).observe(element,{childList:true,subtree:true,attributes:true,attributeFilter:['src']});paint(element);}
})();
