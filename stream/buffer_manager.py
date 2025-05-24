import requests
import numpy as np
from typing import Dict, Optional
from threading import Lock, Thread
import cv2
import queue

class StreamBuffer:
    def __init__(self, chunk_size: int = 1024*1024):
        self.chunk_size = chunk_size
        self.buffer = bytearray()
        self.lock = Lock()
    
    def write(self, data: bytes) -> None:
        with self.lock:
            self.buffer.extend(data)
    
    def read(self, size: int = -1) -> bytes:
        with self.lock:
            if size < 0:
                return bytes(self.buffer)
            data = bytes(self.buffer[:size])
            self.buffer = self.buffer[size:]
            return data

class BufferManager:
    def __init__(self, url: str, headers: Dict[str, str], segment_size: int = 10*1024*1024):
        self.url = url
        self.headers = headers
        self.segment_size = segment_size
        self.current_buffer = StreamBuffer()
        self.download_queue = queue.Queue()
        self.is_running = True
        
        # 启动下载线程
        self.download_thread = Thread(target=self._download_worker, daemon=True)
        self.download_thread.start()
    
    def _download_worker(self):
        with requests.Session() as session:
            response = session.get(self.url, headers=self.headers, stream=True)
            if not response.ok:
                return
            
            for chunk in response.iter_content(chunk_size=64*1024):
                if not self.is_running:
                    break
                if chunk:
                    self.current_buffer.write(chunk)
                    # 通知有新数据可用
                    self.download_queue.put(len(chunk))
    
    def read_frame(self) -> Optional[np.ndarray]:
        while self.is_running:
            try:
                # 等待数据
                size = self.download_queue.get(timeout=1.0)
                # 尝试从缓冲区读取一帧
                data = self.current_buffer.read()
                if len(data) > 0:
                    # 转换为numpy数组
                    arr = np.frombuffer(data, np.uint8)
                    # 尝试解码
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        return frame
            except queue.Empty:
                continue
        return None
    
    def stop(self):
        self.is_running = False
        if self.download_thread.is_alive():
            self.download_thread.join()
