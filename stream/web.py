#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from __future__ import annotations
import os
import platform
import re
import sys
import threading
import traceback
import urllib.parse as urlparse
from dataclasses import dataclass, field
from tkinter import Tk, ttk, scrolledtext, messagebox, LEFT, BOTH, END, NORMAL, DISABLED, SUNKEN

import queue
import subprocess
from yt_dlp import YoutubeDL

try:
    import vlc  # type: ignore
except ImportError as e:
    print("python-vlc 未安装或 VLC 本体缺失。")
    raise

# ============================== 常量配置 ============================== #
BROWSER_CANDIDATES: list[str | None] = ["chrome", "edge", "safari", "firefox", None]
YDL_FORMAT_FILTER = "(bv*+ba/b)[vcodec!*=av01][vcodec!*=hev1][vcodec!*=hvc]"  # 略去 AV1 / HEVC
NETWORK_CACHING_MS = 1500  # VLC 缓存时长
PROXY_DEFAULT_SCHEME = "socks5://"

# ============================== 数据结构 ============================== #
@dataclass
class StreamInfo:
    video_url: str
    audio_url: str | None = None 
    video_headers: dict[str, str] = field(default_factory=dict)
    audio_headers: dict[str, str] | None = None

    def get_playback_info(self) -> tuple[str, str | None, dict]:
        """返回播放器需要的信息"""
        return (
            self.video_url,
            self.audio_url,
            {
                "video": self.video_headers,
                "audio": self.audio_headers if self.audio_headers else {}
            }
        )

# ============================== GUI 主类 ============================== #
class StreamPlayerApp:
    def __init__(self, master: Tk, queue_obj: queue.SimpleQueue = None) -> None:
        self.master = master
        master.title("Python 视频流播放器 (健壮版)")
        master.geometry("900x640")

        # ====== 队列初始化 ====== #
        self.stream_queue = queue_obj if queue_obj is not None else queue.SimpleQueue()

        # ====== VLC 初始化 ====== #
        self.vlc_instance: vlc.Instance | None = None
        self.vlc_player: vlc.MediaPlayer | None = None
        self._init_vlc()

        # ====== UI ====== #
        self._build_widgets()

    # --------------------------- VLC --------------------------- #
    def _init_vlc(self) -> None:
        try:
            opts = [f"--network-caching={NETWORK_CACHING_MS}"]
            if platform.system() == "Darwin":
                opts.append("--vout=macosx")
            self.vlc_instance = vlc.Instance(*opts)
            self.vlc_player = self.vlc_instance.media_player_new()
        except Exception as exc:
            self._fatal_error("VLC 初始化失败", exc)

    # --------------------------- UI --------------------------- #
    def _build_widgets(self) -> None:
        # URL
        url_frame = ttk.LabelFrame(self.master, text="视频页面 URL")
        url_frame.pack(padx=10, pady=10, fill=BOTH)
        ttk.Label(url_frame, text="输入网页地址：").pack(side=LEFT, padx=5, pady=5)
        self.url_entry = ttk.Entry(url_frame, width=70)
        self.url_entry.pack(side=LEFT, expand=True, fill=BOTH, padx=5, pady=5)
        self.url_entry.insert(0, "https://www.bilibili.com/video/BV1ks4y1H7Pt?spm_id_from=333.788.recommend_more_video.1&vd_source=89a2f97388b961603f041db847ae1c95")

        # 代理
        proxy_frame = ttk.LabelFrame(self.master, text="代理 (可选)")
        proxy_frame.pack(padx=10, fill=BOTH)
        ttk.Label(proxy_frame, text="代理 URL：").pack(side=LEFT, padx=5, pady=5)
        self.proxy_entry = ttk.Entry(proxy_frame, width=60)
        self.proxy_entry.pack(side=LEFT, expand=True, fill=BOTH, padx=5, pady=5)

        # 控制按钮
        ctrl_frame = ttk.Frame(self.master); ctrl_frame.pack(pady=5)
        self.extract_btn = ttk.Button(ctrl_frame, text="提取并播放", command=self._start_extract)
        self.extract_btn.pack(side=LEFT, padx=5)
        self.stop_btn = ttk.Button(ctrl_frame, text="停止", command=self._stop_play, state=DISABLED)
        self.stop_btn.pack(side=LEFT, padx=5)

        # 视频帧
        self.video_frame = ttk.Frame(self.master, relief=SUNKEN, width=720, height=405, style="Video.TFrame")
        self.video_frame.pack(padx=10, pady=10, expand=True, fill=BOTH)
        ttk.Style().configure("Video.TFrame", background="black")

        # 日志
        log_frame = ttk.LabelFrame(self.master, text="状态 / 日志")
        log_frame.pack(padx=10, pady=10, fill=BOTH)
        self.log_widget = scrolledtext.ScrolledText(log_frame, height=7, wrap="word", state=DISABLED)
        self.log_widget.pack(expand=True, fill=BOTH, padx=5, pady=5)

        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

    # --------------------------- 核心逻辑 --------------------------- #
    def _start_extract(self) -> None:
        page_url = self.url_entry.get().strip()
        if not self._valid_url(page_url):
            messagebox.showerror("错误", "请输入有效的 http(s) URL")
            return

        proxy = self.proxy_entry.get().strip()
        if proxy and "://" not in proxy:
            proxy = PROXY_DEFAULT_SCHEME + proxy

        self._log(f"开始提取：{page_url}")
        self.extract_btn.config(state=DISABLED); self.stop_btn.config(state=NORMAL)

        t = threading.Thread(target=self._extract_worker, args=(page_url, proxy), daemon=True)
        t.start()

    def _extract_worker(self, url: str, proxy: str | None) -> None:
        try:
            self.stream_queue.put(self._extract_stream(url, proxy))
        except Exception as exc:
            self.stream_queue.put(exc)

    def _extract_stream(self, page_url: str, proxy: str | None) -> StreamInfo:
        last_err: Exception | None = None
        for browser in BROWSER_CANDIDATES:
            try:
                self._log(f"尝试 {browser or '无 Cookie'} …")
                info = self._ydl_extract(page_url, proxy, browser)
                return self._select_best(info)
            except Exception as e:
                last_err = e
                self._log(f"提取失败：{e}")
                continue
        raise RuntimeError(f"全部提取方式失败：{last_err}") from last_err

    # ---------------- yt-dlp ---------------- #
    def _ydl_extract(self, url: str, proxy: str | None, browser: str | None):
        ydl_opts = dict(
            format=YDL_FORMAT_FILTER,
            forceipv4=True,
            quiet=True,
            retries=5,
            fragment_retries=5,
            proxy=proxy or None,
        )
        if browser:
            ydl_opts["cookiesfrombrowser"] = (browser,)
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    @staticmethod
    def _select_best(info) -> StreamInfo:
        fmts = info.get("formats", [])
        v = [f for f in fmts if f.get("vcodec") != "none" and f.get("acodec") == "none"]
        a = [f for f in fmts if f.get("acodec") != "none" and f.get("vcodec") == "none"]
        if v:
            best_v = max(v, key=lambda f: f.get("height") or 0)
            best_a = max(a, key=lambda f: f.get("abr") or 0) if a else None
            return StreamInfo(
                video_url=best_v["url"],
                audio_url=best_a["url"] if best_a else None,
                video_headers=best_v.get("http_headers", {}),
                audio_headers=best_a.get("http_headers", {}) if best_a else None,
            )

        # Fall-back：若站点只给单流
        return StreamInfo(
            video_url=info["url"],
            video_headers=info.get("http_headers", {}),
        )

    # ---------------- 播放 ---------------- #
    def _process_queue(self) -> None:
        try:
            item = self.stream_queue.get_nowait()
        except queue.Empty:
            self.master.after(150, self._process_queue)
            return

        if isinstance(item, Exception):
            self._log("提取出现异常。详细见弹窗。")
            messagebox.showerror("提取失败", f"{item}")
            self._reset_ui()
        else:
            self._play(item)
        self.master.after(150, self._process_queue)

    def _play(self, s: StreamInfo) -> None:
        # macOS 上避免 python-vlc 导致的崩溃，使用外部 VLC CLI 播放
        if platform.system() == "Darwin":
            self._log("macOS: 使用外部 VLC 播放器以规避绑定崩溃")
            cmd = ["/Applications/VLC.app/Contents/MacOS/VLC", s.video_url]
            # 附加 HTTP 请求头
            if s.video_headers.get("Referer"):
                cmd.append(f"--http-referrer={s.video_headers['Referer']}")
            # 附加音频 slave
            if s.audio_url:
                cmd.append(f"--input-slave={s.audio_url}")
            subprocess.Popen(cmd)
            return

        if not self.vlc_player:
            self._fatal_error("VLC 未正确初始化", RuntimeError("vlc_player is None"))
            return

        self._log("准备播放…")
        try:
            # 创建媒体并附加 HTTP 头
            if s.audio_url:
                m = self.vlc_instance.media_new(s.video_url)
                self._add_http_headers(m, s.video_headers)
                if s.audio_headers:
                    self._add_http_headers(m, s.audio_headers)
                m.add_option(f":input-slave={s.audio_url}")
            else:
                m = self.vlc_instance.media_new(s.video_url)
                self._add_http_headers(m, s.video_headers)

            self.vlc_player.set_media(m)
            self._embed_player()
            self.vlc_player.play()
            self._log("开始播放 ✓")
        except Exception as exc:
            self._log(f"嵌入播放失败：{exc}；尝试外部窗口…")
            try:
                self.vlc_player.set_hwnd(None)
                self.vlc_player.play()
            except Exception as exc2:
                self._fatal_error("播放失败", exc2)
                self._reset_ui()

    def _add_http_headers(self, media: vlc.Media, headers: dict[str, str]) -> None:
        """
        给 VLC Media 对象附加 HTTP 请求头，解决 Bilibili 等站点 403 问题
        """
        for k, v in headers.items():
            safe_key = re.sub(r"[^A-Za-z0-9\\-]", "", k).lower()
            media.add_option(f":http-{safe_key}={v}")

    # ---------------- 杂项工具 ---------------- #
    def _embed_player(self) -> None:
        win_id = int(self.video_frame.winfo_id())
        sys_platform = platform.system()
        if sys_platform == "Windows":
            self.vlc_player.set_hwnd(win_id)
        elif sys_platform == "Linux":
            self.vlc_player.set_xwindow(win_id)  # type: ignore
        elif sys_platform == "Darwin":
            try:
                self.vlc_player.set_nsobject(win_id)  # type: ignore
            except Exception:
                self._log("macOS 嵌入失败，将在独立窗口播放。")
        else:
            self._log(f"未知系统 {sys_platform}，将使用独立窗口播放。")

    @staticmethod
    def _valid_url(text: str) -> bool:
        if not text:
            return False
        try:
            parsed = urlparse.urlparse(text)
            return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        except Exception:
            return False

    # ---------------- 日志 & 异常 ---------------- #
    def _log(self, msg: str) -> None:
        self.log_widget.config(state=NORMAL)
        self.log_widget.insert(END, msg + "\n")
        self.log_widget.see(END)
        self.log_widget.config(state=DISABLED)
        print(msg, file=sys.stderr)

    def _fatal_error(self, msg: str, exc: Exception) -> None:
        full = f"{msg}:\n{exc}\n\n{traceback.format_exc()}"
        self._log(full)
        messagebox.showerror(msg, str(exc))
        self.master.quit()

    def _reset_ui(self) -> None:
        self.extract_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)

    def _stop_play(self) -> None:
        if self.vlc_player and self.vlc_player.is_playing():
            self.vlc_player.stop()
        self._reset_ui()
        self._log("播放已停止。")

    def _on_close(self) -> None:
        try:
            if self.vlc_player:
                self.vlc_player.release()
            if self.vlc_instance:
                self.vlc_instance.release()
        finally:
            self.master.destroy()


def main() -> None:
    root = Tk()
    app = StreamPlayerApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app._on_close()


if __name__ == "__main__":
    main()