"""Convert reviewed native Qt captures into bounded website assets.

Captures are input evidence, never generated mock screenshots. This tool does
not launch or change the launcher. Manifest entries identify their exact source.
"""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest',type=Path)
    args=parser.parse_args()
    records=[]
    for item in json.loads(args.manifest.read_text(encoding='utf-8')):
        source=Path(item['source']);target=(ROOT/'site/help-assets'/item['asset']).resolve()
        if not target.is_relative_to(ROOT/'site/help-assets') or target.suffix!='.webp':
            raise ValueError('Invalid screenshot destination')
        image=Image.open(source).convert('RGBA')
        if image.size==(960,600):image=image.crop((8,7,952,589))
        canvas=Image.new('RGBA',image.size,'#fdfcf9')
        canvas.alpha_composite(image)
        image=canvas.convert('RGB')
        image.thumbnail((1200,900),Image.Resampling.LANCZOS)
        image.save(target,'WEBP',quality=88,method=6)
        records.append(dict(asset=item['asset'],source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            width=image.width,height=image.height,bytes=target.stat().st_size))
    print(json.dumps(records,indent=2))

if __name__=='__main__':main()
