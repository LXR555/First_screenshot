# -*- coding: utf-8 -*-
"""安装 / 更新截图工具的入口快捷方式（可移植版，任何 Windows 用户都能跑）。

三种入口：
  ① 桌面「截图.lnk」      参数 --snap  → 点一下 = 立刻框选（已在跑则唤醒）
  ② 开始菜单「截图.lnk」  参数 --snap  → 右键可「固定到任务栏 / 固定到开始屏幕」
  ③ 启动文件夹「截图托盘.lnk」无参数   → 开机自动常驻托盘（--no-autostart 可跳过）

两种运行形态（自动识别，无需手工指定）：
  · 源码态：快捷方式指向 <pythonw.exe> + <scripts\\snapbar.py>
  · 冻结态：快捷方式指向 <截图工具.exe> 本身（PyInstaller 打包后）

用法：
    python install_shortcut.py                 # 安装（含开机自启）
    python install_shortcut.py --no-autostart  # 不装开机自启
    python install_shortcut.py --remove        # 全部卸载
    python install_shortcut.py --reveal        # 打开开始菜单目录（方便拖到任务栏）
    python install_shortcut.py --print         # 只打印将创建的入口，不写任何东西
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
FROZEN = bool(getattr(sys, "frozen", False))


def _menu_dir() -> str:
    """开始菜单 Programs 目录（优先系统已知文件夹，兼容重定向）。"""
    try:
        sys.path.insert(0, HERE)
        import shot
        p = shot.known_folder("Programs")
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    return os.path.join(os.environ.get("APPDATA", HOME),
                        r"Microsoft\Windows\Start Menu\Programs")


def _desktop_dir() -> str:
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import shot
        p = shot.known_folder("Desktop")
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    return os.path.join(HOME, "Desktop")


def _find_pythonw() -> str:
    """找一个带 tkinter 的 pythonw.exe。

    优先级：① setup_env.py 登记的 env.json（换机后由它统一指定解释器）
            ② 当前解释器同目录的 pythonw.exe
    """
    try:
        import json
        ej = os.path.join(os.environ.get("LOCALAPPDATA", HOME), "WorkBuddySnap", "env.json")
        with open(ej, encoding="utf-8") as f:
            p = json.load(f).get("pythonw", "")
        if p and os.path.exists(p):
            return p
    except Exception:
        pass

    cands = []
    exe = sys.executable or ""
    if exe:
        d = os.path.dirname(exe)
        if os.path.basename(exe).lower() == "python.exe":
            cands.append(os.path.join(d, "pythonw.exe"))
        cands.append(exe)
    for name in ("pythonw.exe", "python3w.exe"):
        cands.append(os.path.join(HERE, name))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return exe


class Entry:
    """一个待创建的快捷方式。"""

    def __init__(self, path: str, args: str, desc: str):
        self.path = path
        self.args = args
        self.desc = desc


def build_entries(autostart: bool = True) -> tuple[str, str, str, list]:
    """返回 (target, workdir, icon, entries)。"""
    if FROZEN:
        target = sys.executable                       # 打包后的 exe 自身
        workdir = os.path.dirname(target)
        script_args = ""
    else:
        target = _find_pythonw()
        workdir = HERE
        script_args = f'"{os.path.join(HERE, "snapbar.py")}"'

    ico = os.path.join(SKILL_ROOT, "assets", "snapbar.ico")
    icon = ico if os.path.exists(ico) else target

    menu = _menu_dir()
    entries = [
        Entry(os.path.join(_desktop_dir(), "截图.lnk"), f"{script_args} --snap",
              "截图：单击即框选（结果自动进剪贴板，回输入框 Ctrl+V）"),
        Entry(os.path.join(menu, "截图.lnk"), f"{script_args} --snap",
              "截图：单击即框选（可右键固定到任务栏）"),
    ]
    if autostart:
        entries.append(Entry(os.path.join(menu, "Startup", "截图托盘.lnk"),
                             script_args,
                             "开机常驻：截图托盘图标（任务栏通知区域，单击即框选）"))
    return target, workdir, icon, entries


def _make(shell, target: str, workdir: str, icon: str, e: Entry) -> bool:
    os.makedirs(os.path.dirname(e.path), exist_ok=True)
    lnk = shell.CreateShortcut(e.path)
    lnk.TargetPath = target
    lnk.Arguments = e.args
    lnk.WorkingDirectory = workdir
    lnk.IconLocation = icon + ",0"
    lnk.Description = e.desc
    lnk.Save()
    ok = os.path.exists(e.path)
    print(f"  {'OK ' if ok else '失败'} {e.path}")
    return ok


def show(target: str, workdir: str, icon: str, entries: list) -> None:
    print("解释器 / 可执行：", target)
    print("工作目录      ：", workdir)
    print("图标          ：", icon)
    print("将创建：")
    for e in entries:
        print(f'  {e.path}   ->   "{target}" {e.args}'.rstrip())


def install(autostart: bool = True) -> None:
    import win32com.client

    target, workdir, icon, entries = build_entries(autostart)
    if not target or not os.path.exists(target):
        print("找不到可用的 pythonw.exe，无法创建快捷方式。")
        print("请先安装 Python（安装时勾选 tcl/tk 与 Add to PATH），再重跑本脚本。")
        return

    shell = win32com.client.Dispatch("WScript.Shell")
    print("快捷方式：")
    for e in entries:
        _make(shell, target, workdir, icon, e)

    # autostart=False 时，清掉可能存在的旧自启项
    if not autostart:
        boot = os.path.join(_menu_dir(), "Startup", "截图托盘.lnk")
        if os.path.exists(boot):
            os.remove(boot)
            print("  已移除开机自启：", boot)

    print()
    print("想让任务栏上也有一个「点了就截图」的图标（Windows 11 未开放程序化接口，需手动一次）：")
    print("  · 开始菜单搜索「截图」→ 右键 → 更多 → 固定到任务栏")
    print("  · 之后单击任务栏图标即立刻框选")


def remove() -> None:
    menu = _menu_dir()
    for p in (os.path.join(_desktop_dir(), "截图.lnk"),
              os.path.join(_desktop_dir(), "截图按钮.lnk"),   # 旧版命名，一并清掉
              os.path.join(menu, "截图.lnk"),
              os.path.join(menu, "Startup", "截图托盘.lnk")):
        if os.path.exists(p):
            os.remove(p)
            print("已删除:", p)


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    elif "--reveal" in sys.argv:
        os.startfile(_menu_dir())  # noqa: S606
        print("已打开:", _menu_dir())
    elif "--print" in sys.argv:
        show(*build_entries("--no-autostart" not in sys.argv))
    else:
        install(autostart="--no-autostart" not in sys.argv)
