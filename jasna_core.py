# -*- coding: utf-8 -*-
"""Jasna（视频处理）集成层：参数 schema、命令构造、目录校验。

与字幕模块平行设计，遵循同样的约定：
  - 不依赖任何界面 / 网络库，可单独 import 做单元测试
  - schema 是前端表单与命令行构造的唯一数据源

文案说明逐条参考 Jasna 官方 GUI 的中文 tooltip（jasna/gui/locales/zh.py），
并针对「嵌入到本工具」的场景做了裁剪与补充。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

# --------------------------------------------------------------------------
# 处理模式
# --------------------------------------------------------------------------
# 1 = 只做视频处理   -> 输出 -U
# 2 = 只生成中文字幕 -> 输出 -C
# 3 = 两者都做        -> 输出 -UC

MODE_UNBLUR = 1
MODE_SUBTITLE = 2
MODE_BOTH = 3

MODES = [
    {"value": MODE_UNBLUR, "key": "unblur", "label": "只做视频处理",
     "short": "视频处理", "suffix": "-U",
     "desc": "调用 Jasna 处理视频，输出文件名加 -U 后缀"},
    {"value": MODE_SUBTITLE, "key": "subtitle", "label": "只生成中文字幕",
     "short": "加字幕", "suffix": "-C",
     "desc": "调用 Faster Whisper 生成中文字幕并软封装，输出文件名加 -C 后缀"},
    {"value": MODE_BOTH, "key": "both", "label": "视频处理 + 生成中文字幕",
     "short": "视频处理+字幕", "suffix": "-UC",
     "desc": "先做视频处理，再生成中文字幕，输出文件名加 -UC 后缀"},
]
MODE_VALUES = [m["value"] for m in MODES]
MODE_BY_VALUE = {m["value"]: m for m in MODES}


def normalize_mode(mode) -> int:
    """把任意输入规整为合法模式值，非法值退回「只加字幕」（原行为）。"""
    try:
        v = int(mode)
    except (TypeError, ValueError):
        return MODE_SUBTITLE
    return v if v in MODE_VALUES else MODE_SUBTITLE


# --------------------------------------------------------------------------
# 命名规则（随模式变化）
# --------------------------------------------------------------------------
# 后缀语义：-U = 已完成视频处理，-C = 已加中文字幕，-UC = 两者都有

def processed_flags(stem: str) -> dict:
    """解析文件名后缀，判断该文件已经做过哪些处理。

    ABC     -> {"u": False, "c": False}
    ABC-U   -> {"u": True,  "c": False}
    ABC-C   -> {"u": False, "c": True}
    ABC-UC  -> {"u": True,  "c": True}
    """
    s = (stem or "").lower()
    if s.endswith("-uc"):
        return {"u": True, "c": True}
    if s.endswith("-u"):
        return {"u": True, "c": False}
    if s.endswith("-c"):
        return {"u": False, "c": True}
    return {"u": False, "c": False}


def target_stem_mode(stem: str, mode: int) -> str:
    """按处理模式计算输出文件名（不含扩展名）。

    模式 1（只视频处理）：加 -U；原名以 -C 结尾 -> 把 -C 换成 -UC
        ABC     -> ABC-U
        ABC-C   -> ABC-UC
        ABC-U   -> ABC-U     （已完成视频处理，模式 1 下会被跳过，此处仅兜底）
    模式 2（只加字幕）：加 -C；原名以 -U 结尾 -> 把 -U 换成 -UC
        ABC     -> ABC-C
        ABC-U   -> ABC-UC
    模式 3（两者都做）：一律 -UC
        ABC     -> ABC-UC
    """
    m = normalize_mode(mode)
    flags = processed_flags(stem)
    if m == MODE_UNBLUR:
        # 已处理过的视频不能再加 -U；原名是 -C 产物则升级为 -UC
        if flags["c"]:
            return _swap_suffix(stem, "-UC")
        return stem + "-U"
    if m == MODE_SUBTITLE:
        if flags["u"]:
            return _swap_suffix(stem, "-UC")
        return stem + "-C"
    return stem + "-UC"


def _swap_suffix(stem: str, new_suffix: str) -> str:
    """把结尾的 -C / -U / -UC 换成目标后缀；无已知后缀则直接追加。

    后缀统一输出为大写（-C / -U / -UC），因为后缀本身是语义标记，
    保留原始大小写会让用户在文件列表里看到 e-c-C 这种三段后缀的怪名字。
    """
    low = stem.lower()
    for suf in ("-uc", "-u", "-c"):
        if low.endswith(suf):
            return stem[: len(stem) - len(suf)] + new_suffix
    return stem + new_suffix


def should_skip(stem: str, mode: int) -> bool:
    """按模式判断该文件是否应跳过（已处理过 / 无法再处理）。

    模式 1（只做视频处理）：已完成视频处理的（-U / -UC）跳过
    模式 2（只加字幕）：已加字幕的（-C / -UC）跳过
    模式 3（两者都做）：已做过任意一项的（-U / -C / -UC）都跳过，
             因为两个产物合起来就是 -UC，无法在已处理文件上再叠加
    """
    f = processed_flags(stem)
    m = normalize_mode(mode)
    if m == MODE_UNBLUR:
        return f["u"]
    if m == MODE_SUBTITLE:
        return f["c"]
    return f["u"] or f["c"]


def skip_reason(stem: str, mode: int) -> str:
    """跳过原因（给日志/界面用），不跳过时返回空串。"""
    if not should_skip(stem, mode):
        return ""
    f = processed_flags(stem)
    m = normalize_mode(mode)
    have = []
    if f["u"]:
        have.append("已完成视频处理")
    if f["c"]:
        have.append("已加中文字幕")
    done = "、".join(have)
    if m == MODE_UNBLUR:
        return f"文件名后缀表明{done}，模式「只做视频处理」无需重复处理"
    if m == MODE_SUBTITLE:
        return f"文件名后缀表明{done}，模式「只生成中文字幕」无需重复处理"
    return f"文件名后缀表明{done}，模式「视频处理 + 生成中文字幕」需两者都未处理过"


def auto_selected_mode(stem: str, mode: int, rule: str = "smart") -> bool:
    """自动勾选：按当前模式跳过已处理的文件。

    rule = "smart"（默认）按模式智能判断；rule = "all" 勾选全部。
    """
    if rule == "all":
        return True
    return not should_skip(stem, mode)


# --------------------------------------------------------------------------
# Jasna 参数 schema
# --------------------------------------------------------------------------
# type: bool | int | float | str | enum
# arg:  jasna.exe 的命令行参数名；None 表示不传
# when: None 总是传 | "nonempty" 非空才传 | "true" 为真才传 | "false" 为假才传
#        | "opt" 由 _opt_flags() 按枚举值决定
# range: (min, max, step) 供前端渲染滑块与范围提示
# need:  额外依赖标记，界面据此提示用户需自行准备

JASNA_PARAM_SCHEMA = [
    # ---- 修复模型 ----
    dict(key="restoration_model", label="修复模型 (restoration_model)", type="enum",
         default="basicvsrpp", options=["basicvsrpp", "ltx"],
         option_labels={"basicvsrpp": "BasicVSR++（通用，快）", "ltx": "LTX（细节最多，很慢）"},
         arg="--restoration-model-name", when="always", group="修复模型",
         need="nvidia",
         help="常用模型。速度快，适合大多数视频；除非需要最高细节，否则选它。"
              "LTX 修复细节最多，但慢得多，需要强劲的 NVIDIA GPU，适合短片或喜欢的场景。"),
    dict(key="ltx_model", label="LTX 模型", type="enum", default="distilled",
         options=["distilled", "undistilled"],
         option_labels={"distilled": "蒸馏版（更快）", "undistilled": "未蒸馏（细节稍好，更慢）"},
         arg=None, when=None, group="修复模型", depends_on="restoration_model:ltx",
         help="未蒸馏的细节稍好，但耗时更长。蒸馏版更快。"),
    dict(key="ltx_seed", label="LTX 种子", type="int", default="20260923",
         range=(1, 2147483647, 1), arg="--ltx-seed", when="nonempty",
         group="修复模型", depends_on="restoration_model:ltx",
         help="换一个种子，同一视频会得到另一种效果。保留喜欢的种子即可再次得到相同结果。"),
    dict(key="ltx_fast", label="LTX 快速模式", type="bool", default=False,
         arg="--ltx-fast", when="true", group="修复模型",
         depends_on="restoration_model:ltx", need="rtx50",
         help="约快 1.4 倍，细节略少。仅限 RTX 50 系列。"),
    dict(key="ltx_large_canvas", label="LTX 大面积区域更清晰", type="bool", default=False,
         arg="--ltx-large-canvas", when="true", group="修复模型",
         depends_on="restoration_model:ltx", need="vram10g",
         help="大块区域处理得更细致，但这些部分约慢 3 倍。需要至少 10 GB 可用显存。"),
    dict(key="ltx_trial", label="LTX 试运行（无需许可证，画面不正确）", type="bool",
         default=False, arg="--ltx-trial", when="true", group="修复模型",
         depends_on="restoration_model:ltx",
         help="无需许可证或模型文件的速度测试。画面会不正确。用它来看你的电脑能否运行 LTX 以及速度如何。"),

    # ---- 基本处理 ----
    dict(key="max_clip_size", label="最大片段大小 (max_clip_size)", type="int", default=90,
         range=(10, 720, 10), arg="--max-clip-size", when="nonempty", group="基本处理",
         help="一次处理多少帧画面。数值越大，效果可能越好，但占用更多显存。"
              "建议 60 或更高。参考：60（安全）、90（平衡）、180（最佳质量，"
              "开「编译 BasicVSR++」需 12GB+ 显存）。4K 视频占用更多显存，"
              "较小的片段大小可能产生类似的质量，但处理速度快得多。"),
    dict(key="detection_model", label="检测模型 (detection_model)", type="enum",
         default="rfdetr-v6", options=["rfdetr-v6", "rfdetr-vr-v1", "rfdetr-v6-large",
                                       "zelefans-vr-yolo-v2", "lada-yolov8s"],
         option_labels={"rfdetr-v6": "rfdetr-v6（快速，内置，推荐）",
                        "rfdetr-vr-v1": "rfdetr-vr-v1（VR180，内置）",
                        "rfdetr-v6-large": "rfdetr-v6-large（质量更高，需单独下载）",
                        "zelefans-vr-yolo-v2": "zelefans-vr-yolo-v2（VR180 备用，需单独下载）",
                        "lada-yolev8s": "Lada YOLO（2D 动画可能更合适，需单独下载）"},
         arg="--detection-model", when="always", group="基本处理",
         help="用于寻找需要修复区域的 AI 模型。rfdetr-v6：最新、快速，推荐使用。"
              "rfdetr-vr-v1：VR180 模型，推荐用于 VR。rfdetr-v6-large：质量更高、速度更慢，"
              "需单独下载。Lada YOLO 对 2D 动画可能更合适。"),
    dict(key="detection_score_threshold", label="检测阈值", type="float", default=0.35,
         range=(0.0, 1.0, 0.05), arg="--detection-score-threshold", when="nonempty",
         group="基本处理",
         help="AI 标记修复区域所需的置信度。数值越低 = 检测更多区域（可能误检）；"
              "数值越高 = 检测更少区域（可能漏检）。各模型推荐值："
              "rfdetr-v6 为 0.35，rfdetr-v6-large 为 0.40。"),
    dict(key="fp16_mode", label="FP16 模式", type="bool", default=True,
         arg="--fp16", when="true", group="基本处理",
         help="使用半精度计算来减少显存占用，通常还能提升速度。在现代显卡上几乎无画质损失。"
              "建议 RTX 20 系列及以上显卡开启。"),
    dict(key="compile_basicvsrpp", label="编译 BasicVSR++", type="bool", default=True,
         arg="--compile-basicvsrpp", when="true", group="基本处理",
         help="将修复模型编译为 TensorRT 子引擎，大幅提升速度（约 2-3 倍）。"
              "首次编译需要 15-60 分钟，请关闭其他应用（含浏览器）并勿使用电脑；"
              "引擎会被缓存，后续运行自动复用。引擎显存约 1.9GB（片段 60）、"
              "5.4GB（片段 180）；处理时峰值约 7.6GB（片段 60）、14.7GB（片段 180）。"
              "显存不足请关闭此项或降低片段大小。建议配合片段大小 60-90 开启。"),
    dict(key="batch_size", label="批处理大小", type="int", default=4,
         range=(1, 16, 1), arg="--batch-size", when="nonempty", group="基本处理",
         help="一次并行处理多少批。越大越快但占用更多显存。不确定时保持默认。"),

    # ---- 高级处理 ----
    dict(key="temporal_overlap", label="时间重叠", type="int", default=8,
         range=(0, 30, 1), arg="--temporal-overlap", when="nonempty", group="高级处理",
         help="处理片段之间的重叠帧数，用于减少拼接处的闪烁。数值越高过渡越平滑，"
              "但速度稍慢；超过 20 效果提升不明显。推荐：片段 60 → 6-8，"
              "片段 90 → 8-12，片段 180 → 15-20。"),
    dict(key="max_detection_gap", label="最大检测间隙", type="int", default=2,
         range=(0, 10, 1), arg="--max-detection-gap", when="nonempty", group="高级处理",
         help="填补短暂的检测中断：如果被跟踪的目标消失不超过 N 帧且在相同位置重新出现，"
              "则填补间隙并继续该片段，而不是将其切断。保持较小数值，"
              "以免真正快速出现/消失的画面被错误填补。0 表示禁用。"),
    dict(key="min_detection_duration", label="最短检测持续帧数", type="int", default=2,
         range=(0, 10, 1), arg="--min-detection-duration", when="nonempty", group="高级处理",
         help="丢弃持续少于 N 帧的检测（很可能是单帧误检），这些帧保持原样。"
              "保持较小数值，否则真实存在但只持续几帧的目标会被跳过。0 或 1 表示禁用。"),
    dict(key="scene_detection", label="镜头切换检测", type="bool", default=True,
         arg="--scene-detection", when="true", group="高级处理",
         help="检测硬切镜头（场景切换），并在切换点结束所有正在跟踪的处理片段，"
              "确保片段不会跨越两个不同镜头，避免处理时混合切换前后的画面。推荐始终开启。"),
    dict(key="enable_crossfade", label="启用交叉淡入淡出", type="bool", default=True,
         arg="--enable-crossfade", when="true", group="高级处理",
         help="在片段边界处进行平滑过渡，减少画面闪烁。使用已处理的帧，"
              "不会增加任何额外 GPU 开销。建议始终开启。"),
    dict(key="vr_mode", label="VR180 模式", type="enum", default="auto",
         options=["auto", "off", "sbs", "sbs-fisheye"],
         option_labels={"auto": "自动（推荐）", "off": "关闭",
                        "sbs": "SBS（分眼处理）", "sbs-fisheye": "SBS + 鱼眼"},
         arg="--vr-mode", when="always", group="高级处理",
         help="控制并排 VR180 视频的处理方式。自动模式会在画面为严格 2:1 且高度超过 1080、"
              "文件名含可信片商标记或具有兼容空间元数据时启用，并按片商为每个区域"
              "选择修复投影。SBS 采用相同路由分别处理双眼；SBS + 鱼眼强制对所有区域"
              "使用鱼眼条件化。普通 2D 视频请保持「自动」或「关闭」。"),
    dict(key="denoise_strength", label="降噪强度", type="enum", default="none",
         options=["none", "low", "medium", "high"],
         option_labels={"none": "无", "low": "低（推荐起步）",
                        "medium": "中（推荐起步）", "high": "高（强力平滑）"},
         arg="--denoise", when="always", group="高级处理",
         help="降低修复区域的噪点和颗粒感。强度越高画面越平滑，但可能丢失细节。"
              "无：不降噪。低/中：推荐起步值。高：强力平滑。"),
    dict(key="denoise_step", label="降噪应用时机", type="enum", default="after_primary",
         options=["after_primary", "after_secondary"],
         option_labels={"after_primary": "主修复后（放大之前，256x256 分辨率）",
                        "after_secondary": "二次修复后（放大之后，完整分辨率）"},
         arg="--denoise-step", when="always", group="高级处理",
         help="降噪在处理流程中的应用时机。主修复后：在放大之前降噪，"
              "在 256x256 分辨率下处理。二次修复后：在放大之后、最终输出之前降噪，"
              "在完整分辨率下处理。"),

    # ---- 二次修复 ----
    dict(key="secondary_restoration", label="二次修复（放大）", type="enum", default="none",
         options=["none", "unet-4x", "tvai", "rtx-super-res"],
         option_labels={"none": "无（不放大，速度最快）",
                        "unet-4x": "UNet 4x（质量高，快，支持者专属）",
                        "tvai": "Topaz TVAI（质量高，很慢，需单独安装）",
                        "rtx-super-res": "RTX Super Res（很快，质量尚可）"},
         arg="--secondary-restoration", when="always", group="二次修复",
         help="可选的第二步处理，将修复区域从 256x256 放大到 1024 像素。"
              "可提升清晰度，特别是近景和 4K 视频。UNet 4x 是支持者专属模型，"
              "比 TVAI 快很多，质量高；RTX Super Res 速度快但质量一般；"
              "Topaz TVAI 质量高但需要单独购买安装。"),
    dict(key="rtx_scale", label="RTX 超分缩放", type="enum", default="4",
         options=["2", "4"], option_labels={"2": "2x（512px）", "4": "4x（1024px）"},
         arg="--rtx-scale", when="always", group="二次修复",
         depends_on="secondary_restoration:rtx-super-res",
         help="修复区域的放大倍数。2x = 512px，4x = 1024px。倍数越高越清晰，但速度越慢。"),
    dict(key="rtx_quality", label="RTX 超分质量", type="enum", default="high",
         options=["low", "medium", "high", "ultra"],
         option_labels={"low": "低", "medium": "中", "high": "高", "ultra": "极高"},
         arg="--rtx-quality", when="always", group="二次修复",
         depends_on="secondary_restoration:rtx-super-res",
         help="放大质量。越高画面越好，但速度越慢。"),
    dict(key="rtx_denoise", label="RTX 降噪", type="enum", default="medium",
         options=["none", "low", "medium", "high", "ultra"],
         option_labels={"none": "无", "low": "低", "medium": "中",
                        "high": "高", "ultra": "极高"},
         arg="--rtx-denoise", when="always", group="二次修复",
         depends_on="secondary_restoration:rtx-super-res",
         help="使用 RTX 硬件去除噪点。设为「无」跳过降噪。"),
    dict(key="rtx_deblur", label="RTX 去模糊", type="enum", default="none",
         options=["none", "low", "medium", "high", "ultra"],
         option_labels={"none": "无", "low": "低", "medium": "中",
                        "high": "高", "ultra": "极高"},
         arg="--rtx-deblur", when="always", group="二次修复",
         depends_on="secondary_restoration:rtx-super-res",
         help="使用 RTX 硬件锐化模糊区域。设为「无」跳过锐化。"),
    dict(key="tvai_ffmpeg_path", label="Topaz FFmpeg 路径", type="str",
         default=r"C:\Program Files\Topaz Labs LLC\Topaz Video\ffmpeg.exe",
         arg="--tvai-ffmpeg-path", when="nonempty", group="二次修复",
         depends_on="secondary_restoration:tvai", need="topaz",
         help="Topaz Video 自带的 ffmpeg.exe 完整路径。需先安装 Topaz Video AI。"),
    dict(key="tvai_model", label="Topaz 模型", type="enum", default="iris-2",
         options=["iris-2", "iris-3", "prob-4", "nyx-1"],
         option_labels={"iris-2": "iris-2（推荐默认，质量均衡）",
                        "iris-3": "iris-3", "prob-4": "prob-4", "nyx-1": "nyx-1"},
         arg="--tvai-model", when="always", group="二次修复",
         depends_on="secondary_restoration:tvai", need="topaz",
         help="用于放大的 Topaz AI 模型。可尝试不同模型看哪个效果最好。"),
    dict(key="tvai_scale", label="Topaz 缩放", type="enum", default="4",
         options=["1", "2", "4"],
         option_labels={"1": "1x（256px，不放大）", "2": "2x（512px）", "4": "4x（1024px）"},
         arg="--tvai-scale", when="always", group="二次修复",
         depends_on="secondary_restoration:tvai", need="topaz",
         help="修复区域的放大倍数。倍数越高越清晰，但文件更大、速度更慢。"),
    dict(key="tvai_workers", label="Topaz 工作线程数", type="int", default=2,
         range=(1, 8, 1), arg="--tvai-workers", when="nonempty", group="二次修复",
         depends_on="secondary_restoration:tvai", need="topaz",
         help="同时运行的 Topaz 放大任务数。越多越快，但占用更多 CPU/GPU 资源。"),
    dict(key="tvai_denoise", label="Topaz 降噪", type="bool", default=False,
         arg="--tvai-denoise", when="true", group="二次修复",
         depends_on="secondary_restoration:tvai", need="topaz",
         help="在所选增强模型之前运行基于 Nyx 的降噪操作。可以清理修复区域中的噪点"
              "并保留增强模型的细节，但会增加两次 AI 处理，占用更多 GPU 时间和显存。"),

    # ---- 编码输出 ----
    dict(key="codec", label="输出编解码器", type="enum", default="hevc",
         options=["hevc", "h264", "av1"],
         option_labels={"hevc": "HEVC (H.265)（体积小，画质优秀）",
                        "h264": "H.264 (AVC)（兼容性最好，文件较大）",
                        "av1": "AV1（压缩率最高，需较新播放器与显卡）"},
         arg="--codec", when="always", group="编码输出",
         help="输出视频格式。注意 Jasna 的 AI 处理需要重新编码（不能像字幕那样 copy），"
              "这是视频处理的固有代价。"),
    dict(key="encoder_cq", label="质量 (CQ)", type="int", default="",
         range=(1, 63, 1), arg="--cq", when="nonempty", group="编码输出",
         placeholder="留空 = 用 GPU 默认值（NVIDIA：H.264 25 / HEVC 28 / AV1 35）",
         help="视频目标质量。数值越低，质量越高，但文件越大。"
              "NVIDIA 范围：H.264/HEVC 为 1-51，AV1 为 1-63。"
              "AMD 范围：H.264/HEVC 为 0-51，AV1 为 1-51。不确定时留空用默认值。"),
    dict(key="sharpen_strength", label="锐化", type="float", default=0.0,
         range=(0.0, 1.0, 0.05), arg="--sharpen", when="nonempty", group="编码输出",
         help="让边缘和细节更清晰，在导出时应用。0 表示关闭。0.2-0.5 为轻微增强；"
              "1.0 最强，可能显得生硬。画面越锐利所需文件越大，"
              "如果效果变差请调低 CQ 值。"),
    dict(key="encoder_custom_args", label="自定义编码参数", type="str", default="",
         arg="--encoder-settings", when="nonempty", group="编码输出",
         placeholder='留空。示例：rc-lookahead=32 或 {"rc-lookahead":32}',
         help="高级编码器参数，以逗号分隔的 key=value 格式。如果不清楚用途，请留空。"),
    dict(key="lut_path", label="色彩 LUT (.cube)", type="str", default="",
         arg="--lut", when="nonempty", group="编码输出",
         placeholder="留空。例：D:\\luts\\my-lut.cube",
         help="可选的 .cube 色彩 LUT，在编码前由 GPU 应用。支持 1D 和 3D LUT"
              "（例如 Adobe Premiere 或 DaVinci Resolve 导出的文件）。"
              "可在不重新编码的情况下修正黑色抬升、白平衡等问题。"),
    dict(key="retarget_high_fps", label="将 60 FPS 降至 30 FPS", type="bool", default=False,
         arg="--retarget-high-fps", when="true", group="编码输出",
         help="仅用于离线导出：60 或 59.94 FPS 视频每两帧处理一帧，"
              "并精确编码为 30 或 29.97 FPS。其他帧率保持不变，"
              "音频时序和播放速度不变。"),
    dict(key="fmp4", label="处理中即可播放 (fMP4)", type="bool", default=False,
         arg="--fmp4", when="true", group="编码输出",
         help="MP4 和 MOV 输出在生成过程中即可播放，任务中断后仍可播放。"
              "视频每隔几秒增长一段，完成前播放器可能显示错误的时长。"),
    dict(key="file_conflict", label="同名输出已存在时", type="enum", default="number",
         options=["number", "skip", "overwrite"],
         option_labels={"number": "自动加序号 (2)(3)…（推荐，永不覆盖）",
                        "skip": "跳过该文件", "overwrite": "覆盖"},
         arg=None, when=None, group="编码输出", local_only=True,
         help="输出文件已存在时的处理方式。Jasna 自带该选项，但本工具在调用 Jasna "
              "之前就已算好最终输出路径并自行处理冲突，因此这一项不传给 Jasna——"
              "实际行为以「设置」里的「同名输出已存在时」为准，保留此项仅为对照说明。"),

    # ---- 导出后动作 ----
    dict(key="post_export_action", label="队列完成后执行", type="enum", default="none",
         options=["none", "shutdown", "command"],
         option_labels={"none": "无（不执行任何操作）",
                        "shutdown": "关闭电脑（60 秒倒计时，可取消）",
                        "command": "自定义命令（在系统 shell 中运行）"},
         arg="--post-export-action", when="always", group="导出后动作",
         help="整个队列完成后的可选操作。关闭电脑：60 秒倒计时后关闭计算机，"
              "倒计时期间可取消。任务运行时也可以更改此项。"),
    dict(key="post_export_command", label="自定义命令", type="str", default="",
         arg="--post-export-command", when="nonempty", group="导出后动作",
         depends_on="post_export_action:command",
         placeholder='留空。示例：shutdown /s /t 60',
         help="当「队列完成后执行」为「自定义命令」时，在系统 shell 中运行此命令。"),
    dict(key="post_export_video_command", label="每个视频完成后执行", type="str", default="",
         arg="--post-export-video-command", when="nonempty", group="导出后动作",
         placeholder="留空。占位符：{input} {output} {output_dir} {output_stem} {output_suffix}",
         help="每个视频成功导出后在系统 shell 中运行，并等待命令完成。"
              "留空即可禁用。路径已自动加引号。"),
]

JASNA_SCHEMA_BY_KEY = {f["key"]: f for f in JASNA_PARAM_SCHEMA}
JASNA_PARAM_GROUPS = ["修复模型", "基本处理", "高级处理", "二次修复", "编码输出", "导出后动作"]


def jasna_default_params() -> dict:
    return {f["key"]: f["default"] for f in JASNA_PARAM_SCHEMA}


def _coerce_jasna(field: dict, value):
    t = field["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")
    if t in ("int", "float"):
        s = "" if value is None else str(value).strip()
        if s == "":
            return ""
        try:
            return int(float(s)) if t == "int" else float(s)
        except ValueError:
            return ""
    s = "" if value is None else str(value).strip()
    if t == "enum" and s not in field.get("options", []):
        return field["default"]
    return s


def normalize_jasna_params(params: dict) -> dict:
    """按 schema 规整 Jasna 参数字典：缺失补默认、类型转换、丢弃未知键。"""
    src = params or {}
    out = {}
    for f in JASNA_PARAM_SCHEMA:
        out[f["key"]] = _coerce_jasna(f, src.get(f["key"], f["default"]))
    return out


def jasna_params_digest(params: dict) -> str:
    import json
    return json.dumps(normalize_jasna_params(params), ensure_ascii=False, sort_keys=True)


def jasna_params_dirty(current: dict, baseline: dict) -> bool:
    return jasna_params_digest(current) != jasna_params_digest(baseline or {})


def _opt_flag(field: dict, value) -> list:
    """把 schema 声明的 --xxx/--no-xxx 布尔对按值择一输出。"""
    if value is True:
        return [field.get("arg_true") or field["arg"]]
    if value is False:
        return [field.get("arg_false") or f"--no-{field['arg'].lstrip('-')}"]
    return []


def _dep_satisfied(dep: str, p: dict) -> bool:
    """判断 depends_on 条件是否满足。dep 形如 "字段名:取值"。"""
    if not dep:
        return True
    i = dep.find(":")
    if i < 0:
        return True
    return str(p.get(dep[:i])) == dep[i + 1:]


def build_jasna_args(params: dict, src: Path, dst: Path,
                     working_dir: Path | None = None) -> list:
    """构造 jasna.exe 的参数列表（不含 exe 本身）。

    两类参数**不传**：
    1. post_export_* —— 队列级动作。本工具逐个文件调用 Jasna，Jasna 会把每次
       调用都当成「队列结束」，导致每处理一个文件就关机/执行一次命令。
       这些动作由 JobManager 在整个队列真正结束时统一执行。
    2. depends_on 未满足的项 —— 例如没启用 Topaz TVAI 时，绝不能把
       --tvai-ffmpeg-path 之类传给 Jasna，否则可能触发意外行为或报错。
    """
    p = normalize_jasna_params(params)
    args = []
    for f in JASNA_PARAM_SCHEMA:
        val = p.get(f["key"])
        flag = f.get("arg")
        if not flag:
            continue
        if f["key"] in ("post_export_action", "post_export_command",
                        "post_export_video_command"):
            continue# 队列级动作，本工具自己执行
        if not _dep_satisfied(f.get("depends_on"), p):
            continue          # 所属分支未启用，其子项一律不传
        when = f.get("when")
        if when == "always":
            if val == "" and f["type"] == "str":
                continue
            args.append(f"{flag}={val}")
        elif when == "nonempty":
            if val == "" or val is None:
                continue
            args.append(f"{flag}={val}")
        elif when == "true":
            if val:
                args.append(flag)
        elif when == "false":
            if not val:
                args.append(flag)
        elif when == "opt":
            args += _opt_flag(f, val)
    if working_dir:
        # 统一用正斜杠：Jasna 内部按 POSIX 风格拼路径，Windows 反斜杠
        # 在部分版本上会被当成转义符，导致工作目录找不到。
        args.append("--working-directory=" + str(working_dir).replace("\\", "/"))
    # input / output 保持原样（含中文、空格的路径 Jasna 能正确处理，
    # 走subprocess 列表形式不经过 shell，不存在转义问题）
    args.append(f"--input={src}")
    args.append(f"--output={dst}")
    args.append("--no-browser")
    return args


# --------------------------------------------------------------------------
# Jasna 内置配置方案
# --------------------------------------------------------------------------

def builtin_jasna_profiles() -> dict:
    def mk(**kw):
        d = jasna_default_params()
        d.update(kw)
        return d

    return {
        "标准（1080p 均衡）": mk(
            max_clip_size=90, temporal_overlap=8, codec="hevc",
            detection_score_threshold=0.35,
            help="日常 1080p 视频的平衡点：片段 90 + 重叠 8，HEVC 编码，画质与速度兼顾。"
                 "显存 6GB 起步。"),
        "快速（低显存 / 老显卡）": mk(
            max_clip_size=60, temporal_overlap=6, compile_basicvsrpp=False,
            fp16_mode=True, batch_size=2, codec="hevc",
            detection_score_threshold=0.35,
            help="片段 60 + 关闭模型编译，显存约 6GB 即可运行。速度最快，"
                 "质量略低于标准模式。GTX 16 系 / RTX 20 系建议从这套开始。"),
        "高质量（大显存 / 4K）": mk(
            max_clip_size=180, temporal_overlap=15, compile_basicvsrpp=True,
            codec="hevc", detection_score_threshold=0.35,
            secondary_restoration="rtx-super-res", rtx_scale="4", rtx_quality="high",
            help="片段 180 + 重叠 15 + RTX 超分 4x，开启模型编译。峰值显存约 14.7GB，"
                 "建议 16GB 以上显卡（RTX 3080/4080 及以上）。适合 4K 与近景特写。"),
        "极速（RTX 超分 2x）": mk(
            max_clip_size=90, temporal_overlap=8, codec="hevc",
            secondary_restoration="rtx-super-res", rtx_scale="2", rtx_quality="high",
            help="保持标准模式的速度，同时用 RTX Super Res 2x 放大修复区域。"
                 "不额外增加耗时，质量优于不放大。"),
        "动画 / 2D（YOLO 检测）": mk(
            max_clip_size=90, temporal_overlap=8, codec="hevc",
            detection_model="lada-yolov8s", detection_score_threshold=0.25,
            help="Lada YOLO 检测模型对 2D 动画画面中的目标定位更准，推荐阈值 0.25。"
                 "需确保 model_weights 下有对应权重。"),
    }


def all_jasna_profiles() -> dict:
    """内置 Jasna 配置 + 用户保存的配置。"""
    out = {}
    for name, params in builtin_jasna_profiles().items():
        out[name] = {"params": normalize_jasna_params(params), "builtin": True,
                     "help": params.get("help", ""), "saved_at": ""}
    for name, item in (load_jasna_profiles() or {}).items():
        if isinstance(item, dict):
            out[name] = {"params": normalize_jasna_params(item.get("params", {})),
                         "builtin": False, "help": "", "saved_at": item.get("saved_at", "")}
        else:
            out[name] = {"params": normalize_jasna_params(item), "builtin": False,
                         "help": "", "saved_at": ""}
    return out


def jasna_profiles_path():
    from pathlib import Path as _P
    import core
    return core.app_data_dir() / "jasna_profiles.json"


def load_jasna_profiles() -> dict:
    import json
    import core
    p = jasna_profiles_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    return {}


def _save_jasna_profiles_raw(profiles: dict) -> dict:
    import json
    import core
    core._atomic_write(jasna_profiles_path(),
                       json.dumps(profiles or {}, ensure_ascii=False, indent=2))
    return profiles or {}


def save_jasna_profiles(profiles: dict) -> dict:
    return _save_jasna_profiles_raw(profiles)


def save_jasna_profile(name: str, params: dict, overwrite: bool = False) -> dict:
    import datetime
    import json
    name = (name or "").strip()
    if not name:
        raise ValueError("配置名称不能为空")
    profiles = load_jasna_profiles()
    if name in profiles and not overwrite:
        raise ValueError(f"配置「{name}」已存在，请换一个名字或选择覆盖")
    profiles[name] = {"params": normalize_jasna_params(params),
                      "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    return _save_jasna_profiles_raw(profiles)


def delete_jasna_profile(name: str) -> dict:
    profiles = load_jasna_profiles()
    profiles.pop(name, None)
    return _save_jasna_profiles_raw(profiles)


# --------------------------------------------------------------------------
# Jasna 目录校验与可执行文件查找
# --------------------------------------------------------------------------
# 注意：Jasna 要求安装路径只含英文与数字，故不做中文路径的特殊处理，
# 但会在校验结果里提示。

JASNA_DIR_NAME_HINTS = ["jasna-windows-0.10.0", "jasna-windows", "jasna", "Jasna"]


def jasna_exe(jasna_dir: str) -> Path:
    return Path(jasna_dir or "") / "jasna.exe"


def _is_pe_exe(path) -> bool:
    """粗判是否为 Windows 可执行文件（查 MZ 魔数 + PE 签名）。

    与 core.is_pe_exe() 同逻辑，但在本文件内独立实现——
    jasna_core 刻意不 import core，保持可单独导入做单元测试的分层约束。
    """
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False
            f.seek(0x3C)
            off = f.read(4)
            if len(off) != 4:
                return False
            f.seek(int.from_bytes(off, "little"))
            return f.read(4) == b"PE\x00\x00"
    except (OSError, ValueError):
        return False


def validate_jasna_dir(path: str) -> dict:
    """校验 Jasna 程序目录（含 jasna.exe / model_weights / tools）。"""
    if not path:
        return {"ok": False, "msg": "未配置 Jasna 程序路径"}
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": False, "msg": f"目录不存在：{p}"}
    if not p.is_dir():
        return {"ok": False, "msg": f"不是文件夹：{p}"}
    exe = jasna_exe(p)
    if not exe.exists():
        return {"ok": False, "msg": f"目录中未找到 jasna.exe：{exe}"}
    # 同FW：挡掉「文件名对但不是可执行文件」的坏目录，
    # 否则运行期才弹 WinError 216「与 64 位 Windows 不兼容」。
    if not _is_pe_exe(exe):
        return {"ok": False,
                "msg": f"jasna.exe 不是有效的 Windows 程序（可能选错了目录）：{exe}"}
    models = p / "model_weights"
    weights = []
    if models.is_dir():
        try:
            weights = sorted(x.name for x in models.iterdir()
                             if x.suffix in (".pt", ".pth", ".onnx", ".engine", ".bs1-4"))
        except OSError:
            weights = []
    info = {
        "ok": True,
        "msg": f"已找到 {exe.name}",
        "has_models": models.is_dir(),
        "has_ffmpeg": (p / "tools" / "ffmpeg.exe").exists(),
        "model_count": len(weights),
        "models": weights[:20],
        "warn": "",
    }
    if not models.is_dir():
        info["warn"] = "未找到 model_weights 目录，模型权重可能缺失"
    elif not weights:
        info["warn"] = "model_weights 中未发现模型权重文件"
    if not info["has_ffmpeg"]:
        info["warn"] = (info["warn"] + "；" if info["warn"] else "") + \
            "未找到 tools\\ffmpeg.exe，Jasna 可能无法正常处理视频"
    # Jasna 官方要求安装路径仅含英文与数字
    bad = [ch for ch in p.name if ord(ch) > 127]
    if bad:
        info["warn"] = (info["warn"] + "；" if info["warn"] else "") + \
            f"目录名含非英文字符（{''.join(bad)}），Jasna 官方要求安装路径仅含英文与数字"
    return info


def pick_default_jasna_dir() -> dict:
    """猜测 Jasna 安装目录：程序同级、上级、常见位置。"""
    import os
    cands = []
    here = Path(_exe_dir())
    for base in [here, here.parent, Path.cwd()]:
        for sub in JASNA_DIR_NAME_HINTS:
            cands.append(base / sub)
    for env in ("USERPROFILE", "HOME"):
        root = os.environ.get(env)
        if root:
            for sub in ("Downloads", "Desktop", "Documents", "tools", ""):
                for name in JASNA_DIR_NAME_HINTS:
                    cands.append(Path(root) / sub / name)
    for c in cands:
        r = validate_jasna_dir(str(c))
        if r.get("ok"):
            return {"ok": True, "path": str(c), "msg": "已自动找到"}
    return {"ok": False, "path": "",
            "msg": "未自动找到，请手动指定含 jasna.exe 的目录"}


def _exe_dir() -> str:
    import sys
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    return str(Path(__file__).parent)


# --------------------------------------------------------------------------
# 依赖自检（供界面提示用）
# --------------------------------------------------------------------------

def check_jasna_dependencies(params: dict) -> list:
    """检查所选参数依赖的外部组件，返回 [{level, text}]。

    level: "error" 阻断处理 / "warn" 仅提示
    """
    p = normalize_jasna_params(params)
    out = []
    sec = p.get("secondary_restoration")
    if sec == "tvai":
        ffm = str(p.get("tvai_ffmpeg_path") or "").strip()
        if not ffm:
            out.append({"level": "error",
                        "text": "已选择 Topaz TVAI，但未填写 Topaz FFmpeg 路径。"
                                "请在设置里指定 Topaz Video AI 的 ffmpeg.exe。"})
        elif not Path(ffm).exists():
            out.append({"level": "error",
                        "text": f"已选择 Topaz TVAI，但未找到其 ffmpeg：{ffm}。"
                                "请先安装 Topaz Video AI。"})
    if sec == "unet-4x":
        out.append({"level": "warn",
                    "text": "UNet 4x 是 Jasna 支持者专属模型，需要有效的 Jasna 许可证才能激活。"
                            "若未激活，Jasna 会报错或跳过该步骤。"})
    if p.get("restoration_model") == "ltx":
        out.append({"level": "warn",
                    "text": "LTX 模型需首次下载，且需要较新的 NVIDIA GPU。"
                            "可先用「LTX 试运行」测试速度与兼容性（画面不正确）。"})
    if p.get("ltx_fast"):
        out.append({"level": "warn", "text": "LTX 快速模式仅限 RTX 50 系列显卡。"})
    if p.get("ltx_large_canvas"):
        out.append({"level": "warn", "text": "LTX 大面积区域模式需要至少 10 GB 可用显存。"})
    if p.get("compile_basicvsrpp"):
        out.append({"level": "warn",
                    "text": "首次运行会为当前显卡编译 TensorRT 引擎，需 15-60 分钟，"
                            "期间请勿使用电脑。引擎会缓存到 model_weights，后续复用。"})
    clip = p.get("max_clip_size")
    ov = p.get("temporal_overlap")
    if isinstance(clip, int) and isinstance(ov, int) and ov * 2 >= clip:
        out.append({"level": "error",
                    "text": f"时间重叠（{ov}）必须满足 2×重叠 < 最大片段大小（{clip}），"
                            f"请把重叠调到 {(max(0, clip // 2 - 1))} 以下。"})
    if isinstance(clip, int) and p.get("fp16_mode") and not p.get("compile_basicvsrpp"):
        if clip > 180:
            out.append({"level": "warn",
                        "text": f"片段大小 {clip} 较大且未开启模型编译，处理时显存占用可能很高。"
                                "若爆显存请降到 60-90。"})
    return out
