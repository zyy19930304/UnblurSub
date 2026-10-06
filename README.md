# UnblurSub

[![Windows](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows&logoColor=white)](https://learn.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![Self-test](https://img.shields.io/badge/selftest-350%2F350-4CAF50)](./selftest.py)

**马赛克去除 + 中文字幕生成的 Windows 批量桌面工具。**

把 [Jasna](https://github.com/Kruk2/jasna)（AI 马赛克修复）与
[Faster-Whisper-TransWithAI-ChickenRice](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice)
（语音转录翻译）整合到一个窗口：选好处理模式，批量导入视频，剩下的交给队列。

---

## 目录

- [处理模式](#处理模式)
- [命名与跳过规则](#命名与跳过规则)
- [特性](#特性)
- [快速开始](#快速开始)
- [Jasna 参数配置](#jasna-参数配置)
- [中间视频产物](#中间视频产物)
- [界面说明](#界面说明)
- [安全性设计](#安全性设计)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [开发与测试](#开发与测试)
- [License](#license)

---

## 处理模式

顶部三个按钮对应三条流水线。这是本工具最核心的设计——**你要什么，就只做什么**，
不做无用功（去马赛克必须重新编码，代价很高，没必要的步骤一律不跑）。

| 模式 | 做什么 | 依赖 | 输出后缀 |
|------|--------|------|---------|
| **1 · 只清除马赛克** | Jasna 修复画面 | Jasna | `-U` |
| **2 · 只生成中文字幕** | Faster Whisper 转录 + 软封装 | Faster Whisper + ffmpeg | `-C` |
| **3 · 清除马赛克 + 生成中文字幕** | 先去马赛克，再给结果加字幕 | 两者都要 | `-UC` |

模式 3 的两阶段串行执行：源文件 →（Jasna）→ 中间视频 →（Faster Whisper + ffmpeg）→ 最终文件。
进度条分段推进（去马赛克约占 0–70%，转录封装占 70–100%）。

> **为什么去马赛克必须重新编码？** 字幕可以 `-c copy` 直接塞进容器（不损失画质），
> 但 AI 修复是逐像素重建画面，物理上无法"复制"。这是去马赛克的固有成本，
> 本工具通过「按需选择模式」把它限制在必要的时候。

切换模式时界面会联动：不需要的参数面板置灰（比如模式 1 下字幕 Tab 不可点），
文件列表的输出名与勾选状态立即重算。

---

## 命名与跳过规则

**后缀语义**：`-U` = 已去马赛克，`-C` = 已加中文字幕，`-UC` = 两者都有。

### 输出命名

| 模式 | 原文件名 | 输出 | 说明 |
|------|---------|------|------|
| 1 | `ABC.mp4` | `ABC-U.mp4` | 加 `-U` |
| 1 | `ABC-C.mp4` | `ABC-UC.mp4` | 已有字幕，加去马赛克后升级为 `-UC` |
| 2 | `ABC.mp4` | `ABC-C.mp4` | 加 `-C` |
| 2 | `ABC-U.mp4` | `ABC-UC.mp4` | 已去马赛克，加字幕后升级为 `-UC` |
| 3 | `ABC.mp4` | `ABC-UC.mp4` | 一律 `-UC` |

### 自动跳过

处理前会检查文件名后缀，已做过的处理不会重复劳动：

| 模式 | 跳过的文件 | 原因 |
|------|-----------|------|
| 1 | `*-U`、`*-UC` | 马赛克已经去过了 |
| 2 | `*-C`、`*-UC` | 中文字幕已经加过了 |
| 3 | `*-U`、`*-C`、`*-UC` | 两项都做过才能是 `-UC`，无法在已处理文件上叠加 |

「自动勾选」按钮按当前模式智能跳过；文件列表里被跳过的文件会显示
**橙色「本模式跳过」标签**并置灰，鼠标悬停可看具体原因。
手选框也能勾——真要强制处理就手动勾，流水线会在日志里记录。

大小写不敏感：`abc-c`、`abc-uc` 同样识别为已处理。

> **规则版本**：v3 起命名与跳过规则由处理模式决定。旧版本（v2 及以前）的
> 勾选记录会**自动作废并按当前模式重算**，无需手动清理。

---

## 特性

| 能力 | 说明 |
|------|------|
| **三模式流水线** | 去马赛克 / 加字幕 / 两者兼有，按需选择，避免无谓的重新编码 |
| **Jasna 全参数集成** | 41 项配置全部可在界面调整，6 个分组，附官方中文说明与取值范围 |
| **配置方案管理** | 字幕参数与 Jasna 参数各自独立「另存为 / 重命名 / 删除」，内置 5 套 Jasna 方案开箱可用 |
| **条件显隐** | 只显示当前选中的二级修复分支（选 RTX 超分就不显示 Topaz 的选项） |
| **依赖自检** | 启动前校验参数合法性：Topaz 路径不存在、重叠帧数越界等会明确报错或警告 |
| **批量处理** | 递归扫描视频文件夹，串行队列一次跑一个，避免显存与内存被打爆 |
| **零重编码封装** | 字幕阶段 `ffmpeg -c:v copy -c:a copy` 软封装，画面与音频原样保留 |
| **中文字幕轨** | 封装时写入 `title=中文字幕` + `language=chi`，播放器直接显示为中文字幕 |
| **命令预览** | 预览任意文件在当前模式下将执行的完整命令链，排查参数问题 |
| **三级窗口降级** | pywebview → Edge `--app` → 系统浏览器，任何环境都能打开 |
| **完整日志** | 记录完整命令行、`infer.exe` / `jasna.exe` 原始输出 |

---

## 快速开始

### 前置依赖

| 依赖 | 用于 | 说明 |
|------|------|------|
| **[Jasna](https://github.com/Kruk2/jasna) 0.10.0** | 模式 1 / 3 | Windows 版解压后目录内含 `jasna.exe` / `model_weights` / `tools`。**官方要求安装路径仅含英文与数字**，且需 NVIDIA GTX 16 系 / RTX 20 系及以上显卡 |
| **Faster-Whisper-TransWithAI-ChickenRice** | 模式 2 / 3 | 目录内含 `infer.exe` / `models` / `generation_config.json5` |
| **ffmpeg** | 模式 2 / 3 | 用于封装字幕。程序自动在 `PATH` 与常见位置查找，也可在设置里指定 |
| **Python 3.10+** | 仅源码运行 | 打包 exe 给同事用时他们**不需要**装 Python |

> Jasna 官方说明：首次运行会为你的显卡编译 TensorRT 引擎，NVIDIA 上需 **15–60 分钟**，
> 期间请关闭其他应用并勿使用电脑。引擎会缓存到 `model_weights`，之后自动复用。

### 运行

```bash
git clone https://github.com/zyy19930304/UnblurSub.git
cd UnblurSub
pip install -r requirements.txt
python app.py
```

首次打开会提示配置两个程序目录：填 Jasna 与 Faster Whisper 所在目录
（**各是含 `jasna.exe` / `infer.exe` 的那一层**），可用「自动查找」。
顶栏三个徽章会实时显示就绪状态。

### 打包成 exe

```bat
build.bat
```

产物在 `dist\UnblurSub\UnblurSub.exe`（约 27 MB）。

> **分发时必须整个 `dist\UnblurSub` 文件夹一起压缩**，不能只发 exe。
> 目标机器无需装 Python，但需 **WebView2 运行时**（Win10/11 自带，Win7 需单独下载）。
> 缺 WebView2 时程序会自动降级用 Edge / 浏览器打开。

`build.bat` **不使用系统 Python，而是自建 `.venv` 独立虚拟环境**：
Windows 上常同时存在多个 Python（Microsoft Store 的占位符、conda、托管环境……），
直接 `pip install` 装到哪个解释器完全取决于 `PATH` 顺序，极不可控。
脚本流程为「定位 Python → 建 venv → 升 pip → 装依赖 → **跑自测（不通过则中止）** → PyInstaller 打包」。

---

## Jasna 参数配置

41 项参数按 6 个分组呈现。**每项都带官方中文说明与取值范围**，数值型参数用
「滑块 + 数字框」组合，既能拖也能精确输入；枚举型全部是下拉框并附中文选项说明。

| 分组 | 关键参数 |
|------|---------|
| **修复模型** | BasicVSR++（通用，快）/ LTX（细节最多，慢）；LTX 种子、快速模式、大马赛克模式 |
| **基本处理** | 最大片段大小（10–720）、检测模型与阈值（0–1）、FP16、TensorRT 编译 |
| **高级处理** | 时间重叠（0–30）、检测间隙/时长、镜头切换检测、交叉淡入淡出、VR180 模式、降噪 |
| **二次修复** | UNet 4x / Topaz TVAI / RTX Super Res，修复区域 256→1024 放大 |
| **编码输出** | HEVC / H.264 / AV1、CQ 质量、锐化、LUT、60→30 FPS、fMP4 |
| **导出后动作** | 队列完成后关机（60 秒倒计时可取消）或执行自定义命令 |

![参数界面](./README.assets/screenshot-params.png)

### 内置配置方案

| 配置名 | 适用场景 |
|--------|---------|
| 标准（1080p 均衡） | 片段 90 + 重叠 8，HEVC。日常 1080p 的平衡点，显存 6GB 起步 |
| 快速（低显存 / 老显卡） | 片段 60 + 关闭模型编译。GTX 16 系 / RTX 20 系建议从这套开始 |
| 高质量（大显存 / 4K） | 片段 180 + RTX 超分 4x。峰值约 14.7GB 显存，建议 16GB 以上 |
| 极速（RTX 超分 2x） | 保持标准速度，同时用 RTX 2x 放大修复区域 |
| 动画 / 2D（YOLO 检测） | Lada YOLO 对 2D 动画定位更准，推荐阈值 0.25 |

### 需要额外准备的选项

这几项依赖外部组件，界面上会明确标注，参数面板顶部也会给出提示：

- **Topaz TVAI** —— 需单独购买安装 Topaz Video AI，路径要手动指定
- **UNet 4x** —— Jasna 支持者专属模型，需有效许可证
- **LTX** —— 需较新的 NVIDIA GPU，可用「试运行」测速度（画面不正确）

---

## 中间视频产物

模式 3 需要一个中间文件（去马赛克后、加字幕前）。它写在**临时工作目录**
（`%APPDATA%\UnblurSub\work\`），封装完成后自动删除，源目录保持干净。

设置里可开启「保留中间视频」，开启后中间文件会**移动到源文件所在文件夹**，
便于中途用播放器人工检查去马赛克效果。注意：

- 保留的是**去马赛克后、加字幕前**的文件（扩展名 `.mkv`）
- 失败任务**不会保留**中间文件
- 默认关闭

---

## 界面说明

```
┌─ 顶栏：Faster Whisper / Jasna / ffmpeg 就绪状态 ─────────────────┐
├─ 处理模式：[只清除马赛克 -U] [只加字幕 -C] [两者 -UC]← 当前模式说明 ─┤
├─ 1 · 视频文件 ───────────────┬─ 2 · 参数配置（Tab 切换）──────┤
│  文件夹添加 / 状态标签       │  [字幕参数] [Jasna 参数]        │
│  全选 自动勾选 反选 清空      │  配置方案下拉 + 加载/另存       │
│  文件列表（输出名/跳过标签）   │  6 分组参数（滑块+下拉+说明）     │
│                              │  依赖自检提示条                 │
├─ 3 · 执行进度与日志 ─────────┴──────────────────────────────┤
│  每个文件的进度条 + 状态   │  实时日志（命令、infer/jasna 输出） │
└───────────────────────────────────────────────────────────┘
```

**快捷操作**

- `Ctrl+Enter` —— 开始处理
- **预览命令** —— 显示当前模式下将执行的完整命令链（模式 3 会显示三步）
- **✕ 退出** —— 落盘设置；参数有修改时会询问是否保存为配置

---

## 安全性设计

- **永不静默覆盖** —— 默认加序号；封装先写 `.part` 临时文件，成功后才 `os.replace` 改名
- **空字幕拦截** —— SRT 解析出 0 条时判失败并清理，不产出「有画面没字幕」的空壳文件
- **不覆盖原文件** —— 源视频只读，输出始终是新文件
- **临时目录必清理** —— 无论成功 / 失败 / 取消，工作目录都在 `finally` 里删掉
- **失败保留证据** —— `infer.exe` / `jasna.exe` 的退出码与原始输出完整进日志
- **可中断** —— 停止时通过 pidfile + `psutil` 定位并终止正在跑的子进程
- **启动前预检** —— 参数有阻断级问题（如 Topaz 路径不存在）时直接拒绝启动，
  不让用户跑到一半才失败

---

## 项目结构

```
UnblurSub/
├── app.py              # 桌面程序入口：参数解析 + 三级窗口降级 + 退出联动
├── server.py           # 本地HTTP 服务 + JSON API（纯标准库 ThreadingHTTPServer）
├── core.py             # 核心逻辑：字幕参数 schema、命名规则、扫描、封装
├── jasna_core.py       # Jasna 集成：参数 schema、命令构造、目录校验、依赖自检
├── jobs.py             # 任务队列与流水线：模式驱动的去马赛克 / 字幕双流程
├── selftest.py         # 350 项断言的自测套件（零外部依赖）
├── build.bat           # 自建 venv + 跑自测 + PyInstaller onedir 打包
├── requirements.txt
└── web/                # 前端（原生 HTML/CSS/JS，无框架无构建）
```

**分层约定**：`core.py` 与 `jasna_core.py` **不依赖任何界面 / 网络库**，
可单独 `import` 做单元测试——这是 `selftest.py` 能零依赖跑起来的前提。
业务逻辑只动这两个文件 + `jobs.py`，前端只通过 `/api/*` 通信。

<details>
<summary>HTTP API 一览（31 个端点）</summary>

```
GET  /api/state              POST /api/start
GET  /api/job/state          POST /api/stop
GET  /api/job/logs           POST /api/scan
GET  /api/profiles           POST /api/add_folder
GET  /api/jasna_profiles     POST /api/remove_folder
GET  /api/drives             POST /api/set_selected
POST /api/browse             POST /api/profile/save
POST /api/save_settings      POST /api/profile/delete
POST /api/preview_cmd        POST /api/profile/rename
POST /api/shutdown_prompt    POST /api/jasna_profile/save
POST /api/shutdown           POST /api/jasna_profile/delete
POST /api/reveal             POST /api/jasna_profile/rename
POST /api/validate_fw        POST /api/jasna_check
POST /api/validate_jasna     POST /api/jasna_preview
POST /api/pick_default_fw    POST /api/job/clear_finished
POST /api/pick_default_jasna
```

服务仅监听 `127.0.0.1`，不对外暴露。

</details>

---

## 常见问题

**Q：提示「字幕内容为空（可能无人声或识别失败）」**
A：原版 Faster-Whisper 的模型锁定 `language: ja`，只处理日语。英语等其他语言会输出
0 字节 SRT。可在字幕参数里把 `task` 改为 `transcribe` 配合多语言模型。

**Q：去马赛克报 CUDA / 显存不足**
A：改用「快速（低显存 / 老显卡）」配置——片段大小降到 60、关闭 TensorRT 编译。
仍不够就关掉二次修复（RTX 超分也吃显存）。

**Q：首次运行卡在 15–60 分钟**
A：那是 Jasna 在为你的显卡编译 TensorRT 引擎，属正常流程。期间请关闭浏览器等
其他应用并勿使用电脑；引擎会缓存，之后再运行会自动复用。

**Q：Jasna 报「安装路径无效」**
A：Jasna 官方要求安装路径**仅含英文与数字**，请把它解压到类似
`D:\tools\jasna-windows-0.10.0` 的位置，不要放在中文路径下。

**Q：提示容器不支持软封装字幕**
A：`avi` / `wmv` / `flv` 等容器无法承载字幕流，程序已自动改为输出 `.mkv`。
（此限制只影响模式 2 / 3 的字幕封装；模式 1 的输出格式由 Jasna 的编码设置决定。）

**Q：模式 3 会不会把源文件覆盖掉？**
A：不会。源视频只读，输出始终是新文件。中间视频写在临时目录，
完成后自动删除（可在设置里开启保留）。

**Q：窗口打不开 / 白屏**
A：程序有三级降级（pywebview → Edge 应用窗口 → 系统浏览器）。
命令行参数：`--browser` 强制用浏览器、`--server-only` 只起服务、
`--port 8760` 指定端口、`--no-webview` 跳过 pywebview。

**Q：`build.bat` 报「Dependency installation failed」**
A：网络或代理问题。脚本会打印 pip 原始报错，可手动重试：
`.venv\Scripts\python.exe -m pip install -r requirements.txt`

**Q：打包出来的 exe 能发给同事吗**
A：可以，**整个 `dist\UnblurSub` 文件夹一起压缩分发**。同事无需装 Python，
但仍需 WebView2 运行时。另外 **Jasna 和 Faster Whisper 需另行分发**
（它们体积大且各有独立许可，本工具不内置）。

---

## 开发与测试

```bash
python selftest.py      # 350 项断言，不需要 GPU / 网络 / 任何外部程序
```

覆盖范围：

- **命名规则**（3 模式 × 各种原名、大小写、中文名、空格）
- **跳过规则**（3 模式 × 各种后缀组合、跳过原因文案）
- **Jasna 命令构造**（41 项参数映射、类型规整、越界回落）
- **参数隔离**（未启用的二级修复分支不得传参——否则 Jasna 行为不可预期；
  队列级动作必须剥离——否则每处理一个文件就关机一次）
- **依赖自检**（Topaz 路径缺失、重叠帧数越界、LTX / UNet 提示）
- **目录校验**（缺 `tools\ffmpeg.exe`、缺 `model_weights`、中文目录名各自给出对应警告）
- **字幕侧**：命令构造、封装参数、SRT 规整、覆盖策略、配置持久化
- **API 契约**：31 个路由注册、签名一致性、按模式的前置校验
- **前端容错**：`undefined` 安全读取、无旧规则残留

**提交前请跑一遍 `selftest.py`** —— `build.bat` 也会自动跑，跑不过直接中止打包。

---

## License

MIT © zyy19930304

本工具是 [Jasna](https://github.com/Kruk2/jasna) 与
[Faster-Whisper-TransWithAI-ChickenRice](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice)
的集成前端，**不包含二者代码**。Jasna 为 freeware（部分模型需 supporter license），
Faster-Whisper / Whisper 模型遵循各自许可证（MIT / MIT-CC-BY 等），请自行遵守。
