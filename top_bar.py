"""
top_bar.py

TopBar: P{순위} | LAP {n} | LAST LAP | BEST LAP | CURR LAP | 델타바 | S1 S2 S3
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from graph_widgets import DeltaBar
from telemetry_reader import format_laptime, format_sector


def _stat_block(title: str, point_size: int = 16, color: str = "#ffffff") -> tuple[QWidget, QLabel]:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(4, 2, 4, 2)
    title_label = QLabel(title)
    title_label.setStyleSheet("color: #9e9e9e; font-size: 10px;")
    value_label = QLabel("--")
    font = value_label.font()
    font.setPointSize(point_size)
    font.setBold(True)
    value_label.setFont(font)
    value_label.setStyleSheet(f"color: {color};")
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    return container, value_label


class TopBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        pos_box, self.pos_value = _stat_block("POS")
        lap_box, self.lap_value = _stat_block("LAP")
        last_box, self.last_value = _stat_block("LAST LAP")
        best_box, self.best_value = _stat_block("BEST LAP", color="#ce93d8")
        curr_box, self.curr_value = _stat_block("CURR LAP", color="#fff59d")

        self.delta_bar = DeltaBar()

        s1_box, self.s1_value = _stat_block("S1", point_size=13)
        s2_box, self.s2_value = _stat_block("S2", point_size=13)
        s3_box, self.s3_value = _stat_block("S3", point_size=13)

        for box in (pos_box, lap_box, last_box, best_box, curr_box):
            layout.addWidget(box)
        layout.addWidget(self.delta_bar, stretch=1)
        for box in (s1_box, s2_box, s3_box):
            layout.addWidget(box)

    def update_data(self, data: dict) -> None:
        player = data.get("player") or {}
        place = player.get("place")
        total = player.get("num_vehicles")
        self.pos_value.setText(f"{place}/{total}" if place and total else "--")
        self.lap_value.setText(str(player.get("lap_number", "--")))
        self.last_value.setText(format_laptime(player.get("last_lap_s")))
        self.best_value.setText(format_laptime(player.get("best_lap_s")))
        # 현재 랩 타임은 main.py의 60Hz 보간 타이머가 set_current_laptime_text()로 직접 갱신

        sectors = player.get("sectors") or {}
        last_s1, last_s2, last_s3 = sectors.get("last", (None, None, None))
        self.s1_value.setText(format_sector(last_s1))
        self.s2_value.setText(format_sector(last_s2))
        self.s3_value.setText(format_sector(last_s3))

        self.delta_bar.set_delta(player.get("delta_best_s"))

    def set_current_laptime_text(self, text: str) -> None:
        self.curr_value.setText(text)
