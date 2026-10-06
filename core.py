# -*- coding: utf-8 -*-
"""核心逻辑层：配置、参数 schema、命令构造、命名规则、扫描、封装。

本模块不依赖任何界面/网络库，可单独 import 做单元测试（见 selftest.py）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP_NAME = "FWSubsBatch"
APP_TITLE = "Faster Whisper 批量字幕工具"

# 自动勾选规则版本号。规则语义变更时 +1，
# 用于让旧版本遗留的勾选记录失效并按新规则重算。
# v2: 自动勾选由「排除 -U/-UC」改为「排除 -C / -UC」
RULE_VERSION = 2

# --------------------------------------------------------------------------
# 存储位置
# --------------------------------------------------------------------------

def app_data_dir() -> Path:
    r"""配置与工作目录（%APPDATA%\FWSubsBatch）。"""
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def profiles_path() -> Path:
    return app_data_dir() / "profiles.json"


def work_root() -> Path:
    d = app_data_dir() / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# 默认设置
# --------------------------------------------------------------------------

DEFAULT_VIDEO_EXTS = ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "ts", "m4v", "mpg", "mpeg", "3gp"]

DEFAULT_SETTINGS = {
    "fw_dir": "",                 # Faster Whisper 程序目录（含 infer.exe）
    "ffmpeg_path": "",            # 留空 = 自动探测
    "folders": [],                # 已添加的视频文件夹
    "selected": {},               # {文件路径: True/False} 勾选状态
    "last_profile": "",           # 上次加载的参数配置名
    "video_exts": DEFAULT_VIDEO_EXTS,
    "recursive": True,
    "auto_select_rule": "not_c",  # not_c = 排除 -C / -UC 结尾（已处理产物）
    "existing_output_policy": "number",  # number / skip / overwrite
    "output_dir": "",             # 新视频输出目录，留空 = 与源文件同目录
    "keep_srt": True,             # 封装后保留中间 SRT
    "close_prompt": True,         # 关闭时提示保存未保存的参数配置
    "_auto_select": True,         # 是否按规则自动勾选（用户手动改过则为 False）
    "_rule_version": RULE_VERSION,  # 已应用规则的版本，见 RULE_VERSION
    "window": {"width": 1360, "height": 900},
}


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    p = settings_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k == "window" and isinstance(v, dict):
                        w = dict(DEFAULT_SETTINGS["window"])
                        w.update(v)
                        data["window"] = w
                    else:
                        data[k] = v
        except Exception:
            pass
    # 兼容：老版本只有 selected 列表
    if isinstance(data.get("selected"), list):
        data["selected"] = {x: True for x in data["selected"]}
    # 迁移：旧规则值 not_u（排除 -U/-UC）已改为 not_c（排除 -C/-UC），语义不同，
    # 这里把落在磁盘上的旧值同步成新规则，避免界面下拉显示为空白。
    if data.get("auto_select_rule") == "not_u":
        data["auto_select_rule"] = "not_c"
    if data.get("auto_select_rule") not in ("not_c", "all"):
        data["auto_select_rule"] = DEFAULT_SETTINGS["auto_select_rule"]
    return data


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_settings(settings: dict) -> dict:
    cur = load_settings()
    cur.update(settings or {})
    _atomic_write(settings_path(), json.dumps(cur, ensure_ascii=False, indent=2))
    return cur


def load_profiles() -> dict:
    p = profiles_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    return {}


def save_profiles(profiles: dict) -> dict:
    _atomic_write(profiles_path(), json.dumps(profiles or {}, ensure_ascii=False, indent=2))
    return profiles or {}


def save_profile(name: str, params: dict, overwrite: bool = False) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("配置名称不能为空")
    profiles = load_profiles()
    if name in profiles and not overwrite:
        raise ValueError(f"配置「{name}」已存在，请换一个名字或选择覆盖")
    profiles[name] = {"params": normalize_params(params), "saved_at": _now_iso()}
    save_profiles(profiles)
    return profiles


def delete_profile(name: str) -> dict:
    profiles = load_profiles()
    profiles.pop(name, None)
    return save_profiles(profiles)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# 参数 schema —— 前端表单与命令行构造的唯一数据源
# --------------------------------------------------------------------------
# type: bool | int | float | str | enum | tri
# arg:  infer.exe 的命令行参数名；None 表示不传
# when: None 总是传 | "nonempty" 非空才传 | "true" 为真才传 | "false" 为假才传
#        | "notdefault" 与 default 不同才传

PARAM_SCHEMA = [
    # ---- 基础 ----
    dict(key="device", label="运行设备 (device)", type="enum", default="cuda",
         options=["cuda", "cpu", "auto"], arg="device", when="always", group="基础",
         help="cuda=显卡，cpu=处理器，auto=自动"),
    dict(key="compute_type", label="计算精度 (compute_type)", type="str", default="",
         placeholder="留空=库默认；低显存填 int8_float16", arg="compute_type", when="nonempty",
         group="基础", help="例：int8_float16 / float16 / int8"),
    dict(key="task", label="任务类型 (task)", type="enum", default="translate",
         options=["translate", "transcribe"], arg="task", when="always", group="基础",
         help="translate=翻译成中文，transcribe=原文转录"),
    dict(key="model_name_or_path", label="模型路径 (model_name_or_path)", type="str", default="",
         placeholder="留空=用 models 目录下的主模型", arg="model_name_or_path", when="nonempty",
         group="基础"),
    dict(key="overwrite", label="覆盖已存在字幕 (overwrite)", type="bool", default=False,
         arg="overwrite", when="true", group="基础"),

    # ---- 输入输出 ----
    dict(key="audio_suffixes", label="音频后缀 (audio_suffixes)", type="str",
         default="mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv",
         arg="audio_suffixes", when="always", group="输入输出"),
    dict(key="sub_formats", label="字幕格式 (sub_formats)", type="str", default="srt,vtt,lrc",
         arg="sub_formats", when="always", group="输入输出", help="本工具封装时使用其中的 srt"),
    dict(key="output_dir", label="字幕输出目录 (output_dir)", type="str", default="",
         placeholder="留空=临时工作目录（推荐）", arg=None, when=None, group="输入输出",
         help="留空时字幕生成在临时目录，不污染源文件夹"),
    dict(key="log_level", label="日志级别 (log_level)", type="enum", default="INFO",
         options=["DEBUG", "INFO", "WARNING", "ERROR"], arg="log_level", when="always",
         group="输入输出", help="DEBUG 会输出大量信息"),

    # ---- VAD 与智能切分 ----
    dict(key="vad_threshold", label="VAD 阈值 (vad_threshold)", type="float", default="",
         placeholder="留空=用配置文件(0.5)", arg="vad_threshold", when="nonempty",
         group="VAD/切分", help="0.3~0.7；太大会漏翻，太小会幻听"),
    dict(key="vad_min_speech_duration_ms", label="最小语音时长 ms", type="int", default="",
         arg="vad_min_speech_duration_ms", when="nonempty", group="VAD/切分"),
    dict(key="vad_min_silence_duration_ms", label="最小静音时长 ms", type="int", default="",
         arg="vad_min_silence_duration_ms", when="nonempty", group="VAD/切分"),
    dict(key="vad_speech_pad_ms", label="语音前后填充 ms", type="int", default="",
         arg="vad_speech_pad_ms", when="nonempty", group="VAD/切分"),
    dict(key="smart_split_with_vad", label="智能 VAD 切分", type="tri", default="auto",
         options=["auto", "true", "false"], arg="smart_split_with_vad", when="nonempty",
         group="VAD/切分", help="auto=沿用 Faster Whisper 的 generation_config"),
    dict(key="target_chunk_duration_s", label="切块目标时长（秒）", type="int", default="",
         arg="target_chunk_duration_s", when="nonempty", group="VAD/切分", help="最大 30"),

    # ---- 字幕合并 ----
    dict(key="merge_segments", label="字幕片段合并", type="tri", default="auto",
         options=["auto", "enable", "disable"], arg=None, when=None, group="字幕合并",
         help="auto=沿用配置文件；enable/disable 会覆盖配置文件"),
    dict(key="merge_max_gap_ms", label="合并最大间隔 ms", type="int", default="",
         arg="merge_max_gap_ms", when="nonempty", group="字幕合并", help="建议 500~2000"),
    dict(key="merge_max_duration_ms", label="合并后最大时长 ms", type="int", default="",
         arg="merge_max_duration_ms", when="nonempty", group="字幕合并", help="建议 15000~30000"),

    # ---- 批处理加速 ----
    dict(key="enable_batching", label="启用批处理加速 (enable_batching)", type="bool", default=False,
         arg="enable_batching", when="true", group="批处理", help="需要更多显存（建议 8GB+）"),
    dict(key="batch_size", label="批处理大小 (batch_size)", type="int", default="",
         placeholder="留空=自动检测", arg="batch_size", when="nonempty", group="批处理"),
    dict(key="max_batch_size", label="自动检测上限 (max_batch_size)", type="int", default="8",
         arg="max_batch_size", when="nonempty", group="批处理", help="默认 8"),

    # ---- 高级 ----
    dict(key="generation_config", label="生成参数文件 (generation_config)", type="str", default="",
         placeholder="留空=使用 Faster Whisper 目录下的 generation_config.json5",
         arg="generation_config", when="nonempty", group="高级",
         help="可指定自定义 json5 配置文件"),
]

SCHEMA_BY_KEY = {f["key"]: f for f in PARAM_SCHEMA}
PARAM_GROUPS = ["基础", "输入输出", "VAD/切分", "字幕合并", "批处理", "高级"]


def default_params() -> dict:
    return {f["key"]: f["default"] for f in PARAM_SCHEMA}


def _coerce(field: dict, value):
    t = field["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")
    if t == "int":
        s = "" if value is None else str(value).strip()
        if s == "":
            return ""
        try:
            return int(float(s))
        except ValueError:
            return ""
    if t == "float":
        s = "" if value is None else str(value).strip()
        if s == "":
            return ""
        try:
            return float(s)
        except ValueError:
            return ""
    s = "" if value is None else str(value).strip()
    if t == "enum" and s not in field.get("options", []):
        return field["default"]
    if t == "tri" and s not in field.get("options", []):
        return field["default"]
    return s


def normalize_params(params: dict) -> dict:
    """按 schema 规整参数字典：缺失补默认、类型转换、丢弃未知键。"""
    src = params or {}
    out = {}
    for f in PARAM_SCHEMA:
        out[f["key"]] = _coerce(f, src.get(f["key"], f["default"]))
    return out


def params_digest(params: dict) -> str:
    return json.dumps(normalize_params(params), ensure_ascii=False, sort_keys=True)


def params_dirty(current: dict, baseline: dict) -> bool:
    return params_digest(current) != params_digest(baseline or {})


# --------------------------------------------------------------------------
# 内置默认配置（对应 Faster Whisper 目录下的 5 个 .bat）
# --------------------------------------------------------------------------

def builtin_profiles() -> dict:
    base = default_params()
    base["output_dir"] = ""
    base["log_level"] = "INFO"

    def mk(**kw):
        p = dict(base)
        p.update(kw)
        return p

    return {
        "翻译 · GPU（默认）": mk(device="cuda", task="translate",
                                 help="对应 运行(翻译)(GPU).bat，显存 ≥6GB 使用"),
        "翻译 · CPU": mk(device="cpu", task="translate",
                          help="对应 运行(翻译)(CPU).bat，无显卡时使用"),
        "翻译 · GPU 低显存": mk(device="cuda", compute_type="int8_float16", task="translate",
                                help="对应 运行(翻译)(GPU,低显存模式).bat"),
        "翻译 · GPU 高显存加速": mk(device="cuda", task="translate", enable_batching=True,
                                    max_batch_size=8,
                                    help="对应 运行(翻译)(GPU,高显存加速模式).bat，建议 8GB+ 显存"),
        "翻译 · GPU（字幕输出到「输出」文件夹）": mk(device="cuda", task="translate",
                                                 output_dir="输出",
                                                 help="对应 运行(翻译)(GPU)(输出到当前文件夹).bat"),
        "转录 · GPU（原文，不翻译）": mk(device="cuda", task="transcribe",
                                       help="日文原文转录，不翻译"),
        "转录 · GPU 低显存（原文）": mk(device="cuda", task="transcribe",
                                       compute_type="int8_float16",
                                       help="低显存原文转录"),
    }


def all_profiles() -> dict:
    """内置配置 + 用户保存的配置。"""
    out = {}
    for name, params in builtin_profiles().items():
        out[name] = {"params": normalize_params(params), "builtin": True,
                     "help": params.get("help", ""), "saved_at": ""}
    for name, item in (load_profiles() or {}).items():
        if isinstance(item, dict):
            out[name] = {"params": normalize_params(item.get("params", {})),
                         "builtin": False, "help": "", "saved_at": item.get("saved_at", "")}
        else:
            out[name] = {"params": normalize_params(item), "builtin": False,
                         "help": "", "saved_at": ""}
    return out


# --------------------------------------------------------------------------
# 命令行构造
# --------------------------------------------------------------------------

def build_infer_args(params: dict, input_path: Path, sub_out_dir: Path) -> list:
    """构造 infer.exe 的参数列表。"""
    p = normalize_params(params)
    args = []
    for f in PARAM_SCHEMA:
        val = p.get(f["key"])
        flag = f.get("arg")
        if not flag:
            continue
        when = f.get("when")
        t = f["type"]
        if when == "always":
            if val == "" and t == "str":
                continue
            args.append(f"--{flag}={val}")
        elif when == "nonempty":
            if val == "" or val is None:
                continue
            # tri 类型的 "auto" 表示「沿用配置文件」，不传该参数
            if t == "tri" and val == "auto":
                continue
            args.append(f"--{flag}={val}")
        elif when == "true":
            if val:
                args.append(f"--{flag}")
        elif when == "false":
            if not val:
                args.append(f"--{flag}")
    # 字幕合并三态
    ms = p.get("merge_segments")
    if ms == "enable":
        args.append("--merge_segments")
    elif ms == "disable":
        args.append("--no_merge_segments")
    # 字幕输出目录
    out_dir = (p.get("output_dir") or "").strip()
    if out_dir:
        args.append(f'--output_dir={Path(out_dir).expanduser()}')
    else:
        args.append(f"--output_dir={sub_out_dir}")
    args.append(str(input_path))
    return args


def infer_exe(fw_dir: str) -> Path:
    return Path(fw_dir) / "infer.exe"


def validate_fw_dir(path: str) -> dict:
    """校验 Faster Whisper 程序目录。"""
    if not path:
        return {"ok": False, "msg": "未配置 Faster Whisper 程序路径"}
    p = Path(path).expanduser()
    exe = p / "infer.exe"
    if not p.exists():
        return {"ok": False, "msg": f"目录不存在：{p}"}
    if not p.is_dir():
        return {"ok": False, "msg": f"不是文件夹：{p}"}
    if not exe.exists():
        return {"ok": False, "msg": f"目录中未找到 infer.exe：{exe}"}
    models = p / "models"
    info = {"ok": True, "msg": f"已找到 {exe.name}",
            "has_models": models.exists(),
            "has_generation_config": (p / "generation_config.json5").exists()}
    return info


# --------------------------------------------------------------------------
# ffmpeg 探测
# --------------------------------------------------------------------------

FFMPEG_CANDIDATES = [
    r"D:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
]


def find_ffmpeg(settings: dict | None = None) -> str:
    cfg = (settings or {}).get("ffmpeg_path") or ""
    if cfg:
        p = Path(cfg).expanduser()
        if p.is_dir():
            p = p / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if p.exists():
            return str(p)
    found = shutil.which("ffmpeg")
    if found:
        return found
    for c in FFMPEG_CANDIDATES:
        if Path(c).exists():
            return c
    try:  # imageio-ffmpeg 自带
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return ""


def ffmpeg_broken_pipe_fix(path: str) -> list:
    """Windows 下 output=pipe 容易断，统一加 -nostdin。"""
    return ["-hide_banner", "-nostdin", "-y", "-loglevel", "error"]


# --------------------------------------------------------------------------
# 命名规则
# --------------------------------------------------------------------------

def target_stem(stem: str) -> str:
    """原名 -> 新名（严格按规格：原名加 -C；若原名以 -U 结尾则后缀改为 -UC）。

    ABC      -> ABC-C
    ABC-U    -> ABC-UC
    ABC-C    -> ABC-C-C
    ABC-UC   -> ABC-UC-C
    """
    if stem.endswith("-U"):
        return stem[:-2] + "-UC"
    return stem + "-C"


def is_derived_name(stem: str) -> bool:
    """是否已经是处理过的产物（自动勾选时要排除）。

    规则：文件名后缀含 -C 或 -UC 的视为已处理产物。
    注意：-U 是「待翻译的源文件」，不是产物，所以不排除。
    """
    s = stem.lower()
    return s.endswith("-c") or s.endswith("-uc")


def auto_selected(stem: str, rule: str = "not_c") -> bool:
    """自动勾选规则：文件名后缀不含 -C 或 -UC。"""
    if rule == "all":
        return True
    return not is_derived_name(stem)


def output_video_name(src: Path, out_dir: str = "", policy: str = "number") -> Path:
    """计算新视频文件路径；已存在时按 policy 处理，绝不静默覆盖。"""
    dst_dir = Path(out_dir).expanduser() if out_dir else src.parent
    name = target_stem(src.stem) + src.suffix
    dst = dst_dir / name
    if not dst.exists() or policy == "overwrite":
        return dst
    if policy == "skip":
        return dst
    n = 2
    while True:
        cand = dst_dir / f"{target_stem(src.stem)} ({n}){src.suffix}"
        if not cand.exists():
            return cand
        n += 1


# --------------------------------------------------------------------------
# 容器 -> 字幕编码 / 元数据
# --------------------------------------------------------------------------
# title/lANGUAGE 用于让播放器把这条轨道显示为「中文字幕」

def container_subtitle_codec(ext: str) -> tuple:
    """返回 (ffmpeg 字幕编码器, 支持软封装?)。不支持时返回 (None, False)。"""
    e = (ext or "").lower().lstrip(".")
    if e in ("mp4", "m4v", "mov"):
        return "mov_text", True
    if e in ("mkv", "webm", "mka"):
        return ("webvtt", True) if e == "webm" else ("srt", True)
    if e in ("ts", "mts", "m2ts"):
        return "mov_text", True
    if e in ("avi", "wmv", "flv", "f4v", "mpg", "mpeg", "rm", "rmvb", "vob", "3gp", "ogv"):
        return None, False
    return None, False


def build_mux_args(ffmpeg: str, video: Path, srt: Path, dst: Path,
                   sub_title: str = "中文字幕", sub_lang: str = "chi") -> list:
    codec, ok = container_subtitle_codec(dst.suffix)
    if not ok:
        raise ValueError(f"容器 {dst.suffix or '无扩展名'} 不支持软封装字幕，请改用 mkv/mp4 输出")
    args = [ffmpeg] + ffmpeg_broken_pipe_fix(ffmpeg)
    args += ["-i", str(video), "-i", str(srt)]
    args += ["-map", "0:v?", "-map", "0:a?", "-map", "1:0"]
    args += ["-c:v", "copy", "-c:a", "copy", "-c:s", codec]
    args += ["-metadata:s:s:0", f"title={sub_title}",
             "-metadata:s:s:0", f"language={sub_lang}"]
    if codec == "mov_text":
        args += ["-metadata:s:s:0", f"handler_name={sub_title}"]
    if (dst.suffix or "").lower() == ".mp4":
        args += ["-movflags", "+faststart"]
    args += ["-f", "matroska" if dst.suffix.lower() == ".mkv" else _mux_format(dst.suffix)]
    args += [str(dst)]
    return args


def _mux_format(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    return {"mp4": "mp4", "m4v": "mp4", "mov": "mov", "mkv": "matroska",
            "webm": "webm", "ts": "mpegts"}.get(e, e or "matroska")


def normalize_srt(src: Path, dst: Path) -> int:
    """规整 SRT（去 BOM、统一换行、校验是否为空），返回字幕条数。"""
    raw = src.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        text = raw.decode("gbk", errors="replace")
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out = "\n".join(lines).strip() + "\n"
    dst.write_text(out, encoding="utf-8")
    return len(re.findall(r"\n\d{2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->", "\n" + out))


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------

def scan_folder(folder: str, exts=None, recursive: bool = True,
                skip_dirs=None) -> list:
    """递归（或单层）扫描视频文件。返回 [{path, name, size, mtime}]"""
    root = Path(folder).expanduser()
    if not root.is_dir():
        return []
    extset = {("." + e.lower().lstrip(".")) for e in (exts or DEFAULT_VIDEO_EXTS)}
    skip = {s.lower() for s in (skip_dirs or ["$RECYCLE.BIN", "System Volume Information", ".git"])}
    items = []
    it = root.rglob("*") if recursive else root.glob("*")
    for p in it:
        try:
            if p.is_dir():
                continue
            if any(part.lower() in skip or part.startswith(".") for part in p.parts[len(root.parts):-1]):
                continue
            if p.suffix.lower() not in extset:
                continue
            st = p.stat()
            items.append({
                "path": str(p),
                "name": p.name,
                "rel": str(p.relative_to(root)),
                "ext": p.suffix.lower().lstrip("."),
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        except (OSError, PermissionError):
            continue
    items.sort(key=lambda x: x["rel"].lower())
    return items


def human_size(n: int) -> str:
    x = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or u == "TB":
            return f"{x:.0f} {u}" if u == "B" else f"{x:.1f} {u}"
        x /= 1024
    return f"{x:.1f} TB"


def default_output_dir_for(src: Path, settings: dict) -> Path:
    d = (settings.get("output_dir") or "").strip()
    if d:
        p = Path(d).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return src.parent


def run_capture(cmd: list, cwd=None, timeout=None, on_line=None) -> tuple:
    """执行命令并按行回调输出，返回 (returncode, 合并输出)。"""
    if on_line:
        on_line("$ " + " ".join(str(c) for c in cmd))
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except FileNotFoundError as e:
        return 127, f"无法启动进程：{e}"
    lines = []
    try:
        for raw in proc.stdout:
            s = raw.decode("utf-8", errors="replace").rstrip()
            lines.append(s)
            if on_line:
                on_line(s)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        lines.append("超时，已终止")
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
    return proc.returncode, "\n".join(lines)


if __name__ == "__main__":
    print(json.dumps({"params": default_params(),
                      "profiles": list(all_profiles().keys()),
                      "naming": [target_stem(x) for x in ("ABC", "ABC-U", "ABC-C", "ABC-UC")]},
                     ensure_ascii=False, indent=2))
