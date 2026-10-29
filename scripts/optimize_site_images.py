"""Generate browser-sized site images. Run with Python and Pillow installed."""

from hashlib import sha256
from pathlib import Path
from io import BytesIO
import json
import re

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def asset(stem, image, format, **options):
    buffer = BytesIO()
    image.save(buffer, format=format, **options)
    raw = buffer.getvalue()
    name = f"{stem}-{sha256(raw).hexdigest()[:12]}.{format.lower()}"
    (SITE / name).write_bytes(raw)
    return name, len(raw)


def main():
    image = Image.open(SITE / "launcher-preview.png").convert("RGB")
    images = {}
    for width in (480, image.width):
        resized = image.resize((width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
        images[width] = asset(f"launcher-preview-{width}", resized, "WEBP", quality=90, method=6)
    source = ", ".join(f"/{name} {width}w" for width, (name, _) in images.items())
    picture = (
        '<picture><source type="image/webp" srcset="' + source + '" '
        'sizes="(max-width: 600px) calc(100vw - 48px), (max-width: 900px) min(630px, calc(100vw - 82px)), 560px">'
        '<img fetchpriority="high" decoding="async" src="/launcher-preview.png" width="926" height="505" '
        'alt="Buggcraft 启动器主页，展示游戏登录、版本选择与联机大厅"></picture>'
    )
    icon = Image.open(SITE / "favicon.ico").convert("RGBA")
    favicon, icon_bytes = asset("favicon-32", icon.resize((32, 32), Image.Resampling.LANCZOS), "PNG", optimize=True)
    # Keep the compatibility endpoint and all existing icon sizes, using PNG compression.
    icon.save(SITE / "favicon.ico", format="ICO", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)], bitmap_format="png")
    for path in (SITE / "index.html", ROOT / "frontend/src/site-pages/home.vue"):
        text = path.read_text(encoding="utf-8")
        text, count = re.subn(r'(<div class="preview-window">)(?:<picture>.*?</picture>|<img[^>]*>)', lambda m: m[1] + picture, text)
        assert count == 1, f"Missing unique preview in {path}"
        text = re.sub(r'<link rel="icon"[^>]*>', f'<link rel="icon" type="image/png" sizes="32x32" href="/{favicon}">', text)
        path.write_text(text, encoding="utf-8")
    path = SITE / "download/index.html"
    text = re.sub(r'<link rel="icon"[^>]*>', f'<link rel="icon" type="image/png" sizes="32x32" href="https://bugg.icu/{favicon}">', path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8")
    print(json.dumps({"preview_png_bytes": (SITE / "launcher-preview.png").stat().st_size,
                      "webp": images, "favicon_png": {"file": favicon, "bytes": icon_bytes},
                      "compatibility_ico_bytes": (SITE / "favicon.ico").stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
