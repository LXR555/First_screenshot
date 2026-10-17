# -*- coding: utf-8 -*-
"""首次使用准备：在任意 Windows 机器上把运行环境配好（换机/分发给同事用）。

做四件事：
  1) 找一个「带 tkinter」的 Python（缺 tkinter 的解释器跑不了框选界面，必须筛掉）
  2) 在隔离目录建虚拟环境，不污染系统 Python
  3) 装依赖：Pillow（图像）、pystray（托盘）、pywin32（建快捷方式）
  4) 把结果写进 %LOCALAPPDATA%\\WorkBuddySnap\\env.json，
     之后 snapbar.py / install_shortcut.py 会自动读它找到正确解释器

用法（在本文件所在目录执行）：
    python setup_env.py            # 装到 %LOCALAPPDATA%\\WorkBuddySnap\\env（普通用户/独立使用）
    python setup_env.py --wb       # 装到 ~/.workbuddy/binaries/python/envs/tkgui（WorkBuddy 技能内使用）
    python setup_env.py --check    # 只体检，不改动任何文件
    python setup_env.py --force    # 环境已存在也重建

产物：
    env.json  { python, pythonw, venv, deps, base }
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WorkBuddySnap")
ENV_JSON = os.path.join(APP_DIR, "env.json")
DEPS = ["pillow", "pystray", "pywin32"]

# 各依赖「装没装上」的自检表达式
BUNDLE = {
    "pillow": "import PIL",
    "pystray": "import pystray",
    "pywin32": "import win32com.client",
}
NEED_TK = "import tkinter"


def _run(cmd: list, timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def has_module(py: str, expr: str) -> bool:
    if not py or not os.path.exists(py):
        return False
    code, _ = _run([py, "-c", expr], timeout=60)
    return code == 0


def find_base_python() -> str | None:
    """按「带 tkinter」筛选可用作基底的 Python。"""
    cands: list[str] = []
    if sys.executable:
        cands.append(sys.executable)
    for name in ("python.exe", "python3.exe", "py.exe", "python3.12.exe",
                 "python3.13.exe", "python3.11.exe", "python3.10.exe"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    home = os.path.expanduser("~")
    for d in (r"C:\Python312", r"C:\Python313", r"C:\Python311", r"C:\Python310",
              os.path.join(home, "AppData", "Local", "Programs", "Python")):
        cands.append(os.path.join(d, "python.exe"))
    # `py -3` 启动器（Windows 官方安装器会装）
    w = shutil.which("py.exe")
    if w:
        code, out = _run([w, "-3", "-c", "import sys;print(sys.executable)"], timeout=60)
        if code == 0 and out.strip():
            cands.insert(0, out.strip().splitlines()[-1].strip())

    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        if not os.path.exists(c):
            continue
        if c.lower().endswith("py.exe"):
            continue
        if has_module(c, NEED_TK):
            return c
    return None


def target_venv(wb: bool) -> str:
    if wb:
        return os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries",
                            "python", "envs", "tkgui")
    return os.path.join(APP_DIR, "env")


def venv_python(venv: str) -> str:
    return os.path.join(venv, "Scripts", "python.exe")


def venv_pythonw(venv: str) -> str:
    return os.path.join(venv, "Scripts", "pythonw.exe")


def write_env_json(py: str, pyw: str, venv: str, base: str) -> None:
    deps = {d: has_module(py, BUNDLE[d]) for d in DEPS}
    data = {"python": py, "pythonw": pyw, "venv": venv, "base": base, "deps": deps,
            "python_version": ".".join(str(x) for x in sys.version_info[:3])}
    os.makedirs(APP_DIR, exist_ok=True)
    with open(ENV_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("已写入:", ENV_JSON)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def load_env_json() -> dict:
    try:
        with open(ENV_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def setup(wb: bool = False, force: bool = False) -> int:
    venv = target_venv(wb)
    py = venv_python(venv)
    pyw = venv_pythonw(venv)

    if os.path.exists(py) and not force and has_module(py, NEED_TK) \
            and all(has_module(py, BUNDLE[d]) for d in DEPS):
        print("环境已就绪，无需重建：", venv)
        write_env_json(py, pyw, venv, find_base_python() or "")
        return 0

    base = find_base_python()
    if not base:
        print("✗ 没找到带 tkinter 的 Python。")
        print("  请先安装 Python 3.10+（python.org 官方安装器），")
        print("  安装时务必勾选「tcl/tk and IDLE」与「Add python.exe to PATH」，然后重跑本脚本。")
        return 2
    print("基底解释器:", base)

    if force and os.path.isdir(venv):
        shutil.rmtree(venv, ignore_errors=True)
        print("已清除旧环境")

    if not os.path.exists(py):
        os.makedirs(os.path.dirname(venv), exist_ok=True)
        print("创建虚拟环境:", venv)
        code, out = _run([base, "-m", "venv", venv], timeout=600)
        if code != 0:
            print("✗ 创建虚拟环境失败：\n", out[-1500:])
            return 3

    code, out = _run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                      "--upgrade", "pip"], timeout=600)
    code, out = _run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", *DEPS],
                     timeout=900)
    if code != 0:
        print("✗ 安装依赖失败：\n", out[-1500:])
        return 4

    ok = has_module(py, NEED_TK) and all(has_module(py, BUNDLE[d]) for d in DEPS)
    if not ok:
        print("✗ 依赖校验未通过，请检查网络或代理后重跑。")
        return 5

    print("✓ 环境就绪")
    write_env_json(py, pyw, venv, base)
    return 0


def check() -> int:
    print("当前解释器:", sys.executable)
    print("tkinter    :", "OK" if has_module(sys.executable, NEED_TK) else "缺失")
    for d in DEPS:
        print(f"{d:<10}:", "OK" if has_module(sys.executable, BUNDLE[d]) else "缺失")
    base = find_base_python()
    print("可用基底   :", base or "未找到（需安装带 tkinter 的 Python）")
    ej = load_env_json()
    print("env.json   :", ej or "尚未生成")
    venv = target_venv(True)
    print("技能环境   :", venv, "->", "存在" if os.path.exists(venv_python(venv)) else "不存在")
    return 0 if base else 2


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--check" in args:
        sys.exit(check())
    sys.exit(setup(wb="--wb" in args, force="--force" in args))
