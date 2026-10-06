# -*- coding: utf-8 -*-
"""任务队列与处理流水线：按处理模式执行「去马赛克」与/或「中文字幕」。

三种模式：
  1只清除马赛克   —— jasna.exe 修复 -> 输出 -U
  2 只生成中文字幕 —— infer.exe 转录 -> ffmpeg -c copy 封装 -> 输出 -C
  3 两者都做       —— jasna.exe 修复到中间文件 -> 转录 -> 封装 -> 输出 -UC
                     中间文件按设置（keep_intermediate）决定是否保留

模式 2 的流水线（单个文件）：
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
import jasna_core


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

    def __init__(self, src: Path, settings: dict, params: dict, index: int,
                 mode: int = jasna_core.MODE_SUBTITLE, jasna_params: dict | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.src = src
        self.settings = settings
        self.params = params
        self.jasna_params = jasna_params or {}
        self.mode = jasna_core.normalize_mode(mode)
        self.index = index
        self.status = "pending"     # pending | running | done | failed | skipped | canceled
        self.stage = "等待中"
        self.progress = 0
        self.dst = None
        self.srt = None
        self.intermediate = None    # 模式 3 的中间视频路径
        self.cues = 0
        self.error = ""
        self.args_preview = ""
        self.started_at = 0
        self.ended_at = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "index": self.index, "src": str(self.src),
            "name": self.src.name, "size": self.src.stat().st_size if self.src.exists() else 0,
            "status": self.status, "stage": self.stage, "progress": self.progress,
            "dst": str(self.dst) if self.dst else "", "srt": str(self.srt) if self.srt else "",
            "intermediate": str(self.intermediate) if self.intermediate else "",
            "mode": self.mode, "cues": self.cues, "error": self.error,
            "cmd": self.args_preview,
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
        self._jasna_dir = ""
        self._ffmpeg = ""
        self.running = False
        self.current_id = ""
        self.mode = jasna_core.MODE_SUBTITLE
        self.summary = {"total": 0, "done": 0, "failed": 0, "skipped": 0, "canceled": 0}

    # ---------------- 队列控制 ----------------
    def configure(self, fw_dir: str, ffmpeg: str, jasna_dir: str = ""):
        self._fw_dir = fw_dir or ""
        self._ffmpeg = ffmpeg or ""
        self._jasna_dir = jasna_dir or ""

    def submit(self, files: list, settings: dict, params: dict,
               mode: int = jasna_core.MODE_SUBTITLE,
               jasna_params: dict | None = None) -> list:
        m = jasna_core.normalize_mode(mode)
        with self._lock:
            self.jobs = [Job(Path(f), settings, params, i + 1, m, jasna_params)
                         for i, f in enumerate(files)]
            self.summary = {"total": len(self.jobs), "done": 0, "failed": 0,
                            "skipped": 0, "canceled": 0}
            self.mode = m
            self._stop.clear()
            for j in self.jobs:
                self._q.put(j)
        label = jasna_core.MODE_BY_VALUE[m]["label"]
        self.log.clear()
        self.log.push(f"处理模式：{label}", "ok")
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
            else:
                # 单个视频导出后动作（Jasna GUI 的 post_export_video_command）
                self._run_post_export_video(job)
            finally:
                # 无论成功/失败都清理临时工作目录，避免长期堆积垃圾。
                # 例外：模式 3 且用户开启了「保留中间视频」——
                # 此时把中间视频移出工作目录再删工作目录，否则会连带删掉。
                if wd:
                    self._preserve_intermediate(job, wd)
                    shutil.rmtree(wd, ignore_errors=True)
                job.ended_at = time.time()
                self.current_id = ""
                self._q.task_done()
            # 队列真正跑完（而非被停止）时，执行队列级动作
            if self._q.empty() and not self._stop.is_set():
                self._run_post_export_queue()
                return

    # ---------------- 导出后动作 ----------------
    def _run_post_export_video(self, job: Job):
        """每个视频成功导出后执行自定义命令。"""
        if job.mode not in (jasna_core.MODE_UNBLUR, jasna_core.MODE_BOTH):
            return
        if job.status != "done":
            return
        cmd = str((job.jasna_params or {}).get("post_export_video_command") or "").strip()
        if not cmd:
            return
        real = cmd
        for ph, val in {
            "{input}": job.src,
            "{output}": job.dst,
            "{output_dir}": (job.dst.parent if job.dst else job.src.parent),
            "{output_stem}": (job.dst.stem if job.dst else job.src.stem),
            "{output_suffix}": (job.dst.suffix if job.dst else job.src.suffix),
        }.items():
            real = real.replace(ph, f'"{val}"')
        self.log.push(f"  视频完成动作：{real}", "info")
        rc, _ = _run_process(real, shell=True, log=self.log,
                             stop_flag=self._stop, pidfile=self._pidfile_path())
        if rc != 0:
            self.log.push(f"  视频完成动作返回 {rc}", "warn")

    def _run_post_export_queue(self):
        """整个队列完成后执行动作（关机/ 自定义命令）。"""
        params = {}
        for j in self.jobs:
            if j.jasna_params:
                params = j.jasna_params
                break
        action = str(params.get("post_export_action") or "none")
        if action == "none":
            return
        if action == "shutdown":
            self.log.push("队列完成，60 秒后关闭电脑（可在此期间取消）", "warn")
            rc, _ = _run_process("shutdown /s /t 60", shell=True, log=self.log)
            if rc != 0:
                self.log.push("关机命令执行失败", "error")
            return
        if action == "command":
            cmd = str(params.get("post_export_command") or "").strip()
            if not cmd:
                return
            self.log.push(f"队列完成，执行：{cmd}", "info")
            rc, _ = _run_process(cmd, shell=True, log=self.log)
            if rc != 0:
                self.log.push(f"命令返回 {rc}", "warn")

    def _preserve_intermediate(self, job: Job, wd: Path):
        """模式 3 的中间视频按开关处理：保留则移到源文件旁边，否则留在原地被清理。"""
        inter = getattr(job, "intermediate", None)
        if not inter:
            return
        if job.status != "done":
            # 失败时中间文件没有保留价值，一律随工作目录清掉
            return
        if not job.settings.get("keep_intermediate", False):
            return
        src_dir = job.src.parent
        dst = src_dir / inter.name
        n = 2
        while dst.exists():
            dst = src_dir / f"{inter.stem} ({n}){inter.suffix}"
            n += 1
        try:
            shutil.move(str(inter), str(dst))
            job.intermediate = dst
            self.log.push(f"  中间视频已保留：{dst.name}", "ok")
        except Exception as e:
            self.log.push(f"  中间视频保留失败（将随临时目录清理）：{e}", "warn")

    # ---------------- 单文件流水线 ----------------
    def _process(self, job: Job):
        """按模式执行单文件处理。返回工作目录（供调用方清理），或None。"""
        src: Path = job.src
        st = job.settings
        job.status = "running"
        job.started_at = time.time()
        job.progress = 2

        if not src.exists():
            raise FileNotFoundError(f"源文件不存在：{src}")

        mode = job.mode
        need_jasna = mode in (jasna_core.MODE_UNBLUR, jasna_core.MODE_BOTH)
        need_sub = mode in (jasna_core.MODE_SUBTITLE, jasna_core.MODE_BOTH)

        # ---------- 前置依赖校验 ----------
        ffmpeg = self._ffmpeg or core.find_ffmpeg(st)
        if need_sub and not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，无法封装字幕。请在「设置」中指定 ffmpeg 路径。")
        if need_sub and not core.infer_exe(self._fw_dir).exists():
            raise FileNotFoundError(f"未找到 infer.exe：{core.infer_exe(self._fw_dir)}")
        if need_jasna:
            jexe = jasna_core.jasna_exe(self._jasna_dir)
            if not jexe.exists():
                raise FileNotFoundError(f"未找到 jasna.exe：{jexe}")

        # ---------- 模式相关的跳过判定 ----------
        if jasna_core.should_skip(src.stem, mode):
            job.status = "skipped"
            job.stage = "按模式跳过"
            self.summary["skipped"] += 1
            self.log.push(f"[跳过] {src.name} → "
                          f"{jasna_core.skip_reason(src.stem, mode)}", "warn")
            return None

        # ---------- 输出路径（永不静默覆盖） ----------
        out_dir = st.get("output_dir") or ""
        policy = st.get("existing_output_policy", "number")
        dst = core.output_video_name(src, out_dir, policy, mode)
        if policy == "skip" and dst.exists():
            job.status = "skipped"
            job.stage = "已存在同名输出，跳过"
            job.dst = dst
            self.summary["skipped"] += 1
            self.log.push(f"[跳过] {src.name} → 已存在 {dst.name}", "warn")
            return None

        # 只加字幕且容器不支持软封装时，改用 mkv
        if need_sub and not core.container_subtitle_codec(dst.suffix)[1]:
            self.log.push(f"[提示] {dst.suffix} 容器不支持软封装字幕，改为输出 .mkv", "warn")
            dst = dst.with_suffix(".mkv")

        wd = core.work_root() / f"{job.index:03d}_{src.stem}"[:80]
        if wd.exists():
            shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        job.dst = dst

        self.log.push(f"▶ [{job.index}] {src.name}（{jasna_core.MODE_BY_VALUE[mode]['label']}）")

        # ----------阶段 1：去马赛克（模式 1 / 3） ----------
        video_in = src
        if need_jasna:
            video_in = self._stage_unblur(job, src, dst, wd)

        # ---------- 阶段 2：转录 + 封装（模式 2 / 3） ----------
        if need_sub:
            self._stage_subtitle(job, video_in, dst, wd, ffmpeg)

        # ---------- 收尾 ----------
        job.progress = 100
        job.status = "done"
        job.stage = "完成"
        self.summary["done"] += 1
        mode_label = jasna_core.MODE_BY_VALUE[mode]["short"]
        self.log.push(f"✔ [{job.index}] {src.name} → {dst.name}"
                      f"（{core.human_size(dst.stat().st_size)}，{mode_label}完成）", "ok")
        return wd

    # ---- 阶段 1：Jasna 去马赛克 ----
    def _stage_unblur(self, job: Job, src: Path, dst: Path, wd: Path) -> Path:
        """调用 jasna.exe 去马赛克。返回去马赛克后的视频路径。

        模式 1：直接输出到最终 dst。
        模式 3：输出到工作目录作为中间文件，交给阶段 2 加字幕。
        """
        jexe = jasna_core.jasna_exe(self._jasna_dir)
        is_final = job.mode == jasna_core.MODE_UNBLUR

        if is_final:
            target = dst
        else:
            # 中间文件放工作目录，避免污染源目录
            target = wd / f"{dst.stem}.{self._jasna_ext()}"

        job.stage = "去马赛克中"
        # 模式 3 里去马赛克约占 70% 工期（后面转录+封装占 30%）
        base, span = (2, 68) if not is_final else (2, 96)
        job.progress = base
        args = jasna_core.build_jasna_args(job.jasna_params, src, target, wd)
        job.args_preview = "jasna.exe " + " ".join(args)

        prog = {"p": base}

        def on_line(s: str):
            self.log.push(f"  {s}" if not s.startswith("$ ") else s)
            if "%" in s:
                for tok in s.replace("=", " ").split():
                    if tok.endswith("%"):
                        try:
                            pct = float(tok[:-1])
                            prog["p"] = min(base + span, base + int(span * pct / 100))
                        except ValueError:
                            pass
            job.progress = prog["p"]

        rc, out = _run_process([str(jexe)] + args, cwd=self._jasna_dir, log=self.log,
                               on_line=on_line, stop_flag=self._stop,
                               pidfile=self._pidfile_path())
        if rc != 0 or self._stop.is_set():
            raise RuntimeError(f"去马赛克失败（退出码 {rc}），详见日志")
        if not target.exists() or target.stat().st_size == 0:
            raise RuntimeError(f"去马赛克未产出有效文件：{target.name}")
        job.progress = base + span
        self.log.push(f"  去马赛克完成 → {target.name}"
                      f"（{core.human_size(target.stat().st_size)}）", "ok")
        if not is_final:
            job.intermediate = target
        return target

    @staticmethod
    def _jasna_ext() -> str:
        """中间文件扩展名：用mkv 承载最稳妥（容器限制最少）。"""
        return "mkv"

    # ---- 阶段 2：Faster Whisper 转录 + ffmpeg 封装 ----
    def _stage_subtitle(self, job: Job, video: Path, dst: Path, wd: Path, ffmpeg: str):
        """转录生成 SRT 并软封装进视频（不重新编码）。"""
        # 模式 3 里这一步是收尾；模式 2 里是全部工作
        base = 70 if job.mode == jasna_core.MODE_BOTH else 5
        span = 28 if job.mode == jasna_core.MODE_BOTH else 93

        job.stage = "转录中"
        job.progress = base
        exe = core.infer_exe(self._fw_dir)
        args = core.build_infer_args(job.params, video, wd)
        infer_preview = "infer.exe " + " ".join(args)
        if job.mode == jasna_core.MODE_BOTH:
            job.args_preview += "\n" + infer_preview
        else:
            job.args_preview = infer_preview

        prog = {"p": base}

        def on_line(s: str):
            self.log.push(f"  {s}" if not s.startswith("$ ") else s)
            low = s.lower()
            if "%" in s and any(k in low for k in ("transcrib", "进度", "processing")):
                for tok in s.replace("=", " ").split():
                    if tok.endswith("%"):
                        try:
                            pct = float(tok[:-1])
                            prog["p"] = min(base + span - 2, base + int(span * pct / 100))
                        except ValueError:
                            pass
            job.progress = prog["p"]

        rc, _ = _run_process([str(exe)] + args, cwd=self._fw_dir, log=self.log,
                             on_line=on_line, stop_flag=self._stop,
                             pidfile=self._pidfile_path())
        if rc != 0 or self._stop.is_set():
            raise RuntimeError(f"转录失败（退出码 {rc}）" + ("" if rc == 0 else "，详见日志"))

        # ---------- 校验字幕 ----------
        job.stage = "校验字幕"
        job.progress = base + span - 2
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

        # ---------- 封装 ----------
        job.stage = "封装中"
        job.progress = base + span - 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = dst.with_name(dst.stem + ".part" + dst.suffix)
        if tmp_out.exists():
            tmp_out.unlink()
        mux_cmd = core.build_mux_args(ffmpeg, video, clean, tmp_out)
        self.log.push("  $ " + " ".join(mux_cmd))
        rc, _ = _run_process(mux_cmd, cwd=str(dst.parent), log=self.log,
                             on_line=lambda s: self.log.push("  " + s),
                             stop_flag=self._stop, pidfile=self._pidfile_path())
        if rc != 0 or not tmp_out.exists() or tmp_out.stat().st_size == 0:
            if tmp_out.exists():
                tmp_out.unlink()
            raise RuntimeError(f"字幕封装失败（退出码 {rc}），详见日志")
        policy = job.settings.get("existing_output_policy", "number")
        if dst.exists() and policy != "overwrite":
            dst.unlink()
        os.replace(str(tmp_out), str(dst))

        # 保留独立 SRT 的开关：仅在源文件就是最终输入时才有意义
        # （模式 3 的中间视频是临时的，SRT 跟着中间文件一起清理）
        if not job.settings.get("keep_srt", True) and job.mode != jasna_core.MODE_BOTH:
            side = job.src.with_suffix(".zh.srt")
            shutil.copy2(clean, side)
            self.log.push(f"  字幕已另存：{side.name}")

    def _pidfile_path(self):
        d = core.app_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / "current_child.pid"


def _run_process(cmd, cwd=None, log=None, on_line=None, stop_flag=None,
                 pidfile=None, shell=False):
    """执行子进程；把 stdout/stderr 逐行输出；支持通过 pidfile 记录并终止。

    shell=True 时 cmd 可以是字符串（用于「导出后执行命令」这类场景）。
    """
    import subprocess
    if on_line:
        on_line("$ " + (cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)))
    try:
        if shell:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        else:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
    except FileNotFoundError as e:
        if log:
            log.push(f"无法启动：{e}", "error")
        return 127, str(e)
    if pidfile and not shell:
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
        if pidfile and not shell and pidfile.exists():
            try:
                pidfile.unlink()
            except OSError:
                pass
    return proc.returncode, "\n".join(lines)


MANAGER = JobManager()
