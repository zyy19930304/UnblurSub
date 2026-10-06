# -*- coding: utf-8 -*-
"""本地 HTTP 服务：静态文件 + JSON API。仅监听 127.0.0.1。"""
from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import core
import jasna_core
from jobs import MANAGER

WEB_DIR = (Path(sys._MEIPASS) / "web" if getattr(sys, "frozen", False)
           else Path(__file__).parent / "web")

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png"}


# --------------------------------------------------------------------------
# 业务处理
# --------------------------------------------------------------------------

def _fw_ready() -> bool:
    st = core.load_settings()
    return bool(st.get("fw_dir")) and core.infer_exe(st["fw_dir"]).exists()


def _jasna_ready() -> bool:
    st = core.load_settings()
    return bool(st.get("jasna_dir")) and jasna_core.jasna_exe(st["jasna_dir"]).exists()


def api_state(_body, _q):
    st = core.load_settings()
    profiles = core.all_profiles()
    last = st.get("last_profile") or ""
    baseline = profiles.get(last, {}).get("params") if last else core.default_params()
    jprofiles = jasna_core.all_jasna_profiles()
    jlast = st.get("last_jasna_profile") or ""
    jbaseline = (jprofiles.get(jlast, {}).get("params") if jlast
                 else jasna_core.jasna_default_params())
    return {
        "settings": st,
        "profiles": [{"name": k, **v} for k, v in profiles.items()],
        "schema": core.PARAM_SCHEMA,
        "groups": core.PARAM_GROUPS,
        "defaults": core.default_params(),
        "baseline": baseline,
        "baseline_name": last,
        "jasna_profiles": [{"name": k, **v} for k, v in jprofiles.items()],
        "jasna_schema": jasna_core.JASNA_PARAM_SCHEMA,
        "jasna_groups": jasna_core.JASNA_PARAM_GROUPS,
        "jasna_defaults": jasna_core.jasna_default_params(),
        "jasna_baseline": jbaseline,
        "jasna_baseline_name": jlast,
        "modes": jasna_core.MODES,
        "fw": core.validate_fw_dir(st.get("fw_dir", "")),
        "jasna": jasna_core.validate_jasna_dir(st.get("jasna_dir", "")),
        "ffmpeg": core.find_ffmpeg(st),
        "job": MANAGER.state(),
        "app": {"name": core.APP_TITLE, "version": "2.0.0"},
    }


def api_save_settings(body, _q):
    patch = {k: v for k, v in (body or {}).items() if k in core.DEFAULT_SETTINGS}
    for key in ("fw_dir", "jasna_dir"):
        if key in patch and patch[key]:
            patch[key] = str(Path(patch[key]).expanduser())
    if "mode" in patch:
        patch["mode"] = jasna_core.normalize_mode(patch["mode"])
    # 自动勾选规则或模式变了，旧的勾选记录是按老规则算的，必须清掉重算
    cur = core.load_settings()
    if "auto_select_rule" in patch and cur.get("auto_select_rule") != patch["auto_select_rule"]:
        patch["selected"] = {}
        patch["_auto_select"] = True
    if "mode" in patch and jasna_core.normalize_mode(cur.get("mode")) != patch["mode"]:
        # 换模式等于换一套跳过规则，历史勾选不再适用
        patch["selected"] = {}
        patch["_auto_select"] = True
    st = core.save_settings(patch)
    MANAGER.configure(st.get("fw_dir", ""), core.find_ffmpeg(st), st.get("jasna_dir", ""))
    return {"settings": st, "fw": core.validate_fw_dir(st.get("fw_dir", "")),
            "jasna": jasna_core.validate_jasna_dir(st.get("jasna_dir", "")),
            "ffmpeg": core.find_ffmpeg(st)}


# --------------------------------------------------------------------------
# Jasna：目录校验 / 自动查找 / 参数配置
# --------------------------------------------------------------------------

def api_validate_jasna(body, _q):
    return jasna_core.validate_jasna_dir((body or {}).get("path", ""))


def api_pick_default_jasna(_body, _q):
    return jasna_core.pick_default_jasna_dir()


def api_jasna_profiles(_body, _q):
    return {"profiles": [{"name": k, **v}
                         for k, v in jasna_core.all_jasna_profiles().items()]}


def api_jasna_profile_save(body, _q):
    body = body or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("请填写配置名称")
    jasna_core.save_jasna_profile(name, body.get("params") or {},
                                  overwrite=bool(body.get("overwrite")))
    st = core.save_settings({"last_jasna_profile": name})
    return {"profiles": [{"name": k, **v}
                         for k, v in jasna_core.all_jasna_profiles().items()],
            "last_jasna_profile": st.get("last_jasna_profile")}


def api_jasna_profile_delete(body, _q):
    name = (body or {}).get("name", "")
    if jasna_core.load_jasna_profiles().get(name) is None:
        raise ValueError("内置配置不可删除")
    jasna_core.delete_jasna_profile(name)
    return {"profiles": [{"name": k, **v}
                         for k, v in jasna_core.all_jasna_profiles().items()]}


def api_jasna_profile_rename(body, _q):
    body = body or {}
    old = (body.get("name") or "").strip()
    new = (body.get("new_name") or "").strip()
    if not new:
        raise ValueError("请填写新名称")
    profs = jasna_core.load_jasna_profiles()
    if old not in profs:
        raise ValueError("内置配置不可重命名，请先另存为新配置")
    if new in profs:
        raise ValueError(f"配置「{new}」已存在")
    profs[new] = profs.pop(old)
    jasna_core.save_jasna_profiles(profs)
    core.save_settings({"last_jasna_profile": new})
    return {"profiles": [{"name": k, **v}
                         for k, v in jasna_core.all_jasna_profiles().items()]}


def api_jasna_check(body, _q):
    """检查所选 Jasna 参数的外部依赖与取值合法性。"""
    params = (body or {}).get("params") or {}
    return {"issues": jasna_core.check_jasna_dependencies(params)}


def api_jasna_preview(body, _q):
    """预览 Jasna 命令行，便于排查参数问题。"""
    st = core.load_settings()
    src = Path((body or {}).get("path", ""))
    params = jasna_core.normalize_jasna_params((body or {}).get("params") or {})
    mode = jasna_core.normalize_mode((body or {}).get("mode")
                                     or st.get("mode")
                                     or jasna_core.MODE_UNBLUR)
    out_dir = st.get("output_dir") or ""
    policy = st.get("existing_output_policy", "number")
    dst = core.output_video_name(src, out_dir, policy, mode)
    wd = core.work_root() / "_preview"
    args = jasna_core.build_jasna_args(params, src, dst, wd)
    return {"cmd": "jasna.exe " + " ".join(args), "dst": str(dst),
            "issues": jasna_core.check_jasna_dependencies(params)}


def api_validate_fw(body, _q):
    return core.validate_fw_dir((body or {}).get("path", ""))


def api_pick_default_fw(_body, _q):
    """猜测 Faster Whisper 目录：程序同级或常见位置。"""
    cands = []
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    for base in [here, here.parent, Path.cwd()]:
        for sub in ["faster_whisper_transwithai_windows_cu128", "Faster-Whisper-TransWithAI"]:
            cands.append(base / sub)
    for env in ["USERPROFILE", "HOME"]:
        root = os.environ.get(env)
        if root:
            for sub in ["Downloads", "Desktop", "Documents", ""]:
                cands.append(Path(root) / sub / "faster_whisper_transwithai_windows_cu128")
    for c in cands:
        r = core.validate_fw_dir(str(c))
        if r.get("ok"):
            return {"ok": True, "path": str(c), "msg": "已自动找到"}
    return {"ok": False, "path": "", "msg": "未自动找到，请手动指定含 infer.exe 的目录"}


def api_drives(_body, _q):
    out = []
    try:
        for letter in os.listdrives():
            out.append({"path": letter, "label": letter})
    except Exception:
        pass
    return {"drives": out}


def api_browse(body, _q):
    """内置目录浏览：列出一个目录下的子目录与可执行文件。"""
    path = (body or {}).get("path") or "C:\\"
    p = Path(path)
    if not p.exists():
        return {"path": path, "parent": "", "dirs": [], "files": [],
                "msg": "路径不存在"}
    dirs, files = [], []
    try:
        for c in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            try:
                if c.is_dir():
                    if c.name in ("$RECYCLE.BIN", "System Volume Information"):
                        continue
                    dirs.append({"name": c.name, "path": str(c)})
                else:
                    if c.suffix.lower() in (".exe", ".bat", ".cmd", ".lnk"):
                        files.append({"name": c.name, "path": str(c)})
            except OSError:
                continue
    except PermissionError:
        return {"path": str(p), "parent": str(p.parent), "dirs": [], "files": [],
                "msg": "无权限访问"}
    return {"path": str(p), "parent": str(p.parent), "dirs": dirs[:500],
            "files": files[:100], "msg": ""}


def api_scan(body, _q):
    st = core.load_settings()
    body = body or {}
    folder = body.get("folder", "")
    # 只传 folder 时等价于传单元素 folders（同样需要清理该目录下的幽灵记录）；
    # 两者都不传才完全不清理。
    explicit = body.get("folders")
    folders = list(explicit) if explicit else ([folder] if folder else [])
    exts = body.get("exts") or st.get("video_exts") or core.DEFAULT_VIDEO_EXTS
    recursive = body.get("recursive", st.get("recursive", True))
    rule = st.get("auto_select_rule", "smart")
    mode = jasna_core.normalize_mode(body.get("mode", st.get("mode")))
    items = core.scan_folder(folder, exts, recursive)
    # 规则版本 / 模式 / 规则值任一变化后，旧的勾选记录都按老规则算的，必须失效重算
    if (st.get("_auto_select") or st.get("_rule_version") != core.RULE_VERSION
            or jasna_core.normalize_mode(st.get("mode")) != mode):
        selected = {}
        for it in items:
            it["selected"] = core.auto_selected(Path(it["path"]).stem, rule, mode)
    else:
        selected = st.get("selected") or {}
        for it in items:
            it["selected"] = bool(selected.get(it["path"]))
    for it in items:
        stem = Path(it["path"]).stem
        skip = jasna_core.should_skip(stem, mode)
        it["size_h"] = core.human_size(it["size"])
        it["derived"] = core.is_derived_name(stem)
        # 跳过的文件不给输出名：算出 b-U-U 这类名字只会误导用户，
        # 实际根本不会产出。前端会把该列显示为「—」。
        it["target"] = "" if skip else core.target_stem(stem, mode) + Path(it["path"]).suffix
        it["skip"] = skip
        it["skip_reason"] = jasna_core.skip_reason(stem, mode)

    # 传入 folders 时，顺带清掉「已纳管目录下、但文件已不存在」的勾选记录，
    # 否则这些幽灵记录会一直累积（用户删了文件，配置里还留着）。
    #
    # 范围隔离很重要：只碰已纳管目录内的记录，
    # 目录之外的记录（用户可能在别处保存的）一律不动。
    stale = []
    if folders:
        bases = [f.replace("\\", "/").rstrip("/") for f in folders]
        sel_all = st.get("selected") or {}

        def _in_scope(p):
            q = str(p).replace("\\", "/")
            return any(q.startswith(b + "/") for b in bases)

        def _alive(p):
            """仅对纳管目录内的记录判断文件是否还存在。"""
            if not _in_scope(p):
                return True          # 目录外：一律保留
            try:
                return Path(p).exists()
            except OSError:
                return False

        stale = [k for k in sel_all if _in_scope(k) and not _alive(k)]
        if stale:
            core.save_settings({"selected": {k: v for k, v in sel_all.items() if _alive(k)}})

    return {"folder": folder, "items": items, "count": len(items),
            "auto_count": sum(1 for x in items if x["selected"]),
            "stale_cleared": len(stale)}


def api_add_folder(body, _q):
    st = core.load_settings()
    folder = str((body or {}).get("folder", "")).strip().strip('"')
    if not folder:
        raise ValueError("文件夹路径为空")
    p = Path(folder).expanduser()
    if not p.is_dir():
        raise ValueError(f"文件夹不存在：{p}")
    folders = st.get("folders") or []
    if str(p) not in folders:
        folders.append(str(p))
    st = core.save_settings({"folders": folders, "_auto_select": True})
    return {"folders": st["folders"]}


def api_remove_folder(body, _q):
    st = core.load_settings()
    folder = (body or {}).get("folder", "")
    folders = [f for f in (st.get("folders") or []) if f != folder]
    selected = {k: v for k, v in (st.get("selected") or {}).items()
                if not str(k).startswith(folder)}
    st = core.save_settings({"folders": folders, "selected": selected})
    return {"folders": st["folders"]}


def api_set_selected(body, _q):
    """批量勾选：mode = set / add / del / invert / auto / all / none。

    注意：all / none 必须显式处理，不能落到 else 分支。
    否则会用到 value 的默认值 True，导致「清空勾选」反而全部勾上。
    """
    st = core.load_settings()
    paths = (body or {}).get("paths") or []
    mode = (body or {}).get("mode", "set")
    value = bool((body or {}).get("value", True))
    proc_mode = jasna_core.normalize_mode(
        (body or {}).get("process_mode", st.get("mode")))
    sel = dict(st.get("selected") or {})
    if mode == "auto":
        rule = st.get("auto_select_rule", "smart")
        for p in paths:
            sel[p] = core.auto_selected(Path(p).stem, rule, proc_mode)
    elif mode == "invert":
        for p in paths:
            sel[p] = not sel.get(p, False)
    elif mode == "all":
        for p in paths:
            sel[p] = True
    elif mode == "none":
        for p in paths:
            sel[p] = False
    elif mode == "add":
        for p in paths:
            sel[p] = True
    elif mode == "del":
        for p in paths:
            sel[p] = False
    else:  # set：按 value 显式设置
        for p in paths:
            sel[p] = value
    st = core.save_settings({"selected": sel, "_auto_select": False,
                             "_rule_version": core.RULE_VERSION})
    return {"selected": sel}


def api_profiles(body, _q):
    return {"profiles": [{"name": k, **v} for k, v in core.all_profiles().items()]}


def api_profile_save(body, _q):
    body = body or {}
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("请填写配置名称")
    overwrite = bool(body.get("overwrite"))
    core.save_profile(name, body.get("params") or {}, overwrite)
    st = core.save_settings({"last_profile": name})
    return {"profiles": [{"name": k, **v} for k, v in core.all_profiles().items()],
            "last_profile": st.get("last_profile")}


def api_profile_delete(body, _q):
    name = (body or {}).get("name", "")
    if core.load_profiles().get(name) is None:
        raise ValueError("内置配置不可删除")
    core.delete_profile(name)
    return {"profiles": [{"name": k, **v} for k, v in core.all_profiles().items()]}


def api_rename_profile(body, _q):
    body = body or {}
    old = (body.get("name") or "").strip()
    new = (body.get("new_name") or "").strip()
    if not new:
        raise ValueError("请填写新名称")
    profs = core.load_profiles()
    if old not in profs:
        raise ValueError("内置配置不可重命名，请先另存为新配置")
    if new in profs:
        raise ValueError(f"配置「{new}」已存在")
    profs[new] = profs.pop(old)
    core.save_profiles(profs)
    core.save_settings({"last_profile": new})
    return {"profiles": [{"name": k, **v} for k, v in core.all_profiles().items()]}


def api_start(body, _q):
    body = body or {}
    st = core.load_settings()
    mode = jasna_core.normalize_mode(body.get("mode", st.get("mode")))
    need_jasna = mode in (jasna_core.MODE_UNBLUR, jasna_core.MODE_BOTH)
    need_sub = mode in (jasna_core.MODE_SUBTITLE, jasna_core.MODE_BOTH)

    if need_jasna:
        j = jasna_core.validate_jasna_dir(st.get("jasna_dir", ""))
        if not j.get("ok"):
            raise ValueError(j.get("msg") or "Jasna 路径未配置")
    if need_sub:
        fw = core.validate_fw_dir(st.get("fw_dir", ""))
        if not fw.get("ok"):
            raise ValueError(fw.get("msg") or "Faster Whisper 路径未配置")
    paths = [p for p in (body.get("paths") or []) if p]
    if not paths:
        raise ValueError("没有勾选任何文件")

    params = core.normalize_params(body.get("params") or {})
    jparams = jasna_core.normalize_jasna_params(body.get("jasna_params") or {})

    # 参数合法性预检：有 error 级问题就不启动，避免跑到一半才失败
    if need_jasna:
        blocking = [i for i in jasna_core.check_jasna_dependencies(jparams)
                    if i["level"] == "error"]
        if blocking:
            raise ValueError(blocking[0]["text"])

    MANAGER.configure(st.get("fw_dir", ""), core.find_ffmpeg(st), st.get("jasna_dir", ""))
    jobs = MANAGER.submit(paths, st, params, mode, jparams)
    return {"jobs": jobs, "state": MANAGER.state()}


def api_stop(_body, _q):
    MANAGER.stop(True)
    return {"ok": True}


def api_job_state(_body, _q):
    return MANAGER.state()


def api_job_logs(_body, q):
    seq = int((q.get("since") or ["0"])[0])
    items, cur = MANAGER.log.since(seq)
    return {"lines": items, "seq": cur}


def api_job_logs_clear(_body, _q):
    """清空日志。

    必须同时做两件事，缺一就会出现「清空后旧日志又冒出来」：
      1. 后端推进水位线，把清空前的行彻底作废；
      2. 返回该水位线，让前端把游标推过去 —— 否则前端仍拿着旧游标去拉，
         缓冲里残留的行会被再次捞回来。
    """
    return {"ok": True, "cleared_at": MANAGER.log.clear()}


def api_clear_finished(_body, _q):
    return {"removed": MANAGER.clear_finished(), "state": MANAGER.state()}


def api_reveal(path, _q):
    """在资源管理器中定位文件。"""
    p = Path(path)
    if not p.exists():
        raise ValueError("文件不存在")
    if os.name == "nt":
        os.startfile(str(p.parent))  # noqa: S606
    return {"ok": True}


def api_preview_cmd(body, _q):
    """预览某个文件在当前模式下将执行的完整命令链，便于排查参数问题。"""
    st = core.load_settings()
    body = body or {}
    src = Path(body.get("path", ""))
    mode = jasna_core.normalize_mode(body.get("mode", st.get("mode")))
    params = core.normalize_params(body.get("params") or {})
    jparams = jasna_core.normalize_jasna_params(body.get("jasna_params") or {})
    wd = core.work_root() / "_preview"
    dst = core.output_video_name(src, st.get("output_dir", ""),
                                  st.get("existing_output_policy", "number"), mode)
    lines = []
    inter = None
    if mode in (jasna_core.MODE_UNBLUR, jasna_core.MODE_BOTH):
        # 模式 3 的 Jasna 产物是中间文件；模式 1 直接就是最终输出
        inter = wd / f"{dst.stem}.mkv" if mode == jasna_core.MODE_BOTH else dst
        lines.append("jasna.exe " + " ".join(
            jasna_core.build_jasna_args(jparams, src, inter, wd)))
    if mode in (jasna_core.MODE_SUBTITLE, jasna_core.MODE_BOTH):
        base = inter if (mode == jasna_core.MODE_BOTH and inter) else src
        lines.append("infer.exe " + " ".join(core.build_infer_args(params, base, wd)))
        lines.append("ffmpeg " + " ".join(core.build_mux_args(
            core.find_ffmpeg(st) or "ffmpeg", base, wd / "mux.srt", dst)))
    return {"cmd": "\n".join(lines), "dst": str(dst),
            "skip": jasna_core.should_skip(src.stem, mode),
            "skip_reason": jasna_core.skip_reason(src.stem, mode)}


def api_shutdown_prompt(body, _q):
    """关窗前的处理：保存所有设置 + 可选把未保存的参数另存为配置。"""
    body = body or {}
    saved = core.save_settings(body.get("settings") or {})
    result = {"settings_saved": True, "profile_saved": False, "profile_name": "",
              "jasna_profile_saved": False}
    # 字幕参数配置
    name = (body.get("profile_name") or "").strip()
    if body.get("save_profile") and name:
        try:
            core.save_profile(name, body.get("params") or {}, overwrite=bool(body.get("overwrite")))
            core.save_settings({"last_profile": name})
            result["profile_saved"] = True
            result["profile_name"] = name
        except Exception as e:
            result["error"] = str(e)
    # Jasna 参数配置（与字幕参数各自独立保存）
    jname = (body.get("jasna_profile_name") or "").strip()
    if body.get("save_jasna_profile") and jname:
        try:
            jasna_core.save_jasna_profile(jname, body.get("jasna_params") or {},
                                           overwrite=bool(body.get("overwrite")))
            core.save_settings({"last_jasna_profile": jname})
            result["jasna_profile_saved"] = True
        except Exception as e:
            result["error"] = str(e)
    return result


def api_shutdown(body, _q):
    """通知主进程退出（由 app.py 提供的回调接管）。"""
    cb = globals().get("SHUTDOWN_HOOK")
    if cb:
        threading.Timer(0.4, cb).start()
    return {"ok": True}


ROUTES = {
    "/api/state": api_state,
    "/api/save_settings": api_save_settings,
    "/api/validate_fw": api_validate_fw,
    "/api/pick_default_fw": api_pick_default_fw,
    "/api/validate_jasna": api_validate_jasna,
    "/api/pick_default_jasna": api_pick_default_jasna,
    "/api/jasna_profiles": api_jasna_profiles,
    "/api/jasna_profile/save": api_jasna_profile_save,
    "/api/jasna_profile/delete": api_jasna_profile_delete,
    "/api/jasna_profile/rename": api_jasna_profile_rename,
    "/api/jasna_check": api_jasna_check,
    "/api/jasna_preview": api_jasna_preview,
    "/api/drives": api_drives,
    "/api/browse": api_browse,
    "/api/scan": api_scan,
    "/api/add_folder": api_add_folder,
    "/api/remove_folder": api_remove_folder,
    "/api/set_selected": api_set_selected,
    "/api/profiles": api_profiles,
    "/api/profile/save": api_profile_save,
    "/api/profile/delete": api_profile_delete,
    "/api/profile/rename": api_rename_profile,
    "/api/start": api_start,
    "/api/stop": api_stop,
    "/api/job/state": api_job_state,
    "/api/job/logs": api_job_logs,
    "/api/job/logs/clear": api_job_logs_clear,
    "/api/job/clear_finished": api_clear_finished,
    "/api/preview_cmd": api_preview_cmd,
    "/api/shutdown_prompt": api_shutdown_prompt,
    "/api/shutdown": api_shutdown,
    "/api/reveal": lambda body, q: api_reveal((body or {}).get("path", ""), q),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FWSubsBatch/1.0"

    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # ---------- 工具 ----------
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _static(self, rel: str):
        rel = rel.lstrip("/") or "index.html"
        try:
            target = (WEB_DIR / rel).resolve()
            target.relative_to(WEB_DIR.resolve())   # 目录穿越防护
        except (ValueError, OSError):
            self._send(403, b"forbidden", "text/plain")
            return
        if not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        self._send(200, target.read_bytes(), MIME.get(target.suffix, "application/octet-stream"))

    # ---------- 路由 ----------
    def do_GET(self):
        u = urlparse(self.path)
        fn = ROUTES.get(u.path)
        if fn:
            return self._json(fn(None, parse_qs(u.query)))
        self._static(u.path)

    def do_POST(self):
        u = urlparse(self.path)
        fn = ROUTES.get(u.path)
        if not fn:
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception as e:
            return self._json({"error": f"请求解析失败：{e}"}, 400)
        try:
            self._json(fn(body, parse_qs(u.query)))
        except Exception as e:
            self._json({"error": str(e)}, 400)


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve(port: int = 0) -> tuple:
    """启动服务，返回 (httpd, port)。"""
    srv = QuietServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]
