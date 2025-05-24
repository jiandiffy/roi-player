#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import cv2
import os
from PyQt5 import QtCore, QtGui, QtWidgets, QtMultimedia, QtNetwork
import requests

from buffer_manager import BufferManager




class VideoLabel(QtWidgets.QLabel):
    """用于显示视频帧并处理鼠标框选"""

    roiChanged = QtCore.pyqtSignal(QtCore.QRect)  # 对外发送 ROI 变化信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self._rubber_band = QtWidgets.QRubberBand(
            QtWidgets.QRubberBand.Rectangle, self
        )
        self._origin = QtCore.QPoint()
        self._selecting = False
        self._roi = None  # type: QtCore.QRect | None
        self.setMinimumSize(400, 300)  # 设置最小尺寸

    # ------------------------- 鼠标事件 ------------------------- #

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            self._origin = event.pos()
            self._rubber_band.setGeometry(QtCore.QRect(self._origin, QtCore.QSize()))
            self._rubber_band.show()
            self._selecting = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        """鼠标移动事件"""
        if self._selecting:
            rect = QtCore.QRect(self._origin, event.pos()).normalized()
            self._rubber_band.setGeometry(rect)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton and self._selecting:
            self._selecting = False
            self._rubber_band.hide()
            rect = self._rubber_band.geometry()
            
            # 过滤过小的 ROI，避免误操作
            if rect.width() > 10 and rect.height() > 10:
                # 如果已经有 ROI，计算相对于当前 ROI 的新区域
                if self._roi:
                    # 计算选择区域相对于当前 ROI 的相对位置
                    rel_x = (rect.x() - self._roi.x()) / self._roi.width()
                    rel_y = (rect.y() - self._roi.y()) / self._roi.height()
                    rel_w = rect.width() / self._roi.width()
                    rel_h = rect.height() / self._roi.height()
                    
                    # 创建新的相对 ROI
                    new_rect = QtCore.QRect(
                        self._roi.x() + int(rel_x * self._roi.width()),
                        self._roi.y() + int(rel_y * self._roi.height()),
                        int(rel_w * self._roi.width()),
                        int(rel_h * self._roi.height())
                    )
                    self._roi = new_rect
                else:
                    self._roi = rect
            else:
                self._roi = None
                
            self.roiChanged.emit(self._roi if self._roi else QtCore.QRect())
        super().mouseReleaseEvent(event)

    # ----------------------- 对外接口 ----------------------- #

    def current_roi(self) -> QtCore.QRect | None:
        return self._roi

    def clear_roi(self) -> None:
        self._roi = None
        self.update()


# --------- 可点击跳转的进度条 --------- #
class VideoSlider(QtWidgets.QSlider):
    """点击任意位置即可跳帧的水平进度条"""

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            # 计算鼠标横向位置对应的帧索引
            val = QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                event.pos().x(),
                self.width(),
            )
            self.setValue(val)
            # 主动发射 sliderMoved 以复用现有逻辑
            self.sliderMoved.emit(val)
            event.accept()
        super().mousePressEvent(event)


class VideoPlayer(QtWidgets.QMainWindow):
    """主窗口：负责解码、定时刷新与 ROI 裁剪"""

    def __init__(self, video_source: str, headers: dict = None, audio_url: str = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("视频 ROI 工具")
        self._is_stream = bool(headers)
        
        # ---------- 视频解码 ---------- #
        self._video_source = video_source
        self._headers = headers
        self._session = requests.Session()  # 用于重用连接
        
        if self._is_stream:
            # 对于流媒体，首先创建一个session并验证连接
            if headers and headers.get("video"):
                self._session.headers.update(headers["video"])
            response = self._session.get(video_source, stream=True)
            if not response.ok:
                raise RuntimeError(f"无法访问视频流: {response.status_code}")
            
            self._cap = cv2.VideoCapture(video_source)
            self._total_frames = 1000  # 默认值
            self._duration_ms = 40000  # 默认40秒
        else:
            self._cap = cv2.VideoCapture(video_source)
            self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self._duration_ms = int(self._total_frames * (1000/25))  # 假设25fps
            
        if not self._cap.isOpened():
            raise RuntimeError(f"无法打开视频源: {video_source}")
        
        fps = self._cap.get(cv2.CAP_PROP_FPS) or 25
        self._interval_ms = int(1000 / fps)

        # ---------- 音频处理 ---------- #
        self._media_player = QtMultimedia.QMediaPlayer()
        if audio_url:
            # 创建带headers的网络请求
            if headers and headers.get("audio"):
                # ----------- 修改后 -----------
                request = QtNetwork.QNetworkRequest(QtCore.QUrl(audio_url))
                for k, v in headers["audio"].items():
                    request.setRawHeader(k.encode(), v.encode())

                # QMediaResource 可以携带完整的 request（含自定义头）
                media_res   = QtMultimedia.QMediaResource(request)
                media       = QtMultimedia.QMediaContent(media_res)
                self._media_player.setMedia(media)        # ✓ 能正确加载
            else:
                self._media_player.setMedia(
                    QtMultimedia.QMediaContent(QtCore.QUrl(audio_url))
                )
        else:
            # 本地文件
            self._media_player.setMedia(
                QtMultimedia.QMediaContent(QtCore.QUrl.fromLocalFile(os.path.abspath(video_source)))
            )
        
        self._media_player.setVolume(100)
        self._media_player.error.connect(self._on_audio_error)
        # 让音频时钟驱动视频渲染
        self._media_player.positionChanged.connect(self._on_audio_tick)
        # 延迟500ms再调用play()，以确保音频输出正确启动
        QtCore.QTimer.singleShot(500, self._media_player.play)

        # ---------- UI ---------- #
        self._label = VideoLabel(self)
        # 构建带进度条的中央布局
        central = QtWidgets.QWidget(self)
        vbox = QtWidgets.QVBoxLayout(central)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(self._label)
        self.setCentralWidget(central)
        self._label.roiChanged.connect(self._on_roi_changed)

        # ---------- 控制面板 ---------- #
        self._control_panel = ControlPanel(self)
        self._control_panel._pause_btn.clicked.connect(self._toggle_pause)
        self._control_panel._reset_roi_btn.clicked.connect(self._reset_roi)
        self._control_panel._rotate_btn.clicked.connect(self._rotate_90)
        self._control_panel._slider.setStyleSheet("QSlider::handle:horizontal { width: 8px; }")
        
        # 统一使用毫秒作为进度条单位
        self._control_panel._slider.setRange(0, self._duration_ms)
        self._control_panel._slider.sliderMoved.connect(self._on_slider_moved)

        # 音量滑条联动
        self._control_panel._volume_slider.valueChanged.connect(self._on_volume_changed)
        
        # ---------- 定时器播放 ---------- #

        # ---------- 鼠标位置检测定时器 ---------- #
        self._mouse_check_timer = QtCore.QTimer(self)
        self._mouse_check_timer.setInterval(100)  # 100ms = 0.1秒
        self._mouse_check_timer.timeout.connect(self._check_mouse_position)
        self._mouse_check_timer.start()

        # ---------- 状态 ---------- #
        self._roi = None  # type: QtCore.QRect | None
        self._rotation = 0  # 当前旋转角度（0/90/180/270）
        self._paused = False  # 播放/暂停状态
        
        # 显示并定位控制面板
        self._update_control_panel_position()

        # 初始定位但隐藏控制面板
        self._control_panel.show()  # 先显示以便用户知道面板位置
        QtCore.QTimer.singleShot(2000, self._control_panel.hide)  # 2秒后自动隐藏

    def resizeEvent(self, event):
        """窗口大小改变时重新定位控制面板"""
        super().resizeEvent(event)
        self._update_control_panel_position()

    def _update_control_panel_position(self):
        """更新控制面板位置"""
        panel_height = 60
        panel_width = self.width() * 0.8
        x = (self.width() - panel_width) / 2
        y = self.height() - panel_height - 20
        self._control_panel.setGeometry(int(x), int(y), int(panel_width), panel_height)
        self._control_panel.raise_()

    # ---------------- 鼠标位置感知 ---------------- #

    def _on_roi_changed(self, rect: QtCore.QRect):
        self._roi = rect if rect.isValid() and not rect.isNull() else None

    def _reset_roi(self):
        self._roi = None
        self._label.clear_roi()

    def _rotate_90(self):
        """顺时针旋转 90°"""
        self._rotation = (self._rotation + 90) % 360

    def _toggle_pause(self):
        """暂停/继续播放"""
        self._paused = not self._paused
        if self._paused:
            self._media_player.pause()
            self._control_panel._pause_btn.setText("▶")
        else:
            self._media_player.play()
            self._control_panel._pause_btn.setText("⏸")

    def _on_volume_changed(self, value):
        """处理音量变化"""
        self._media_player.setVolume(value)

    def _on_slider_moved(self, value):
        """统一使用毫秒为单位进行跳转"""
        target_ms = value
        
        if self._is_stream:
            # 重新打开视频流
            self._cap.release()
            self._cap = cv2.VideoCapture(self._video_source)
            # 设置时间戳
            self._cap.set(cv2.CAP_PROP_POS_MSEC, target_ms)
        else:
            self._cap.set(cv2.CAP_PROP_POS_MSEC, target_ms)
        
        # 同步音频位置
        if not self._paused:
            self._media_player.setPosition(target_ms)
            self._media_player.play()
        
        self._render_frame()

    # --------------------- 帧刷新 --------------------- #

    def _render_frame(self):
        ret, frame = self._cap.read()
        if not ret:
            if not self._is_stream:
                # 本地文件循环播放
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._media_player.setPosition(0)
                if not self._paused:
                    self._media_player.play()
            return

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # 更新进度条
        current_ms = int(self._cap.get(cv2.CAP_PROP_POS_MSEC))
        if current_ms > 0:  # 避免无效值
            self._control_panel._slider.blockSignals(True)
            self._control_panel._slider.setValue(current_ms)
            self._control_panel._slider.blockSignals(False)

        # ----------------------------------------------
        # 旋转与 ROI 裁剪，然后显示到 QLabel
        # ----------------------------------------------
        if self._rotation == 90:
            frame_rgb = cv2.rotate(frame_rgb, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation == 180:
            frame_rgb = cv2.rotate(frame_rgb, cv2.ROTATE_180)
        elif self._rotation == 270:
            frame_rgb = cv2.rotate(frame_rgb, cv2.ROTATE_90_COUNTERCLOCKWISE)

        if self._roi:
            h_label, w_label = self._label.height(), self._label.width()
            y_scale = frame_rgb.shape[0] / h_label
            x_scale = frame_rgb.shape[1] / w_label
            x1 = int(self._roi.left() * x_scale)
            y1 = int(self._roi.top() * y_scale)
            x2 = int(self._roi.right() * x_scale)
            y2 = int(self._roi.bottom() * y_scale)
            frame_rgb = frame_rgb[y1:y2, x1:x2]

        # 转为 QImage 并显示
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w
        qt_img = QtGui.QImage(frame_rgb.data.tobytes(), w, h, bytes_per_line, QtGui.QImage.Format_RGB888)
        pix = QtGui.QPixmap.fromImage(qt_img)
        self._label.setPixmap(pix.scaled(self._label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

    def _on_audio_tick(self, pos_ms: int):
        """音频时钟 → 渲染对应时间戳的视频帧"""
        if self._cap is None:
            return
        delta = abs(self._cap.get(cv2.CAP_PROP_POS_MSEC) - pos_ms)
        if delta > 80:
            self._cap.set(cv2.CAP_PROP_POS_MSEC, pos_ms)
        self._render_frame()

    def _check_mouse_position(self):
        """检查鼠标位置并控制控制面板的显示"""
        cursor_pos = QtGui.QCursor.pos()
        panel_rect = self._control_panel.geometry()
        video_rect = self._label.geometry()
        
        # 将鼠标位置从全局坐标转换为窗口坐标
        window_pos = self.mapFromGlobal(cursor_pos)
        
        # 检查鼠标是否在视频区域内或控制面板区域内
        if video_rect.contains(window_pos) or panel_rect.contains(window_pos):
            # 如果鼠标在视频区域或控制面板区域内，显示控制面板
            if not self._control_panel.isVisible():
                self._control_panel.show()
        else:
            # 如果鼠标不在这些区域内，隐藏控制面板
            if self._control_panel.isVisible():
                self._control_panel.hide()

    # -------------------- 关闭清理 -------------------- #

    def closeEvent(self, event: QtGui.QCloseEvent):
        """窗口关闭事件"""
        self._mouse_check_timer.stop()
        if self._cap and self._cap.isOpened():
            self._cap.release()
        if hasattr(self, '_session'):
            self._session.close()
        
        # 停止并清理音频播放器
        self._media_player.stop()
        self._media_player.setMedia(QtMultimedia.QMediaContent())
        
        event.accept()

    # -------------------- 音频错误处理 -------------------- #
    def _on_audio_error(self, error):
        """音频错误处理，打印详细错误信息"""
        error_msg = self._media_player.errorString()
        print(f"音频播放错误: {error}, {error_msg}")

# 新增控制面板类
class ControlPanel(QtWidgets.QWidget):
    """半透明悬浮控制面板"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # 设置无边框窗口
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        
        # 创建水平布局
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(15)
        
        # 设置固定高度
        self.setFixedHeight(60)
        
        # 设置样式
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: white;
                border: none;
                padding: 8px;
                font-size: 18px;
                border-radius: 12px;
                min-width: 36px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 40);
            }
            QSlider {
                height: 24px;
                background: transparent;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: rgba(255, 255, 255, 80);
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: white;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QSlider::sub-page:horizontal {
                background: rgb(82, 139, 255);
                border-radius: 2px;
            }
            QSlider::handle:horizontal:hover {
                background: rgb(235, 235, 235);
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 8px;
            }
        """)
        
        # 创建按钮和滑块
        self._pause_btn = QtWidgets.QPushButton("⏸", self)
        self._pause_btn.setCheckable(False)  # 设置为普通按钮
        self._reset_roi_btn = QtWidgets.QPushButton("o", self)
        self._rotate_btn = QtWidgets.QPushButton("⟳", self)
        self._slider = VideoSlider(QtCore.Qt.Horizontal, self)
        
        # 添加到布局
        layout.addWidget(self._pause_btn)
        layout.addWidget(self._reset_roi_btn)
        layout.addWidget(self._rotate_btn)
        layout.addWidget(self._slider, 1)  # 1表示伸展因子

        # 音量调节滑条
        self._volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(100)
        self._volume_slider.setToolTip("音量")
        
        layout.addWidget(self._volume_slider)

        # 默认隐藏
        self.hide()

    def paintEvent(self, event):
        """绘制半透明黑色圆角矩形背景"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        # 使用更透明的黑色背景（透明度值改为120）
        painter.setBrush(QtGui.QColor(0, 0, 0, 120))  # 半透明黑色
        painter.setPen(QtCore.Qt.NoPen)
        # 增加圆角弧度（从20增加到25）
        painter.drawRoundedRect(self.rect(), 25, 25)
        super().paintEvent(event)

def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    
    if len(sys.argv) < 2:
        print("用法: python player.py <video_source> [audio_url]")
        sys.exit(1)
        
    video_source = sys.argv[1]
    audio_url = sys.argv[2] if len(sys.argv) > 2 else None
    headers = {}
    
    player = VideoPlayer(video_source, headers=headers, audio_url=audio_url)
    player.resize(800, 600)
    player.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()