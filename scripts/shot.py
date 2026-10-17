#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
shot.py - 本机截图（Windows，零第三方依赖的可选降级版）

能力：
  full    全屏截图（多显示器合并）
  screen  指定显示器截图（--monitor N，从 1 开始）
  region  区域截图（--region x,y,w,h，桌面绝对坐标，可为负）
  active  前台窗口截图
  window  按标题关键词匹配窗口截图（--title 关键词）——即使被遮挡也能拍（PrintWindow）
  list    列出显示器与可见窗口（含标题/坐标/尺寸），不改动任何文件

输出：stdout 一行 JSON。默认存到 ~/Desktop/Screenshots/yyyy-MM-dd/

依赖：
  截图本身只用 ctypes + 系统 GDI，无需第三方库；
  若安装了 Pillow 则额外支持 JPEG 压缩与等比例缩放，未安装时退回 BMP 输出。

用法：
  python shot.py --mode full --tag desktop
  python shot.py --mode window --title 订单 --format jpg --width 1600 --clipboard
  python shot.py --mode region --region 100,200,800,600
  python shot.py --mode list
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time
from datetime import datetime

# ----------------------------------------------------------------------------
# Pillow 可选
# ----------------------------------------------------------------------------
try:
    from PIL import Image  # type: ignore

    HAS_PIL = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    HAS_PIL = False

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]

SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002
CF_DIB = 8
GMEM_MOVEABLE = 0x0002
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


def known_folder(name: str = "Desktop") -> str:
    r"""取系统「已知文件夹」的真实路径。

    为什么不能直接用 `~/Desktop`：很多机器（尤其装了 OneDrive 的）会把桌面
    重定向到 `C:\\Users\\<用户名>\\OneDrive\\桌面`，写死 `~\Desktop` 会凭空
    建出一个假 Desktop 目录，用户再也找不到自己的截图。
    """
    GUID_MAP = {
        "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
        "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
        "Programs": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",
    }
    guid_s = GUID_MAP.get(name)
    if guid_s:
        try:
            import uuid

            class _GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                            ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

            g = _GUID.from_buffer_copy(uuid.UUID(guid_s).bytes_le)
            shell32 = ctypes.WinDLL("shell32")
            ole32 = ctypes.WinDLL("ole32")
            shell32.SHGetKnownFolderPath.argtypes = [
                ctypes.POINTER(_GUID), ctypes.c_uint32, wt.HANDLE,
                ctypes.POINTER(ctypes.c_wchar_p)]
            shell32.SHGetKnownFolderPath.restype = ctypes.c_long
            p = ctypes.c_wchar_p()
            if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                            ctypes.byref(p)) == 0 and p.value:
                path = p.value
                try:
                    ole32.CoTaskMemFree(p)
                except Exception:
                    pass
                return path
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), name)


def set_dpi_aware() -> None:
    """让坐标与截图像素一致，避免 125%/150% 缩放下尺寸错位。"""
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def virtual_screen() -> tuple[int, int, int, int]:
    gsm = user32.GetSystemMetrics
    return (gsm(SM_XVIRTUALSCREEN), gsm(SM_YVIRTUALSCREEN),
            gsm(SM_CXVIRTUALSCREEN), gsm(SM_CYVIRTUALSCREEN))


def list_monitors() -> list[dict]:
    out: list[dict] = []

    def _cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append({
                "index": len(out) + 1,
                "device": mi.szDevice,
                "primary": bool(mi.dwFlags & 1),
                "x": r.left, "y": r.top,
                "width": r.right - r.left, "height": r.bottom - r.top,
            })
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    out.sort(key=lambda m: m["index"])
    return out


def list_windows() -> list[dict]:
    out: list[dict] = []

    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        title = buf.value.strip()
        if not title:
            return True
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append({
            "title": title,
            "handle": int(hwnd),
            "x": r.left, "y": r.top,
            "width": r.right - r.left, "height": r.bottom - r.top,
        })
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def find_window(needle: str) -> dict | None:
    # 排除最小化窗口（坐标被系统移到 -32000，截不到内容）
    hits = [w for w in list_windows()
            if needle.lower() in w["title"].lower() and w["x"] > -30000 and w["width"] > 0]
    if not hits:
        return None
    hits.sort(key=lambda w: w["width"] * w["height"], reverse=True)
    return hits[0]


def grab(x: int, y: int, w: int, h: int, hwnd: int | None = None,
         print_window: bool = True) -> tuple[bytes, int, int]:
    """抓取一块像素，返回 (BGRA bytes, width, height)。"""
    if w <= 0 or h <= 0:
        raise ValueError(f"截图区域无效: {w}x{h}")
    src = user32.GetDC(0)
    if not src:
        raise OSError("GetDC 失败")
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if hwnd and print_window:
            ok = user32.PrintWindow(wt.HWND(hwnd), mem, PW_RENDERFULLCONTENT)
            if not ok:
                gdi32.BitBlt(mem, 0, 0, w, h, src, x, y, SRCCOPY)
        else:
            gdi32.BitBlt(mem, 0, 0, w, h, src, x, y, SRCCOPY)

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h  # 负值 = 自上而下
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        if got == 0:
            raise OSError("GetDIBits 失败")
        return buf.raw, w, h
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, src)


def to_image(raw: bytes, w: int, h: int):
    if HAS_PIL:
        return Image.frombuffer("RGBA", (w, h), raw, "raw", "BGRA", w * 4, 1).convert("RGB")
    px = bytearray(w * h * 3)
    step = 3
    for i in range(w * h):
        j = i * 4
        k = i * step
        px[k] = raw[j + 2]
        px[k + 1] = raw[j + 1]
        px[k + 2] = raw[j]
    return px


def image_is_blank(raw: bytes, w: int, h: int) -> bool:
    """抽样判断是否为纯色（PrintWindow 对部分 GPU 加速窗口会返回全黑/全白）。"""
    if not HAS_PIL:
        return False
    img = to_image(raw, w, h).resize((48, 48))
    colors = img.getcolors(maxcolors=64)
    return colors is not None and len(colors) <= 1


def save_bmp(path: str, raw: bytes, w: int, h: int) -> None:
    row = w * 4
    body = bytearray()
    for y in range(h - 1, -1, -1):
        body += raw[y * row:(y + 1) * row][: w * 3]  # BGRA -> BGR 紧凑行
        pad = (4 - (w * 3) % 4) % 4
        body += b"\x00" * pad
    size_header = 14 + 40
    with open(path, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", size_header + len(body), 0, 0, size_header))
        f.write(struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(body), 2835, 2835, 0, 0))
        f.write(bytes(body))


def save_image(path: str, img, fmt: str, quality: int, raw: bytes | None = None,
               w: int = 0, h: int = 0) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if HAS_PIL:
        if fmt == "jpg":
            img.save(path, "JPEG", quality=quality, subsampling=0, optimize=True)
        else:
            img.save(path, "PNG", optimize=True)
    else:
        save_bmp(path, raw or b"", w, h)


def copy_to_clipboard(raw: bytes, w: int, h: int) -> bool:
    """把截图放进剪贴板（CF_DIB），可直接 Ctrl+V 粘进微信/钉钉。"""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GlobalAlloc.restype = wt.HGLOBAL
        k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [wt.HGLOBAL]
        k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
        user32.SetClipboardData.restype = wt.HANDLE
        user32.SetClipboardData.argtypes = [ctypes.c_uint, wt.HANDLE]

        header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, w * h * 4, 2835, 2835, 0, 0)
        # CF_DIB 要求自下而上
        row = w * 4
        flipped = b"".join(raw[y * row:(y + 1) * row] for y in range(h - 1, -1, -1))
        data = header + flipped

        if not user32.OpenClipboard(0):
            return False
        try:
            user32.EmptyClipboard()
            hmem = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not hmem:
                return False
            ptr = k32.GlobalLock(hmem)
            ctypes.memmove(ptr, data, len(data))
            k32.GlobalUnlock(hmem)
            if not user32.SetClipboardData(CF_DIB, hmem):
                return False
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Windows 本机截图工具", add_help=True)
    p.add_argument("--mode", default="full",
                   choices=["full", "screen", "region", "active", "window", "list"])
    p.add_argument("--monitor", type=int, default=1, help="screen 模式的显示器序号，从 1 开始")
    p.add_argument("--region", default="", help="region 模式：x,y,w,h（桌面绝对坐标）")
    p.add_argument("--title", default="", help="window 模式：窗口标题关键词")
    p.add_argument("--handle", type=int, default=0, help="直接指定窗口句柄（list 模式可取到）")
    p.add_argument("--out", default="", help="输出文件完整路径（单张时有效）")
    p.add_argument("--outdir", default="", help="输出目录，默认 ~/Desktop/Screenshots/yyyy-MM-dd")
    p.add_argument("--tag", default="shot", help="文件名前缀（建议英文/拼音，中文用 --tag-b64）")
    p.add_argument("--tag-b64", default="", help="base64(UTF-8) 形式的文件名前缀，中文名保险写法")
    p.add_argument("--delay", type=int, default=0, help="截图前等待秒数（切换窗口用）")
    p.add_argument("--width", type=int, default=0, help="等比例缩放到指定宽度（需 Pillow）")
    p.add_argument("--format", default="png", choices=["png", "jpg"])
    p.add_argument("--quality", type=int, default=85, help="JPEG 质量 1-100")
    p.add_argument("--clipboard", action="store_true", help="同时复制到剪贴板")
    p.add_argument("--no-print-window", action="store_true", help="窗口截图强制用屏幕拷贝（不用 PrintWindow）")
    p.add_argument("--count", type=int, default=1, help="连拍张数")
    p.add_argument("--interval", type=float, default=0.0, help="连拍间隔秒")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    set_dpi_aware()

    if args.mode == "list":
        print(json.dumps({
            "ok": True, "mode": "list",
            "virtual": dict(zip(("x", "y", "width", "height"), virtual_screen())),
            "monitors": list_monitors(),
            "windows": list_windows(),
        }, ensure_ascii=False))
        return 0

    tag = args.tag
    if args.tag_b64:
        tag = base64.b64decode(args.tag_b64).decode("utf-8")

    outdir = args.outdir
    if not outdir:
        outdir = os.path.join(known_folder("Desktop"), "Screenshots",
                              datetime.now().strftime("%Y-%m-%d"))
    os.makedirs(outdir, exist_ok=True)

    vx, vy, vw, vh = virtual_screen()
    window_title = ""
    hwnd = None

    if args.mode == "window":
        if args.handle:
            hwnd = args.handle
        else:
            if not args.title:
                raise SystemExit("-Mode window 需要 --title 或 --handle")
            win = find_window(args.title)
            if not win:
                print(json.dumps({"ok": False, "error": f"未找到标题含「{args.title}」的可见窗口"},
                                 ensure_ascii=False))
                return 2
            hwnd, window_title = win["handle"], win["title"]
    elif args.mode == "active":
        hwnd = int(user32.GetForegroundWindow())
        for w in list_windows():
            if w["handle"] == hwnd:
                window_title = w["title"]
                break

    if args.delay > 0:
        time.sleep(args.delay)

    ext = "jpg" if args.format == "jpg" else "png"
    files = []
    clipboard_ok = None

    for i in range(1, max(1, args.count) + 1):
        if i > 1 and args.interval > 0:
            time.sleep(args.interval)

        if args.mode == "full":
            raw, w, h = grab(vx, vy, vw, vh)
        elif args.mode == "screen":
            mons = list_monitors()
            if not 1 <= args.monitor <= len(mons):
                print(json.dumps({"ok": False, "error": f"显示器序号越界: {args.monitor}"},
                                 ensure_ascii=False))
                return 2
            m = mons[args.monitor - 1]
            raw, w, h = grab(m["x"], m["y"], m["width"], m["height"])
        elif args.mode == "region":
            parts = [int(v) for v in args.region.split(",") if v.strip() != ""]
            if len(parts) != 4:
                raise SystemExit("--region 需要 x,y,w,h")
            raw, w, h = grab(parts[0], parts[1], parts[2], parts[3])
        else:  # active / window
            r = wt.RECT()
            if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
                print(json.dumps({"ok": False, "error": "取窗口位置失败（窗口可能已关闭）"},
                                 ensure_ascii=False))
                return 2
            cw, ch = r.right - r.left, r.bottom - r.top
            raw, w, h = grab(r.left, r.top, cw, ch, hwnd=hwnd, print_window=not args.no_print_window)
            # PrintWindow 对 GPU 加速窗口可能返回纯色，回退屏幕拷贝
            if not args.no_print_window and image_is_blank(raw, w, h):
                raw, w, h = grab(r.left, r.top, cw, ch, hwnd=None, print_window=False)

        img = to_image(raw, w, h)
        if args.width and args.width > 0 and HAS_PIL and args.width < w:
            ratio = args.width / w
            img = img.resize((args.width, int(round(h * ratio))), Image.LANCZOS)
            w, h = img.size

        if args.clipboard:
            clipboard_ok = copy_to_clipboard(raw, w, h)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{i}" if args.count > 1 else ""
        path = args.out if (args.out and args.count == 1) else os.path.join(
            outdir, f"{tag}_{stamp}_{args.mode}{suffix}.{ext}")
        save_image(path, img, args.format, args.quality, raw=raw, w=w, h=h)
        files.append({"path": os.path.abspath(path), "width": w, "height": h,
                      "bytes": os.path.getsize(path)})

    print(json.dumps({
        "ok": True, "mode": args.mode, "format": ext, "tag": tag,
        "outdir": os.path.abspath(outdir), "window": window_title,
        "has_pillow": HAS_PIL, "clipboard": clipboard_ok, "files": files,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 让 Agent 拿到结构化错误
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(1)
