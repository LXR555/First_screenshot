---
name: screenshot
version: 1.1.0
display_name: 本机截图与打码
display_name_en: Local Screenshot & Masking
description: 在本机（Windows）直接截取屏幕内容并做打码/圈注，供 Agent 取证、汇报配图、发给客户前遮挡敏感信息。支持全屏（多显示器合并）、指定显示器、区域、前台窗口、按标题匹配窗口（被遮挡也能拍）、列出窗口与显示器六种模式；支持延迟截图、等比例缩放、JPEG 压缩、复制到剪贴板（可直接 Ctrl+V 粘进微信/钉钉）、连拍；打码支持纯色遮盖、马赛克、高斯模糊、红框圈注，坐标可用像素或百分比。触发场景：(1) 用户说"截个图/截屏/截图给我看看/把这一屏拍下来"，(2) 需要把某个窗口（订单系统、报价表、浏览器、报错框）的画面保存成图片，(3) 截图前要遮掉手机号、客户姓名、金额、报价等敏感字段，(4) 在图上圈出重点区域，(5) 汇报/文档/PPT 需要配屏图，(6) 需要列出当前打开了哪些窗口及其坐标尺寸。不用于生成图片（用 ImageGen）、不用于网页整页滚动长截图。另有 GUI 常驻截图入口（snapbar.py）：默认以图标常驻**任务栏通知区域**，**按 F9（全局热键，任何程序前台时都生效）或左键单击图标**即进入全屏框选，拖拽框选后自动进剪贴板，回到输入框 Ctrl+V 即可发给 AI；可选吸附 WorkBuddy 窗口旁的悬浮按钮形态（--float）；任务栏/开始菜单快捷方式带 --snap，点击即截图（已在运行则唤醒，不重复起进程）。
description_zh: Windows 本机截图 + 打码技能：全屏/多屏/区域/前台窗口/标题匹配窗口/窗口清单六种模式，支持延迟、缩放、JPEG 压缩、剪贴板、连拍；打码支持纯色遮盖、马赛克、模糊、红框圈注。用于取证、汇报配图、外发前遮挡手机号/客户名/金额。
description_en: Windows local screenshot and masking skill. Six modes (full screen, specific monitor, region, foreground window, window-by-title, window/monitor listing), plus delay, resize, JPEG compression, clipboard copy and burst capture. Masking supports solid fill, mosaic, blur and highlight boxes for redacting phone numbers, client names and amounts before sending out.
agent_created: true
---

# 本机截图与打码 Skill

在本机直接截屏、必要时打码后交付。截图本身用 Python `ctypes` 调 Windows GDI 完成，**不依赖任何第三方截图软件**；缩放/JPEG/打码用 Pillow。

## 运行环境

**换机 / 分发给别的电脑：先跑一次准备脚本**（自动挑带 tkinter 的 Python、建隔离环境、装依赖、登记路径）：

```bash
python "<技能目录>/scripts/setup_env.py" --wb     # 装到 ~/.workbuddy/.../envs/tkgui（技能内用）
python "<技能目录>/scripts/setup_env.py"          # 装到 %LOCALAPPDATA%\WorkBuddySnap\env（独立使用）
python "<技能目录>/scripts/setup_env.py" --check  # 只体检，不改动任何文件
```

它把解释器路径写进 `%LOCALAPPDATA%\WorkBuddySnap\env.json`，`install_shortcut.py` 会自动读它。
**换机后不要手改本文里的绝对路径**，以 `env.json` 为准。

本机（Windows 11 / 用户 Administrator）现状：

| 用途 | 解释器 |
| --- | --- |
| 脚本与打码（`shot.py` / `mask.py`） | `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（Pillow 12.x） |
| GUI（`snapbar.py` / `install_shortcut.py`） | `...\envs\tkgui\Scripts\python.exe`（tkinter 8.6 + Pillow + pystray + pywin32）；启动用同目录 `pythonw.exe` |

受管 3.13.12 编译时没带 tkinter，所以 GUI 必须用 tkgui 环境；两套环境都已由 `setup_env.py` 登记。

若提示缺少 Pillow：`<解释器> -m pip install pillow`。

**必须用 Bash 工具直接调用该解释器**，不要用 PowerShell 调 Python、也不要写 `.ps1`——本机杀软 AMSI 会把含 `Add-Type`+`DllImport` 的 PowerShell 脚本判为恶意内容直接拦截（已实测，报错为"此脚本包含恶意内容"）。

## 快速开始

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
S="C:/Users/Administrator/.workbuddy/skills/screenshot/scripts/shot.py"

# 全屏（多显示器合并）
"$PY" "$S" --mode full --tag desktop

# 某窗口（标题关键词匹配，被遮挡也能拍；最常用）
"$PY" "$S" --mode window --title 订单 --tag order

# 截完立刻可粘贴：压缩 + 进剪贴板
"$PY" "$S" --mode window --title 报价 --format jpg --width 1600 --clipboard
```

每次执行都会在 stdout 打出一行 JSON，**务必解析 `files[0].path` 拿到真实落盘路径**再交付/引用，不要自己拼路径。

```json
{"ok": true, "mode": "full", "format": "png", "outdir": "...", "window": "",
 "has_pillow": true, "clipboard": null,
 "files": [{"path": "C:\\...\\full_20261017_015217_full.png", "width": 2560, "height": 1440, "bytes": 370294}]}
```

失败时返回 `{"ok": false, "error": "..."}`（退出码 2 多为"未找到窗口/序号越界"）。

## 模式选择

| 模式 | 用途 | 关键参数 |
| --- | --- | --- |
| `full` | 全屏，多显示器合并为一张 | 无 |
| `screen` | 只截某一个显示器 | `--monitor N`（从 1 开始，先用 `list` 查） |
| `region` | 截一块固定区域 | `--region x,y,w,h`（桌面绝对坐标，可为负） |
| `active` | 截当前前台窗口 | 无（建议先 `--delay 2` 再切过去） |
| `window` | 按标题关键词匹配窗口 | `--title 关键词` 或 `--handle 句柄` |
| `list` | 列出显示器 + 所有可见窗口（标题/句柄/坐标/尺寸） | 不改动任何文件 |

判断顺序：用户点名了窗口 → `window`；说"这一屏/整个屏" → `full`；给了坐标或"左上角那块" → `region`；不确定有什么窗口 → 先 `list`。

## 全部参数（shot.py）

| 参数 | 说明 |
| --- | --- |
| `--mode` | 六种模式，默认 `full` |
| `--monitor N` | `screen` 模式的显示器序号 |
| `--region x,y,w,h` | `region` 模式的矩形（桌面绝对坐标） |
| `--title 关键词` | `window` 模式标题包含匹配（不区分大小写） |
| `--handle 句柄` | 直接指定窗口句柄，比标题更准（`list` 可取） |
| `--out 路径` | 指定输出文件（仅单张有效） |
| `--outdir 目录` | 输出目录，默认 `~/Desktop/Screenshots/yyyy-MM-dd/` |
| `--tag 前缀` | 文件名前缀，默认 `shot`；建议英文/拼音，中文用 `--tag-b64` |
| `--tag-b64 B64` | 中文前缀的保险写法：`base64(UTF-8("回款列表"))` |
| `--delay N` | 截图前等 N 秒（切窗口、等页面加载） |
| `--width N` | 等比例缩放到宽度 N（只缩不放） |
| `--format png\|jpg` | 默认 png；jpg 体积小得多，外发/贴聊天窗优先 jpg |
| `--quality N` | JPEG 质量，默认 85 |
| `--clipboard` | 同时放进剪贴板，可直接 Ctrl+V |
| `--no-print-window` | 窗口截图强制改用屏幕拷贝（PrintWindow 拍不全时用） |
| `--count N` / `--interval S` | 连拍 N 张、间隔 S 秒 |

## 打码 / 圈注（mask.py）

外发前必做的一件事：把手机号、客户姓名、金额、报价遮掉。坐标默认按像素（图片左上角为原点），加 `--pct` 后按百分比 0-100 写，**省去换算，推荐**。

```bash
M="C:/Users/Administrator/.workbuddy/skills/screenshot/scripts/mask.py"

# 遮掉左上那块 + 把重点区域打马赛克并加红框
"$PY" "$M" --in "{截图路径}" --fill "10%,8%,30%,6%" --pct \
    --mosaic "55%,20%,35%,12%" --pct --box "55%,20%,35%,12%" --pct
```

| 参数 | 作用 |
| --- | --- |
| `--fill "x,y,w,h"` | 纯色遮盖（默认黑），**遮盖敏感信息首选，不可逆** |
| `--mosaic "x,y,w,h"` | 马赛克，`--mosaic-size` 控制块大小（默认 12） |
| `--blur "x,y,w,h"` | 高斯模糊，`--blur-radius` 控制强度（默认 12） |
| `--box "x,y,w,h"` | 红框圈注，`--stroke` / `--line-width` 控制样式 |
| `--pct` | 坐标按百分比解释 |
| `--out 路径` | 默认输出 `<原名>_masked.png` |

多个区域重复传同名参数即可（`--fill a --fill b`）。**默认输出 PNG，不覆盖原图。**

## 场景与命名规范

见 `references/usecases.md`（取证/汇报/外发三类场景的具体做法、命名规则、打码尺度）。

## GUI 形态：任务栏托盘（默认）／悬浮按钮（可选）

给「人」用的常驻入口。两种形态，**默认托盘**——它就在任务栏上，且不依赖 WorkBuddy 是否打开。

| 形态 | 启动 | 长什么样 | 怎么用 |
| --- | --- | --- | --- |
| **tray（默认）** | `snapbar.py` | 图标常驻**任务栏通知区域**（蓝底白相机） | **按 F9 = 立刻框选**（全局热键，任何程序前台时都生效）；也可左键单击图标；右键 = 菜单（框选 / 截整屏 / 打开文件夹 / 创建或删除快捷方式自启 / 退出） |
| `--float` | `snapbar.py --float` | 置顶圆形按钮，吸附 WorkBuddy 右下角（输入框一侧）并跟随窗口 | 左键单击 = 框选；拖动 = 挪位置；右键 = 菜单 |

`--snap` = 「拉起即框选」，供任务栏/开始菜单快捷方式使用：已有实例在跑就通过命名事件通知它立刻截图，本进程随即退出；没有实例才拉起托盘并马上进入框选（实测唤醒→遮罩出现 0.66 s）。

**全局热键**：默认 `F9`（`F1`~`F12` 可配）。实现见 `snapbar._start_hotkey`：`RegisterHotKey(hwnd=None)` + **独立线程跑 `GetMessageW`**——hwnd 传 NULL 时 WM_HOTKEY 是 post 给「注册它的那个线程」，而 Tk 的 mainloop 会先 `PeekMessage` 把这条线程消息吃掉又没有窗口过程接，**在主线程注册永远收不到**。热键触发后只做 `queue.put(start_snip)` 回主线程。
配置：`%LOCALAPPDATA%\WorkBuddySnap\env.json` 的 `"hotkey"`（写 `""`/`off`/`none` 即禁用；缺省 F9）；临时换键用 `--hotkey F10`，本次禁用用 `--no-hotkey`。注册失败（错误码 1409＝被别的软件占用）会弹窗提示并写日志，托盘点击不受影响。

框选时的交互（两形态一致）：左键拖拽框选 · 松开完成 · `ESC`/右键取消 · 双击截整屏 · 贴边 12px 自动吸附满屏 · 60 秒无操作自动取消。
截图完成：① 剪贴板（`CF_DIB`）② 存档 `<SAVE_ROOT>/yyyy-MM-dd/snip_*.png` ③ 固定路径 `%LOCALAPPDATA%\WorkBuddySnap\last.png`（**Agent 要读"最近一张截图"直接读这个路径**）；随后自动把焦点还给 WorkBuddy，`Ctrl+V` 即可粘贴。日志：同目录 `snapbar.log`。
`<SAVE_ROOT>` 默认 `桌面\Screenshots`，可用 `env.json` 的 `"save_root"` 改成任意目录（支持 `%USERPROFILE%` 这类变量，父目录不存在会自动建）；本次指定用 `--outdir`。

### 启动与安装

```bash
PYG="C:/Users/Administrator/.workbuddy/binaries/python/envs/tkgui/Scripts/python.exe"
PYW="C:/Users/Administrator/.workbuddy/binaries/python/envs/tkgui/Scripts/pythonw.exe"

# 首次/换机：准备环境（自动找带 tkinter 的 Python + 建 venv + 装依赖 + 写 env.json）
"$PYG" ".../scripts/setup_env.py" --wb

# 常驻（托盘形态，后台无控制台）
"$PYW" ".../scripts/snapbar.py"

# 唤起一次框选（任务栏/开始菜单快捷方式用的就是这个参数）
"$PYW" ".../scripts/snapbar.py" --snap

# 安装/删除入口：桌面 + 开始菜单（--snap）、启动文件夹（开机自启托盘）
"$PYW" ".../scripts/snapbar.py" --install      # 等价于 install_shortcut.py（会弹提示框告知结果）
"$PYW" ".../scripts/snapbar.py" --uninstall    # 撤销
"$PYW" ".../scripts/snapbar.py" --install --dry-run   # 只预览将创建哪些入口，不写盘
"$PYG" ".../scripts/install_shortcut.py" [--no-autostart] [--remove] [--reveal] [--print]

# 自检
"$PYG" ".../scripts/snapbar.py" --selftest "1304,217,600,420"
"$PYG" ".../scripts/snapbar.py" --demo          # 遮罩视觉演示（屏幕会闪 1.3 秒）
"$PYG" ".../scripts/snapbar.py" --help          # 完整参数说明
```

无控制台（`pythonw.exe` / 打包 exe）时结果通过**系统消息框**展示；批处理里可设 `SNAPTOOL_NO_MSGBOX=1` 跳过弹窗（仍写日志）。

### 分发给别人

三种形态与对方要做的步骤见 **`references/分发与安装.md`**：

| 对方情况 | 给什么 | 对方做什么 |
| --- | --- | --- |
| 只有 Windows，没装 Python（多数同事） | 免安装单文件 `截图工具.exe`（PyInstaller 打包，约 19 MB） | 解压 → 双击 → 完事；想要桌面图标/自启就跑一次 `截图工具.exe --install` |
| 会用 WorkBuddy | 技能包 zip | 解压到 `~/.workbuddy/skills/screenshot/` → `setup_env.py --wb` |
| 会 Python | 源码脚本 | `setup_env.py` → `snapbar.py --install` |

**随包文档**：免安装版带 `README.txt`（使用说明）+ `LICENSE.txt`（许可）；技能包内为 `README.md` + `LICENSE.txt`。自有代码 MIT，第三方组件（CPython / pystray / Pillow / pywin32 / PyInstaller）许可声明见 `LICENSE.txt`，转发给别人时**两个文件都要带上**。

exe 打包命令（本机复现）：

```bash
cd <临时目录>
"$PYG" -m PyInstaller --noconfirm --clean --onefile --noconsole --name SnapTool \
  --icon "<技能>/assets/snapbar.ico" \
  --paths "<技能>/scripts" --add-data "<技能>/assets;assets" \
  --hidden-import shot --hidden-import pystray._win32 \
  "<技能>/scripts/snapbar.py"
# 产物 dist/SnapTool.exe → 改名为 截图工具.exe
```

### 固定到任务栏（Windows 11 需手动一次）

Win11 **没有**开放程序化固定接口：`ShellExecuteW("taskbarpin", lnk)` 返回 42（找不到该动词），Shell COM 动词列表里也只有「固定到"开始"」，实测均无效。因此：

1. 默认用**托盘常驻**满足"任务栏上随时能点"——图标已在通知区域；
2. 若图标被 Win11 收进「隐藏的图标」弹层：把 `HKCU\Control Panel\NotifyIconSettings\<key>\IsPromoted` 设为 `1`，重启 `explorer.exe` 生效（`<key>` 由 explorer 生成，凭 `ExecutablePath` 匹配 `pythonw.exe` 找到）；
3. 想要任务栏上一个「点了就截图」的应用图标：开始菜单搜索「截图」→ 右键 → 更多 → **固定到任务栏**（手动一次即可）。

### 实现要点

抓屏/窗口枚举/剪贴板全部 import 同目录 `shot.py`；只有遮罩绘制用 tkinter。变暗效果是「全屏原图铺底 + 4 块 `stipple` 网点黑矩形围出选区」，拖动时只挪矩形、不重建位图，2560×1440 下不卡。托盘图标由 `make_tray_icon()` 按 32px 单独重画（直接缩 256px 大图会糊），落盘 `assets\tray.png`。单实例检测与唤醒共用一条命名事件 `Local\WorkBuddySnapBarWake`（自动重置），比"查窗口 + 命名互斥体"可靠。托盘菜单与事件等待都在别的线程，**必须经 `queue` 回主线程**再碰 tkinter。

## 坑与硬约束

| 现象 | 处理 |
| --- | --- |
| PowerShell 脚本被拦"包含恶意内容" | 本机 AMSI 误判，改走 Python 脚本；不要尝试关闭杀软 |
| 目标窗口是最小化状态 | 先还原窗口再截，最小化时拍不到内容（坐标为 -32000） |
| `window` 模式拍到全黑/全白/残缺 | 已自动回退屏幕拷贝；若仍不对，加 `--no-print-window` 并先把窗口切到前台 |
| 截图里带进了 WorkBuddy 自己的窗口 | 用 `window` 模式按标题拍，或 `--delay 3` 后手动切到目标窗口 |
| 高 DPI（125%/150%）下尺寸与坐标对不上 | 已自动开 DPI 感知，坐标为物理像素；`region` 坐标请从 `list` 输出的窗口坐标推算 |
| 外发图太大（>2MB） | 加 `--format jpg --width 1600` |
| 微信/钉钉里想直接粘贴 | 加 `--clipboard`，截图会自动进剪贴板（CF_DIB） |
| 没装 Pillow | 会退回 BMP 输出（体积大、缩放失效），按上文装 Pillow |
| 需要网页整页长截图 | 本技能只拍可视区域；长图请用浏览器自带"捕获整页"或专用工具 |
| 悬浮按钮外圈有方形底色 | Canvas/Toplevel 背景必须用穿透色（`CHROMA="#FF00FE"` + `-transparentcolor`），否则圆外露出方底 |
| 框选时把按钮自己也拍进图 | 进入框选前先 `withdraw()` 按钮窗并留 90 ms 让窗口真正消失，再抓屏 |
| 遮罩图上"尺寸标签"只有空框没有字 | Canvas 里背景框必须 `tag_lower(bg, text)`，用 `tag_raise` 会把文字盖住 |
| 命令行里出现 `WScript.Shell` 被安全策略拦截 | 不要写在 `python -c` 里；把创建快捷方式的代码写成 `.py` 文件再执行（见 `install_shortcut.py`） |
| Win11 想自动「固定到任务栏」 | 官方**无此接口**：`ShellExecuteW("taskbarpin", lnk)` 返回 42、Shell COM 动词里只有「固定到"开始"」，实测均无效 → 改用托盘常驻 + 手动固定一次 |
| 新加的托盘图标在任务栏上看不见 | 被 Win11 收进「隐藏的图标」弹层：设 `HKCU\Control Panel\NotifyIconSettings\<key>\IsPromoted=1`，再重启 `explorer.exe`；`<key>` 凭 `ExecutablePath` 含 `pythonw.exe` 定位 |
| 托盘/事件回调里直接操作 tkinter | 会随机崩（Tk 非线程安全）：回调只 `queue.put(fn)`，主线程用 `after(80)` 的泵取出执行 |
| 看到两个 `pythonw.exe` 进程 | 正常现象：venv 的 `pythonw.exe` 是 redirector，会再拉起 base 解释器；判重靠命名事件，不要按进程数判断 |
| 想改 WorkBuddy 界面里的按钮 | 官方无 UI 注入点，不要改 `app.asar`（更新会被覆盖、可能启动失败）；用本技能的托盘/悬浮入口 |
| 别人拿去用，命令里的绝对路径全失效 | 不要把本机路径当接口：换机先跑 `setup_env.py`，脚本统一从 `env.json` 取解释器；资源路径一律由 `__file__` / `sys._MEIPASS` 推导 |
| 截图跑到一个凭空出现的 `Desktop` 目录 | 桌面被 OneDrive 重定向了。**不要用 `~\Desktop` 拼路径**，改用 `shot.known_folder("Desktop")`（走 `SHGetKnownFolderPath`） |
| 打包 exe 后任务管理器出现两个同名进程 | PyInstaller onefile 的正常现象：父进程负责解包、子进程跑代码。判重靠命名事件，不要数进程 |
| exe 分发后被杀软报毒 | PyInstaller 单文件包的常见误报（不联网、不写系统目录）。让用户加排除项，或改用技能包/源码形态；**不要让用户关杀软** |
| 想替换正在运行的 exe 报"设备或资源忙" | exe 被锁定属正常：先退出托盘实例（托盘右键 → 退出）再覆盖 |
| 给同事的 `.txt` 说明在记事本里是乱码 | Windows 记事本按本地代码页猜编码，含中文的 `.txt` 必须存 **UTF-8 带 BOM**（Python 用 `encoding='utf-8-sig'`） |
| 传了不认识的命令行参数 | 会静默落到默认托盘形态。要预览行为用 `--install --dry-run`，或先 `--help` |
| 在主线程注册热键，按了没反应也不报错 | hwnd=NULL 的 WM_HOTKEY 是**投给注册它的线程**；Tk mainloop 的 `PeekMessage` 会先把它吃掉、又没有窗口过程接。必须另起线程 `RegisterHotKey` + `GetMessageW` |
| 想验证热键是否真的注册成功（键被注入不进去时） | 从**另一个进程**抢注同一个键：返回 `False` + 错误码 **1409** 就说明本工具确实持有它；对照组换个空闲键（如 F10）应注册成功。这比模拟按键可靠——沙箱里 `SendInput` 会返回 1 但系统输入队列不认 |
| 想验证热键的「投递→分发」而不依赖真实按键 | `PostThreadMessageW(热键线程tid, WM_HOTKEY=0x0312, 0x5301, 0)`。坑：`Popen` 拿到的 pid 不一定等于真实进程 pid（venv 的 `pythonw` 会再拉一层），按 pid 枚举线程会扑空；**从日志里读应用自己报的 tid** 最稳 |
| 改完代码 `os.startfile` 启动的托盘过几秒就没了 | 在沙箱/作业对象里启动的子进程会随调用结束被回收；`CREATE_BREAKAWAY_FROM_JOB` 通常被拒（WinError 5），`schtasks` 若被黑名单拦截也不要绕。**结论：由用户双击一次快捷方式**，之后开机自启会接管 |

红线：截图里若含客户姓名、报价、居间费、付款回单等 L2 敏感信息，**外发前必须先打码**；未打码的图只留在本地，不上传云端、不进任何在线 AI 对话。
