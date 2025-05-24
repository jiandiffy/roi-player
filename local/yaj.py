#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import cv2
import os
from PyQt5 import QtCore, QtGui, QtWidgets, QtMultimedia, QtMultimediaWidgets




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
        self.setMinimumSize(4, 3)  # 设置最小尺寸

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

    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("视频 ROI 工具")

        # ---------- 视频解码 ---------- #
        self._cap = cv2.VideoCapture(video_path)
        if not self._cap.isOpened():
            raise RuntimeError(f"无法打开视频文件: {video_path}")
        fps = self._cap.get(cv2.CAP_PROP_FPS) or 25
        self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._interval_ms = int(1000 / fps)
        
        # ---------- 音频处理 ---------- #
        self._media_player = QtMultimedia.QMediaPlayer()
        # 使用绝对路径，避免相对路径在 QMediaPlayer 中无法解析
        audio_url = QtCore.QUrl.fromLocalFile(os.path.abspath(video_path))
        self._media_player.setMedia(QtMultimedia.QMediaContent(audio_url))
        self._media_player.setVolume(100)  # 设置音量为100%
        # 若发生错误，输出调试信息
        self._media_player.error.connect(self._on_audio_error)
        # 开始音频播放
        self._media_player.play()
        
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
        self._control_panel._slider.setRange(0, self._total_frames - 1)
        self._control_panel._slider.sliderMoved.connect(self._on_slider_moved)

        # 音量滑条联动
        self._control_panel._volume_slider.valueChanged.connect(self._media_player.setVolume)

        # ---------- 定时器播放 ---------- #
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._next_frame)
        self._timer.start(self._interval_ms)

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
            self._timer.stop()
            self._media_player.pause()
            self._control_panel._pause_btn.setText("▶")
        else:
            self._timer.start(self._interval_ms)
            self._media_player.play()
            self._control_panel._pause_btn.setText("⏸")

    def _on_slider_moved(self, value):
        """跳转到指定帧并立即刷新画面和音频"""
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, value)
        
        # 同步音频位置
        if not self._paused:
            # 计算新的音频位置（毫秒）
            new_time = int(value * self._interval_ms)
            self._media_player.setPosition(new_time)
        
        self._next_frame()

    # --------------------- 帧刷新 --------------------- #

    def _next_frame(self):
        ret, frame = self._cap.read()
        if not ret:
            # 到达文件末尾时重置视频和音频
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._media_player.setPosition(0)
            if not self._paused:
                self._media_player.play()
            return

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # 根据当前旋转角度旋转帧
        if self._rotation == 90:
            frame_rgb = cv2.rotate(frame_rgb, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation == 180:
            frame_rgb = cv2.rotate(frame_rgb, cv2.ROTATE_180)
        elif self._rotation == 270:
            frame_rgb = cv2.rotate(frame_rgb, cv2.ROTATE_90_COUNTERCLOCKWISE)

        if self._roi:
            # 将 QLabel 坐标映射到视频帧坐标
            h_label, w_label = self._label.height(), self._label.width()
            y_scale = frame_rgb.shape[0] / h_label
            x_scale = frame_rgb.shape[1] / w_label
            x1 = int(self._roi.left() * x_scale)
            y1 = int(self._roi.top() * y_scale)
            x2 = int(self._roi.right() * x_scale)
            y2 = int(self._roi.bottom() * y_scale)
            frame_rgb = frame_rgb[y1:y2, x1:x2]


        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w

        qt_img = QtGui.QImage(
            frame_rgb.data.tobytes(),  
            w,
            h,
            bytes_per_line,
            QtGui.QImage.Format_RGB888,
        )
        pix = QtGui.QPixmap.fromImage(qt_img)
        self._label.setPixmap(
            pix.scaled(
                self._label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )
        # 同步进度条
        current_frame = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
        self._control_panel._slider.blockSignals(True)
        self._control_panel._slider.setValue(current_frame)
        self._control_panel._slider.blockSignals(False)

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
        if self._cap.isOpened():
            self._cap.release()
            
        # 停止并清理音频播放器
        self._media_player.stop()
        self._media_player.setMedia(QtMultimedia.QMediaContent())  # 使用空的 QMediaContent 对象
        
        event.accept()

    # -------------------- 音频错误处理 -------------------- #
    def _on_audio_error(self, error_code):
        """Handle audio playback errors."""
        print("QMediaPlayer 错误:", self._media_player.errorString())

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
        print("用法: python video_player_roi.py <video_file>")
        sys.exit(1)

    player = VideoPlayer(sys.argv[1])
    player.resize(800, 600)
    player.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()