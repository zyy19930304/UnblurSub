# -*- coding: utf-8 -*-
"""Faster Whisper 批量字幕工具 —— 桌面程序入口。

窗口三级降级：pywebview（原生 WebView2）→ Edge --app → 系统默认浏览器。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import core  # noqa: E402
import server  # noqa: E402

CREATE_NO_WINDOW = 0x08000000


def find_edge() -> str:
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in cands:
        if Path(c).exists():
            return c
    return ""


def profile_dir() -> Path:
    d = core.app_data_dir() / "webprofile"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--server-only", action="store_true", help="只启动服务，不开窗口（调试用）")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-webview", action="store_true", help="跳过 pywebview")
    ap.add_argument("--browser", action="store_true", help="强制用浏览器打开")
    args = ap.parse_args()

    import core as _c
    st = _c.load_settings()
    srv, port = server.serve(args.port)
    url = f"http://127.0.0.1:{port}/"
    print(f"[FWSubsBatch] 服务已启动：{url}")
    print(f"[FWSubsBatch] 配置目录：{core.app_data_dir()}")

    quit_flag = threading.Event()
    holder = {"window": None}

    def request_quit():
        """前端点了「退出」：销毁窗口 + 结束主循环。"""
        quit_flag.set()
        w = holder.get("window")
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass

    server.SHUTDOWN_HOOK = request_quit

    if args.server_only:
        try:
            while not quit_flag.is_set():
                time.sleep(0.4)
        except KeyboardInterrupt:
            pass
        return 0

    win = st.get("window") or {}
    width, height = int(win.get("width", 1360)), int(win.get("height", 900))

    # 1) pywebview
    if not args.no_webview and not args.browser:
        try:
            import webview
            _launch_webview(url, width, height, quit_flag, holder)
            return 0
        except ImportError:
            print("[FWSubsBatch] 未安装 pywebview，降级到浏览器窗口")
        except Exception as e:
            print(f"[FWSubsBatch] pywebview 启动失败（{e}），降级到浏览器窗口")

    # 2) Edge --app
    edge = find_edge()
    opened = False
    if edge and not args.browser:
        try:
            subprocess.Popen(
                [edge, f"--app={url}", f"--user-data-dir={profile_dir()}",
                 "--no-first-run", "--no-default-browser-check", "--window-size=1360,900"],
                creationflags=CREATE_NO_WINDOW | 0x00000008)
            opened = True
            print("[FWSubsBatch] 已用 Edge 应用窗口打开")
        except Exception as e:
            print(f"[FWSubsBatch] Edge 启动失败：{e}")
    if not opened:
        webbrowser.open(url)
        print("[FWSubsBatch] 已用系统默认浏览器打开")

    try:
        while not quit_flag.is_set():
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    time.sleep(0.3)
    srv.shutdown()
    return 0


def _launch_webview(url: str, width: int, height: int, quit_flag: threading.Event,
                    holder: dict):
    import webview

    api = _Bridge(quit_flag, holder)

    win = webview.create_window(
        core.APP_TITLE, url, width=width, height=height,
        min_size=(1040, 720), js_api=api, confirm_close=False,
        background_color="#0f1216",
    )
    holder["window"] = win

    def on_closing():
        # 允许前端拦截：参数未保存时前端会自行弹窗，走 /api/shutdown 退出
        quit_flag.set()
        return True

    try:
        win.events.closing += on_closing
    except Exception:
        pass

    # 后台线程：前端调 /api/shutdown 后销毁窗口，让 webview.start() 返回
    def watcher():
        while not quit_flag.wait(0.3):
            continue
        try:
            win.destroy()
        except Exception:
            pass

    threading.Thread(target=watcher, daemon=True).start()

    gui = "edgechromium" if os.name == "nt" else None
    try:
        webview.start(gui=gui, debug=False)
    except TypeError:
        webview.start()
    finally:
        quit_flag.set()


class _Bridge:
    """暴露给前端的 pywebview js_api（前端主要走 HTTP，这里做兜底）。"""

    def __init__(self, quit_flag, holder: dict):
        self.quit_flag = quit_flag
        self.holder = holder

    def quit(self):
        self.quit_flag.set()
        try:
            w = self.holder.get("window")
            if w is not None:
                w.destroy()
        except Exception:
            pass

    def save(self, payload):
        try:
            return server.api_shutdown_prompt(payload or {}, None)
        except Exception as e:
            return {"error": str(e)}


if __name__ == "__main__":
    sys.exit(main())
