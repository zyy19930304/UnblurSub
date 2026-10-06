# -*- coding: utf-8 -*-
"""核心逻辑自检：命名规则、勾选规则、命令构造、封装参数、配置持久化。

运行：python selftest.py
不依赖 infer.exe / ffmpeg 实体，纯函数与参数级验证。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402

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
section("1. 输出文件命名规则（原名加 -C；若原名以 -U 结尾则把 -U 改成 -UC）")
check("ABC -> ABC-C", core.target_stem("ABC"), "ABC-C")
check("ABC-U -> ABC-UC", core.target_stem("ABC-U"), "ABC-UC")
check("ABC-UC -> ABC-UC-C", core.target_stem("ABC-UC"), "ABC-UC-C")
check("ABC-C -> ABC-C-C", core.target_stem("ABC-C"), "ABC-C-C")
check("第01话 -> 第01话-C", core.target_stem("第01话"), "第01话-C")
check("movie title 2024-U -> ...-UC", core.target_stem("movie title 2024-U"), "movie title 2024-UC")
check("带空格 中文名-U", core.target_stem("[生肉] 测试 中文-U"), "[生肉] 测试 中文-UC")
check("多段-U 只替换最后一段", core.target_stem("A-U-U"), "A-U-UC")
check("名字中间有 U 不受影响", core.target_stem("UC 版 副本"), "UC 版 副本-C")

# ---------------------------------------------------------------- 2. 自动勾选规则
section("2. 自动勾选规则（勾选文件名后缀不含 -C 或 -UC）")
check("ABC 自动勾选", core.auto_selected("ABC"), True)
check("ABC-U 自动勾选（-U 是源文件，不是产物）", core.auto_selected("ABC-U"), True)
check("ABC-C 不勾选（已处理）", core.auto_selected("ABC-C"), False)
check("ABC-UC 不勾选（已处理）", core.auto_selected("ABC-UC"), False)
check("小写 -c 不勾选", core.auto_selected("abc-c"), False)
check("小写 -uc 不勾选", core.auto_selected("abc-uc"), False)
check("名字中间含 -C 仍勾选", core.auto_selected("ABC-C-raw"), True)
check("名字中间含 -U 仍勾选", core.auto_selected("ABC-U-raw"), True)
check("rule=all 时全部勾选", core.auto_selected("ABC-C", "all"), True)
check("rule=all 时产物也勾选", core.auto_selected("ABC-UC", "all"), True)
check("is_derived: ABC False", core.is_derived_name("ABC"), False)
check("is_derived: ABC-U False", core.is_derived_name("ABC-U"), False)
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
        check("默认 auto_select_rule", s["auto_select_rule"], "not_c")
        check("默认 policy=number", s["existing_output_policy"], "number")

        # 旧配置迁移：磁盘上存的是 not_u / 未知值时应落回新规则
        (core.settings_path()).write_text(
            json.dumps({"auto_select_rule": "not_u"}), encoding="utf-8")
        check("旧值 not_u 自动迁移为 not_c",
              core.load_settings()["auto_select_rule"], "not_c")
        (core.settings_path()).write_text(
            json.dumps({"auto_select_rule": "garbage"}), encoding="utf-8")
        check("未知规则值回落默认",
              core.load_settings()["auto_select_rule"], "not_c")
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
            "auto_select_rule": "not_c",
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
check("renderBadges 对 st.settings 做了保护", "st.settings ? " in web_js, True)

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

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 56)
print(f"  通过 {PASS} / {PASS + FAIL}   失败 {FAIL}")
if FAILURES:
    print("  失败明细：")
    for n, g, w in FAILURES:
        print(f"   - {n}: 实际 {g!r} != 期望 {w!r}")
print("=" * 56)
sys.exit(1 if FAIL else 0)
