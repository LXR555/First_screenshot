# screenshot — 本机截图技能

常驻任务栏的截图托盘程序：按 **F9**（或点托盘图标）→ 拖框 → 截图直接进剪贴板，到任何输入框 `Ctrl+V` 即可粘贴。单文件 exe 分发版不需要 Python、不需要管理员权限、不联网。

本目录是**技能包（源码版）**，给需要在 WorkBuddy 中直接调用、或想自己改代码的人；给同事的**免安装分发版**是另一个包（`截图工具.exe` + `README.txt` + `LICENSE.txt`）。

## 目录结构

| 文件/目录 | 作用 |
| --- | --- |
| `SKILL.md` | 技能主文档（加载本技能时的入口说明） |
| `scripts/snapbar.py` | 托盘 + 全局热键 + 框选主程序（入口） |
| `scripts/shot.py` | 抓屏/裁剪/剪贴板/落盘核心逻辑 |
| `scripts/install_shortcut.py` | 快捷方式 / 开机自启安装与卸载 |
| `scripts/setup_env.py` | 构建/运行环境登记（env.json） |
| `scripts/mask.py` | 框选遮罩视觉演示 |
| `assets/` | 托盘图标（snapbar.ico、tray.png） |
| `references/usecases.md` | 使用场景示例 |
| `references/分发与安装.md` | 分发、安装、FAQ |
| `LICENSE.txt` | 许可：自有代码 MIT + 第三方组件声明 |

## 快速开始（源码运行）

```bat
%LOCALAPPDATA%\WorkBuddySnap\env.json  里登记的 pythonw 直接跑：
  <env.json 中的 pythonw> "%~dp0scripts\snapbar.py"
```

或按 `SKILL.md` 的说明，用 PyInstaller 重新打包成单文件 exe：

```bat
pyinstaller --onefile --noconsole --paths scripts ^
  --add-data "assets;assets" --icon assets/snapbar.ico ^
  --hidden-import shot --hidden-import pystray._win32 ^
  scripts/snapbar.py
```

配置集中在 `%LOCALAPPDATA%\WorkBuddySnap\env.json`：`save_root`（保存目录）、`hotkey`（全局热键，默认 F9）。详见 `README` 对应章节与 `references/分发与安装.md`。

## 许可

自有代码 **MIT**（Copyright (c) 2026 刘军平）；依赖组件 CPython / pystray / Pillow / pywin32 / PyInstaller 的许可声明见 [`LICENSE.txt`](LICENSE.txt)。
