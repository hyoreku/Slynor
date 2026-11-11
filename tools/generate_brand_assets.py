"""Rebuild static exports from the supplied silhouette; runtime lava is CSS."""
from pathlib import Path
from PIL import Image,ImageFilter,ImageChops
import base64
r=Path(__file__).resolve().parents[1]
assets=r/'frontend/assets/app-icons'
source=Image.open(assets/'source.webp').convert('RGBA')
alpha=source.getchannel('A'); mask_image=Image.new('RGBA',source.size,'white');mask_image.putalpha(alpha);mask_image.save(assets/'mask.png')
pixel=alpha.resize((16,16),Image.Resampling.BOX).point(lambda x:255 if x>110 else 0).resize((48,48),Image.Resampling.NEAREST);pixel_image=Image.new('RGBA',(48,48),'white');pixel_image.putalpha(pixel);pixel_image.save(assets/'pixel-mask.png')
styles=[('white',[(255,255,255)]),('accent',[(160,121,255)]),('lava',[(255,68,123),(63,204,255),(255,192,81)]),('pixel',[(255,255,255)]),('song',[(125,114,240),(241,131,175),(79,202,187)]),('chrome',[(223,230,242),(92,114,144),(249,252,255)]),('neon',[(83,248,234),(152,111,255)]),('cosmos',[(75,80,188),(224,177,255),(58,215,217)])]
for i,(name,cols) in enumerate(styles,1):
 im=Image.new('RGBA',(48,48));pixels=im.load()
 for y in range(48):
  for x in range(48):
   f=((x+y*.6)/76.8)*(len(cols)-1);j=min(len(cols)-1,int(f));k=min(j+1,len(cols)-1);rgb=tuple(round(cols[j][c]*(1-(f-j))+cols[k][c]*(f-j)) for c in range(3));pixels[x,y]=(*rgb,255)
 im.putalpha(pixel if i==4 else alpha)
 if i==7:im.putalpha(ImageChops.subtract(alpha,alpha.filter(ImageFilter.MinFilter(3))))
 im.save(assets/f'{i}.webp',lossless=True)
 # Static SVG compatibility/export; interactive rendering uses CSS and the source alpha mask.
 from io import BytesIO
 b=BytesIO();im.save(b,format='PNG');data=base64.b64encode(b.getvalue()).decode()
 (assets/f'{i}.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><image width="48" height="48" href="data:image/png;base64,{data}"/></svg>')
