#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
snapbar.py - WorkBuddy 手动截图悬浮按钮（框选截图 → 剪贴板 → 回对话 Ctrl+V）

设计要点
--------
WorkBuddy 是打包的 Electron 应用，官方只开放「技能/专家/连接器/资料库」这类对话能力，
没有 UI 注入点。直接改 app.asar 会在应用更新时被覆盖、且可能因完整性校验导致启动失败。
所以本工具用**置顶悬浮按钮**实现同等体验：按钮自动吸附在 WorkBuddy 主窗口右下角
（输入框一侧），点击即进入全屏框选模式，截完自动复制进剪贴板 —— 回到输入框 Ctrl+V
即可作为图片附件发给 AI（已确认 WorkBuddy 输入框的粘贴处理支持 image/* 文件）。

两种形态（`--mode`）
----
tray （默认）系统托盘常驻：图标就在任务栏通知区域，**左键单击 = 立刻框选截图**，
              右键 = 菜单（框选 / 全屏 / 打开文件夹 / 退出）。不依赖 WorkBuddy 是否打开。
float        置顶悬浮按钮：吸附在 WorkBuddy 窗口右下角（输入框一侧）。旧形态，保留可选。

交互
----
唤醒      ：**全局热键 F9**（任何程序前台时按下即可，注册方式见 `_start_hotkey`）
           也可单击托盘图标 / 点桌面·开始菜单快捷方式
框选模式：左键拖拽框选 · 松开完成 · ESC/右键取消 · 双击截整屏 · 60 秒无操作自动取消
完成后  ：图片进剪贴板 + 落盘存档 + 焦点自动切回 WorkBuddy，直接 Ctrl+V

保存位置
----
默认 桌面\\Screenshots\\<日期>\\snip_*.png；可用 env.json 的 `save_root` 改到任意目录
（子目录按日期自动建，父目录不存在会自动创建）。

唤醒（`--snap`）
----
任务栏/开始菜单快捷方式带 `--snap`，点一下即进入框选：
  · 已有实例在跑 → 通过命名事件 `Local\\WorkBuddySnapBarWake` 通知它立刻截图，本进程立即退出
  · 没有实例     → 拉起托盘常驻，并马上进入框选
同一条命名事件同时承担「单实例检测」，无需窗口/互斥体。

依赖：仅标准库 + Pillow（tray 模式另需 pystray）；截图与剪贴板走 ctypes/GDI（复用同目录 shot.py）
运行：用带 tkinter 的解释器执行（见 SKILL.md 的 GUI 版说明），无需控制台时用 pythonw.exe
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import shot  # noqa: E402  复用抓屏 / 窗口枚举 / 剪贴板 / DPI 处理

if sys.stdout is None:  # pythonw.exe 下没有 stdout，避免 print 崩溃
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import tkinter as tk  # noqa: E402
from PIL import Image, ImageTk  # noqa: E402

# ---------------------------------------------------------------------------
# Win32 常量与封装
# ---------------------------------------------------------------------------
user32 = shot.user32

HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
GA_ROOT = 2

user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wt.BOOL
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND

# 资源目录：脚本态＝技能根目录（scripts/..）；PyInstaller 冻结态＝解包目录 _MEIPASS
FROZEN = bool(getattr(sys, "frozen", False))
RES_DIR = getattr(sys, "_MEIPASS", None) or os.path.abspath(os.path.join(HERE, ".."))
ASSETS_DIR = os.path.join(RES_DIR, "assets")
BUNDLED_ICO = os.path.join(ASSETS_DIR, "snapbar.ico")
BUNDLED_TRAY = os.path.join(ASSETS_DIR, "tray.png")

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WorkBuddySnap")
LAST_PNG = os.path.join(APP_DIR, "last.png")
LOG_FILE = os.path.join(APP_DIR, "snapbar.log")
# setup_env.py 写的环境登记；save_root / hotkey 这类用户偏好也放这里（见 references\分发与安装.md）
ENV_JSON = os.path.join(APP_DIR, "env.json")


def load_cfg() -> dict:
    """读 env.json。文件缺失 / JSON 损坏 / 非对象一律当空配置，绝不影响启动。"""
    try:
        with open(ENV_JSON, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _expand(p) -> str:
    """展开 %USERPROFILE% / ~ 并去掉误写的引号与首尾空白。"""
    return os.path.expandvars(os.path.expanduser(str(p))).strip().strip('"').strip("'")


def default_save_root() -> str:
    """默认保存根：桌面\\Screenshots。桌面可能被 OneDrive 重定向，走系统已知文件夹接口。"""
    return os.path.join(shot.known_folder("Desktop"), "Screenshots")


CFG = load_cfg()
# 保存根目录：env.json 里有 save_root 就用它（可写 %USERPROFILE% 这类变量），否则回退桌面
SAVE_ROOT = _expand(CFG["save_root"]) if CFG.get("save_root") else default_save_root()
# 全局热键：env.json 的 hotkey；空字符串 / none / off 表示不注册
HOTKEY_SPEC = _expand(CFG["hotkey"]) if CFG.get("hotkey") is not None else "F9"
# 托盘小图标放可写目录：装到 Program Files 时程序目录是只读的
TRAY_ICON = os.path.join(APP_DIR, "tray.png")
ICON_ICO = BUNDLED_ICO if os.path.exists(BUNDLED_ICO) else ""

BTN = 52          # 按钮直径（像素）
BTN_GAP = 6       # 与 WorkBuddy 窗口右边缘的间距
CARD_H = 58       # 吸附时相对窗口底部的抬高量（贴近输入框）
TARGET_TITLE = "WorkBuddy"
WINDOW_TITLE = "WorkBuddy 截图按钮"   # float 形态的按钮窗口标题
CHROMA = "#FF00FE"  # 穿透色：圆形以外的画布区域设为该色并整窗透明，做出真圆角按钮

WAKE_EVENT = "Local\\WorkBuddySnapBarWake"   # 单实例 + 唤醒通道（自动重置事件）

# 全局热键：RegisterHotKey + 独立消息泵线程（Tk 的 mainloop 会吞掉 hwnd=NULL 的线程消息，
# 所以在主线程注册会收不到 WM_HOTKEY，必须另起一条线程自己 GetMessage）
HOTKEY_ID = 0x5301
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_TABLE = {f"F{i}": 0x6F + i for i in range(1, 13)}   # F1=0x70 … F12=0x7B


def log(msg: str) -> None:
    """无控制台时写日志，便于排查。"""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        if sys.stdout and not sys.stdout.closed:
            print(line, end="")
    except Exception:
        pass


def info_box(text: str, title: str = "截图工具") -> None:
    """系统消息框：无控制台（pythonw.exe / 打包 exe）时唯一可见的输出方式。

    设环境变量 SNAPTOOL_NO_MSGBOX=1 可跳过弹窗（批处理/自动化测试用，仍写日志）。
    """
    log(text.replace("\n", " "))
    if os.environ.get("SNAPTOOL_NO_MSGBOX") == "1":
        return
    try:
        user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_uint]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, text, title, 0x00000040)  # MB_OK | MB_ICONINFORMATION
    except Exception:
        pass


def make_tray_icon(size: int = 32, save: bool = True) -> Image.Image:
    """生成托盘专用小图标（蓝底白相机），并落盘到 assets\\tray.png 复用。

    托盘要的是 16/20/24px 的小图，直接缩 256px 的圆角按钮图会糊，
    所以按目标尺寸重新画一版：无描边、占比更满、笔画更粗。
    """
    from PIL import ImageDraw
    s = size * 4                      # 先 4 倍画再降采样，边缘更干净
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, s - 1, s - 1), fill=(29, 78, 216, 255))
    u = s / 32.0
    d.rounded_rectangle((6 * u, 11 * u, 26 * u, 24 * u), radius=int(3 * u),
                        fill=(255, 255, 255, 255))
    d.rectangle((12 * u, 8 * u, 20 * u, 12 * u), fill=(255, 255, 255, 255))
    d.ellipse((12.5 * u, 14 * u, 19.5 * u, 21 * u), fill=(29, 78, 216, 255))
    im = im.resize((size, size), Image.LANCZOS)
    if save:
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            im.save(TRAY_ICON, "PNG")
        except Exception:
            pass
    return im


def toplevel_hwnd(widget: tk.Misc) -> int:
    """取 Tk 控件的顶层 Win32 窗口句柄（winfo_id 给的是子窗口）。"""
    try:
        h = user32.GetAncestor(wt.HWND(widget.winfo_id()), GA_ROOT)
        return int(h) if h else 0
    except Exception:
        return 0


def place_window(widget: tk.Misc, x: int, y: int, w: int = 0, h: int = 0,
                 topmost: bool = True) -> None:
    """用 SetWindowPos 按屏幕物理坐标摆窗口（绕开 tk geometry 负坐标/多屏的限制）。"""
    hwnd = toplevel_hwnd(widget)
    flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
    if w <= 0 or h <= 0:
        flags |= SWP_NOSIZE
    user32.SetWindowPos(wt.HWND(hwnd), HWND_TOPMOST if topmost else None,
                        int(x), int(y), int(w), int(h), flags)


_KEEP: list = []          # 保持句柄引用，进程退出前不释放


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
    k.CreateEventW.restype = wt.HANDLE
    k.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
    k.OpenEventW.restype = wt.HANDLE
    k.SetEvent.argtypes = [wt.HANDLE]
    k.SetEvent.restype = wt.BOOL
    k.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    k.WaitForSingleObject.restype = wt.DWORD
    return k


def _u32_hotkey():
    """单独拿一份 user32 句柄并声明热键相关签名（use_last_error 才能读到 1409）。"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    u.RegisterHotKey.restype = wt.BOOL
    u.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    u.UnregisterHotKey.restype = wt.BOOL
    u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    u.GetMessageW.restype = ctypes.c_int          # 0=WM_QUIT, -1=错误，不能当 BOOL
    return u


def open_wake_channel() -> tuple[int, bool]:
    """创建（或探测）唤醒事件。

    返回 (句柄, 是否已有实例)。用一条 `Local\\` 自动重置事件同时承担两件事：
      · 单实例检测 —— ERROR_ALREADY_EXISTS(183) 说明已有实例在跑；
      · 唤醒通道   —— 后来者 SetEvent 即可让已有实例立刻截图。
    比「查窗口 + 命名互斥体」更可靠（`Global\\` 前缀在普通权限下可能创建失败）。
    """
    try:
        k32 = _k32()
        ctypes.set_last_error(0)
        h = k32.CreateEventW(None, False, False, WAKE_EVENT)
        err = ctypes.get_last_error()
        if not h:
            return 0, False
        _KEEP.append(h)
        return int(h), err == 183
    except Exception as exc:
        log(f"事件通道创建失败（退化为不检测单实例）: {exc}")
        return 0, False


def ping_wake_channel() -> bool:
    """通知已有实例「立刻截图」。"""
    try:
        k32 = _k32()
        EVENT_MODIFY_STATE = 0x0002
        h = k32.OpenEventW(EVENT_MODIFY_STATE, False, WAKE_EVENT)
        if not h:
            return False
        ok = bool(k32.SetEvent(h))
        k32.CloseHandle(h)
        return ok
    except Exception as exc:
        log(f"唤醒失败: {exc}")
        return False


def float_button_alive() -> bool:
    """旧 float 形态实例是否在跑（事件通道之外的兜底）。"""
    try:
        return bool(user32.FindWindowW(None, WINDOW_TITLE))
    except Exception:
        return False


def workbuddy_rect() -> dict | None:
    """WorkBuddy 主窗口的物理坐标；最小化时返回 None。"""
    try:
        w = shot.find_window(TARGET_TITLE)
    except Exception:
        return None
    if not w:
        return None
    if w["x"] < -30000 or w["width"] < 200 or w["height"] < 200:
        return None
    return w


def refocus_workbuddy() -> None:
    """截图完成后把焦点还给 WorkBuddy，方便直接 Ctrl+V。"""
    w = workbuddy_rect()
    if w:
        try:
            user32.SetForegroundWindow(wt.HWND(w["handle"]))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 提示气泡
# ---------------------------------------------------------------------------
class Toast:
    """截图完成后的小提示，1.8 秒自动消失。"""

    def __init__(self, master: tk.Tk, text: str, x: int, y: int):
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#111827")
        lbl = tk.Label(self.win, text=text, bg="#111827", fg="#E5E7EB",
                       font=("Microsoft YaHei UI", 10), padx=14, pady=9)
        lbl.pack()
        self.win.update_idletasks()
        w = self.win.winfo_reqwidth()
        h = self.win.winfo_reqheight()
        place_window(self.win, x, y, w, h)
        self.win.after(1800, self._close)

    def _close(self) -> None:
        try:
            self.win.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 框选遮罩
# ---------------------------------------------------------------------------
class SnipOverlay:
    """全屏半透明遮罩 + 鼠标拖拽框选。

    实现：先抓整屏原图铺底（亮），再压 4 块 stipple 网点黑矩形模拟"整体变暗"，
    选区位置不放遮罩 → 自然露出亮色原图，即"挖亮"效果；拖动时只挪 4 个矩形，
    不重建位图，因此大屏下也不卡。
    """

    EDGE = 12  # 鼠标离屏幕边缘多近算贴边（用于自动吸附满屏）

    def __init__(self, master: tk.Tk, on_done, on_cancel=None):
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.vx, self.vy, self.vw, self.vh = shot.virtual_screen()
        raw, w, h = shot.grab(self.vx, self.vy, self.vw, self.vh)
        self.base = shot.to_image(raw, w, h)          # 全屏原图（PIL）
        if not hasattr(self.base, "crop"):            # 极端情况下退回 BMP 模式
            raise RuntimeError("需要 Pillow 才能使用框选截图")
        self.W, self.H = w, h
        self.x0 = self.y0 = 0
        self.cur = None                               # (x1,y1,x2,y2) 已规范化
        self.dragging = False
        self._alive = True

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{w}x{h}+0+0")

        self.canvas = tk.Canvas(self.win, width=w, height=h, highlightthickness=0,
                                bd=0, cursor="crosshair", bg="#000000")
        self.canvas.pack(fill="both", expand=True)

        self._photo = ImageTk.PhotoImage(self.base)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        # 4 块遮罩（stipple 模拟半透明），初始覆盖全屏
        self.shade = [self._shade_rect() for _ in range(4)]
        # 选区描边 + 四角手柄 + 尺寸标签 + 操作提示
        self.outline = self.canvas.create_rectangle(0, 0, 0, 0, outline="#3B82F6",
                                                    width=2, state="hidden")
        self.outline2 = self.canvas.create_rectangle(0, 0, 0, 0, outline="#FFFFFF",
                                                     width=1, state="hidden", dash=(4, 3))
        self.handles = [self.canvas.create_rectangle(0, 0, 0, 0, fill="#3B82F6",
                                                     outline="#FFFFFF", state="hidden")
                        for _ in range(4)]
        self.size_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="#111827",
                                                    outline="#3B82F6", state="hidden")
        self.size_tx = self.canvas.create_text(0, 0, text="", fill="#F9FAFB",
                                               font=("Microsoft YaHei UI", 10, "bold"),
                                               state="hidden", anchor="nw")
        self.hint = self.canvas.create_text(
            self.W // 2, self.H - 34,
            text="拖拽框选区域　·　松开完成　·　双击截整屏　·　ESC / 右键取消",
            fill="#E5E7EB", font=("Microsoft YaHei UI", 11),
            anchor="s")
        self.hint_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill="#111827",
                                                    outline="", stipple="gray50")
        bb = self.canvas.bbox(self.hint)
        if bb:
            self.canvas.coords(self.hint_bg, bb[0] - 18, bb[1] - 10, bb[2] + 18, bb[3] + 10)
            self.canvas.tag_lower(self.hint_bg, self.hint)

        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._full)
        self.canvas.bind("<Button-3>", lambda e: self._cancel())
        self.win.bind("<Escape>", lambda e: self._cancel())

        place_window(self.win, self.vx, self.vy, self.W, self.H)
        self.win.lift()
        self.win.focus_force()
        self.win.grab_set()
        # 极端情况下（焦点被抢）兜底的自动退出
        self.win.after(60000, self._timeout)

    # -- 绘制辅助 ---------------------------------------------------------
    def _shade_rect(self):
        return self.canvas.create_rectangle(0, 0, 0, 0, fill="#000000",
                                            outline="", stipple="gray75")

    def _layout_shade(self, a=None) -> None:
        """4 块遮罩围出选区；a=None 表示整屏变暗。"""
        if a is None:
            x1, y1, x2, y2 = 0, 0, 0, 0
            rects = [(0, 0, self.W, self.H), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)]
        else:
            x1, y1, x2, y2 = a
            rects = [
                (0, 0, self.W, y1),            # 上
                (0, y2, self.W, self.H),       # 下
                (0, y1, x1, y2),               # 左
                (x2, y1, self.W, y2),          # 右
            ]
        for r, (ax1, ay1, ax2, ay2) in zip(self.shade, rects):
            self.canvas.coords(r, ax1, ay1, ax2, ay2)

    def _draw_selection(self, x1, y1, x2, y2) -> None:
        self.canvas.coords(self.outline, x1, y1, x2, y2)
        self.canvas.coords(self.outline2, x1 + 1, y1 + 1, x2 - 1, y2 - 1)
        for h, (px, py) in zip(self.handles, [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]):
            self.canvas.coords(h, px - 4, py - 4, px + 4, py + 4)
        for item in (self.outline, self.outline2, self.size_bg, self.size_tx, *self.handles):
            self.canvas.itemconfigure(item, state="normal")
        for item in (self.hint, self.hint_bg):
            self.canvas.itemconfigure(item, state="hidden")

        w, hh = x2 - x1, y2 - y1
        text = f"{w} × {hh} px"
        ly = y1 - 34
        if ly < 4:                       # 顶部空间不够就放选区内部
            ly = y1 + 8
        self.canvas.coords(self.size_tx, x1 + 10, ly + 6)
        self.canvas.itemconfigure(self.size_tx, text=text, anchor="nw")
        bb = self.canvas.bbox(self.size_tx)
        if bb:
            self.canvas.coords(self.size_bg, bb[0] - 7, bb[1] - 5, bb[2] + 7, bb[3] + 5)
            self.canvas.tag_lower(self.size_bg, self.size_tx)   # 背景必须在文字之下

    # -- 鼠标事件 ---------------------------------------------------------
    def _press(self, e) -> None:
        self.dragging = True
        self.x0, self.y0 = e.x, e.y
        self.cur = None
        self._layout_shade(None)

    def _norm(self, e) -> tuple[int, int, int, int]:
        x1, x2 = sorted((self.x0, e.x))
        y1, y2 = sorted((self.y0, e.y))
        # 贴边自动吸附满屏，便于整屏/整块截取
        if x1 <= self.EDGE:
            x1 = 0
        if y1 <= self.EDGE:
            y1 = 0
        if x2 >= self.W - self.EDGE:
            x2 = self.W
        if y2 >= self.H - self.EDGE:
            y2 = self.H
        return x1, y1, x2, y2

    def _move(self, e) -> None:
        if not self.dragging:
            return
        self.cur = self._norm(e)
        self._layout_shade(self.cur)
        self._draw_selection(*self.cur)

    def _release(self, e) -> None:
        if not self.dragging:
            return
        self.dragging = False
        self.cur = self._norm(e)
        x1, y1, x2, y2 = self.cur
        if x2 - x1 < 4 or y2 - y1 < 4:      # 误点：视为取消
            self._cancel()
            return
        self._finish(self.cur)

    def _full(self, _e=None) -> None:
        self._finish((0, 0, self.W, self.H))

    def _timeout(self) -> None:
        if self._alive:
            self._cancel()

    def _finish(self, rect) -> None:
        x1, y1, x2, y2 = rect
        img = self.base.crop(rect)
        self._close()
        try:
            self.on_done(img, (x1 + self.vx, y1 + self.vy, x2 + self.vx, y2 + self.vy))
        except Exception as exc:
            log(f"回调失败: {exc}")

    def _cancel(self) -> None:
        self._close()
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass

    def _close(self) -> None:
        if not self._alive:
            return
        self._alive = False
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 悬浮按钮
# ---------------------------------------------------------------------------
class SnapBar:
    def __init__(self, save_dir: str | None = None, mode: str = "tray",
                 hotkey: str | None = None):
        self.save_dir = save_dir
        self.mode = mode              # "tray"（任务栏托盘，默认）| "float"（吸附 WorkBuddy 的悬浮按钮）
        self.hotkey = HOTKEY_SPEC if hotkey is None else hotkey
        self.follow = (mode == "float")
        self.busy = False
        self.win = None               # 仅 float 形态有
        self.tray = None
        self.q: queue.Queue = queue.Queue()   # 托盘/事件线程 → Tk 主线程 的请求队列

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(WINDOW_TITLE)

        dpi = 96.0
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
        except Exception:
            pass
        self.scale = max(1.0, dpi / 96.0)

        if mode == "float":
            self._build_float()
            self._follow_once()
            self.root.after(400, self._tick)

        self._pump()                  # 启动队列泵
        if mode == "tray":
            self._build_tray()
        self._start_wake_listener()
        self._start_hotkey()

    # -- 形态构建 ---------------------------------------------------------
    def _build_float(self) -> None:
        """悬浮按钮形态：吸附在 WorkBuddy 右下角（输入框一侧）。"""
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=CHROMA)
        self.btn = int(BTN * self.scale)
        self.c = tk.Canvas(self.win, width=self.btn, height=self.btn,
                           highlightthickness=0, bd=0, bg=CHROMA)
        self.c.pack()
        try:
            self.win.attributes("-transparentcolor", CHROMA)
        except Exception as exc:      # 个别主题不支持时退化为方底
            log(f"透明色不受支持，按钮为方形底: {exc}")
        self._draw_button(hover=False)
        self._bind_events()

        self.menu = tk.Menu(self.root, tearoff=0, font=("Microsoft YaHei UI", 9))
        self.menu.add_command(label="框选截图", command=self.start_snip)
        self.menu.add_command(label="打开截图文件夹", command=self.open_folder)
        self.menu.add_separator()
        self.menu.add_command(label="重新吸附到 WorkBuddy", command=self.reattach)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)

    def _build_tray(self) -> None:
        """托盘形态：图标常驻任务栏通知区域，左键单击即框选。"""
        try:
            import pystray
            from pystray import Menu, MenuItem
        except Exception as exc:
            log(f"托盘库缺失（pip install pystray），退化为无界面常驻: {exc}")
            return
        hk = (self.hotkey or "").strip().upper()
        hk = hk if hk in VK_TABLE else ""
        hint = f"{hk} / 单击图标" if hk else "单击图标"
        self.tray = pystray.Icon(
            "WorkBuddySnap",
            self._tray_image(),
            f"截图 —— {hint}即框选",
            menu=Menu(
                MenuItem(f"框选截图（{hint}）",
                         lambda *_: self.q.put(self.start_snip), default=True),
                MenuItem("截取整个屏幕", lambda *_: self.q.put(self.snip_full)),
                MenuItem("打开截图文件夹", lambda *_: self.q.put(self.open_folder)),
                Menu.SEPARATOR,
                MenuItem("创建桌面快捷方式 / 开机自启",
                         lambda *_: self.q.put(self.install_entries)),
                MenuItem("删除快捷方式与开机自启",
                         lambda *_: self.q.put(self.remove_entries)),
                Menu.SEPARATOR,
                MenuItem("退出", lambda *_: self.q.put(self.quit)),
            ))
        threading.Thread(target=self._run_tray, daemon=True, name="snapbar-tray").start()
        log("托盘图标已启动（位置：任务栏通知区域）")

    def _run_tray(self) -> None:
        try:
            self.tray.run()
        except Exception as exc:
            log(f"托盘运行异常: {exc}")

    def _tray_image(self) -> Image.Image:
        for cand in (TRAY_ICON, BUNDLED_TRAY):
            try:
                if cand and os.path.exists(cand):
                    return Image.open(cand).convert("RGBA")
            except Exception:
                continue
        return make_tray_icon()

    # -- 跨线程请求泵（任务栏托盘 / 唤醒事件 → Tk 主线程）-----------------
    def _pump(self) -> None:
        while True:
            try:
                fn = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:
                log(f"任务执行异常: {type(exc).__name__}: {exc}")
        self.root.after(80, self._pump)

    def _start_wake_listener(self) -> None:
        """后台等待唤醒事件：任务栏/开始菜单快捷方式（带 --snap）点击时触发。

        必须走队列回主线程 —— tkinter 控件只能在主线程碰。
        """
        h, _ = open_wake_channel()
        if not h:
            return

        def loop() -> None:
            k32 = _k32()
            while True:
                try:
                    r = k32.WaitForSingleObject(wt.HANDLE(h), 0xFFFFFFFF)
                except Exception:
                    return
                if r != 0:            # WAIT_FAILED / 句柄失效
                    return
                log("收到唤醒信号：立即进入框选")
                self.q.put(self.start_snip)

        threading.Thread(target=loop, daemon=True, name="snapbar-wake").start()

    def _start_hotkey(self) -> None:
        """全局热键：任何程序前台时按下都能进框选。

        必须在**独立线程**里 RegisterHotKey + GetMessage：hwnd 传 NULL 时 WM_HOTKEY 是
        post 给「注册该热键的线程」，而 Tk 的 mainloop 会先 PeekMessage 把这条线程消息
        消费掉、又没有窗口过程接，导致主线程注册永远收不到。
        """
        spec = (self.hotkey or "").strip()
        if not spec or spec.lower() in ("none", "off", "no", "0", "false"):
            log("全局热键：已禁用（env.json 的 hotkey 为空，或启动时带 --no-hotkey）")
            return
        vk = VK_TABLE.get(spec.upper())
        if not vk:
            log(f"全局热键：不认识的键名 {spec!r}（支持 F1~F12），已跳过")
            return

        def loop() -> None:
            try:
                u32 = _u32_hotkey()
            except Exception as exc:
                log(f"全局热键初始化失败: {exc}")
                return
            try:
                ctypes.set_last_error(0)
                ok = u32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, vk)
            except Exception as exc:
                log(f"全局热键注册异常: {exc}")
                return
            if not ok:
                err = ctypes.get_last_error()
                log(f"全局热键 {spec} 注册失败（错误码 {err}；1409=已被其他程序占用）")
                self.q.put(lambda: self.msgbox(
                    f"全局热键 {spec} 注册失败，可能被其他软件占用了。\n\n"
                    "托盘图标单击仍然可以截图。\n"
                    "想换一个键：在 env.json 把 hotkey 改成 F10 等后重启本程序。", "截图工具"))
                return
            log(f"全局热键已注册：{spec}（任何程序前台时按下即进入框选）")

            msg = wt.MSG()
            try:
                log("热键线程 tid=%d 进入消息循环"
                    % ctypes.WinDLL("kernel32").GetCurrentThreadId())
                while True:
                    r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if r == 0 or r < 0:        # WM_QUIT / 出错
                        break
                    if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                        log(f"全局热键 {spec} 触发 → 进入框选")
                        self.q.put(self.start_snip)   # 回 Tk 主线程执行
            except Exception as exc:
                log(f"热键消息循环异常: {type(exc).__name__}: {exc}")
            try:
                u32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                pass
            log("全局热键线程退出")

        threading.Thread(target=loop, daemon=True, name="snapbar-hotkey").start()

    # -- 绘制 -------------------------------------------------------------
    def _draw_button(self, hover: bool) -> None:
        s = self.btn
        self.c.delete("all")
        pad = 1
        bg = "#2563EB" if hover else "#1D4ED8"
        ring = "#93C5FD" if hover else "#3B82F6"
        self.c.create_oval(pad, pad, s - pad, s - pad, fill=bg, outline=ring,
                           width=max(1, int(1.6 * self.scale)))
        cx = cy = s / 2
        u = s / 52.0        # 以 52 像素为基准缩放图形
        # 相机机身
        self.c.create_rectangle(cx - 14 * u, cy - 8 * u, cx + 14 * u, cy + 10 * u,
                                fill="white", outline="")
        # 取景框凸起
        self.c.create_rectangle(cx - 6 * u, cy - 12 * u, cx + 3 * u, cy - 8 * u,
                                fill="white", outline="")
        # 镜头
        self.c.create_oval(cx - 6 * u, cy - 4 * u, cx + 6 * u, cy + 7 * u,
                           fill=bg, outline="")
        self.c.create_oval(cx - 2 * u, cy - 1 * u, cx + 2 * u, cy + 3 * u,
                           fill="white", outline="")

    # -- 交互 -------------------------------------------------------------
    def _bind_events(self) -> None:
        self.c.bind("<Enter>", lambda e: self._draw_button(hover=True))
        self.c.bind("<Leave>", lambda e: self._draw_button(hover=False))
        self.c.bind("<Button-1>", self._on_press)
        self.c.bind("<B1-Motion>", self._on_drag)
        self.c.bind("<ButtonRelease-1>", self._on_release)
        self.c.bind("<Button-3>", self._on_menu)
        self.c.bind("<Double-Button-1>", lambda e: None)
        self.root.bind("<F1>", lambda e: self.start_snip())

    def _on_press(self, e) -> None:
        self._press_xy = (e.x_root, e.y_root)
        try:
            self._press_geo = (self.win.winfo_x(), self.win.winfo_y())
        except Exception:
            self._press_geo = (0, 0)
        self._moved = False

    def _on_drag(self, e) -> None:
        if not hasattr(self, "_press_xy"):
            return
        dx = e.x_root - self._press_xy[0]
        dy = e.y_root - self._press_xy[1]
        if abs(dx) > 4 or abs(dy) > 4:
            if not self._moved:
                self._moved = True
                self.follow = False        # 手动挪过就不再自动吸附
            place_window(self.win, self._press_geo[0] + dx, self._press_geo[1] + dy,
                         self.btn, self.btn)

    def _on_release(self, e) -> None:
        if not getattr(self, "_moved", False):
            self.start_snip()

    def _on_menu(self, e) -> None:
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # -- 吸附跟随 ---------------------------------------------------------
    def reattach(self) -> None:
        self.follow = True
        self._follow_once()

    def _follow_once(self) -> None:
        w = workbuddy_rect()
        if not w:
            place_window(self.win, 40, 40, self.btn, self.btn)
            return
        x = w["x"] + w["width"] + BTN_GAP
        y = w["y"] + w["height"] - self.btn - int(CARD_H * self.scale)
        vx, vy, vw, vh = shot.virtual_screen()
        if x + self.btn > vx + vw:          # 右侧放不下 → 贴窗口内部右下角
            x = w["x"] + w["width"] - self.btn - int(14 * self.scale)
        if y < 0:
            y = 0
        if y + self.btn > vy + vh:
            y = vy + vh - self.btn - 4
        place_window(self.win, x, y, self.btn, self.btn)

    def _tick(self) -> None:
        try:
            if self.follow and not self.busy:
                if workbuddy_rect() is None:
                    if self.win.state() != "withdrawn":
                        self.win.withdraw()
                else:
                    if self.win.state() == "withdrawn":
                        self.win.deiconify()
                    self._follow_once()
        except Exception as exc:
            log(f"跟随异常: {exc}")
        self.root.after(400, self._tick)

    # -- 截图主流程 -------------------------------------------------------
    def _hide_self(self) -> None:
        """float 形态下先把自己藏起来，避免把按钮本身拍进图里。"""
        if self.win is None:
            return
        try:
            self.win.withdraw()
            self.root.update_idletasks()
            time.sleep(0.09)                          # 等窗口真正从屏幕消失
        except Exception:
            pass

    def start_snip(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._hide_self()
        try:
            SnipOverlay(self.root, self._on_done, self._on_cancel)
        except Exception as exc:
            log(f"进入框选失败: {exc}")
            self._restore()

    def snip_full(self) -> None:
        """托盘菜单：跳过框选，直接截整个屏幕（多屏合并）。"""
        if self.busy:
            return
        self.busy = True
        try:
            self._hide_self()
            vx, vy, vw, vh = shot.virtual_screen()
            raw, w, h = shot.grab(vx, vy, vw, vh)
            self._on_done(shot.to_image(raw, w, h), (vx, vy, vx + vw, vy + vh))
        except Exception as exc:
            log(f"全屏截图失败: {exc}")
            self.busy = False
            self._restore()

    def _on_cancel(self) -> None:
        log("已取消框选")
        self._restore()

    def _on_done(self, img: Image.Image, rect) -> None:
        """截图完成：① 剪贴板 ② 落盘 ③ 写 last.png ④ 提示 ⑤ 焦点还回 WorkBuddy。"""
        w, h = img.size
        result = {"ok": True, "width": w, "height": h, "rect": list(rect)}

        # ① 剪贴板（CF_DIB，自上而下 BGRA）
        try:
            raw = img.convert("RGBA").tobytes("raw", "BGRA")
            result["clipboard"] = bool(shot.copy_to_clipboard(raw, w, h))
        except Exception as exc:
            result["clipboard"] = False
            result["clipboard_error"] = str(exc)

        # ② 落盘
        try:
            outdir = self.save_dir or os.path.join(SAVE_ROOT, datetime.now().strftime("%Y-%m-%d"))
            os.makedirs(outdir, exist_ok=True)
            path = os.path.join(outdir, f"snip_{datetime.now():%Y%m%d_%H%M%S}.png")
            img.save(path, "PNG", optimize=True)
            result["path"] = path
        except Exception as exc:
            result["path"] = ""
            result["save_error"] = str(exc)

        # ③ 固定路径副本，方便 AI/其他程序直接读取最近一张
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            img.save(LAST_PNG, "PNG")
            result["last"] = LAST_PNG
        except Exception:
            pass

        result["bytes"] = os.path.getsize(result["path"]) if result.get("path") else 0
        log("截图完成 " + json.dumps(result, ensure_ascii=False))

        # ④ 提示气泡（float 贴按钮上方 / tray 贴任务栏右下角）
        self._restore()
        tip = f"✅ 已复制 {w}×{h}　回输入框 Ctrl+V 即可发送"
        if not result.get("clipboard"):
            tip = f"已保存 {w}×{h}（剪贴板写入失败）"
        self._toast(tip)

        # ⑤ 焦点还给 WorkBuddy
        self.root.after(60, refocus_workbuddy)

    def _toast(self, text: str) -> None:
        try:
            vx, vy, vw, vh = shot.virtual_screen()
            if self.mode == "float" and self.win is not None:
                bx, by = self.win.winfo_x(), self.win.winfo_y()
                Toast(self.root, text, max(8, bx - 150), max(8, by - 52))
            else:
                Toast(self.root, text, max(8, vx + vw - 440), max(8, vy + vh - 160))
        except Exception as exc:
            log(f"提示气泡失败: {exc}")

    def _restore(self) -> None:
        self.busy = False
        if self.win is None:          # 托盘形态：无按钮需要恢复
            return
        try:
            self.win.deiconify()
        except Exception:
            pass
        if self.follow:
            self._follow_once()

    def open_folder(self) -> None:
        try:
            target = self.save_dir or os.path.join(
                SAVE_ROOT, datetime.now().strftime("%Y-%m-%d"))
            if not os.path.isdir(target):
                target = SAVE_ROOT
            os.startfile(target)  # noqa: S606
        except Exception as exc:
            log(f"打开目录失败: {exc}")

    # -- 给「别人」用的入口（幂等，可反复点）--------------------------------
    def msgbox(self, text: str, title: str = "截图工具") -> None:
        """无控制台（pythonw / exe）时的可见提示。"""
        info_box(text, title)

    def install_entries(self) -> None:
        """创建桌面/开始菜单快捷方式与开机自启（等价于命令行 --install）。"""
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import install_shortcut
            import win32com.client
            target, workdir, icon, entries = install_shortcut.build_entries(True)
            shell = win32com.client.Dispatch("WScript.Shell")
            ok = sum(1 for e in entries
                     if install_shortcut._make(shell, target, workdir, icon, e))
            text = (f"已就绪 {ok}/{len(entries)} 个入口：\n\n"
                    "· 桌面「截图」：点一下立刻框选\n"
                    "· 开始菜单「截图」：右键可固定到任务栏\n"
                    "· 已开启开机自启（重启后托盘图标自动出现）\n\n"
                    "用法：单击任务栏的相机图标 → 拖拽框选 → 结果已进剪贴板，"
                    "任意程序里 Ctrl+V 粘贴。")
        except Exception as exc:
            text = f"创建快捷方式失败：{type(exc).__name__}: {exc}"
        log(text.replace("\n", " "))
        self.msgbox(text)

    def remove_entries(self) -> None:
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import install_shortcut
            install_shortcut.remove()
            text = "已删除桌面/开始菜单快捷方式与开机自启。\n托盘图标仍在运行，右键退出即可。"
        except Exception as exc:
            text = f"卸载失败：{type(exc).__name__}: {exc}"
        self.msgbox(text)

    def quit(self) -> None:
        log(f"退出（形态：{self.mode}）")
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# 无交互自检：验证「抓屏 → 裁剪 → 剪贴板 → 落盘」全链路
# ---------------------------------------------------------------------------
def selftest(region: str) -> int:
    shot.set_dpi_aware()
    parts = [int(v) for v in region.split(",")]
    if len(parts) != 4:
        print(json.dumps({"ok": False, "error": "region 需为 x,y,w,h"}, ensure_ascii=False))
        return 2
    x, y, w, h = parts
    raw, gw, gh = shot.grab(x, y, w, h)
    img = shot.to_image(raw, gw, gh)
    clip = shot.copy_to_clipboard(img.convert("RGBA").tobytes("raw", "BGRA"), gw, gh)
    outdir = os.path.join(SAVE_ROOT, datetime.now().strftime("%Y-%m-%d"))
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(APP_DIR, exist_ok=True)
    path = os.path.join(outdir, f"selftest_{datetime.now():%Y%m%d_%H%M%S}.png")
    img.save(path, "PNG", optimize=True)
    img.save(LAST_PNG, "PNG")
    # 读回剪贴板校验
    clip_read = None
    try:
        user32.OpenClipboard(0)
        try:
            clip_read = bool(user32.IsClipboardFormatAvailable(shot.CF_DIB))
        finally:
            user32.CloseClipboard()
    except Exception:
        pass
    print(json.dumps({"ok": True, "width": gw, "height": gh, "clipboard_written": clip,
                      "clipboard_readback": clip_read, "path": path, "last": LAST_PNG,
                      "bytes": os.path.getsize(path)}, ensure_ascii=False))
    return 0


def demo() -> int:
    """演示/自检遮罩视觉：自动模拟一次拖拽，抓下遮罩画面后取消。"""
    import types
    shot.set_dpi_aware()
    root = tk.Tk()
    root.withdraw()
    state: dict = {}

    def on_done(img, rect):
        state["done"] = (img.size, rect)

    overlay = SnipOverlay(root, on_done, lambda: state.update(cancelled=True))
    vw, vh = overlay.vw, overlay.vh

    def snap(tag):
        raw, w, h = shot.grab(overlay.vx, overlay.vy, vw, vh)
        d = os.path.join(SAVE_ROOT, "_selftest")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"overlay_{tag}_{datetime.now():%H%M%S}.png")
        shot.to_image(raw, w, h).save(p, "PNG", optimize=True)
        state[f"shot_{tag}"] = p

    def ev(x, y):
        return types.SimpleNamespace(x=int(x), y=int(y))

    x1, y1 = int(vw * 0.26), int(vh * 0.30)
    x2, y2 = int(vw * 0.64), int(vh * 0.68)

    root.after(300, lambda: snap("idle"))            # 起始态：应看到底部操作提示
    root.after(500, lambda: overlay._press(ev(x1, y1)))
    root.after(700, lambda: overlay._move(ev((x1 + x2) / 2, y1)))
    root.after(850, lambda: overlay._move(ev(x2, y2)))
    root.after(1200, lambda: snap("select"))         # 选中态：应看到边框/手柄/尺寸标签

    def fin():
        overlay._cancel()
        root.after(200, root.destroy)

    root.after(1450, fin)
    root.mainloop()
    print(json.dumps({"ok": True, **state}, ensure_ascii=False, default=str))
    return 0


USAGE = f"""截图工具 —— 框选截图，结果自动进剪贴板

按 {HOTKEY_SPEC or "（未设热键）"} 直接框选；也可以单击托盘相机图标。
框选中：左键拖拽 · 松开完成 · ESC / 右键取消 · 双击截整屏 · 60 秒无操作自动取消。

命令行参数：
  --install            创建桌面快捷方式、开始菜单项与开机自启（有可见提示）
  --uninstall          删除上述快捷方式与开机自启
  --snap               立即进入框选（快捷方式用；已有实例会被唤醒而不是重复启动）
  --float              改用吸附在 WorkBuddy 窗口旁的悬浮按钮形态
  --outdir <目录>      本次截图存到指定目录（不改配置）
  --hotkey <键名>      临时换热键，如 F10（仅本次运行）
  --no-hotkey          本次运行不注册全局热键
  --selftest x,y,w,h   无交互自检（抓屏→裁剪→剪贴板→落盘）
  --demo               遮罩视觉演示（自动模拟一次拖拽后取消）
  --help               显示本说明

保存位置：{SAVE_ROOT}\\<日期>\\snip_*.png
          （改 env.json 的 "save_root" 可换目录，支持 %USERPROFILE% 这类变量）
全局热键：改 env.json 的 "hotkey"（F1~F12；留空或不写 hotkey 键＝默认 F9；写 "" 或 "off" ＝禁用）
最近一张：%LOCALAPPDATA%\\WorkBuddySnap\\last.png
运行日志：%LOCALAPPDATA%\\WorkBuddySnap\\snapbar.log
"""


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        if FROZEN:
            info_box(USAGE, "截图工具 — 使用说明")
        return 0

    if "--install" in argv or "--uninstall" in argv:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        try:
            import install_shortcut
        except Exception as exc:
            info_box(f"缺少安装模块：{exc}")
            return 1
        if "--uninstall" in argv:
            install_shortcut.remove()
            info_box("已删除桌面快捷方式、开始菜单项与开机自启。\n"
                     "托盘图标仍在运行，右键选「退出」即可关闭。")
        elif "--dry-run" in argv:
            target, workdir, icon, entries = install_shortcut.build_entries(
                "--no-autostart" not in argv)
            lines = [f"解释器/可执行：{target}", f"工作目录：{workdir}", "将创建："]
            lines += [f"· {e.path}  {e.args}".rstrip() for e in entries]
            info_box("\n".join(lines))
        else:
            install_shortcut.install(autostart="--no-autostart" not in argv)
            info_box("已创建：\n\n"
                     "· 桌面「截图」：点一下立刻框选\n"
                     "· 开始菜单「截图」：右键可固定到任务栏\n"
                     "· 开机自启（重启后托盘图标自动出现）\n\n"
                     "想撤销：再运行一次本程序并加 --uninstall，\n"
                     "或在托盘图标上右键选「删除快捷方式与开机自启」。")
        return 0

    if "--demo" in argv:
        return demo()
    if "--selftest" in argv:
        i = argv.index("--selftest")
        region = argv[i + 1] if len(argv) > i + 1 and "," in argv[i + 1] else "0,0,600,400"
        return selftest(region)

    shot.set_dpi_aware()

    mode = "tray"                                  # 默认：任务栏托盘常驻
    if "--float" in argv:                          # 切回吸附 WorkBuddy 的悬浮按钮
        mode = "float"
    if "--mode" in argv:
        j = argv.index("--mode")
        mode = argv[j + 1] if len(argv) > j + 1 else mode
    snap_now = "--snap" in argv                    # 点任务栏图标：拉起即进框选

    save_dir = ""
    if "--outdir" in argv:
        j = argv.index("--outdir")
        save_dir = argv[j + 1] if len(argv) > j + 1 else ""

    hk = HOTKEY_SPEC                          # 来自 env.json，缺省 F9
    if "--no-hotkey" in argv:
        hk = ""
    if "--hotkey" in argv:
        j = argv.index("--hotkey")
        hk = argv[j + 1] if len(argv) > j + 1 else hk

    _h, already = open_wake_channel()
    if already or float_button_alive():
        if snap_now:
            ok = ping_wake_channel()
            log(f"已有实例在运行 → 转交截图指令（送达：{ok}）")
        else:
            log("已有实例在运行，退出")
        return 0

    log(f"启动截图工具（形态：{mode}{'，启动即框选' if snap_now else ''}"
        f"，保存根：{save_dir or SAVE_ROOT}，热键：{hk or '关闭'}）")
    bar = SnapBar(save_dir or None, mode=mode, hotkey=hk)
    if snap_now:
        bar.root.after(300, bar.start_snip)
    bar.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # 结构化错误，便于排查
        log(f"致命错误: {type(exc).__name__}: {exc}")
        sys.exit(1)
