"""Generate the Windows executable icon from logo.png; never modify tray.png."""
from pathlib import Path
from PIL import Image

def build(root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    source=root/'frontend/assets/logo.png'
    destination=root/'frontend/assets/icons/app.ico'
    with Image.open(source) as original:
        if original.format!='PNG' or max(original.size)>4096:
            raise ValueError('logo.png must be PNG, no larger than 4096px.')
        icon=original.convert('RGBA')
        if icon.width!=icon.height:
            raise ValueError('logo.png must be square.')
        destination.parent.mkdir(parents=True,exist_ok=True)
        icon.save(destination,format='ICO',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    return destination

if __name__=='__main__':
    build()
    print('Application icon rebuilt from logo.png. Tray icon unchanged.')
