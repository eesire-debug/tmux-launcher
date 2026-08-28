#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Tmux 连接器应用图标 (512 + 256 PNG)。"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageOps

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmux-launcher.png")
S = 512

DEJAVU = "/usr/share/fonts/truetype/dejavu/"

def font(name, size):
    return ImageFont.truetype(DEJAVU + name, size)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# 底板：深蓝渐变 + 大圆角
grad = Image.linear_gradient("L").resize((S, S))
base = ImageOps.colorize(grad, black=(21, 26, 38), white=(37, 44, 60)).convert("RGBA")
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([8, 8, S - 8, S - 8], radius=100, fill=255)
img.paste(base, (0, 0), mask)
d = ImageDraw.Draw(img)

# 窗口主体
WX0, WX1, WY0, WY1 = 36, S - 36, 36, S - 56
d.rounded_rectangle([WX0, WY0, WX1, WY1], radius=26, fill=(26, 32, 46, 255),
                    outline=(64, 74, 96, 255), width=4)

# 标题栏
TB1 = 112
d.rounded_rectangle([WX0 + 4, WY0 + 4, WX1 - 4, TB1], radius=20, fill=(46, 55, 74, 255))
for i, c in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
    d.ellipse([70 + i * 54, 68, 70 + i * 54 + 34, 102], fill=c)
f_title = font("DejaVuSans-Bold.ttf", 30)
d.text((S // 2 - 38, 64), "tmux", font=f_title, fill=(196, 206, 222, 255))

# 内容区：左窗格 + 右窗格(上下两块)
CY0, CY1 = TB1 + 8, WY1 - 50
d.rectangle([WX0 + 8, CY0, 300, CY1], fill=(17, 21, 31, 255), outline=(58, 68, 88, 255), width=3)
RX0, RX1 = 308, WX1 - 8
d.rectangle([RX0, CY0, RX1, (CY0 + CY1) // 2], fill=(17, 21, 31, 255), outline=(58, 68, 88, 255), width=3)
d.rectangle([RX0, (CY0 + CY1) // 2 + 8, RX1, CY1], fill=(17, 21, 31, 255), outline=(58, 68, 88, 255), width=3)

f_mono = font("DejaVuSansMono-Bold.ttf", 30)
GREEN = (46, 204, 113)
# 左窗格提示符 + 绿色块光标
d.text((64, CY0 + 26), "$", font=f_mono, fill=GREEN)
d.rectangle([116, CY0 + 30, 136, CY0 + 58], fill=GREEN)
# 右窗格提示符
d.text((RX0 + 18, CY0 + 20), "$", font=f_mono, fill=(120, 200, 160, 255))
d.text((RX0 + 18, (CY0 + CY1) // 2 + 28), "$", font=f_mono, fill=(120, 200, 160, 255))

# 底部 tmux 状态栏（绿色）
SY0 = CY1 + 10
d.rounded_rectangle([WX0 + 4, SY0, WX1 - 4, WY1 - 8], radius=16, fill=GREEN)
d.rounded_rectangle([54, SY0 + 10, 210, WY1 - 24], radius=10, fill=(16, 38, 28, 255))
f_st = font("DejaVuSansMono-Bold.ttf", 25)
d.text((68, SY0 + 12), "[0] main", font=f_st, fill=GREEN)
d.text((222, SY0 + 12), "1:bash*", font=f_st, fill=(12, 66, 40, 255))
d.text((WX1 - 120, SY0 + 12), "12:34", font=f_st, fill=(12, 66, 40, 255))

img.save(OUT, "PNG")
img.resize((256, 256), Image.LANCZOS).save(OUT.replace(".png", "-256.png"), "PNG")
print("icon saved:", OUT, img.size)
