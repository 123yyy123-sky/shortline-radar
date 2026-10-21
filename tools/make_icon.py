"""生成 assets/icon.ico。

画三根向上的红K线 + 一道雷达扫掠弧，配色与界面一致（深底、涨红）。
全部用 Pillow 绘制，无外部素材。

用法：
    python tools/make_icon.py
"""
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'assets')

S = 1024                      # 超采样画布，最后缩到各尺寸
BG_TOP = (22, 30, 43)         # #161E2B
BG_BOT = (11, 15, 21)         # #0B0F15
BORDER = (45, 58, 78)
RED = (255, 77, 79)           # 涨红
RED_DIM = (176, 48, 50)
AMBER = (232, 163, 61)
GREEN = (47, 168, 108)


def rounded_mask(size, radius):
    m = Image.new('L', (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def build():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))

    # 垂直渐变底
    grad = Image.new('RGBA', (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        gd.line([(0, y), (S, y)], fill=c + (255,))
    img.paste(grad, (0, 0), rounded_mask(S, int(S * 0.22)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, S - 4, S - 4], radius=int(S * 0.22),
                        outline=BORDER + (255,), width=max(2, S // 190))

    # 一条极淡的基线，让三根柱子「站」在同一平面上
    d.line([(int(S * 0.13), int(S * 0.815)), (int(S * 0.87), int(S * 0.815))],
           fill=BORDER + (170,), width=max(2, S // 230))

    # 三根红K线：实心矩形为实体，细线为上下影，高度递增
    # (中心x, 实体上沿y, 实体下沿y, 影线上y, 影线下y)
    candles = [
        (0.255, 0.605, 0.735, 0.545, 0.795),
        (0.475, 0.475, 0.665, 0.415, 0.725),
        (0.695, 0.330, 0.560, 0.268, 0.620),
    ]
    bw = int(S * 0.128)                       # 实体宽
    ww = max(3, int(S * 0.026))               # 影线宽
    for i, (cxp, bt, bb, wt, wb) in enumerate(candles):
        x = int(S * cxp)
        col = RED if i == len(candles) - 1 else RED_DIM
        # 影线
        d.rounded_rectangle([x - ww // 2, int(S * wt), x + ww // 2, int(S * wb)],
                            radius=ww // 2, fill=col + (255,))
        # 实体
        d.rounded_rectangle([x - bw // 2, int(S * bt), x + bw // 2, int(S * bb)],
                            radius=int(bw * 0.18), fill=col + (255,))

    # 最后一根顶上点一颗绿点？不 —— 用琥珀色小方块表示「已封板」信号
    bx = int(S * 0.695)
    sq = int(S * 0.052)
    d.rounded_rectangle([bx - sq // 2, int(S * 0.185), bx + sq // 2, int(S * 0.185) + sq],
                        radius=int(sq * 0.3), fill=AMBER + (255,))
    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    big = build()
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    frames = [big.resize((s, s), Image.LANCZOS) for s in sizes]
    out = os.path.join(OUT_DIR, 'icon.ico')
    frames[-1].save(out, format='ICO', sizes=[(s, s) for s in sizes])
    # 附带一张 PNG，README 和截图预览用
    big.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT_DIR, 'icon.png'), format='PNG')
    print(f'icon.ico  {os.path.getsize(out) / 1024:.1f} KB  {sizes}')
    print(f'icon.png  256x256')


if __name__ == '__main__':
    sys.exit(main())
