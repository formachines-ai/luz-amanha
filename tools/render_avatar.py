"""One-off renderer: draws public/brand/avatar.svg shapes (hard-coded to match) to PNG. Needs Pillow; not part of build.py."""
from PIL import Image, ImageDraw
S = 8
def render(size):
    k = size / 512 * S
    im = Image.new("RGB", (int(512*k), int(512*k)), "#12263f")
    d = ImageDraw.Draw(im)
    d.pieslice([96*k, 180*k, 416*k, 500*k], 180, 360, fill="#ffb81c")
    d.rounded_rectangle([64*k, 340*k, 448*k, 368*k], 14*k, fill="#f4f1e8")
    c = tuple(round(a*.45 + b*.55) for a, b in zip((0x12, 0x26, 0x3f), (0xf4, 0xf1, 0xe8)))
    d.rounded_rectangle([128*k, 392*k, 384*k, 420*k], 14*k, fill=c)
    return im.resize((size, size), Image.LANCZOS)
for n in (512, 1024):
    render(n).save(f"public/brand/avatar-{n}.png", optimize=True)
