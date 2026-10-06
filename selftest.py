# -*- coding: utf-8 -*-
"""核心逻辑自检：命名规则、勾选规则、命令构造、封装参数、配置持久化。

运行：python selftest.py
不依赖 infer.exe / ffmpeg 实体，纯函数与参数级验证。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import jasna_core  # noqa: E402

PASS, FAIL = 0, 0
FAILURES = []


def check(name: str, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append((name, got, want))
        print(f"  [FAIL] {name}\n         实际: {got!r}\n         期望: {want!r}")


def check_true(name: str, cond, detail=""):
    check(name + (f" ({detail})" if detail else ""), bool(cond), True)


def section(t):
    print(f"\n=== {t} ===")


# ---------------------------------------------------------------- 1. 命名规则
section("1. 输出文件命名规则（随处理模式变化：-U=已去马赛克 -C=已加字幕 -UC=两者）")

# 模式 1：只清除马赛克 -> 加 -U；原名 -C 结尾则升级为 -UC
check("模式1 ABC -> ABC-U", core.target_stem("ABC", 1), "ABC-U")
check("模式1 ABC-C -> ABC-UC", core.target_stem("ABC-C", 1), "ABC-UC")
check("模式1 ABC-C -> ABC-UC", core.target_stem("ABC-C", 1), "ABC-UC")
check("模式1 中文名", core.target_stem("第01话 开场", 1), "第01话 开场-U")
check("模式1 带空格名", core.target_stem("movie title 2024", 1), "movie title 2024-U")
check("模式1 小写 -c 也识别", core.target_stem("ABC-c", 1), "ABC-UC")

# 模式 2：只生成中文字幕 -> 加 -C；原名 -U 结尾则改为 -UC
check("模式2 ABC -> ABC-C", core.target_stem("ABC", 2), "ABC-C")
check("模式2 ABC-U -> ABC-UC", core.target_stem("ABC-U", 2), "ABC-UC")
check("模式2 中文名", core.target_stem("第01话 开场", 2), "第01话 开场-C")
check("模式2 小写 -u 也识别", core.target_stem("ABC-u", 2), "ABC-UC")

# 模式 3：两者都做 -> 一律 -UC
check("模式3 ABC -> ABC-UC", core.target_stem("ABC", 3), "ABC-UC")
check("模式3 中文名", core.target_stem("第01话 开场", 3), "第01话 开场-UC")

# 不传 mode 时默认按模式 2（与原行为兼容）
check("默认 mode 等于模式2", core.target_stem("ABC"), "ABC-C")
check("非法 mode 回退模式2", core.target_stem("ABC", 99), "ABC-C")
check("非法 mode 回退模式2（字符串）", core.target_stem("ABC", "abc"), "ABC-C")

# 后缀解析
check("parsed: ABC", jasna_core.processed_flags("ABC"), {"u": False, "c": False})
check("parsed: ABC-U", jasna_core.processed_flags("ABC-U"), {"u": True, "c": False})
check("parsed: ABC-C", jasna_core.processed_flags("ABC-C"), {"u": False, "c": True})
check("parsed: ABC-UC", jasna_core.processed_flags("ABC-UC"), {"u": True, "c": True})
check("parsed: 小写 abc-uc", jasna_core.processed_flags("abc-uc"), {"u": True, "c": True})

# ---------------------------------------------------------------- 2. 跳过与自动勾选
section("2. 按模式跳过规则与自动勾选")
# 模式 1：已去马赛克的跳过
check("模式1 ABC 不跳过", jasna_core.should_skip("ABC", 1), False)
check("模式1 ABC-C 不跳过（还能去马赛克）", jasna_core.should_skip("ABC-C", 1), False)
check("模式1 ABC-U 跳过（已去马赛克）", jasna_core.should_skip("ABC-U", 1), True)
check("模式1 ABC-UC 跳过", jasna_core.should_skip("ABC-UC", 1), True)
# 模式 2：已加字幕的跳过
check("模式2 ABC 不跳过", jasna_core.should_skip("ABC", 2), False)
check("模式2 ABC-U 不跳过（还能加字幕）", jasna_core.should_skip("ABC-U", 2), False)
check("模式2 ABC-C 跳过", jasna_core.should_skip("ABC-C", 2), True)
check("模式2 ABC-UC 跳过", jasna_core.should_skip("ABC-UC", 2), True)
# 模式 3：做过任意一项都跳过
check("模式3 ABC 不跳过", jasna_core.should_skip("ABC", 3), False)
check("模式3 ABC-U 跳过", jasna_core.should_skip("ABC-U", 3), True)
check("模式3 ABC-C 跳过", jasna_core.should_skip("ABC-C", 3), True)
check("模式3 ABC-UC 跳过", jasna_core.should_skip("ABC-UC", 3), True)

check_true("跳过原因为空（不跳过时）", jasna_core.skip_reason("ABC", 1) == "")
check_true("模式1 跳过原因含「已去马赛克」",
           "已去马赛克" in jasna_core.skip_reason("ABC-U", 1))
check_true("模式2 跳过原因含「已加中文字幕」",
           "已加中文字幕" in jasna_core.skip_reason("ABC-C", 2))
check_true("模式3 跳过原因非空", jasna_core.skip_reason("ABC-U", 3) != "")

# 自动勾选 = 按模式跳过
check("自动: 模式1 ABC 勾选", core.auto_selected("ABC", "smart", 1), True)
check("自动: 模式1 ABC-U 不勾选", core.auto_selected("ABC-U", "smart", 1), False)
check("自动: 模式2 ABC-U 勾选（源文件）", core.auto_selected("ABC-U", "smart", 2), True)
check("自动: 模式2 ABC-C 不勾选", core.auto_selected("ABC-C", "smart", 2), False)
check("自动: 模式3 ABC-C 不勾选", core.auto_selected("ABC-C", "smart", 3), False)
check("自动: 模式3 ABC-U 不勾选", core.auto_selected("ABC-U", "smart", 3), False)
check("自动: 名字中间含 -C 仍勾选", core.auto_selected("ABC-C-raw", "smart", 2), True)
check("自动: rule=all 全勾选", core.auto_selected("ABC-C", "all", 2), True)
check("自动: rule=all 时产物也勾选", core.auto_selected("ABC-UC", "all", 3), True)

# is_derived：带任一标记都算产物
check("is_derived: ABC False", core.is_derived_name("ABC"), False)
check("is_derived: ABC-U True", core.is_derived_name("ABC-U"), True)
check("is_derived: ABC-C True", core.is_derived_name("ABC-C"), True)
check("is_derived: ABC-UC True", core.is_derived_name("ABC-UC"), True)

# ---------------------------------------------------------------- 3. 命令构造
section("3. infer.exe 命令构造")
p = core.default_params()
args = core.build_infer_args(p, Path("D:/v/a.mp4"), Path("D:/tmp"))
s = " ".join(args)
check_true("包含 --device=cuda", "--device=cuda" in s, s)
check_true("包含 --task=translate", "--task=translate" in s)
check_true("包含 --sub_formats", "--sub_formats=srt,vtt,lrc" in s)
check_true("包含 --audio_suffixes", "--audio_suffixes=mp3" in s)
check_true("包含 --max_batch_size=8", "--max_batch_size=8" in s)
check_true("最后一个参数是输入文件", args[-1].replace("\\", "/") == "D:/v/a.mp4", args[-1])
check_true("默认不传 --overwrite", "--overwrite" not in s)
check_true("未设 output_dir 时传临时目录", "--output_dir=D:\\tmp" in s or "--output_dir=D:/tmp" in s, s)
check_true("smart_split=auto 不传该参数", "--smart_split_with_vad" not in s, s)

p2 = dict(p, overwrite=True, compute_type="int8_float16", enable_batching=True,
          max_batch_size=16, vad_threshold=0.6, merge_segments="enable",
          smart_split_with_vad="false", model_name_or_path="models/whisper-base")
a2 = " ".join(core.build_infer_args(p2, Path("x.mp4"), Path("t")))
check_true("overwrite 生效", "--overwrite" in a2, a2)
check_true("compute_type 生效", "--compute_type=int8_float16" in a2)
check_true("enable_batching 生效", "--enable_batching" in a2)
check_true("max_batch_size 被覆盖", "--max_batch_size=16" in a2)
check_true("vad_threshold 生效", "--vad_threshold=0.6" in a2)
check_true("merge_segments=enable", "--merge_segments" in a2)
check_true("merge_segments=enable 不带 no_", "--no_merge_segments" not in a2)
check_true("smart_split_with_vad=false", "--smart_split_with_vad=false" in a2)
check_true("model 路径生效", "--model_name_or_path=models/whisper-base" in a2)

p3 = dict(p, merge_segments="disable", output_dir="输出")
a3 = " ".join(core.build_infer_args(p3, Path("x.mp4"), Path("t")))
check_true("merge_segments=disable", "--no_merge_segments" in a3)
check_true("disable 不带 enable", "--merge_segments " not in a3 + " ")
check_true("output_dir 覆盖临时目录", "--output_dir=输出" in a3, a3)

section("3b. 内置配置与 bat 一致性")
bp = core.builtin_profiles()
check("内置配置数量 >= 5", len(bp) >= 5, True)
check("GPU 默认 device", bp["翻译 · GPU（默认）"]["device"], "cuda")
check("CPU 配置 device", bp["翻译 · CPU"]["device"], "cpu")
check("低显存 compute_type", bp["翻译 · GPU 低显存"]["compute_type"], "int8_float16")
check("高显存加速 enable_batching", bp["翻译 · GPU 高显存加速"]["enable_batching"], True)
check("高显存加速 max_batch_size", bp["翻译 · GPU 高显存加速"]["max_batch_size"], 8)
check("输出到输出文件夹", bp["翻译 · GPU（字幕输出到「输出」文件夹）"]["output_dir"], "输出")
check("全部默认 translate", all(v["task"] == "translate" for k, v in bp.items() if "转录" not in k), True)

# ---------------------------------------------------------------- 4. 封装参数
section("4. ffmpeg 软封装参数（不重新编码，轨道名=中文字幕）")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    v, s_, d = td / "a.mp4", td / "a.srt", td / "a-C.mp4"
    m = core.build_mux_args("ffmpeg", v, s_, d)
    ms = " ".join(m)
    check_true("视频流 copy", "-c:v copy" in ms, ms)
    check_true("音频流 copy", "-c:a copy" in ms, ms)
    check_true("字幕编码 mov_text (mp4)", "-c:s mov_text" in ms)
    check_true("轨道名 title=中文字幕", "title=中文字幕" in ms, ms)
    check_true("语言 chi", "language=chi" in ms)
    check_true("handler_name=中文字幕 (mp4)", "handler_name=中文字幕" in ms)
    check_true("未出现 re-encode 编码器", "libx264" not in ms and "aac" not in ms)

    d2 = td / "a-C.mkv"
    m2 = " ".join(core.build_mux_args("ffmpeg", v, s_, d2))
    check_true("mkv 字幕编码 srt", "-c:s srt" in m2, m2)
    check_true("mkv format matroska", "-f matroska" in m2)
    check_true("mkv 也有中文标题", "title=中文字幕" in m2)

    check("webm -> webvtt", core.container_subtitle_codec(".webm")[0], "webvtt")
    check("mov -> mov_text", core.container_subtitle_codec(".mov")[0], "mov_text")
    check("avi 不支持软封装", core.container_subtitle_codec(".avi")[1], False)
    try:
        core.build_mux_args("ffmpeg", v, s_, td / "a-C.avi")
        check("avi 封装应报错", "no-raise", "ValueError")
    except ValueError:
        check("avi 封装抛 ValueError", "ok", "ok")

    # SRT 规整
    raw = td / "r.srt"
    raw.write_bytes("﻿1\n00:00:01,000 --> 00:00:02,000\n你好\n\n2\n00:00:03,000 --> 00:00:04,500\n世界\n".encode("utf-8"))
    out = td / "c.srt"
    n = core.normalize_srt(raw, out)
    check("SRT 条数识别", n, 2)
    check("BOM 已去除", out.read_bytes()[:3] != b"\xef\xbb\xbf", True)
    empty = td / "e.srt"
    empty.write_text("", encoding="utf-8")
    check("空 SRT 条数为 0", core.normalize_srt(empty, td / "e2.srt"), 0)

# ---------------------------------------------------------------- 5. 输出路径
section("5. 输出路径与覆盖策略")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    src = td / "movie.mp4"
    src.write_bytes(b"x")
    dst1 = core.output_video_name(src, "", "number")
    check("首次输出名", dst1.name, "movie-C.mp4")
    dst1.write_bytes(b"y")
    check("已存在时加序号（不覆盖）", core.output_video_name(src, "", "number").name, "movie-C (2).mp4")
    check("skip 策略返回原名", core.output_video_name(src, "", "skip").name, "movie-C.mp4")
    check("overwrite 策略返回原名", core.output_video_name(src, "", "overwrite").name, "movie-C.mp4")
    srcu = td / "show-U.mp4"
    dstu = core.output_video_name(srcu, "", "number")
    check("-U 源文件输出 -UC", dstu.name, "show-UC.mp4")
    od = td / "outdir"
    check("指定输出目录", core.output_video_name(src, str(od), "number").parent, od)

# ---------------------------------------------------------------- 6. 扫描
section("6. 递归扫描与后缀过滤")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    (td / "sub" / "deep").mkdir(parents=True)
    names = ["a.mp4", "b.mkv", "c.avi", "d-U.mp4", "e-UC.mp4", "h-C.mp4",
             "sub/f.mp4", "sub/deep/g.mov", "sub/notes.txt", "readme.md"]
    for n in names:
        (td / n).write_bytes(b"0")
    items = core.scan_folder(str(td))
    paths = {i["rel"].replace("\\", "/") for i in items}
    check("递归找到 8 个视频", len(items), 8)
    check_true("含子目录文件", "sub/deep/g.mov" in paths)
    check_true("排除 txt", "sub/notes.txt" not in paths)
    check_true("排除 md", "readme.md" not in paths)
    flat = core.scan_folder(str(td), recursive=False)
    check("非递归只 6 个", len(flat), 6)
    byname = {i["rel"].replace("\\", "/"): i for i in items}
    sel = [n for n, i in byname.items() if core.auto_selected(Path(n).stem)]
    check_true("自动勾选排除 -C 产物", "h-C.mp4" not in sel)
    check_true("自动勾选排除 -UC 产物", "e-UC.mp4" not in sel)
    check_true("自动勾选包含 -U 源文件", "d-U.mp4" in sel)
    check("自动勾选数量（8 - 2 产物）", len(sel), 6)
    check("human_size", core.human_size(1536), "1.5 KB")

# ---------------------------------------------------------------- 7. 参数规整与脏检查
section("7. 参数规整 / 脏检查")
check("缺失键补默认", core.normalize_params({})["device"], "cuda")
check("非法枚举回落默认", core.normalize_params({"device": "gpu"})["device"], "cuda")
check("空串保持空串", core.normalize_params({"compute_type": ""})["compute_type"], "")
check("数字字符串转 int", core.normalize_params({"batch_size": "4"})["batch_size"], 4)
check("脏检查：相同为否", core.params_dirty({"device": "cuda"}, {"device": "cuda"}), False)
check("脏检查：不同为是", core.params_dirty({"device": "cuda"}, {"device": "cpu"}), True)
check("脏检查：忽略顺序", core.params_dirty({"device": "cuda", "task": "translate"},
                                          {"task": "translate", "device": "cuda"}), False)
check("schema 项数", len(core.PARAM_SCHEMA) >= 20, True)

# ---------------------------------------------------------------- 8. 配置持久化
section("8. 设置与配置方案持久化")
with tempfile.TemporaryDirectory() as td:
    old_dir = os.environ.get("APPDATA")
    os.environ["APPDATA"] = td
    try:
        s = core.load_settings()
        check("默认 auto_select_rule", s["auto_select_rule"], "smart")
        check("默认 mode=2", s["mode"], 2)
        check("默认 keep_intermediate=False", s["keep_intermediate"], False)
        check("默认 policy=number", s["existing_output_policy"], "number")

        # 旧配置迁移：v1/v2 的 not_u / not_c / 未知值都应收敛为 smart
        for old in ("not_u", "not_c", "garbage"):
            (core.settings_path()).write_text(
                json.dumps({"auto_select_rule": old}), encoding="utf-8")
            check(f"旧值 {old} 迁移为 smart",
                  core.load_settings()["auto_select_rule"], "smart")
        # 旧配置没有 mode 字段时回落为 2
        (core.settings_path()).write_text("{}", encoding="utf-8")
        check("缺 mode 回落为 2", core.load_settings()["mode"], 2)
        (core.settings_path()).write_text(
            json.dumps({"mode": 99}), encoding="utf-8")
        check("非法 mode 回落为 2", core.load_settings()["mode"], 2)
        (core.settings_path()).write_text(
            json.dumps({"mode": 1}), encoding="utf-8")
        check("合法 mode=1 保持", core.load_settings()["mode"], 1)
        (core.settings_path()).write_text(
            json.dumps({"auto_select_rule": "all"}), encoding="utf-8")
        check("合法值 all 保持不变",
              core.load_settings()["auto_select_rule"], "all")
        (core.settings_path()).write_text("{}", encoding="utf-8")

        core.save_settings({"fw_dir": "D:/fw", "last_profile": "x"})
        check("设置已持久化", core.load_settings()["fw_dir"], "D:/fw")
        core.save_profile("我的低显存", {"device": "cuda", "compute_type": "int8_float16"}, False)
        profs = core.load_profiles()
        check_true("配置已保存", "我的低显存" in profs)
        check("配置内容正确", profs["我的低显存"]["params"]["compute_type"], "int8_float16")
        try:
            core.save_profile("我的低显存", {}, False)
            check("重名应报错", "no-raise", "ValueError")
        except ValueError:
            check("重名抛 ValueError", "ok", "ok")
        core.save_profile("我的低显存", {"device": "cpu"}, True)
        check("覆盖成功", core.load_profiles()["我的低显存"]["params"]["device"], "cpu")
        allp = core.all_profiles()
        check_true("内置与用户配置合并", len(allp) >= 8)
        check_true("内置标记正确", allp["翻译 · GPU（默认）"]["builtin"] is True)
        check_true("用户配置标记正确", allp["我的低显存"]["builtin"] is False)
        core.delete_profile("我的低显存")
        check("删除配置成功", "我的低显存" not in core.all_profiles(), True)
    finally:
        if old_dir is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_dir

# ---------------------------------------------------------------- 9. 路径校验
section("9. Faster Whisper 路径校验")
check("空路径不通过", core.validate_fw_dir("")["ok"], False)
check("不存在路径不通过", core.validate_fw_dir("D:/definitely/not/here")["ok"], False)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    r = core.validate_fw_dir(str(td))
    check("缺 infer.exe 不通过", r["ok"], False)
    (td / "infer.exe").write_bytes(b"MZ")
    (td / "models").mkdir()
    (td / "generation_config.json5").write_text("{}", encoding="utf-8")
    r2 = core.validate_fw_dir(str(td))
    check("含 infer.exe 通过", r2["ok"], True)
    check("检测到 models", r2["has_models"], True)
    check("检测到 generation_config", r2["has_generation_config"], True)
    check("infer_exe 拼接", core.infer_exe(str(td)).name, "infer.exe")

# ---------------------------------------------------------------- 10. 路由签名
section("10. 服务路由签名一致性（防 GET/POST 调用不匹配）")
import inspect  # noqa: E402
import server as _srv  # noqa: E402
bad = []
for name, fn in _srv.ROUTES.items():
    try:
        sig = list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        continue
    if len(sig) != 2:
        bad.append((name, sig))
check("所有路由均为 (body, query) 两参数", bad, [])
check("路由数量 >= 20", len(_srv.ROUTES) >= 20, True)

# 逐个以 GET/POST 方式试调（不产生副作用的那几个）
for path, fn in [("/api/state", _srv.api_state), ("/api/profiles", _srv.api_profiles),
                 ("/api/drives", _srv.api_drives), ("/api/job/state", _srv.api_job_state),
                 ("/api/job/logs", _srv.api_job_logs), ("/api/pick_default_fw", _srv.api_pick_default_fw)]:
    try:
        fn(None, {})
        ok = True
    except Exception as e:
        ok = False
        err = e
    check_true(f"GET {path} 可调用", ok, "" if ok else str(err))
for path, body in [("/api/validate_fw", {"path": "D:/nope"}), ("/api/browse", {"path": "D:/nope"}),
                   ("/api/profile/delete", {"name": "翻译 · GPU（默认）"})]:
    fn = _srv.ROUTES[path]
    try:
        fn(body, {})
        check_true(f"POST {path} 抛业务异常而非 TypeError", True)
    except ValueError:
        check_true(f"POST {path} 抛业务异常而非 TypeError", True)
    except TypeError as e:
        check_true(f"POST {path} 抛业务异常而非 TypeError", False, str(e))

# ---------------------------------------------------------------- 11. 前端/后端契约
# ----------------------------------------------- 10b. 规则版本与历史勾选失效
section("10b. 规则版本 / 历史勾选失效")
check("RULE_VERSION 已定义", isinstance(core.RULE_VERSION, int) and core.RULE_VERSION >= 2, True)
check("_rule_version 在默认设置中", "_rule_version" in core.DEFAULT_SETTINGS, True)
check("_auto_select 在默认设置中", "_auto_select" in core.DEFAULT_SETTINGS, True)

with tempfile.TemporaryDirectory() as td:
    old_dir = os.environ.get("APPDATA")
    os.environ["APPDATA"] = td
    try:
        # 模拟旧版本留下的勾选记录：-U 被标为 False（老规则的结果）
        for n in ("a.mp4", "b-U.mp4", "c-C.mp4"):
            (Path(td) / n).write_bytes(b"0")
        core.save_settings({
            "auto_select_rule": "smart",
            "mode": 2,
            "_auto_select": False,
            "_rule_version": 1,          # 老版本号
            "selected": {"a.mp4": True, "b-U.mp4": False, "c-C.mp4": False},
        })
        r = _srv.api_scan({"folder": td}, None)
        names = {Path(x["path"]).name: x["selected"] for x in r["items"]}
        check("版本过期时 -U 会被重新勾选", names.get("b-U.mp4"), True)
        check("版本过期时 -C 保持未勾选", names.get("c-C.mp4"), False)
        check("版本过期时普通文件勾选", names.get("a.mp4"), True)

        # 规则切换应清空历史勾选
        _srv.api_save_settings({"auto_select_rule": "all"}, None)
        s2 = core.load_settings()
        check("切换规则后 _auto_select 归 True", s2["_auto_select"], True)
        check("切换规则后 selected 已清空", s2["selected"], {})
    finally:
        if old_dir is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_dir

section("11. 前端交互契约（浏览弹窗 / 退出通道 / 设置保存）")
web_js = (Path(__file__).parent / "web" / "app.js").read_text(encoding="utf-8")
web_html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")

check("app.js 存在 openBrowse", "function openBrowse" in web_js, True)
check("openBrowse 会打开 modalBrowse", "openModal('#modalBrowse')" in web_js, True)
check("modalBrowse 在 HTML 中存在", 'id="modalBrowse"' in web_html, True)
check("browseTo 会绑定目录点击事件", "browseList div[data-path]" in web_js, True)
check("browseTo 有空目录兜底", "此目录下没有子文件夹" in web_js, True)

check("finishQuit 调用 pywebview 桥接", "pywebview.api.quit" in web_js, True)
check("finishQuit 兜底走 HTTP shutdown", "/api/shutdown" in web_js, True)
check("doQuit 不再从 DOM 全量覆盖设置", "await saveSettings({}).catch" not in web_js,
      True)
check("存在增量保存函数 persistSettings", "function persistSettings" in web_js, True)

# app.py：SHUTDOWN_HOOK 必须能销毁 pywebview 窗口
app_py = (Path(__file__).parent / "app.py").read_text(encoding="utf-8")
check("SHUTDOWN_HOOK 会销毁窗口", "holder[\"window\"]" in app_py or "w.destroy()" in app_py, True)
check("pywebview 有后台 watcher 线程", "def watcher():" in app_py, True)
check("_Bridge.quit 会销毁窗口", "w.destroy()" in app_py, True)

# 后端：save_settings 只接受白名单字段（防止前端误传空值覆盖）
with tempfile.TemporaryDirectory() as td:
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = td
    try:
        core.save_settings({"fw_dir": "D:/keepme", "video_exts": ["mp4", "mkv"]})
        # 模拟前端只提交 last_profile（修复后的 doQuit 行为）
        r = _srv.api_save_settings({"last_profile": ""}, None)
        check("只提交 last_profile 时 fw_dir 不被清空",
              r["settings"]["fw_dir"], "D:/keepme")
        check("只提交 last_profile 时 video_exts 不被清空",
              r["settings"]["video_exts"], ["mp4", "mkv"])
        # 模拟前端误传空字符串（旧版doQuit 的 bug）
        r2 = _srv.api_save_settings({"fw_dir": "", "video_exts": []}, None)
        check("⚠ 传空值确实会覆盖（这正是需要前端修复的原因）",
              r2["settings"]["fw_dir"], "")
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old

# ---------------------------------------------------------------- 12. 批量操作回归
section("12. 批量操作回归（清空勾选 / 重新扫描移除已删文件）")

# --- 12a. mode=none 必须真的清空（曾因缺 none 分支而全部勾上）---
with tempfile.TemporaryDirectory() as td:
    old_dir = os.environ.get("APPDATA")
    os.environ["APPDATA"] = td
    try:
        core.save_settings({"_rule_version": core.RULE_VERSION, "_auto_select": False})
        paths = [f"D:/v/{n}.mp4" for n in ("a", "b", "c")]
        _srv.api_set_selected({"paths": paths, "mode": "all"}, None)
        check("mode=all 全部勾上",
              all(_srv.api_set_selected({"paths": paths, "mode": "all"}, None)["selected"][p]
                  for p in paths), True)
        r = _srv.api_set_selected({"paths": paths, "mode": "none"}, None)
        check("mode=none 全部取消（回归：曾错误地全部勾上）",
              any(r["selected"][p] for p in paths), False)
        r2 = _srv.api_set_selected({"paths": paths, "mode": "none"}, None)
        check("mode=none 重复调用仍为空",
              any(r2["selected"][p] for p in paths), False)
        r3 = _srv.api_set_selected({"paths": paths, "mode": "invert"}, None)
        check("mode=invert 后变为全选",
              all(r3["selected"][p] for p in paths), True)
        r4 = _srv.api_set_selected({"paths": paths, "mode": "del"}, None)
        check("mode=del 全部取消", any(r4["selected"][p] for p in paths), False)
        r5 = _srv.api_set_selected({"paths": paths, "mode": "set", "value": True}, None)
        check("mode=set+value=True 全部勾上", all(r5["selected"][p] for p in paths), True)
    finally:
        if old_dir is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_dir

# --- 12b. 重新扫描后已删除文件不应留在 selected 里 ---
with tempfile.TemporaryDirectory() as td:
    old_dir = os.environ.get("APPDATA")
    os.environ["APPDATA"] = td
    try:
        vdir = Path(td) / "vids"
        vdir.mkdir()
        keep = vdir / "keep.mp4"
        gone = vdir / "gone.mp4"
        for f in (keep, gone):
            f.write_bytes(b"0")
        core.save_settings({"_rule_version": core.RULE_VERSION, "_auto_select": False,
                            "selected": {str(keep): True, str(gone): True}})
        gone.unlink()                      # 模拟用户删除文件
        r = _srv.api_scan({"folder": str(vdir), "folders": [str(vdir)]}, None)
        names = sorted(x["name"] for x in r["items"])
        check("扫描结果不含已删文件", names, ["keep.mp4"])
        sel = core.load_settings()["selected"]
        check("已删文件的勾选记录被清理", str(gone) in sel, False)
        check("未删文件的记录保留", sel.get(str(keep)), True)
        check("stale_cleared 计数正确", r["stale_cleared"], 1)
        # 再次扫描不应报错（stale 为空的情况）
        r2 = _srv.api_scan({"folder": str(vdir), "folders": [str(vdir)]}, None)
        check("重复扫描 stale_cleared 为 0", r2["stale_cleared"], 0)
        # 只传 folder（不传 folders）时：等价于单目录清理，范围仅限该目录
        # 注意用「真实存在但不在该目录下」的文件，才能验证范围隔离
        outside = Path(td) / "outside.mp4"
        outside.write_bytes(b"0")
        other = str(outside)
        core.save_settings({"selected": {str(keep): True, other: True}})
        _srv.api_scan({"folder": str(vdir)}, None)
        sel2 = core.load_settings()["selected"]
        check("只传 folder 时保留目录外记录", other in sel2, True)
        check("只传 folder 时保留目录内有效记录", sel2.get(str(keep)), True)
        # 两者都不传时完全不清理
        core.save_settings({"selected": {other: True}})
        _srv.api_scan({}, None)
        check("不传 folder/folders 时不清理",
              other in core.load_settings()["selected"], True)
    finally:
        if old_dir is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_dir

check("scanAll 用整目录替换而非累加", "S.files.delete(key)" in web_js, True)
check("scanAll 传 folders 让后端清理", "folders: fs" in web_js, True)
check("无文件夹时清空列表", "S.files.clear()" in web_js, True)

# ---------------------------------------------------------------- 13. 列表滚动布局
section("13. 任务列表滚动（曾因用 table 导致无法滚动）")
css = (Path(__file__).parent / "web" / "style.css").read_text(encoding="utf-8")

# 关键：滚动容器不能是 table/tbody，overflow 在表格行组上不生效
check("HTML 用 div 作滚动容器（非 table）", '<div class="jobs" id="jobList">' in web_html, True)
check("已移除 table.jobs 结构", '<table class="jobs">' not in web_html, True)
check("renderJobs 输出 div 行（非 tr）", 'class="job-row' in web_js, True)
check("renderJobs 不再输出 <tr>", "`<tr class=" not in web_js and "'<tr" not in web_js, True)

check(".jobs 有 overflow:auto", re.search(r"\.jobs\{[^}]*overflow:auto", css) is not None, True)
check(".jobs 有 min-height:0（关键）",
      re.search(r"\.jobs\{[^}]*min-height:0", css) is not None, True)
check(".log-body 用 minmax(0,1fr) 限制行轨道",
      "grid-template-rows:minmax(0,1fr)" in css.replace(" ", "").replace("\n", "")
      or "minmax(0,1fr)" in css, True)
check(".log-body 有 overflow:hidden",
      re.search(r"\.log-body\{[^}]*overflow:hidden", css) is not None, True)
check(".log-box 也有 min-height:0",
      re.search(r"\.log-box\{[^}]*min-height:0", css) is not None, True)
check(".job-row 用 grid 布局", re.search(r"\.job-row\{[^}]*display:grid", css) is not None, True)
check("任务列表有专门的滚动条样式", ".jobs::-webkit-scrollbar-thumb" in css, True)
check("空状态有提示", 'class="j-empty"' in web_js, True)

# ------------------------------------------- 13b. 后端响应残缺时的前端容错
section("13b. 前端容错（曾因 S.settings=undefined 报reading fw_dir）")
check("存在 cfg() 安全读取函数", "function cfg(" in web_js, True)
check("cfg 对 undefined 返回 fallback", "S.settings && S.settings[key]" in web_js, True)
check("存在 applySettings() 校验函数", "function applySettings(" in web_js, True)
check("applySettings 拒绝非对象", "typeof s !== 'object'" in web_js, True)
check("applySettings 失败返回 false", "return false;" in web_js, True)

# 所有 S.settings.xxx 直接访问都必须改走 cfg()
direct = re.findall(r"S\.settings\.[a-z_]+", web_js)
allowed = {"S.settings[key]"}          # 仅 cfg() 内部允许
leftover = [d for d in direct if d not in allowed]
check("无残留的 S.settings.xxx 直接访问（崩溃隐患）", leftover, [])
check("renderSettingsForm 改用 cfg", "$('#inpFwDir').value = cfg('fw_dir')" in web_js, True)
check("scanAll 改用 cfg", "const fs = cfg('folders', []);" in web_js, True)
check("start() 校验 fw_dir 改用 cfg", "path: cfg('fw_dir', '')" in web_js, True)
check("saveSettings 对残缺响应抛明确错误", "服务返回异常（缺少 settings）" in web_js, True)
# v2：renderBadges 重写后改为统一走 cfg()，不再直读 st.settings
check("renderBadges 改用 cfg 读取路径", "b.title = cfg('fw_dir');" in web_js, True)
check("renderBadges 不再直读 st.settings", "st.settings ? " not in web_js, True)

# 后端必须始终返回 settings 键
with tempfile.TemporaryDirectory() as td:
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = td
    try:
        r = _srv.api_save_settings({"fw_dir": "D:/x"}, None)
        check("后端返回含 settings 键", "settings" in r, True)
        check("后端返回含 fw 键", "fw" in r, True)
        check("后端返回含 ffmpeg 键", "ffmpeg" in r, True)
        check("settings 是对象", isinstance(r.get("settings"), dict), True)
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old

# ================================================================
# 14. Jasna 集成（去马赛克模块）
# ================================================================
section("14. Jasna 参数 schema 与内置配置")
check("JASNA schema 项数 >= 35", len(jasna_core.JASNA_PARAM_SCHEMA) >= 35, True)
check("JASNA 分组数", len(jasna_core.JASNA_PARAM_GROUPS), 6)
check("Jasna 6 个分组齐全",
      set(jasna_core.JASNA_PARAM_GROUPS),
      {"修复模型", "基本处理", "高级处理", "二次修复", "编码输出", "导出后动作"})
# 每项都必须有中文说明与取值依据（用户明确要求）
_no_help = [f["key"] for f in jasna_core.JASNA_PARAM_SCHEMA if not f.get("help")]
check("所有参数都有说明文案", _no_help, [])
_no_arg = [f["key"] for f in jasna_core.JASNA_PARAM_SCHEMA
           if not f.get("arg") and not f.get("depends_on") and not f.get("local_only")]
check("所有参数都映射到 CLI 或有说明", _no_arg, [])
# 数值型必须有 range，否则界面无法提示范围
_bad_range = [f["key"] for f in jasna_core.JASNA_PARAM_SCHEMA
              if f["type"] in ("int", "float") and f.get("arg") and not f.get("range")]
check("所有数值参数都声明了取值范围", _bad_range, [])
# 枚举型必须有中文选项标签
_bad_opt = [f["key"] for f in jasna_core.JASNA_PARAM_SCHEMA
            if f["type"] == "enum" and not f.get("option_labels")]
check("所有枚举参数都有中文选项标签", _bad_opt, [])

jp = jasna_core.jasna_default_params()
check("默认检测模型", jp["detection_model"], "rfdetr-v6")
check("默认片段大小", jp["max_clip_size"], 90)
check("默认编码器", jp["codec"], "hevc")
check_true("内置 Jasna 配置 >= 4套", len(jasna_core.builtin_jasna_profiles()) >= 4)
check_true("内置配置均带说明",
           all(v.get("help") for v in jasna_core.builtin_jasna_profiles().values()))

# ---------------------------------------------------------------- 15. Jasna 命令构造
section("15. jasna.exe 命令构造与参数隔离")
_a = jasna_core.build_jasna_args(jp, Path("D:/v/in.mkv"), Path("D:/v/out.mkv"), Path("D:/wd"))
_s = " ".join(_a)
check_true("含 --restoration-model-name", "--restoration-model-name" in _s, _s[:120])
check_true("含 --max-clip-size=90", "--max-clip-size=90" in _s)
check_true("含 --detection-model=rfdetr-v6", "--detection-model=rfdetr-v6" in _s)
check_true("含 --codec=hevc", "--codec=hevc" in _s)
check_true("含 --working-directory", "--working-directory=D:/wd" in _s, _s)
check_true("工作目录用正斜杠（Jasna 按 POSIX 风格解析）",
           "--working-directory=D:/wd" in _a, str([x for x in _a if "working" in x]))
check_true("含 --input", "--input=D:" in _s or "--input" in _s)
check_true("含 --output", "--output" in _s)
check_true("FP16 默认开启", "--fp16" in _a)
check_true("模型编译默认开启", "--compile-basicvsrpp" in _a)
# --no-browser 是固定常量（禁用 Jasna 自带浏览器），不是被关闭的布尔项
check_true("已开启布尔项不带 --no- 前缀",
           not any(x.startswith("--no-") for x in _a if x != "--no-browser"),
           str([x for x in _a if x.startswith("--no-")]))

# 关键：未启用的分支不得传参，否则 Jasna 行为不可预期
check_true("默认不传 --tvai-*", not any("--tvai" in x for x in _a))
check_true("默认不传 --rtx-*", not any("--rtx" in x for x in _a))
check_true("未选 LTX 时不传 --ltx-seed", not any("--ltx" in x for x in _a))

_p = dict(jp); _p["secondary_restoration"] = "rtx-super-res"
_a2 = jasna_core.build_jasna_args(_p, Path("i.mkv"), Path("o.mkv"), None)
check_true("选 RTX 后传 --rtx-scale", any("--rtx-scale" in x for x in _a2))
check_true("选 RTX 后仍不传 --tvai-*", not any("--tvai" in x for x in _a2))

_p = dict(jp); _p["secondary_restoration"] = "tvai"
_a3 = jasna_core.build_jasna_args(_p, Path("i.mkv"), Path("o.mkv"), None)
check_true("选 TVAI 后传 --tvai-model", any("--tvai-model" in x for x in _a3))
check_true("选 TVAI 后不传 --rtx-*", not any("--rtx" in x for x in _a3))

_p = dict(jp); _p["restoration_model"] = "ltx"
_a4 = jasna_core.build_jasna_args(_p, Path("i.mkv"), Path("o.mkv"), None)
check_true("选 LTX 后传 --ltx-seed", any("--ltx-seed" in x for x in _a4))

# 队列级动作必须剥离（否则每处理一个文件就关机一次）
_p = dict(jp); _p["post_export_action"] = "shutdown"; _p["post_export_command"] = "shutdown /s /t 60"
_a5 = jasna_core.build_jasna_args(_p, Path("i.mkv"), Path("o.mkv"), None)
check_true("命令中不含 post-export", not any("post-export" in x for x in _a5))
check_true("命令中不含 shutdown 字样", not any("shutdown" in x for x in _a5))

# 参数规整
check_true("越界值被规整到类型",
           isinstance(jasna_core.normalize_jasna_params({"max_clip_size": "abc"})["max_clip_size"], str))
check("缺失键补默认",
      jasna_core.normalize_jasna_params({})["detection_model"], "rfdetr-v6")
check("未知键被丢弃", "no_such_key" in jasna_core.normalize_jasna_params({"no_such_key": 1}), False)
check("非法枚举回落默认",
      jasna_core.normalize_jasna_params({"codec": "vp9"})["codec"], "hevc")

# ---------------------------------------------------------------- 16. Jasna 依赖自检
section("16. Jasna 参数合法性预检")
_iss = jasna_core.check_jasna_dependencies(jp)
check_true("默认配置无阻断级问题", not [i for i in _iss if i["level"] == "error"], str(_iss))
_p = dict(jp); _p["secondary_restoration"] = "tvai"; _p["tvai_ffmpeg_path"] = "D:/nope/ffmpeg.exe"
_iss = jasna_core.check_jasna_dependencies(_p)
check_true("TVAI 缺 ffmpeg 报 error",
           any(i["level"] == "error" and "Topaz" in i["text"] for i in _iss), str(_iss))
_p = dict(jp); _p["tvai_ffmpeg_path"] = ""
_iss = jasna_core.check_jasna_dependencies(dict(jp, secondary_restoration="tvai", tvai_ffmpeg_path=""))
check_true("TVAI 未填路径报 error",
           any(i["level"] == "error" and "未填写" in i["text"] for i in _iss), str(_iss))
# 2*overlap 必须 < clip_size（Jasna 硬约束）
_iss = jasna_core.check_jasna_dependencies(dict(jp, max_clip_size=20, temporal_overlap=15))
check_true("重叠过大报 error",
           any(i["level"] == "error" and "重叠" in i["text"] for i in _iss), str(_iss))
_iss = jasna_core.check_jasna_dependencies(dict(jp, restoration_model="ltx"))
check_true("选 LTX 给出提示",
           any("LTX" in i["text"] for i in _iss), str(_iss))
_iss = jasna_core.check_jasna_dependencies(dict(jp, ltx_fast=True))
check_true("LTX 快速模式提示仅限 50 系",
           any("RTX 50" in i["text"] for i in _iss), str(_iss))
_iss = jasna_core.check_jasna_dependencies(dict(jp, secondary_restoration="unet-4x"))
check_true("UNet 4x 提示需许可证",
           any(i["level"] == "warn" and "许可证" in i["text"] for i in _iss), str(_iss))

# ---------------------------------------------------------------- 17. Jasna 目录校验
section("17. Jasna 目录校验与 API 路由")
check("空路径不通过", jasna_core.validate_jasna_dir("")["ok"], False)
check("不存在的路径不通过", jasna_core.validate_jasna_dir("D:/definitely_not_here_12345")["ok"], False)
check("jasna_exe 拼接正确",
      jasna_core.jasna_exe("D:/jasna").name, "jasna.exe")
_r = jasna_core.validate_jasna_dir("D:/no_such_dir_xyz")
check_true("失败时给出中文原因", "未配置" in _r["msg"] or "不存在" in _r["msg"], _r["msg"])

with tempfile.TemporaryDirectory() as td:
    old_dir = os.environ.get("APPDATA")
    os.environ["APPDATA"] = td
    try:
        # 伪造一个 Jasna 目录结构
        jd = Path(td) / "jasna-test"
        (jd / "model_weights").mkdir(parents=True)
        (jd / "tools").mkdir()
        (jd / "jasna.exe").write_bytes(b"MZ")
        (jd / "tools" / "ffmpeg.exe").write_bytes(b"MZ")
        (jd / "model_weights" / "x.onnx").write_bytes(b"x")
        _r = jasna_core.validate_jasna_dir(str(jd))
        check("伪造目录校验通过", _r["ok"], True)
        check("识别出模型权重", _r["model_count"], 1)
        check("识别出内置 ffmpeg", _r["has_ffmpeg"], True)
        check("无警告", _r["warn"], "")
        # 缺 tools\ffmpeg.exe 应给警告但不阻断
        (jd / "tools" / "ffmpeg.exe").unlink()
        _r = jasna_core.validate_jasna_dir(str(jd))
        check("缺 ffmpeg 仍通过", _r["ok"], True)
        check_true("缺 ffmpeg 给出警告", "ffmpeg" in _r["warn"], _r["warn"])
        # 缺 model_weights 应给警告
        shutil.rmtree(jd / "model_weights")
        _r = jasna_core.validate_jasna_dir(str(jd))
        check_true("缺 model_weights 给出警告", "model_weights" in _r["warn"], _r["warn"])
        # 中文目录名应提示（Jasna 官方要求纯英文路径）
        cn = Path(td) / "中文目录jasna"
        cn.mkdir()
        (cn / "jasna.exe").write_bytes(b"MZ")
        _r = jasna_core.validate_jasna_dir(str(cn))
        check_true("中文目录名给出提示", "英文" in _r["warn"], _r["warn"])

        # ---- API 路由存在性 ----
        for p in ["/api/jasna_profiles", "/api/jasna_profile/save",
                  "/api/jasna_profile/delete", "/api/jasna_profile/rename",
                  "/api/jasna_check", "/api/jasna_preview",
                  "/api/validate_jasna", "/api/pick_default_jasna"]:
            check_true(f"路由已注册 {p}", p in _srv.ROUTES, sorted(_srv.ROUTES))

        # ---- state 返回 Jasna 数据 ----
        _st = _srv.api_state(None, None)
        check("state 含 jasna_schema", isinstance(_st.get("jasna_schema"), list), True)
        check("state 含 jasna_groups", len(_st.get("jasna_groups") or []), 6)
        check("state 含 jasna_profiles", len(_st.get("jasna_profiles") or []) >= 4, True)
        check("state 含 modes", len(_st.get("modes") or []), 3)
        check("state 含 jasna 校验结果", "jasna" in _st, True)
        check("state 含 jasna_baseline", "jasna_baseline" in _st, True)

        # ---- Jasna 配置 CRUD ----
        jasna_core.save_jasna_profile("我的超分配置", {"max_clip_size": 120}, False)
        _pr = jasna_core.all_jasna_profiles()
        check_true("Jasna 配置已保存", "我的超分配置" in _pr)
        check("配置内容正确", _pr["我的超分配置"]["params"]["max_clip_size"], 120)
        check_true("用户配置标记为非内置", _pr["我的超分配置"]["builtin"] is False)
        try:
            jasna_core.save_jasna_profile("我的超分配置", {}, False)
            check("Jasna 配置重名应报错", "no-raise", "ValueError")
        except ValueError:
            check("Jasna 配置重名抛 ValueError", "ok", "ok")
        jasna_core.save_jasna_profile("我的超分配置", {"max_clip_size": 150}, True)
        check("Jasna 配置覆盖成功",
              jasna_core.load_jasna_profiles()["我的超分配置"]["params"]["max_clip_size"], 150)
        try:
            _srv.api_jasna_profile_delete({"name": "标准（1080p 均衡）"}, None)
            check("删除内置 Jasna 配置应报错", "no-raise", "ValueError")
        except ValueError:
            check("删除内置 Jasna 配置抛 ValueError", "ok", "ok")
        jasna_core.delete_jasna_profile("我的超分配置")
        check_true("Jasna 配置已删除", "我的超分配置" not in jasna_core.all_jasna_profiles())

        # ---- start 按模式校验依赖 ----
        _sv = Path(td) / "scan"; _sv.mkdir()
        (_sv / "a.mp4").write_bytes(b"0")
        _srv.api_add_folder({"folder": str(_sv)}, None)
        _jp = jasna_core.jasna_default_params()
        # 模式 1 但未配置 jasna_dir -> 应报错
        core.save_settings({"mode": 1, "jasna_dir": "", "fw_dir": ""})
        try:
            _srv.api_start({"paths": [str(_sv / "a.mp4")], "mode": 1,
                            "jasna_params": _jp}, None)
            check("模式1 缺 Jasna 应报错", "no-raise", "ValueError")
        except ValueError as e:
            check_true("模式1 缺 Jasna 抛错", "Jasna" in str(e), str(e))
        # 模式 2 但未配置 fw_dir -> 应报错
        core.save_settings({"mode": 2, "jasna_dir": "", "fw_dir": ""})
        try:
            _srv.api_start({"paths": [str(_sv / "a.mp4")], "mode": 2}, None)
            check("模式2 缺 FW 应报错", "no-raise", "ValueError")
        except ValueError as e:
            check_true("模式2 缺 FW 抛错", "Faster Whisper" in str(e) or "infer" in str(e), str(e))
        # 模式 2 且未配 Jasna -> 不应因 Jasna 报错（只加字幕不需要 Jasna）
        core.save_settings({"mode": 2, "jasna_dir": "", "fw_dir": "D:/fw_dummy"})
        # 伪造一个可用的 FW 目录，让校验通过，从而单独验证「空列表」这一项
        fwd = Path(td) / "fw-test"
        fwd.mkdir()
        (fwd / "infer.exe").write_bytes(b"MZ")
        core.save_settings({"mode": 2, "jasna_dir": "", "fw_dir": str(fwd)})
        try:
            _srv.api_start({"paths": [], "mode": 2}, None)
            check("空文件列表应报错", "no-raise", "ValueError")
        except ValueError as e:
            check_true("空列表抛错", "没有勾选" in str(e), str(e))
        # 模式 2 即使 Jasna 未配置也允许启动（只加字幕不需要 Jasna）
        _r = _srv.api_start({"paths": [str(_sv / "a.mp4")], "mode": 2,
                             "params": core.default_params()}, None)
        check("模式2 无需 Jasna 即可提交", len(_r["jobs"]), 1)
        _srv.MANAGER.stop(True)

        # ---- 预览命令按模式给出不同命令链 ----
        core.save_settings({"mode": 1, "jasna_dir": str(jd), "fw_dir": "D:/fw_dummy"})
        _pv = _srv.api_preview_cmd({"path": str(_sv / "a.mp4"), "mode": 1,
                                    "jasna_params": _jp}, None)
        check_true("模式1 预览含 jasna", "jasna.exe" in _pv["cmd"], _pv["cmd"][:80])
        check_true("模式1 预览不含 infer", "infer.exe" not in _pv["cmd"], _pv["cmd"][:80])
        _pv2 = _srv.api_preview_cmd({"path": str(_sv / "a.mp4"), "mode": 2}, None)
        check_true("模式2 预览含 infer", "infer.exe" in _pv2["cmd"], _pv2["cmd"][:80])
        check_true("模式2 预览含 ffmpeg", "ffmpeg" in _pv2["cmd"], _pv2["cmd"][:80])
        check_true("模式2 预览不含 jasna", "jasna.exe" not in _pv2["cmd"], _pv2["cmd"][:80])
        _pv3 = _srv.api_preview_cmd({"path": str(_sv / "a.mp4"), "mode": 3,
                                     "jasna_params": _jp, "params": core.default_params()}, None)
        check_true("模式3 预览含三者",
                   ("jasna.exe" in _pv3["cmd"] and "infer.exe" in _pv3["cmd"]
                    and "ffmpeg" in _pv3["cmd"]), _pv3["cmd"][:120])
        check("模式3 输出名带 -UC", _pv3["dst"].endswith("-UC.mp4"), True)

        # ---- scan 按模式返回 skip/target ----
        for n in ["m1.mp4", "m2-U.mp4", "m3-C.mp4", "m4-UC.mp4"]:
            (_sv / n).write_bytes(b"0")
        _exp = {1: {"m1.mp4": False, "m2-U.mp4": True, "m3-C.mp4": False, "m4-UC.mp4": True},
                2: {"m1.mp4": False, "m2-U.mp4": False, "m3-C.mp4": True, "m4-UC.mp4": True},
                3: {"m1.mp4": False, "m2-U.mp4": True, "m3-C.mp4": True, "m4-UC.mp4": True}}
        for md, exp in _exp.items():
            core.save_settings({"mode": md})
            _r = _srv.api_scan({"folder": str(_sv), "mode": md}, None)
            got = {Path(x["path"]).name: x["skip"] for x in _r["items"] if Path(x["path"]).name in exp}
            check(f"模式{md} 跳过判定正确", got, exp)
            # 跳过的文件不应给出输出名
            _bad = [x["name"] for x in _r["items"] if x["skip"] and x["target"]]
            check(f"模式{md} 跳过项无输出名", _bad, [])
            # 跳过的文件不应被勾选
            _sel = [x["name"] for x in _r["items"] if x["skip"] and x["selected"]]
            check(f"模式{md} 跳过项未勾选", _sel, [])
    finally:
        if old_dir is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_dir

# ---------------------------------------------------------------- 18. 前端契约
section("18. 前端已适配 Jasna 与模式")
_js = (Path(__file__).parent / "web" / "app.js").read_text(encoding="utf-8")
_html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
for key in ["renderJasnaParams", "renderJasnaProfiles", "applyMode", "setMode",
            "switchTab", "checkJasnaIssues", "markJasnaDirty", "loadJasnaProfile",
            "checkDirs", "modeSkipHint"]:
    check_true(f"前端存在 {key}()", f"function {key}(" in _js)
for eid in ["modeBtns", "modeHint", "jasnaForm", "selJasnaProfile", "inpJasnaDir",
            "btnJasnaBrowse", "btnJasnaAuto", "jasnaBadge", "jasnaIssues",
            "paramTabs", "paneJasna", "chkKeepIntermediate", "btnPickJasna"]:
    check_true(f"HTML 含 #{eid}", f'id="{eid}"' in _html)
check_true("前端保存时带 mode", "mode: S.mode" in _js)
check_true("前端 start 传 jasna_params", "jasna_params: S.jasnaParams" in _js)
check_true("前端调 /api/jasna_profile/save", "/api/jasna_profile/save" in _js)
check_true("前端不再有旧规则 not_c", "not_c" not in _js)
check_true("旧检查函数已移除（改为 checkDirs）", "function checkFw(" not in _js)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 56)
print(f"  通过 {PASS} / {PASS + FAIL}   失败 {FAIL}")
if FAILURES:
    print("  失败明细：")
    for n, g, w in FAILURES:
        print(f"   - {n}: 实际 {g!r} != 期望 {w!r}")
print("=" * 56)
sys.exit(1 if FAIL else 0)
