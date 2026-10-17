#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mask.py - 截图打码 / 圈注（遮盖敏感信息后再外发）

作用：把截图中的手机号、客户姓名、金额、报价等敏感字段遮掉，或圈注重点。
依赖：Pillow（技能自带的隔离环境已安装）。

坐标：默认用像素（图片左上角为原点），加 --pct 则用百分比 0-100（更省事）。

用法：
  python mask.py --in shot.png --fill "120,40,300,28" --out clean.png
  python mask.py --in shot.png --mosaic "120,40,300,28" --mosaic-size 14
  python mask.py --in shot.png --fill "20%,30%,40%,6%" --pct --box "20%,30%,40%,6%" --pct
  python mask.py --in shot.png --blur "10%,10%,30%,20%" --pct
  多个区域可重复传参：--fill a --fill b
输出：stdout 一行 JSON。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter
except Exception:
    print(json.dumps({"ok": False, "error": "缺少 Pillow。请先安装：python -m pip install pillow"},
                     ensure_ascii=False))
    sys.exit(1)


def parse_rect(s: str, w: int, h: int, pct: bool) -> tuple[int, int, int, int]:
    parts = [p.strip().rstrip("%") for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError(f"区域格式应为 x,y,w,h ：{s}")
    vals = [float(p) for p in parts]
    if pct:
        x = int(round(vals[0] / 100.0 * w))
        y = int(round(vals[1] / 100.0 * h))
        rw = int(round(vals[2] / 100.0 * w))
        rh = int(round(vals[3] / 100.0 * h))
    else:
        x, y, rw, rh = (int(round(v)) for v in vals)
    # 夹到图内
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    rw = max(1, min(rw, w - x))
    rh = max(1, min(rh, h - y))
    return x, y, rw, rh


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="截图打码/圈注工具")
    p.add_argument("--in", dest="src", required=True, help="输入图片")
    p.add_argument("--out", default="", help="输出路径，默认 <原名>_masked.png")
    p.add_argument("--fill", action="append", default=[], help="纯色遮盖区域 x,y,w,h")
    p.add_argument("--mosaic", action="append", default=[], help="马赛克区域 x,y,w,h")
    p.add_argument("--blur", action="append", default=[], help="高斯模糊区域 x,y,w,h")
    p.add_argument("--box", action="append", default=[], help="圈注红框区域 x,y,w,h")
    p.add_argument("--pct", action="store_true", help="坐标按百分比 0-100 解释")
    p.add_argument("--color", default="#000000", help="遮盖填充色")
    p.add_argument("--stroke", default="#FF3B30", help="圈注线条颜色")
    p.add_argument("--line-width", type=int, default=3)
    p.add_argument("--mosaic-size", type=int, default=12, help="马赛克块大小（像素）")
    p.add_argument("--blur-radius", type=float, default=12.0, help="模糊半径")
    args = p.parse_args(argv)

    if not os.path.isfile(args.src):
        print(json.dumps({"ok": False, "error": f"输入文件不存在: {args.src}"}, ensure_ascii=False))
        return 1

    img = Image.open(args.src).convert("RGB")
    w, h = img.size
    applied = []

    # 1) 纯色遮盖 / 马赛克 / 模糊
    fill_rects = [parse_rect(s, w, h, args.pct) for s in args.fill]
    mosaic_rects = [parse_rect(s, w, h, args.pct) for s in args.mosaic]
    blur_rects = [parse_rect(s, w, h, args.pct) for s in args.blur]

    if fill_rects:
        d = ImageDraw.Draw(img)
        for r in fill_rects:
            d.rectangle([r[0], r[1], r[0] + r[2] - 1, r[1] + r[3] - 1], fill=args.color)
            applied.append({"type": "fill", "rect": list(r), "color": args.color})

    for r in mosaic_rects:
        box = (r[0], r[1], r[0] + r[2], r[1] + r[3])
        crop = img.crop(box)
        bw = max(1, crop.width // max(2, args.mosaic_size))
        bh = max(1, crop.height // max(2, args.mosaic_size))
        small = crop.resize((bw, bh), Image.BILINEAR)
        img.paste(small.resize(crop.size, Image.NEAREST), box)
        applied.append({"type": "mosaic", "rect": list(r), "block": args.mosaic_size})

    for r in blur_rects:
        box = (r[0], r[1], r[0] + r[2], r[1] + r[3])
        img.paste(img.crop(box).filter(ImageFilter.GaussianBlur(args.blur_radius)), box)
        applied.append({"type": "blur", "rect": list(r), "radius": args.blur_radius})

    # 2) 圈注
    box_rects = [parse_rect(s, w, h, args.pct) for s in args.box]
    if box_rects:
        d = ImageDraw.Draw(img)
        for r in box_rects:
            d.rectangle([r[0], r[1], r[0] + r[2] - 1, r[1] + r[3] - 1],
                        outline=args.stroke, width=args.line_width)
            applied.append({"type": "box", "rect": list(r), "color": args.stroke})

    out = args.out
    if not out:
        base, _ = os.path.splitext(args.src)
        out = f"{base}_masked.png"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    img.save(out, "PNG", optimize=True)

    print(json.dumps({
        "ok": True,
        "input": os.path.abspath(args.src),
        "output": os.path.abspath(out),
        "width": w, "height": h,
        "applied": applied,
        "bytes": os.path.getsize(out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(1)
