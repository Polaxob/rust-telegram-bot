"""Генерация иконки бота RustyTrack 512x512."""
import math
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 512

img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
px = img.load()

# ─── Радиальный градиент фона: тёмный антрацит с ржавым оттенком ───
c1 = (30, 26, 24)   # центр — тёмный тёплый
c2 = (12, 10, 10)   # края — почти чёрный
cx = cy = SIZE / 2
max_r = SIZE / 2 * 1.2
for y in range(SIZE):
    for x in range(SIZE):
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy) / max_r
        d = min(d, 1.0)
        r = int(c1[0] + (c2[0] - c1[0]) * d)
        g = int(c1[1] + (c2[1] - c1[1]) * d)
        b = int(c1[2] + (c2[2] - c1[2]) * d)
        px[x, y] = (r, g, b)

# ─── Окружность радара ───
draw = ImageDraw.Draw(img)
RAD = 178
ORANGE = (255, 106, 0)
ORANGE_SOFT = (255, 155, 60)
DIM = (255, 106, 0, 90)

# Внешний ободок
draw.ellipse([cx - RAD - 14, cy - RAD - 14, cx + RAD + 14, cy + RAD + 14],
             outline=ORANGE, width=6)
# Основной круг
draw.ellipse([cx - RAD, cy - RAD, cx + RAD, cy + RAD], outline=(90, 80, 74), width=3)
# Внутренние кольца
for k, w in ((0.66, 2), (0.33, 2)):
    r_ = RAD * k
    draw.ellipse([cx - r_, cy - r_, cx + r_, cy + r_], outline=(70, 62, 58), width=w)

# Крест прицела
draw.line([cx - RAD, cy, cx + RAD, cy], fill=(70, 62, 58), width=2)
draw.line([cx, cy - RAD, cx, cy + RAD], fill=(70, 62, 58), width=2)

# ─── Развёртка радара (сектор) ───
sweep_angle = 62
sweep_rot = 48  # наклон сектора
overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)
# Полупрозрачный сектор-конус
od.pieslice([cx - RAD, cy - RAD, cx + RAD, cy + RAD],
            start=-sweep_rot, end=-(sweep_rot - sweep_angle),
            fill=(255, 106, 0, 55))
# Яркая линия развёртки
a = math.radians(-sweep_rot)
edx = cx + (RAD - 6) * math.cos(a)
edy = cy + (RAD - 6) * math.sin(a)
od.line([cx, cy, edx, edy], fill=(255, 165, 60, 255), width=5)
img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
draw = ImageDraw.Draw(img)

# Точки-цели на радаре (засечки)
from PIL import ImageDraw as _D
import random
random.seed(42)

def target(x, y, big=False):
    col = ORANGE if big else (255, 180, 90)
    r_ = 9 if big else 6
    draw.ellipse([x - r_, y - r_, x + r_, y + r_], outline=col, width=3)
    draw.ellipse([x - r_ - 4, y - r_ - 4, x + r_ + 4, y + r_ + 4], outline=col, width=1)

# Цель в центре — активная
target(cx, cy, big=True)

# ─── Буква "R" в центре ───
try:
    font = ImageFont.truetype("C:\\Windows\\Fonts\\arialbd.ttf", 128)
except Exception:
    font = ImageFont.load_default()

r = font.getbbox("R")
tw, th = r[2] - r[0], r[3] - r[1]
tx = cx - tw / 2 - r[0]
ty = cy - th / 2 - r[1]
text_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
td = ImageDraw.Draw(text_layer)
td.text((tx, ty), "R", font=font, fill=(255, 165, 60, 255))
# Лёгкая тень
shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
sd.text((tx + 3, ty + 4), "R", font=font, fill=(0, 0, 0, 160))
shadow = shadow.filter(ImageFilter.GaussianBlur(4))
img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGBA")
img = Image.alpha_composite(img, text_layer).convert("RGB")

# ─── Плавное затухание краёв под круглый аватар ───
mask = Image.new("L", (SIZE, SIZE), 0)
md = ImageDraw.Draw(mask)
md.ellipse([0, 0, SIZE, SIZE], fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(1))

final = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
final.paste(img, (0, 0), mask)

final.save("D:\\rust-telegram-bot\\assets\\RustyTrack.png")
print("Saved: D:\\rust-telegram-bot\\assets\\RustyTrack.png")
print(final.size, final.mode)