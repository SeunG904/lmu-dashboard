"""
graph_widgets.py

- TelemetryGraphPanel: 속도(숫자) + 페달(스로틀/브레이크/클러치, 최근10초 스크롤 그래프)
  + 스티어링(회전하는 휠 아이콘)
- DeltaBar: 베스트랩 대비 델타를 중앙 기준 좌우로 표시하는 바
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

GRAPH_WINDOW_SECONDS = 10.0


class _LineGraphCanvas(QWidget):
    """series: list of (label, color_hex, index_in_sample) / sample = (t, speed, throttle, brake, clutch, steering)"""

    def __init__(self, series: list[tuple[str, str, int]], y_min: float, y_max: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series = series
        self._y_min = y_min
        self._y_max = y_max
        self._samples: list[tuple[float, ...]] = []
        self.setMinimumHeight(90)

    def set_samples(self, samples: list[tuple[float, ...]]) -> None:
        self._samples = samples
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1a1a1a"))

        w, h = self.width(), self.height()
        painter.setPen(QColor("#424242"))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = h * frac
            painter.drawLine(QPointF(0, y), QPointF(w, y))

        if len(self._samples) < 2:
            return

        def to_x(t: float) -> float:
            return w * (1.0 + t / GRAPH_WINDOW_SECONDS)

        def to_y(value: float) -> float:
            ratio = (value - self._y_min) / (self._y_max - self._y_min or 1.0)
            ratio = max(0.0, min(1.0, ratio))
            return h * (1.0 - ratio)

        for _, color_hex, field_idx in self._series:
            pen = QPen(QColor(color_hex), 2)
            painter.setPen(pen)
            points = [QPointF(to_x(s[0]), to_y(s[field_idx])) for s in self._samples]
            for i in range(len(points) - 1):
                painter.drawLine(points[i], points[i + 1])


class SpeedNumberWidget(QWidget):
    """속도를 큰 숫자로 표시"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.value_label = QLabel("0")
        font = QFont()
        font.setPointSize(40)
        font.setBold(True)
        self.value_label.setFont(font)
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet("color: #29b6f6;")

        unit_label = QLabel("km/h")
        unit_label.setAlignment(Qt.AlignCenter)
        unit_label.setStyleSheet("color: #9e9e9e;")

        layout.addWidget(self.value_label)
        layout.addWidget(unit_label)

    def set_speed(self, speed_kmh: float) -> None:
        self.value_label.setText(f"{speed_kmh:.0f}")


FIXED_WHEEL_RANGE_DEG = 900.0  # 게임 값이 없을 때의 기본값 (TinyPedal도 게임의 실제 값을 그대로 사용)


class SteeringWheelWidget(QWidget):
    """포뮬러(F1) 스타일 스티어링 휠 - 현재 각도만큼 회전.
    각도 계산은 TinyPedal과 동일하게 게임이 주는 실제 mPhysicalSteeringWheelRange를 사용한다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle_deg = 0.0
        self.setMinimumSize(110, 100)

    def set_steering(self, steering_frac: float, wheel_range_deg: float = FIXED_WHEEL_RANGE_DEG) -> None:
        # steering_frac: -1.0(좌) ~ +1.0(우), wheel_range_deg: 게임이 보고하는 락투락 전체 각도
        self._angle_deg = steering_frac * (wheel_range_deg / 2.0)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2 - 6

        painter.translate(cx, cy)
        painter.rotate(self._angle_deg)

        rim_color = QColor("#e0e0e0")
        pen = QPen(rim_color, 6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # 몸체: 가로로 넓적한 라운드 사각형 (F1 휠 실루엣)
        body_w, body_h = 64, 34
        painter.drawRoundedRect(int(-body_w / 2), int(-body_h / 2), body_w, body_h, 14, 14)

        # 좌우 그립(핸들) 연장부
        for side in (-1, 1):
            x0 = side * (body_w / 2 - 4)
            painter.drawLine(QPointF(x0, -body_h / 2 + 4), QPointF(x0 + side * 10, -body_h / 2 - 6))
            painter.drawLine(QPointF(x0, body_h / 2 - 4), QPointF(x0 + side * 10, body_h / 2 + 6))

        # 중앙 디스플레이(MFD) 스크린
        screen_w, screen_h = 26, 14
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#101010"))
        painter.drawRoundedRect(int(-screen_w / 2), int(-screen_h / 2), screen_w, screen_h, 2, 2)
        painter.setBrush(QColor("#43a047"))
        painter.drawRect(int(-screen_w / 2 + 2), int(-screen_h / 2 + 2), int(screen_w - 4), 3)

        painter.resetTransform()
        painter.setPen(QColor("#9e9e9e"))
        painter.drawText(self.rect().adjusted(0, h - 18, 0, 0), Qt.AlignCenter, f"{self._angle_deg:.0f}°")


class TelemetryGraphPanel(QGroupBox):
    """속도(숫자) + 페달(스로틀·브레이크·클러치, 최근10초) + 스티어링(휠 아이콘)"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("TELEMETRY", parent)
        outer = QHBoxLayout(self)

        self.speed_widget = SpeedNumberWidget()
        outer.addWidget(self.speed_widget, stretch=1)

        pedal_col = QVBoxLayout()
        self.pedal_canvas = _LineGraphCanvas(
            [("throttle", "#43a047", 2), ("brake", "#e53935", 3)],
            y_min=0.0,
            y_max=1.0,
        )
        pedal_col.addWidget(self.pedal_canvas)
        outer.addLayout(pedal_col, stretch=3)

        self.steering_widget = SteeringWheelWidget()
        outer.addWidget(self.steering_widget, stretch=1)

    def set_samples(self, samples: list[tuple[float, ...]]) -> None:
        self.pedal_canvas.set_samples(samples)

    def set_live(self, speed_kmh: float, steering_frac: float, wheel_range_deg: float) -> None:
        self.speed_widget.set_speed(speed_kmh)
        self.steering_widget.set_steering(steering_frac, wheel_range_deg)


class DeltaBar(QWidget):
    """베스트랩 대비 델타를 중앙 기준 좌우로 표시 (초록=빠름/왼쪽, 빨강=느림/오른쪽)"""

    MAX_DELTA_S = 2.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._delta: float | None = None
        self.setMinimumSize(160, 22)

    def set_delta(self, delta_s: float | None) -> None:
        self._delta = delta_s
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#1a1a1a"))

        center = w / 2
        painter.setPen(QColor("#616161"))
        painter.drawLine(int(center), 0, int(center), h)

        if self._delta is None:
            return

        ratio = max(-1.0, min(1.0, self._delta / self.MAX_DELTA_S))
        bar_color = QColor("#e53935") if ratio > 0 else QColor("#43a047")
        bar_width = abs(ratio) * center
        x_start = center if ratio > 0 else center - bar_width
        painter.fillRect(int(x_start), 2, int(bar_width), h - 4, bar_color)

        painter.setPen(QColor("#ffffff"))
        sign = "+" if self._delta >= 0 else ""
        painter.drawText(self.rect(), Qt.AlignCenter, f"{sign}{self._delta:.3f}")
