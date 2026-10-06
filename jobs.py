# -*- coding: utf-8 -*-
"""任务队列与处理流水线：转录 -> 封装（不重新编码）。

流水线（每个文件）：
  1. 在临时工作目录调用 infer.exe 生成 SRT
  2. 规整校验 SRT
  3. ffmpeg -c copy 把 SRT 封入视频，轨道名 = 中文字幕
  4. 输出文件名按命名规则（原名 -C；若原名以 -U 结尾则为 -UC）
"""
from __future__ import annotations

import os
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

import core


class LogBus:
    """环形日志缓冲，供前端轮询。"""

    def __init__(self, limit: int = 4000):
        self._buf = []
        self._lock = threading.Lock()
        self._seq = 0
        self.limit = limit

    def push(self, text: str, level: str = "info") -> int:
        with self._lock:
            self._seq += 1
            self._buf.append({"seq": self._seq, "t": time.strftime("%H:%M:%S"),
                              "level": level, "text": text})
            if len(self._buf) > self.limit:
                del self._buf[: len(self._buf) - self.limit]
            return self._seq

    def since(self, seq: int = 0, limit: int = 800) -> tuple:
        with self._lock:
            items = [x for x in self._buf if x["seq"] > seq]
            return items[-limit:], self._seq

    def clear(self):
        with self._lock:
            self._buf.clear()


class Job:
    """单个文件的处理任务。"""

    def __init__(self, src: Path, settings: dict, params: dict, index: int):
        self.id = uuid.uuid4().hex[:12]
        self.src = src
        self.settings = settings
        self.params = params
        self.index = index
        self.status = "pending"     # pending | running | done | failed | skipped | canceled
        self.stage = "等待中"
        self.progress = 0
        self.dst = None
        self.srt = None
        self.cues = 0
        self.error = ""
        self.started_at = 0
        self.ended_at = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "index": self.index, "src": str(self.src),
            "name": self.src.name, "size": self.src.stat().st_size if self.src.exists() else 0,
            "status": self.status, "stage": self.stage, "progress": self.progress,
            "dst": str(self.dst) if self.dst else "", "srt": str(self.srt) if self.srt else "",
            "cues": self.cues, "error": self.error,
            "elapsed": round((self.ended_at or time.time()) - self.started_at, 1) if self.started_at else 0,
        }


class JobManager:
    """串行队列（一次一个 infer.exe，避免显存/内存炸掉）。"""

    def __init__(self):
        self.log = LogBus()
        self.jobs: list[Job] = []
        self._q: queue.Queue = queue.Queue()
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._fw_dir = ""
        self._ffmpeg = ""
        self.running = False
        self.current_id = ""
        self.summary = {"total": 0, "done": 0, "failed": 0, "skipped": 0, "canceled": 0}

    # ---------------- 队列控制 ----------------
    def configure(self, fw_dir: str, ffmpeg: str):
        self._fw_dir = fw_dir or ""
        self._ffmpeg = ffmpeg or ""

    def submit(self, files: list, settings: dict, params: dict) -> list:
        with self._lock:
            self.jobs = [Job(Path(f), settings, params, i + 1) for i, f in enumerate(files)]
            self.summary = {"total": len(self.jobs), "done": 0, "failed": 0,
                            "skipped": 0, "canceled": 0}
            self._stop.clear()
            for j in self.jobs:
                self._q.put(j)
        self.log.clear()
        self.log.push(f"已加入 {len(self.jobs)} 个文件到队列", "ok")
        self._ensure_thread()
        return [j.to_dict() for j in self.jobs]

    def _ensure_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self.running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self, cancel_running: bool = True):
        self.log.push("收到停止指令", "warn")
        self._stop.set()
        if cancel_running:
            self._kill_current()

    def clear_finished(self):
        with self._lock:
            keep = [j for j in self.jobs if j.status in ("pending", "running")]
            removed = len(self.jobs) - len(keep)
            self.jobs = keep
            for i, j in enumerate(self.jobs):
                j.index = i + 1
        return removed

    def state(self) -> dict:
        with self._lock:
            return {
                "running": self.running and not self._stop.is_set(),
                "stopping": self._stop.is_set(),
                "current": self.current_id,
                "jobs": [j.to_dict() for j in self.jobs],
                "summary": dict(self.summary),
            }

    def _kill_current(self):
        pid_file = getattr(self, "_pidfile", None)
        if pid_file and os.path.exists(pid_file):
            try:
                import psutil  # 可选
            except ImportError:
                psutil = None
            try:
                if psutil:
                    for p in psutil.process_iter(["pid", "cmdline"]):
                        cl = p.info.get("cmdline") or []
                        if any(str(pid_file.name) in str(c) for c in cl):
                            p.kill()
                else:
                    with open(pid_file) as f:
                        for line in f:
                            pid = int(line.strip().split("|")[0])
                            try:
                                os.kill(pid, 9)
                            except Exception:
                                pass
            except Exception:
                pass
        self._kill_flag = True

    # ---------------- 工作线程 ----------------
    def _worker(self):
        while True:
            try:
                job = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._stop.is_set() and self._q.empty():
                    self.running = False
                    self.current_id = ""
                    self.log.push("队列已停止", "warn")
                    return
                continue
            if self._stop.is_set():
                job.status = "canceled"
                job.stage = "已取消"
                self.summary["canceled"] += 1
                self._q.task_done()
                continue
            self.current_id = job.id
            wd = None
            try:
                wd = self._process(job)
            except Exception as e:
                job.status = "failed"
                job.stage = "异常"
                job.error = f"{e}"
                self.summary["failed"] += 1
                self.log.push(f"[失败] {job.src.name}: {e}", "error")
                self.log.push(traceback.format_exc(), "error")
            finally:
                # 无论成功/失败都清理临时工作目录，避免长期堆积垃圾
                if wd:
                    shutil.rmtree(wd, ignore_errors=True)
                job.ended_at = time.time()
                self.current_id = ""
                self._q.task_done()

    # ---------------- 单文件流水线 ----------------
    def _process(self, job: Job):
        src: Path = job.src
        st = job.settings
        job.status = "running"
        job.started_at = time.time()
        job.progress = 2

        if not src.exists():
            raise FileNotFoundError(f"源文件不存在：{src}")

        ffmpeg = self._ffmpeg or core.find_ffmpeg(st)
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，无法封装字幕。请在「设置」中指定 ffmpeg 路径。")
        exe = core.infer_exe(self._fw_dir)
        if not exe.exists():
            raise FileNotFoundError(f"未找到 infer.exe：{exe}")

        # 输出路径（永不静默覆盖）
        out_dir = st.get("output_dir") or ""
        policy = st.get("existing_output_policy", "number")
        dst = core.output_video_name(src, out_dir, policy)
        if policy == "skip" and dst.exists():
            job.status = "skipped"
            job.stage = "已存在同名输出，跳过"
            job.dst = dst
            self.summary["skipped"] += 1
            self.log.push(f"[跳过] {src.name} → 已存在 {dst.name}", "warn")
            return None

        codec, ok = core.container_subtitle_codec(dst.suffix)
        if not ok:
            self.log.push(f"[提示] {dst.suffix} 容器不支持软封装字幕，改为输出 .mkv", "warn")
            dst = dst.with_suffix(".mkv")

        wd = core.work_root() / f"{job.index:03d}_{src.stem}"[:80]
        if wd.exists():
            shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        # ---------- 1. 转录 ----------
        job.stage = "转录中"
        job.progress = 5
        self.log.push(f"▶ [{job.index}] {src.name}")
        args = core.build_infer_args(job.params, src, wd)

        # 进度：根据日志行数粗略推进
        prog_holder = {"p": 5}

        def on_line(s: str):
            self.log.push(f"  {s}" if not s.startswith("$ ") else s)
            low = s.lower()
            if "%" in s and any(k in low for k in ("transcrib", "进度", "processing")):
                for tok in s.replace("=", " ").split():
                    if tok.endswith("%"):
                        try:
                            prog_holder["p"] = min(90, 5 + int(float(tok[:-1]) * 0.85))
                        except ValueError:
                            pass
            job.progress = prog_holder["p"]

        job.args_preview = "infer.exe " + " ".join(args)
        rc, out = _run_process([str(exe)] + args, cwd=self._fw_dir,
                               log=self.log, on_line=on_line,
                               stop_flag=self._stop, pidfile=self._pidfile_path())
        if rc != 0 or self._stop.is_set():
            raise RuntimeError(f"转录失败（退出码 {rc}）" + ("" if rc == 0 else "，详见日志"))

        # ---------- 2. 找 SRT ----------
        job.stage = "校验字幕"
        job.progress = 92
        cands = sorted(wd.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            raise RuntimeError("未生成 .srt 字幕文件")
        raw_srt = cands[0]
        job.srt = raw_srt

        clean = wd / "mux.srt"
        cues = core.normalize_srt(raw_srt, clean)
        if cues <= 0:
            raise RuntimeError("字幕内容为空（可能无人声或识别失败）")
        job.cues = cues
        self.log.push(f"  字幕 {cues} 条 → {raw_srt.name}", "ok")

        # ---------- 3. 封装 ----------
        job.stage = "封装中"
        job.progress = 95
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = dst.with_name(dst.stem + ".part" + dst.suffix)
        if tmp_out.exists():
            tmp_out.unlink()
        mux_cmd = core.build_mux_args(ffmpeg, src, clean, tmp_out)
        self.log.push("  $ " + " ".join(mux_cmd))
        rc, _ = _run_process(mux_cmd, cwd=str(dst.parent), log=self.log,
                             on_line=lambda s: self.log.push("  " + s),
                             stop_flag=self._stop, pidfile=self._pidfile_path())
        if rc != 0 or not tmp_out.exists() or tmp_out.stat().st_size == 0:
            if tmp_out.exists():
                tmp_out.unlink()
            raise RuntimeError(f"字幕封装失败（退出码 {rc}），详见日志")
        if dst.exists() and policy != "overwrite":
            dst.unlink()
        os.replace(str(tmp_out), str(dst))
        job.dst = dst

        # ---------- 4. 收尾 ----------
        job.progress = 100
        job.status = "done"
        job.stage = "完成"
        self.summary["done"] += 1
        self.log.push(f"✔ [{job.index}] {src.name} → {dst.name}（{core.human_size(dst.stat().st_size)}，"
                      f"字幕轨「中文字幕」）", "ok")

        if not st.get("keep_srt", True):
            # 把 SRT 放到源文件旁边
            side = src.with_suffix(".zh.srt")
            shutil.copy2(clean, side)
            self.log.push(f"  字幕已另存：{side.name}")
        return wd

    def _pidfile_path(self):
        d = core.app_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / "current_child.pid"


def _run_process(cmd, cwd=None, log=None, on_line=None, stop_flag=None, pidfile=None):
    """执行子进程；把 stdout/stderr 逐行输出；支持通过 pidfile 记录并终止。"""
    import subprocess
    if on_line:
        on_line("$ " + " ".join(str(c) for c in cmd))
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except FileNotFoundError as e:
        if log:
            log.push(f"无法启动：{e}", "error")
        return 127, str(e)
    if pidfile:
        try:
            pidfile.write_text(f"{proc.pid}|{' '.join(str(c) for c in cmd)}", encoding="utf-8")
        except Exception:
            pass
    lines = []
    try:
        for raw in proc.stdout:
            s = raw.decode("utf-8", errors="replace").rstrip()
            lines.append(s)
            if on_line:
                on_line(s)
        proc.wait()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        if pidfile and pidfile.exists():
            try:
                pidfile.unlink()
            except OSError:
                pass
    return proc.returncode, "\n".join(lines)


MANAGER = JobManager()
