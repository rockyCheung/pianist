import sys, os, tempfile, markdown
import json
import time
from threading import Thread, Lock
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QDialog, QGridLayout,
    QLineEdit, QGraphicsDropShadowEffect, QSizePolicy,
    QGraphicsView, QGraphicsScene, QGraphicsWidget, QGraphicsProxyWidget, QSlider, QComboBox, QScrollArea, QGroupBox,
    QStyle, QTextEdit, QTextBrowser, QGraphicsRectItem, QGraphicsOpacityEffect
)
from PySide6.QtCore import (
    Qt, QUrl, QTimer, QPoint, Signal, QObject,
    QSize, QPropertyAnimation, QEasingCurve, QRectF, QEvent, QTranslator, Property
)
from PySide6.QtMultimedia import QSoundEffect, QMediaPlayer, QAudioOutput
from PySide6.QtGui import QColor, QPainter, QKeyEvent, QFont, QLinearGradient, QTransform
import rtmidi
from pydub import AudioSegment


class CoverAnimProxy(QObject):
    def __init__(self, cover_item):
        super().__init__()
        self.cover = cover_item

    def get_opacity(self):
        return self.cover.opacity()

    def set_opacity(self, value):
        self.cover.setOpacity(value)

    opacity = Property(float, get_opacity, set_opacity)


class PianoSignal(QObject):
    midi_note_on = Signal(int, int)  # MIDI音符按下信号
    midi_note_off = Signal(int)  # MIDI音符释放信号


class PianoKey(QPushButton):
    def __init__(self, note, volume, file_format, is_black=False, parent=None):
        super().__init__(parent)
        # 新增原始位置记录
        self.edge_highlight = None
        self.shadow = None
        self.audio_output = None
        self.shadow_anim = None
        self.original_geometry = None
        self.note = note
        self.sound = None
        self.file_format = file_format if file_format else 'wav'
        self.is_black = is_black
        # 默认音量设为80%
        self.volume = volume if volume else 0.8
        self.press_anim = QPropertyAnimation(self, b"geometry")
        self.release_anim = QPropertyAnimation(self, b'geometry')

        # 初始化样式和布局
        self.init_style()
        self.init_label()
        self.init_sound()
        self.init_animations()

    def init_label(self):
        self.update_label_style()

    def update_label_style(self):
        font = QFont("Arial", 8 if self.is_black else 10)
        font.setItalic(True)
        self.setText(self.note[:-1])
        self.setFont(font)

    def init_style(self):
        # 公共阴影参数
        self.shadow = QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(15 if self.is_black else 8)
        self.shadow.setColor(QColor(0, 0, 0, 180))
        self.shadow.setOffset(3, 5)  # 初始偏移量
        self.setGraphicsEffect(self.shadow)

        if self.is_black:
            self.setFixedSize(22, 102)
            # 改进后的黑键样式
            self.setStyleSheet(f"""
                        QPushButton {{
                            background: qradialgradient(
                                cx:0.3 cy:0.3, radius:1,
                                stop:0 #404040,
                                stop:0.6 #303030,
                                stop:1 #202020
                            );
                            border: 1px solid #000000;
                            border-top: 1px solid #606060;
                            color: #FFFFFF;
                        }}
                        QPushButton:hover {{
                            background: qradialgradient(
                                cx:0.3 cy:0.3, radius:1,
                                stop:0 #505050,
                                stop:0.7 #404040,
                                stop:1 #303030
                            );
                        }}
                        QPushButton:pressed {{
                            background: qradialgradient(
                                cx:0.4 cy:0.4, radius:1,
                                stop:0 #202020,
                                stop:1 #101010
                            );
                        }}
                    """)
            # 强化悬浮阴影
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(12)
            shadow.setXOffset(4)
            shadow.setYOffset(6)
            shadow.setColor(QColor(0, 0, 0, 180))
            self.setGraphicsEffect(shadow)
            self.raise_()  # 替代z-index

        else:
            self.setMinimumWidth(34)
            self.setFixedHeight(140)
            # 改进后的白键样式
            self.setStyleSheet(f"""
                        QPushButton {{
                            background: qlineargradient(
                                x1:0 y1:0, x2:0 y2:1,
                                stop:0 #f8f8f8,
                                stop:0.3 #f0f0f0,
                                stop:0.7 #e8e8e8,
                                stop:1 #e0e0e0
                            );
                            border-right: 2px solid #c0c0c0;
                            border-left: 1px solid #f0f0f0;
                            border-bottom: 3px solid #a0a0a0;
                            color: #060606;
                        }}
                        QPushButton:hover {{
                            background: qlineargradient(
                                x1:0 y1:0, x2:0 y2:1,
                                stop:0 #f0f0f0,
                                stop:0.5 #e8e8e8,
                                stop:1 #e0e0e0
                            );
                        }}
                        QPushButton:pressed {{
                            background: qradialgradient(
                                cx:0.4 cy:0.4, radius:1,
                                stop:0 #d0d0d0,
                                stop:1 #c0c0c0
                            );
                            border-bottom: 1px solid #808080;
                        }}
                    """)
            # 添加边缘高光
            self.edge_highlight = QGraphicsOpacityEffect()
            self.edge_highlight.setOpacity(0.3)
            self.setGraphicsEffect(self.edge_highlight)
            # 添加象牙质感纹理
            texture = QLinearGradient(0, 0, 0, self.height())
            texture.setColorAt(0.3, QColor(255, 255, 255, 30))
            texture.setColorAt(0.7, QColor(200, 200, 200, 10))
            self.setGraphicsEffect(QGraphicsOpacityEffect(opacity=0.8))

    def init_sound(self):
        # 每次初始化前清除旧资源
        if self.sound:
            self.sound.deleteLater()
            self.sound = None

        # 支持更多高音质格式
        supported_formats = ['wav', 'flac', 'mp3', 'ogg', 'm4a']
        if self.file_format not in supported_formats:
            self.file_format = 'wav'  # 默认回退到wav格式

        # 优先尝试加载无损格式
        format_priority = ['flac', 'wav', 'm4a', 'ogg', 'mp3']
        sound_file = None
        for fmt in format_priority:
            sound_file = self.load_audio_file(fmt)
            self.file_format = fmt
            if sound_file:
                break

        if not sound_file:
            print(f"错误：{self.note} 的所有格式音频文件缺失！")
            self.sound = None  # 显式设置为None
            return

        try:
            if self.file_format in ['mp3', 'ogg', 'flac', 'm4a']:
                # 初始化QMediaPlayer及其音频输出
                self.sound = QMediaPlayer()
                self.audio_output = QAudioOutput()  # 必须显式创建音频输出对象
                self.sound.setAudioOutput(self.audio_output)
                self.audio_output.setVolume(self.volume)  # 在此处设置音量
            else:
                # 初始化QSoundEffect
                self.sound = QSoundEffect()
                self.sound.setVolume(self.volume)

            # 加载音频源
            self.sound.setSource(QUrl.fromLocalFile(sound_file))
            # print(f"成功加载音频：{sound_file}")

        except Exception as e:
            print(f"音频加载失败：{sound_file}，错误：{str(e)}")
            self.generate_fallback_sound()  # 确保失败时设为None

    def generate_fallback_sound(self):
        """生成应急正弦波音频"""
        try:
            from scipy.io.wavfile import write
            import numpy as np

            # 生成1秒440Hz正弦波
            sample_rate = 44100
            t = np.linspace(0, 1, sample_rate)
            waveform = 0.5 * np.sin(2 * np.pi * 440 * t)

            # 保存临时文件
            temp_file = os.path.join(tempfile.gettempdir(), f"{self.note}_fallback.wav")
            write(temp_file, sample_rate, waveform)

            self.sound = QSoundEffect()
            self.sound.setVolume(self.volume)  # 添加音量设置
            self.sound.setSource(QUrl.fromLocalFile(temp_file))
            print(f"已生成应急音频：{temp_file}")
        except ImportError:
            print('警告：scipy未安装，无法生成应急音频')
            return
        except Exception as e:
            print(f"应急音频生成失败: {str(e)}")
            # 添加基本蜂鸣声作为最后的应急方案
            self.sound = QSoundEffect()
            self.sound.setVolume(0.1)
            self.sound.setSource(QUrl.fromLocalFile('sounds/beep.wav'))

    def init_animations(self):
        # 确保图形效果已初始化后再创建动画
        if self.graphicsEffect() is None:
            self.init_style()  # 确保阴影效果已设置

        # 按压动画
        self.press_anim.setDuration(80)
        self.press_anim.setEasingCurve(QEasingCurve.Type.OutQuad)

        # 释放动画
        self.release_anim.setDuration(220)
        self.release_anim.setEasingCurve(QEasingCurve.Type.OutBack)

    def press(self):
        # 在调用动画前检查是否存在
        if self.shadow_anim is not None:
            self.shadow_anim.start()
        # 停止所有动画
        if self.press_anim.state() == QPropertyAnimation.State.Running:
            self.press_anim.stop()
        if self.release_anim.state() == QPropertyAnimation.State.Running:
            self.release_anim.stop()

        if self.sound is None:
            print(f"警告：{self.note} 音频未初始化，跳过播放")
            self.init_sound()
            return
        try:
            # 分类控制音频
            if isinstance(self.sound, QSoundEffect):
                if self.sound.status() == QSoundEffect.Status.Ready:
                    self.sound.play()  # 确保音频已加载
            elif isinstance(self.sound, QMediaPlayer):
                self.sound.stop()
                self.sound.setPosition(0)  # 重置播放位置
                self.sound.play()
        except Exception as e:
            print(f"播放失败 [{self.note}]: {str(e)}")
        self.press_anim.setStartValue(self.original_geometry)
        self.press_anim.setEndValue(self.original_geometry.translated(0, 5))
        self.press_anim.start()

    def release(self):
        if self.shadow_anim is not None:
            self.shadow_anim.setDirection(QPropertyAnimation.Direction.Backward)
            self.shadow_anim.start()
        # 停止音频播放
        if self.sound is not None:
            # 创建淡出动画
            if isinstance(self.sound, QMediaPlayer):
                self.sound.stop()
            elif isinstance(self.sound, QSoundEffect):
                fade_anim = QPropertyAnimation(self.sound, b"volume")
                fade_anim.setDuration(200)
                fade_anim.setStartValue(self.volume)
                fade_anim.setEndValue(0)
                fade_anim.finished.connect(self.sound.stop)
                fade_anim.start()
        # 使用记录的原始位置
        self.release_anim.setStartValue(self.geometry())
        self.release_anim.setEndValue(self.original_geometry.translated(0, -5))
        self.release_anim.start()

    def load_audio_file(self, fmt):
        current_file = f'sounds/{self.note}.{fmt}'
        if os.path.exists(current_file):
            return current_file
        return None


class PianoKeyItem(QGraphicsWidget):
    # 定义信号
    rotation_angle_changed: Signal = Signal(float)
    perspective_depth_changed: Signal = Signal(int)

    def __init__(self, note, volume, file_format, is_black=False, parent=None):
        super().__init__(parent)
        self.note = note
        self.is_black = is_black
        self.proxy = QGraphicsProxyWidget(self)
        self.key_widget = PianoKey(note, volume, file_format, is_black)
        self.proxy.setWidget(self.key_widget)
        self.setZValue(1 if is_black else 0)
        self.setAcceptHoverEvents(True)
        # 设置旋转中心为底部中心
        self.setTransformOriginPoint(self.boundingRect().center().x(), self.boundingRect().height())

        # 添加覆盖层
        self.cover = QGraphicsRectItem(self)
        self.cover.setBrush(QColor(160, 160, 160, 120))  # 灰色半透明
        self.cover.setPen(Qt.PenStyle.NoPen)
        self.cover.setZValue(3)  # 确保在最上层
        self.cover.hide()
        self.current_octave = 0  # 初始音程
        # 创建动画代理
        self.cover_proxy = CoverAnimProxy(self.cover)
        self.cover_anim = QPropertyAnimation(self.cover_proxy, b"opacity")
        self.cover_anim.setDuration(300)

        # 3D旋转参数
        self._rotation_angle = 0.0  # 绕X轴旋转角度
        self._perspective_depth = 50  # 透视深度（模拟3D效果）
        # 3D旋转动画
        self.rotate_anim = QPropertyAnimation(self, b"rotation_angle")
        self.rotate_anim.setDuration(120)
        self.rotate_anim.setStartValue(0)
        self.rotate_anim.setEndValue(-1.2 if is_black else -0.8)
        self.rotate_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        # 透视变换动画
        self.perspective_anim = QPropertyAnimation(self, b"perspective_depth")
        self.perspective_anim.setDuration(100)
        self.perspective_anim.setStartValue(50)
        self.perspective_anim.setEndValue(30)
        self.perspective_anim.setEasingCurve(QEasingCurve.Type.Linear)

    def get_rotation_angle(self):
        return self._rotation_angle

    def set_rotation_angle(self, angle):
        if self._rotation_angle != angle:
            self._rotation_angle = angle
            self._apply_3d_transform()
            # 手动发射信号
            self.rotation_angle_changed.emit(angle)

    def get_perspective_depth(self):
        return self._perspective_depth

    def set_perspective_depth(self, depth):
        if self._perspective_depth != depth:
            self._perspective_depth = depth
            self._apply_3d_transform()
            # 手动发射信号
            self.perspective_depth_changed.emit(depth)

    # 修改属性声明，添加notify信号（可选）
    # 声明动态属性（无需显式继承 QObject）
    rotation_angle = Property(
        float,
        lambda self: self._rotation_angle,
        lambda self, value: self.set_rotation_angle(value),
        notify=rotation_angle_changed
    )

    perspective_depth = Property(
        int,
        lambda self: self._perspective_depth,
        lambda self, value: self.set_perspective_depth(value),
        notify=perspective_depth_changed
    )

    def _apply_3d_transform(self):
        """应用3D透视变换矩阵"""
        transform = QTransform()
        # 绕X轴旋转（模拟按键下沉）
        transform.rotate(self._rotation_angle, Qt.Axis.XAxis)
        # 添加透视变形（近大远小）
        transform.translate(0, self._perspective_depth * 0.1)
        transform.scale(1 - abs(self._rotation_angle) * 0.01,
                        1 - abs(self._rotation_angle) * 0.03)
        self.setTransform(transform)

    def set_geometry(self, rect):
        self.setGeometry(rect)
        self.proxy.setGeometry(rect)
        self.key_widget.original_geometry = self.geometry()
        # 更新覆盖层尺寸
        self.cover.setRect(rect)

    def update_cover(self, current_octave):
        """根据当前音程更新覆盖层可见性"""
        try:
            note_octave = int(self.note[-1])
            if note_octave != current_octave:
                self.cover_anim.stop()
                self.cover_anim.setStartValue(0.0)
                self.cover_anim.setEndValue(0.7)
                self.cover_anim.start()
                self.cover.show()
            else:
                self.cover_anim.stop()
                self.cover_anim.setStartValue(self.cover.opacity())
                self.cover_anim.setEndValue(0.0)
                self.cover_anim.finished.connect(self.cover.hide)
                self.cover_anim.start()
        except:
            pass

    def hoverEnterEvent(self, event):
        self.setZValue(self.zValue() + 0.1)

    def hoverLeaveEvent(self, event):
        self.setZValue(1 if self.is_black else 0)

    def press(self):
        # 启动动画
        self.rotate_anim.start()
        self.perspective_anim.start()

        # 动态调整音效
        if self.key_widget.sound:
            # 根据旋转角度调整音量
            volume_anim = QPropertyAnimation(self.key_widget.sound, b"volume")
            volume_anim.setDuration(120)
            volume_anim.setStartValue(self.key_widget.volume)
            volume_anim.setEndValue(min(self.key_widget.volume * 1.2, 1.0))
            volume_anim.start()

        self.key_widget.press()

    def release(self):
        self.rotate_anim.setDirection(QPropertyAnimation.Direction.Backward)
        self.perspective_anim.setDirection(QPropertyAnimation.Direction.Backward)
        self.rotate_anim.start()
        self.perspective_anim.start()
        self.key_widget.release()


class PianoWidget(QWidget):
    # 类级别信号声明
    octave_changed = Signal(int)

    notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    key_sequence = [
        'C', 'D', 'E', 'F', 'G', 'A', 'B',
        # 白键映射
        'C#', 'D#', 'F#', 'G#', 'A#'
        # 黑键映射
    ]

    def __init__(self):
        super().__init__()

        self.record_start = None
        self.icons = self.init_icons()
        # 初始化布局参数
        self.x_pos = 0  # 初始横坐标
        self.y_pos = 80  # 初始纵坐标
        # 生成白键并记录MIDI信息
        self.white_height = 140  # 白键高度
        self.white_width = 34  # 白键初始宽度
        # 生成钢琴键
        self.start_note = 21  # A0
        self.end_note = 108  # C8
        self.black_width = 22  # 初始黑键宽度
        self.black_hight = 102
        self.position_ratio = 0.25

        # 加载全局配置
        config = self.load_config()
        self.global_volume = config.get('volume', 0.8)  # 默认音量
        self.current_octave = 0  # 默认音程为0
        self.black_key_pattern = [
            0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0  # 标准钢琴黑键模式
        ]
        self.file_format = config.get('file_format', 'flac')
        # print(f'file_format:{self.file_format}')
        self.key_map = self.load_keymap(config.get('keymap'))
        self.white_keys_data = []  # 存储白键位置信息
        self.black_keys_data = []  # 存储黑键位置信息
        self.white_items = []
        self.black_items = []
        self.recording = False
        self.record_data = []
        self.midi_in = None
        self.signals = PianoSignal()
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.adjust_layout)

        # 初始化图形界面
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setMinimumHeight(160)

        # 控制面板
        self.control_layout = QHBoxLayout()
        self.record_btn = None
        self.play_btn = None
        self.settings_btn = None
        self.help_btn = None
        self.octave_label = QLabel(f"{self.current_octave}")
        # 新增音量滑块控制
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.global_volume * 100))
        self.main_layout = QVBoxLayout()
        self.init_ui()
        self.init_midi()
        # 初始化加载音频文件
        self.preload_audio()
        self.current_path = "help.md"
        self.help_view = QTextEdit()
        self.help_dialog = QDialog(self)
        self.init_help_dialog()
        self.midi_lock = Lock()
        # 正确连接信号
        self.octave_changed.connect(self.update_key_covers)

    def init_ui(self):
        # 添加全局样式
        self.setStyleSheet("""
            /* macOS 风格全局设置 */
            QPushButton { 
                qproperty-iconSize: 14px; 
                margin: 2px;
            }
            QGroupBox {
                border: 1px solid #C6C6C8;
                border-radius: 8px;
                margin-top: 16px;
                padding-top: 24px;
                font: 14px 'SF Pro Text';
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #1F1F1F;
            }
        """)
        self.setWindowTitle(self.tr('Pianist'))
        self.setMinimumSize(900, 330)

        # 新增Mac风格样式表
        self.volume_slider.setStyleSheet("""
            QSlider {
                height: 16px;
            }
            QSlider::groove:horizontal {
                background: #e0e0e0;
                height: 2px;
                border-radius: 1px;
                margin: 7px 0;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #c0c0c0;
                width: 14px;
                height: 14px;
                margin: -6px 0;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #f8f8f8;
                border-color: #a0a0a0;
            }
            QSlider::handle:horizontal:pressed {
                background: #f0f0f0;
            }
            QSlider::add-page:horizontal {
                background: #d0d0d0;
            }
            QSlider::sub-page:horizontal {
                background: #007aff;
            }
        """)
        # 在PianoWidget类中设置滑块尺寸策略
        self.volume_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        self.volume_slider.setFixedWidth(200)  # 设置合适宽度
        # 通过图形效果添加阴影
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(4)
        shadow.setXOffset(0)
        shadow.setYOffset(2)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.volume_slider.setGraphicsEffect(shadow)

        # 控制面板布局
        self.control_layout.setSpacing(15)
        self.control_layout.setContentsMargins(10, 10, 10, 10)
        # 左侧控制组
        left_group = QGroupBox(self.tr("Performance Control"))
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(8, 12, 8, 12)

        # 音程显示
        octave_row = QHBoxLayout()
        octave_row.addWidget(QLabel(self.tr("Active Interval：")))
        # octave_label = QLabel(f"{self.current_octave + 1}")
        self.octave_label.setStyleSheet("font-weight: bold; color: #007AFF;")
        octave_row.addWidget(self.octave_label)
        octave_row.addStretch()
        left_layout.addLayout(octave_row)
        # 按钮组
        btn_group = QHBoxLayout()
        self.record_btn = self.create_styled_button(self.tr("Track"), "record", "destructive")
        self.play_btn = self.create_styled_button(self.tr("Play"), "play", "primary")
        btn_group.addWidget(self.record_btn)
        btn_group.addWidget(self.play_btn)
        left_layout.addLayout(btn_group)
        left_group.setLayout(left_layout)
        # 右侧控制组
        right_group = QGroupBox(self.tr("Settings"))
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(8, 12, 8, 12)

        # 音量控制
        volume_layout = QHBoxLayout()
        volume_layout.addWidget(QLabel(self.tr("􀊨 Volume")))  # 音量图标
        self.volume_slider.setFixedWidth(150)  # 调整滑块宽度
        volume_layout.addWidget(self.volume_slider)
        right_layout.addLayout(volume_layout)
        right_btn_group = QHBoxLayout()
        # 设置按钮
        self.settings_btn = self.create_styled_button(self.tr("Advanced Settings"), "settings", "control")
        # 帮助按钮
        self.help_btn = self.create_styled_button(self.tr("Help"), "help", "control")
        right_btn_group.addWidget(self.settings_btn)
        right_btn_group.addWidget(self.help_btn)
        right_layout.addLayout(right_btn_group)
        right_group.setLayout(right_layout)
        # 添加分组到主面板
        self.control_layout.addWidget(left_group)
        self.control_layout.addWidget(right_group)
        current_x_pos = self.x_pos
        current_y_pos = self.y_pos
        for midi_note in range(self.start_note, self.end_note + 1):
            note_name = self.midi_to_note(midi_note)
            if midi_note == 65:
                current_x_pos = self.x_pos
            if midi_note >= 65:
                current_y_pos = 0
            if '#' not in note_name:
                item = PianoKeyItem(note=note_name, volume=self.global_volume, file_format=self.file_format,
                                    is_black=False)
                self.scene.addItem(item)
                item.set_geometry(QRectF(current_x_pos, current_y_pos, self.white_width, self.white_height))
                self.white_items.append(item)
                self.white_keys_data.append({
                    'midi': midi_note,
                    'x_start': current_x_pos,
                    'x_end': current_x_pos + self.white_width / 2,
                    'y_pos': current_y_pos
                })
                current_x_pos += self.white_width / 2

        # 生成黑键（基于音高关系）
        for i in range(len(self.white_keys_data) - 1):
            current = self.white_keys_data[i]
            next_ = self.white_keys_data[i + 1]

            # 检查是否需要插入黑键
            if next_['midi'] - current['midi'] == 2:
                black_midi = current['midi'] + 1
                black_note = self.midi_to_note(black_midi)

                # 计算黑键位置（位于两个白键之间的1/4处）
                x_center = current['x_start'] + (next_['x_start'] - current['x_start']) * self.position_ratio

                item = PianoKeyItem(note=black_note, volume=self.global_volume, file_format=self.file_format,
                                    is_black=True)
                self.scene.addItem(item)
                balck_x_start = x_center + 7
                item.set_geometry(QRectF(
                    balck_x_start,  # 22px宽度居中
                    current['y_pos'],
                    self.black_width,
                    self.black_hight
                ))
                self.black_items.append(item)
                self.black_keys_data.append({
                    'white_pair': (i, i + 1),
                    'position_ratio': self.position_ratio,
                    'midi': black_midi,
                    'x_start': balck_x_start,
                    'x_end': balck_x_start + self.black_width / 2,
                    'y_pos': current['y_pos']
                })
        self.scene.setSceneRect(0, 0, 900, 330)

        # 主布局
        self.main_layout.addLayout(self.control_layout)
        self.main_layout.addWidget(self.view)
        self.setLayout(self.main_layout)

        # 信号连接
        self.record_btn.clicked.connect(self.toggle_recording)
        self.play_btn.clicked.connect(self.play_recording)
        self.settings_btn.clicked.connect(self.show_settings)
        self.signals.midi_note_on.connect(self.handle_midi_note)
        self.signals.midi_note_off.connect(lambda note: self.handle_midi_note(note, 0))
        self.volume_slider.valueChanged.connect(self.update_global_volume)
        self.help_btn.clicked.connect(self.show_help)  # 新增连接

    def update_key_covers(self, octave):
        """更新所有琴键的覆盖层状态"""
        for item in self.white_items + self.black_items:
            item.update_cover(octave)

    def update_global_volume(self, value):
        self.global_volume = value / 100
        for item in self.white_items + self.black_items:
            if item.key_widget.sound:
                if isinstance(item.key_widget.sound, QMediaPlayer):
                    item.key_widget.sound.audioOutput().setVolume(self.global_volume)
                else:
                    item.key_widget.sound.setVolume(self.global_volume)

    def preload_audio(self):
        """预加载所有音频到内存"""
        missing_notes = []
        for item in self.white_items + self.black_items:
            item.key_widget.init_sound()
            # 预加载到缓冲区
            if isinstance(item.key_widget.sound, QSoundEffect):
                item.key_widget.sound.play()
                item.key_widget.sound.stop()
            if item.key_widget.sound is None:
                missing_notes.append(item.note)

        if missing_notes:
            print(f"警告：以下音符加载失败：{', '.join(missing_notes)}")

    # 在 PianoWidget 类中新增图标初始化方法
    def init_icons(self):
        # 使用Qt标准图标
        icons = {
            "record": self.style().standardIcon(QStyle.StandardPixmap.SP_DriveCDIcon),
            "play": self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            "settings": self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "volume": self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume),
            "help": self.style().standardIcon(QStyle.StandardPixmap.SP_DialogHelpButton)
        }
        return icons

    def create_styled_button(self, text, icon_name, button_type="default"):
        """创建 macOS 风格按钮"""
        # 优化后的 macOS 动态立体按钮配色
        colors = {
            "default": {  # 中性操作（返回/取消）
                "normal": "#F5F5F7",  # 浅灰基底
                "hover": "#E5E5EA",  # 加深 6% + 添加 1px 边框
                "pressed": "#D1D1D6",  # 加深 12% + 内阴影效果
                "text": "#000000",  # 保持高对比度
                "border": "#E0E0E0"  # 新增边框色
            },
            "primary": {  # 核心操作（播放/确认）
                "normal": "#0A84FF",  # 调整蓝色饱和度(+8%)
                "hover": "#007AFF",  # 苹果官方动态蓝
                "pressed": "#0040DD",  # 加深 20% 模拟按压景深
                "text": "#FFFFFF",  # 保持纯净白
                "glow": "rgba(10,132,255,0.2)"  # 新增悬停光晕
            },
            "destructive": {  # 危险操作（删除/重置）
                "normal": "#FF453A",  # 系统标准警告红
                "hover": "#FF3B30",  # 提高亮度 5%
                "pressed": "#E93B34",  # 加深 + 添加红色投影
                "text": "#FFFFFF",
                "shadow": "0 2px 4px rgba(255,69,58,0.3)"  # 按压动态投影
            },
            "control": {  # 工具类按钮（设置/帮助）
                "normal": "#FFFFFF",  # 纯白基底
                "hover": "#F5F5F7",  # 微灰过渡
                "pressed": "#E5E5EA",  # 加深 8%
                "text": "#000000",
                "border": "#D8D8D8",  # 新增亚克力质感边框
                "separator": "#ECECEC"  # 按钮间隔线
            }
        }

        btn = QPushButton(text)
        btn.setIcon(self.icons[icon_name])
        btn.setIconSize(QSize(14, 14))

        # 根据按钮类型选择配色
        style = colors.get(button_type, colors["default"])

        # macOS 风格样式表
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {style['normal']};
                color: {style['text']};
                border: 1px solid #C6C6C8;
                border-radius: 6px;
                padding: 5px 12px;
                min-width: 80px;
                font: 13px 'SF Pro Text';
                spacing: 6px;
            }}
            QPushButton:hover {{
                background-color: {style['hover']};
                border-color: #AEAEB2;
            }}
            QPushButton:pressed {{
                background-color: {style['pressed']};
                border-color: #8E8E93;
            }}
            QPushButton:disabled {{
                background-color: #F5F5F7;
                color: #8E8E93;
            }}
        """)

        # 添加平滑过渡动画
        animation = QPropertyAnimation(btn, b"geometry")
        animation.setDuration(100)
        btn.pressed.connect(lambda: animation.start())

        return btn

    def midi_to_note(self, midi_number):
        octave = (midi_number // 12) - 1
        note_index = midi_number % 12
        return f"{self.notes[note_index]}{octave}"

    def load_keymap(self, keymap):
        try:
            with open(keymap) as f:
                return json.load(f)
        except FileNotFoundError:
            return self.create_default_keymap()
        except Exception as e:
            print(f"加载键位配置失败: {e}")
            return self.create_default_keymap()

    @staticmethod
    def load_config():
        config_path = 'config.json'
        default_config = {
            'volume': 0.8,
            'file_format': 'flac',
            'keymap': 'keymap.json'
        }
        try:
            with open(config_path) as f:
                return json.load(f)
        except FileNotFoundError:
            return default_config

    def save_config(self):
        config = {
            'volume': self.global_volume,
            'file_format': self.file_format,
            'keymap': 'keymap.json'
        }
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)

    def create_default_keymap(self):
        key_map = {}
        for midi_note in range(21, 109):  # MIDI音符范围从21到108
            note_name = self.midi_to_note(midi_note)
            octave = (midi_note // 12) - 1  # 计算音程
            base_note = note_name[:-1]  # 去掉音程部分（如 C4 -> C）
            if '#' in base_note:
                # 黑键：Shift + 基础音符 + 音程
                key_map[note_name] = f"Shift+{base_note[0]}{octave}"
            else:
                # 白键：直接对应音符 + 音程
                key_map[note_name] = f"{base_note}{octave}"
        return key_map

    def save_keymap(self, new_map):
        with open('keymap.json', 'w') as f:
            json.dump(new_map, f, indent=2)
        self.key_map = new_map

    # 优化内存管理：添加资源清理方法
    def cleanup(self):
        """清理音频资源"""
        for item in self.white_items + self.black_items:
            if item.key_widget.sound:
                item.key_widget.sound.stop()
                item.key_widget.sound.deleteLater()
        self.scene.clear()

    def closeEvent(self, event):
        self.cleanup()
        event.accept()

    def adjust_layout(self):
        # 在布局调整时添加琴键间隙
        key_spacing = 1  # 白键间距

        for idx, white in enumerate(self.white_items):
            rect = QRectF(self.x_pos + (idx * key_spacing),
                          self.y_pos,
                          self.white_width - key_spacing,
                          self.white_height)# 添加间隙# 调整宽度
            white.set_geometry(rect)
        # 黑键位置微调
        for black in self.black_items:
            rect = black.geometry().adjusted(-1, 0, 1, 0)  # 横向扩展1px
            black.set_geometry(rect)

        view_width = self.view.width()
        white_count = len(self.white_items)
        if white_count == 0:
            return

        # 计算白键宽度（最小34px）
        white_width = max(view_width / white_count, self.white_width)
        x_pos = 0

        # 更新白键布局并记录新位置
        for idx, white in enumerate(self.white_items):
            white.set_geometry(
                QRectF(self.white_keys_data[idx]['x_start'], self.white_keys_data[idx]['y_pos'], white_width, self.white_height))
            self.white_keys_data[idx]['x_start'] = x_pos
            self.white_keys_data[idx]['x_end'] = x_pos + white_width / 2
            x_pos += white_width

        # 更新黑键位置
        for idx, black in enumerate(self.black_items):
            if idx >= len(self.black_keys_data):
                continue

            info = self.black_keys_data[idx]
            i1, i2 = info['white_pair']

            try:
                current = self.white_keys_data[i1]
                next_ = self.white_keys_data[i2]
            except IndexError:
                continue

            # 动态计算位置
            x_center = current['x_start'] + (next_['x_start'] - current['x_start']) * info['position_ratio']
            black.set_geometry(QRectF(
                x_center + 7,
                current['y_pos'],
                self.black_width,
                self.black_hight
            ))
            self.black_keys_data[idx]['x_start'] = x_center + 7

        self.scene.setSceneRect(0, 0, 900, 330)

    def note_to_midi(self, note_name: str) -> int:
        """将音符名称转换为MIDI编号"""
        try:
            base = note_name[:-1]
            octave = int(note_name[-1])
            return (octave + 1) * 12 + self.notes.index(base)
        except:
            return 0  # 默认值

    def init_midi(self):
        try:
            self.midi_in = rtmidi.MidiIn()
            ports = self.midi_in.get_ports()
            if ports:
                self.midi_in.open_port(0)
                self.midi_in.set_callback(self.midi_callback)
                print(f"已连接MIDI设备: {ports[0]}")
            else:
                print("未找到可用MIDI设备")
        except Exception as e:
            print(f"MIDI初始化失败: {e}")

    def midi_callback(self, event, data=None):
        with self.midi_lock:
            message, _ = event
            if message[0] == 0x90:  # Note On
                self.signals.midi_note_on.emit(message[1], message[2])
            elif message[0] == 0x80:  # Note Off
                self.signals.midi_note_off.emit(message[1])

    def handle_midi_note(self, note, velocity):
        note_name = self.midi_to_note(note)
        for item in self.white_items + self.black_items:
            if item.note == note_name:
                if velocity > 0:
                    item.press()
                else:
                    item.release()
                if self.recording:
                    self.record_data.append({
                        'time': time.monotonic() - self.record_start,  # 修复：使用 monotonic
                        'type': 'on' if velocity > 0 else 'off',
                        'note': note_name
                    })
                break

    def toggle_recording(self):
        self.recording = not self.recording
        self.record_btn.setText(self.tr("Stop Recording") if self.recording else self.tr("Start Recording"))
        if self.recording:
            self.record_data = []
            self.record_start = time.time()
            print("录音开始...")
        else:
            print(f"录音结束，共记录{len(self.record_data)}个事件")

    def play_recording(self):
        if not self.record_data:
            return

        def playback():
            start_time = self.record_data[0]['time']
            for event in self.record_data:
                elapsed = event['time'] - start_time
                time.sleep(max(0, elapsed))
                for item in self.white_items + self.black_items:
                    if item.note == event['note']:
                        if event['type'] == 'on':
                            item.press()
                        else:
                            item.release()
                        break
                start_time = event['time']

        Thread(target=playback).start()

    def show_settings(self):
        dialog = SettingsDialog(
            self.key_map,
            self.global_volume,
            self.file_format,
            self
        )
        if dialog.exec():
            # 保存键位映射
            new_map = {note: entry.text().upper() for note, entry in dialog.entries.items()}
            self.save_keymap(new_map)

            # 保存全局设置
            self.global_volume = dialog.volume_slider.value() / 100
            self.file_format = dialog.format_combo.currentText()
            self.save_config()

            # 应用新设置
            self.update_global_volume(self.global_volume * 100)
            self.reload_audio_format()

    def reload_audio_format(self):
        """重新加载音频文件格式"""
        for item in self.white_items + self.black_items:
            item.key_widget.file_format = self.file_format
            item.key_widget.init_sound()

    def keyPressEvent(self, event: QKeyEvent):
        # 忽略自动重复事件
        if event.isAutoRepeat():
            return
        key = event.text().upper()
        is_shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier

        # 检测数字键-音程
        event_key = event.key()
        if Qt.Key.Key_0 <= event_key <= Qt.Key.Key_7:
            new_octave = event_key - Qt.Key.Key_0
            if new_octave != self.current_octave:
                self.current_octave = new_octave

            self.octave_label.setText(f"{self.current_octave}")
            self.octave_changed.emit(new_octave)  # 触发更新信号
            return

        if is_shift:
            key = f"Shift+{key}"

        # 组合音符和音程
        note_with_octave = f"{key}{self.current_octave}"
        # print(f'组合键：{note_with_octave}')
        if note_with_octave in self.key_map.values():

            note = next(k for k, v in self.key_map.items() if v == note_with_octave)
            for item in self.white_items + self.black_items:
                if item.note == note:
                    item.press()
                    if self.recording:
                        self.record_data.append({'time': time.time() - self.record_start, 'type': 'on', 'note': note})
                        break

    def keyReleaseEvent(self, event: QKeyEvent):
        # 忽略自动重复事件
        if event.isAutoRepeat():
            return
        key = event.text().upper()
        is_shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier

        if is_shift:
            key = f"Shift+{key}"

        # 组合音符和音程
        note_with_octave = f'{key}{self.current_octave}'
        if note_with_octave in self.key_map.values():
            note = next(k for k, v in self.key_map.items() if v == note_with_octave)
            for item in self.white_items + self.black_items:
                if item.note == note:
                    item.release()
                    if self.recording:
                        self.record_data.append({'time': time.time() - self.record_start, 'type': 'off', 'note': note})
                        break

    def note_to_index(self, note_name: str) -> int:
        """精确转换音符名称到索引"""
        # 分离基础音符（处理类似C#4的情况）
        base_note = note_name[:-1].replace('#', '')
        modifier = '#' if '#' in note_name else ''

        try:
            return self.notes.index(f"{base_note}{modifier}")
        except ValueError:
            print(f"无效的音符名称: {note_name}")
            return 0

    def load_markdown(self, md_path):
        """加载并转换指定Markdown文件"""
        try:
            base_dir = os.path.dirname(__file__)
            full_path = os.path.join(base_dir, "help", md_path)

            with open(full_path, "r", encoding="utf-8") as f:
                md_content = f.read()

            # 转换时保留原始链接结构
            html = markdown.markdown(md_content, extensions=['extra'])
            return self._apply_custom_styles(html)

        except Exception as e:
            return f"<p style='color:red'>无法加载文档: {str(e)}</p >"

    def _apply_custom_styles(self, html):
        """添加链接样式和交互支持"""
        return f"""
        <html>
        <head>
            <style>
                a {{
                    color: #3498db;
                    text-decoration: none;
                    border-bottom: 1px dotted #3498db;
                }}
                a:hover {{
                    color: #2980b9;
                    border-bottom-style: solid;
                }}
            </style>
        </head>
        <body>
            {html}
        </body>
        </html>
        """

    def init_help_dialog(self):
        self.help_dialog = QDialog(self)
        self.help_dialog.setWindowTitle(self.tr("Help System"))
        self.help_dialog.resize(900, 600)

        # 创建带历史记录的浏览器组件
        self.help_view = QTextBrowser()
        self.help_view.setOpenLinks(False)  # 禁用默认链接处理
        self.help_view.anchorClicked.connect(self.handle_link_click)

        # 加载初始页面
        self.help_view.setHtml(self.load_markdown(self.current_path))

        # 添加导航按钮
        btn_back = QPushButton(self.tr("Back"))
        btn_back.clicked.connect(self.navigate_back)

        # 布局设置
        layout = QVBoxLayout()
        layout.addWidget(self.help_view)

        nav_layout = QHBoxLayout()
        nav_layout.addWidget(btn_back)
        nav_layout.addStretch()

        layout.addLayout(nav_layout)
        self.help_dialog.setLayout(layout)

    def handle_link_click(self, link):
        """处理Markdown内部链接"""
        if link.scheme() == "file" or not link.scheme():
            # 处理相对路径链接
            new_path = os.path.join(os.path.dirname(self.current_path), link.path())
            if new_path.endswith(".md"):
                self.current_path = new_path
                self.help_view.setHtml(self.load_markdown(new_path))

    def navigate_back(self):
        """返回上一级页面"""
        if self.current_path != "help.md":
            self.current_path = "help.md"
            self.help_view.setHtml(self.load_markdown(self.current_path))

    def show_help(self):
        """显示帮助窗口"""
        self.help_dialog.exec()


class SettingsDialog(QDialog):
    def __init__(self, key_map, current_volume, current_format, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
                    QDialog {
                        background-color: #f5f5f7;
                        font-family: -apple-system, BlinkMacSystemFont;
                    }
                    QLabel {
                        color: #1f1f1f;
                        font-size: 13px;
                    }
                    QLineEdit {
                        background: #ffffff;
                        border: 1px solid #c8c7cc;
                        border-radius: 4px;
                        padding: 6px 8px;
                        font-size: 13px;
                        selection-background-color: #007AFF;
                    }
                    QLineEdit:focus {
                        border-color: #007AFF;
                    }
                    QSlider::groove:horizontal {
                        height: 4px;
                        background: #e0e0e0;
                        border-radius: 2px;
                    }
                    QSlider::sub-page:horizontal {
                        background: #007AFF;
                        border-radius: 2px;
                    }
                    QSlider::handle:horizontal {
                        background: #ffffff;
                        border: 1px solid #c8c7cc;
                        width: 16px;
                        height: 16px;
                        margin: -6px 0;
                        border-radius: 8px;
                    }
                    QComboBox {
                        background: #ffffff;
                        border: 1px solid #c8c7cc;
                        border-radius: 4px;
                        padding: 6px 20px 6px 8px;
                        font-size: 13px;
                    }
                    QComboBox::drop-down {
                        width: 20px;
                        border: none;
                    }
                    QComboBox::down-arrow {
                        image: url(help/fonts/arrow-down.svg);
                        width: 12px;
                        height: 12px;
                    }
                    QPushButton {
                        background-color: #007AFF;
                        color: white;
                        border: none;
                        border-radius: 6px;
                        padding: 6px 12px;
                        font-size: 13px;
                        min-width: 80px;
                    }
                    QPushButton:hover {
                        background-color: #0063CC;
                    }
                    QPushButton:pressed {
                        background-color: #004999;
                    }
                    QPushButton#cancel {
                        background-color: #e0e0e0;
                        color: #1f1f1f;
                    }
                    QPushButton#cancel:hover {
                        background-color: #d0d0d0;
                    }
                    QPushButton#cancel:pressed {
                        background-color: #c0c0c0;
                    }
                """)
        # 存储动画对象的字典
        self.animations = {}
        self.key_map = key_map
        self.current_volume = current_volume
        self.current_format = current_format
        self.entries = {}
        self.volume_slider = None
        self.format_combo = None
        self.btn_box = None
        self.btn_save = None
        self.btn_cancel = None
        self.scroll = None
        self.content = None
        self.layout = None
        self.main_layout = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(self.tr("Settings"))
        self.setMinimumSize(500, 400)
        # 添加macOS风格特性
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, False)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.layout = QGridLayout(self.content)
        # 调整布局间距
        self.layout.setVerticalSpacing(12)
        self.layout.setHorizontalSpacing(20)
        self.layout.setContentsMargins(20, 15, 20, 15)

        # 键位映射设置
        row = 0
        self.layout.addWidget(QLabel(self.tr("Keyboard Remapping：")), row, 0, 1, 2)
        row += 1
        for note, key in self.key_map.items():
            label = QLabel(note)
            entry = QLineEdit(key)
            entry.setMaxLength(20)
            self.entries[note] = entry
            self.layout.addWidget(label, row, 0)
            self.layout.addWidget(entry, row, 1)
            row += 1

        # 全局音量控制
        self.layout.addWidget(QLabel(self.tr("Global Volume Control：")), row, 0)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self.current_volume * 100))
        self.layout.addWidget(self.volume_slider, row, 1)
        row += 1

        # 音频格式选择
        self.layout.addWidget(QLabel(self.tr("Audio Format：")), row, 0)
        self.format_combo = QComboBox()
        self.format_combo.addItems(['wav', 'flac', 'mp3', 'ogg', 'm4a'])
        self.format_combo.setCurrentText(self.current_format)
        self.layout.addWidget(self.format_combo, row, 1)
        row += 1

        # 按钮面板
        self.btn_box = QHBoxLayout()
        self.btn_save = QPushButton(self.tr("Save"))
        self.btn_cancel = QPushButton(self.tr("Abort"))
        # 修改按钮对象名称以应用特殊样式
        self.btn_save.setObjectName("save")
        self.btn_cancel.setObjectName("cancel")

        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_box.addWidget(self.btn_save)
        self.btn_box.addWidget(self.btn_cancel)
        self.layout.addLayout(self.btn_box, row, 0, 1, 2)
        self.scroll.setWidget(self.content)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.addWidget(self.scroll)
        # 添加动画效果
        for note, entry in self.entries.items():
            # 安装事件过滤器
            entry.installEventFilter(self)
            anim = QPropertyAnimation(entry, b"geometry")
            anim.setDuration(100)
            self.animations[entry] = anim

        # 设置滚动区域样式
        self.scroll.setStyleSheet("""
            QScrollArea { border: none; }
            QScrollBar:vertical {
                background: #f0f0f0;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: #c0c0c0;
                border-radius: 4px;
                min-height: 20px;
            }
             /* ... 其他样式 ... */
            QLineEdit:focus {
                border-color: #007AFF;
                background-color: #ffffff;
            }
        """)

    def eventFilter(self, obj, event):
        """事件过滤器处理焦点进入事件"""
        if event.type() == QEvent.Type.FocusIn:
            if obj in self.animations:
                anim = self.animations[obj]
                # 设置动画参数（示例：轻微下移效果）
                anim.setStartValue(obj.geometry())
                anim.setEndValue(obj.geometry().translated(0, 2))
                anim.start()
        return super().eventFilter(obj, event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    # 加载翻译
    translator = QTranslator()
    if translator.load("translations/zh_CN.qm"):
        app.installTranslator(translator)

    # 检查声音目录
    if not os.path.exists("sounds"):
        print("错误：缺少声音目录'sounds'")
        sys.exit(1)

    piano = PianoWidget()
    piano.show()
    sys.exit(app.exec())
