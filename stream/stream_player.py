from PyQt5.QtWidgets import QApplication
import sys
from web import StreamPlayerApp, StreamInfo
from player import VideoPlayer
from tkinter import Tk
import queue

def play_stream(stream_info: StreamInfo) -> None:
    """使用player.py播放web.py提取的流"""
    video_url, audio_url, headers = stream_info.get_playback_info()
    
    app = QApplication(sys.argv)
    player = VideoPlayer(video_url, headers=headers, audio_url=audio_url)
    player.resize(800, 600)
    player.show()
    sys.exit(app.exec())

def main():
    # 先用web.py提取流
    root = Tk()
    stream_queue = queue.SimpleQueue()
    app = StreamPlayerApp(root, stream_queue)
    
    def check_queue():
        try:
            item = stream_queue.get_nowait()
            if isinstance(item, Exception):
                print(f"提取失败: {item}")
                root.destroy()
                return
            root.destroy()
            play_stream(item)
        except queue.Empty:
            root.after(100, check_queue)
    
    root.after(100, check_queue)
    root.mainloop()

if __name__ == "__main__":
    main()
