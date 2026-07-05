"""
main.py

LMU Telemetry Dashboard - 진입점

상단바(순위/랩타임/델타바/섹터) + 스탠딩(압축형) + 트랙맵 + 타이어 +
연료/VE + 온보드정보 + 섹터비교 + 앞뒤격차 + 텔레메트리 그래프,
독립 실행형 대시보드 창.

랩타임(현재 랩)만 60Hz로 로컬 보간해서 부드럽게 흐르도록 처리한다.
"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QGridLayout, QMainWindow, QSizePolicy, QWidget


class AspectRatioWidget(QWidget):
    """자식 위젯을 항상 16:9 비율로 유지시키는 래퍼.
    창을 넓게/좁게 늘려도 내부 콘텐츠는 16:9로 고정되고, 남는 공간은
    레터박스(검은 여백)로 채워져서 일부 패널만 과도하게 늘어나는 것을 막는다."""

    def __init__(self, content: QWidget, aspect_w: int = 16, aspect_h: int = 9, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aspect_w = aspect_w
        self._aspect_h = aspect_h
        self._content = content
        self._content.setParent(self)
        self.setStyleSheet("background-color: #000000;")

    def resizeEvent(self, event) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        target_ratio = self._aspect_w / self._aspect_h
        if h == 0:
            return
        current_ratio = w / h
        if current_ratio > target_ratio:
            new_h = h
            new_w = int(h * target_ratio)
        else:
            new_w = w
            new_h = int(w / target_ratio)
        x = (w - new_w) // 2
        y = (h - new_h) // 2
        self._content.setGeometry(x, y, new_w, new_h)
        super().resizeEvent(event)

from dashboard_widgets import (
    FuelVEPanel,
    GapPanel,
    OnboardPanel,
    SectorPanel,
    StandingsPanel,
    TimingPanel,
    TirePanel,
    TrackMapWidget,
)
from graph_widgets import TelemetryGraphPanel, GRAPH_WINDOW_SECONDS
from telemetry_reader import TelemetryReader, format_laptime
from top_bar import TopBar

DATA_INTERVAL_MS = 50  # 20Hz: 공유 메모리 폴링 + 대부분의 패널 갱신
SMOOTH_INTERVAL_MS = 16  # ~60Hz: 현재 랩타임을 0.001초 단위로 실시간 보간 갱신


class DashboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LMU Telemetry Dashboard")
        self.resize(1300, 820)

        self.reader = TelemetryReader()
        self.reader.open()

        # 60Hz 보간용 상태
        self._base_current_lap_time: float | None = None
        self._base_captured_at: float | None = None
        self._connected = False

        central = QWidget()
        aspect_wrapper = AspectRatioWidget(central, 16, 9)
        self.setCentralWidget(aspect_wrapper)
        layout = QGridLayout(central)

        self.top_bar = TopBar()
        self.top_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.top_bar, 0, 0, 1, 3)

        self.standings_panel = StandingsPanel()
        self.track_map_panel = TrackMapWidget()
        self.tire_panel = TirePanel()
        self.fuel_ve_panel = FuelVEPanel()
        self.sector_panel = SectorPanel()
        self.gap_panel = GapPanel()
        self.onboard_panel = OnboardPanel()
        self.graph_panel = TelemetryGraphPanel()
        self.timing_panel = TimingPanel()

        layout.addWidget(self.standings_panel, 1, 0, 2, 1)
        layout.addWidget(self.track_map_panel, 1, 1, 2, 1)
        layout.addWidget(self.tire_panel, 1, 2)
        layout.addWidget(self.gap_panel, 2, 2)

        layout.addWidget(self.onboard_panel, 3, 0)
        layout.addWidget(self.fuel_ve_panel, 3, 1)
        layout.addWidget(self.sector_panel, 3, 2)

        layout.addWidget(self.timing_panel, 4, 0)
        layout.addWidget(self.graph_panel, 4, 1, 1, 2)

        layout.setColumnStretch(0, 4)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)

        # 상단바 줄은 늘어나지 않게 고정하고, 나머지 줄에만 여유 공간을 배분
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 3)
        layout.setRowStretch(2, 3)
        layout.setRowStretch(3, 3)
        layout.setRowStretch(4, 2)

        self.data_timer = QTimer(self)
        self.data_timer.timeout.connect(self._data_tick)
        self.data_timer.start(DATA_INTERVAL_MS)

        self.smooth_timer = QTimer(self)
        self.smooth_timer.timeout.connect(self._smooth_tick)
        self.smooth_timer.start(SMOOTH_INTERVAL_MS)

    def _data_tick(self) -> None:
        self.reader.update()
        data = self.reader.snapshot()

        self._connected = bool(data.get("connected"))
        if not self._connected:
            self.top_bar.pos_value.setText("--")
            return

        player = data.get("player") or {}
        self._base_current_lap_time = player.get("current_lap_time_s")
        self._base_captured_at = time.monotonic()

        self.top_bar.update_data(data)
        self.standings_panel.update_data(data)
        self.track_map_panel.update_data(data)
        self.tire_panel.update_data(data)
        self.fuel_ve_panel.update_data(data)
        self.sector_panel.update_data(data)
        self.gap_panel.update_data(data)
        self.onboard_panel.update_data(data)
        self.timing_panel.update_data(data)
        self.graph_panel.set_samples(self.reader.graph_samples(GRAPH_WINDOW_SECONDS))
        self.graph_panel.set_live(
            speed_kmh=player.get("speed_kmh", 0.0),
            steering_frac=player.get("steering_frac", 0.0),
            wheel_range_deg=player.get("wheel_range_deg", 900.0),  # 게임이 보고하는 실제 값 (TinyPedal과 동일 방식)
        )

    def _smooth_tick(self) -> None:
        if not self._connected or self._base_current_lap_time is None or self._base_captured_at is None:
            return
        elapsed = time.monotonic() - self._base_captured_at
        smoothed = self._base_current_lap_time + elapsed
        text = format_laptime(smoothed)
        self.top_bar.set_current_laptime_text(text)
        self.timing_panel.set_current_laptime_text(text)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.reader.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = DashboardWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
