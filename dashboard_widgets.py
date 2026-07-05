"""
dashboard_widgets.py

패널 목록:
  1) StandingsPanel   - 압축형 순위표 (베스트/라스트/S1S2S3/피트-아웃랩/타이어/연료/격차)
  2) TrackMapWidget    - 트랙맵 (흰 트랙 + 수직 섹터 구분선 + 클래스별 색상 + 내차 보라)
  3) TirePanel         - 타이어 (표면/이너레이어/코어 온도, 잔여율, 전랩 마모량)
  4) FuelVEPanel       - 연료/VE
  5) TimingPanel       - 랩타임 (현재/전랩/최근5평균/베스트)
  6) SectorPanel       - 섹터 타임 (전랩 vs 최근5랩평균 vs 델타)
  7) GapPanel          - 앞/뒤 격차
  8) OnboardPanel      - TC/ABS/브레이크밸런스/모터맵
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from telemetry_reader import PLAYER_MAP_COLOR, format_delta, format_gap, format_laptime, format_sector

TRACK_COLOR = QColor("#ffffff")
SECTOR_LINE_COLOR = QColor("#29b6f6")
YELLOW_FLAG_COLOR = QColor("#fdd835")


def _value_label(text: str = "--", point_size: int = 16, bold: bool = True) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSize(point_size)
    font.setBold(bold)
    label.setFont(font)
    label.setAlignment(Qt.AlignCenter)
    return label


class StandingsPanel(QGroupBox):
    """압축형 순위표: 이름(축약)/라스트/VE잔량/격차/상태(PIT·OUT)/타이어%"""

    COLUMNS = ["POS", "NAME", "LAST", "VE%", "GAP", "STAT", "TIRE%"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("STANDINGS", parent)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        font = self.table.font()
        font.setPointSize(11)
        self.table.setFont(font)
        self.table.verticalHeader().setDefaultSectionSize(30)
        layout.addWidget(self.table)

    def update_data(self, data: dict) -> None:
        vehicles = data.get("vehicles") or []
        self.table.setRowCount(len(vehicles))
        for row, v in enumerate(vehicles):
            name = v.get("driver_name_short") or v["driver_name"] or v["vehicle_name"] or "-"
            ve = v.get("ve_pct")

            values = [
                str(v["place"]),
                name,
                format_laptime(v["last_lap_s"]).replace("--:--.---", "--"),
                f"{ve:.0f}%" if ve is not None else "-",
                format_gap(v["gap_to_leader"]) if v["place"] != 1 else "LEAD",
                v.get("status") or "",
                f"{v['tire_avg_remaining_pct']:.0f}%" if v.get("tire_avg_remaining_pct") is not None else "-",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if v["is_player"]:
                    item.setBackground(QBrush(QColor("#37474f")))
                if col == 5 and text == "PIT":
                    item.setForeground(QBrush(QColor("#fdd835")))
                elif col == 5 and text == "OUT":
                    item.setForeground(QBrush(QColor("#29b6f6")))
                self.table.setItem(row, col, item)


class TrackMapWidget(QGroupBox):
    """트랙맵: 흰색 궤적 + 수직 섹터 구분선 + 로컬 옐로 + 클래스별 색상 위치
    + 트랙명/기온/트랙온도/세션베스트/바람 컴퍼스 오버레이"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("TRACK MAP", parent)
        layout = QVBoxLayout(self)
        self.canvas = _TrackMapCanvas()
        self.canvas.setMinimumSize(220, 220)
        layout.addWidget(self.canvas)

    def update_data(self, data: dict) -> None:
        self.canvas.set_data(
            track_path=data.get("track_path") or {},
            vehicles=data.get("vehicles") or [],
            sector_flags=data.get("sector_flags") or [0, 0, 0],
            track_name=data.get("track_name") or "",
            session_best_s=data.get("session_best_s"),
            weather=data.get("weather") or {},
        )


class _TrackMapCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._track_path: dict[int, tuple[float, float]] = {}
        self._vehicles: list[dict] = []
        self._sector_flags = [0, 0, 0]
        self._track_name = ""
        self._session_best_s: float | None = None
        self._weather: dict = {}

    def set_data(
        self,
        track_path: dict,
        vehicles: list[dict],
        sector_flags: list[int],
        track_name: str = "",
        session_best_s: float | None = None,
        weather: dict | None = None,
    ) -> None:
        self._track_path = track_path
        self._vehicles = vehicles
        self._sector_flags = sector_flags
        self._track_name = track_name
        self._session_best_s = session_best_s
        self._weather = weather or {}
        self.update()

    def _sector_of_bucket(self, bucket: int, total_buckets: int) -> int:
        third = total_buckets / 3.0
        if bucket < third:
            return 0
        if bucket < 2 * third:
            return 1
        return 2

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#212121"))

        self._draw_overlays(painter)

        if len(self._track_path) < 10:
            painter.setPen(QColor("#9e9e9e"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Drive one lap to draw\nthe track map")
            return

        xs = [p[0] for p in self._track_path.values()]
        zs = [p[1] for p in self._track_path.values()]
        min_x, max_x = min(xs), max(xs)
        min_z, max_z = min(zs), max(zs)
        span_x = max(max_x - min_x, 1.0)
        span_z = max(max_z - min_z, 1.0)

        margin = 32
        w = self.width() - 2 * margin
        h = self.height() - 2 * margin
        scale = min(w / span_x, h / span_z)

        def to_screen(x: float, z: float) -> QPointF:
            return QPointF(margin + (x - min_x) * scale, margin + (z - min_z) * scale)

        sorted_buckets = sorted(self._track_path.keys())
        total_buckets = max(sorted_buckets) + 1 if sorted_buckets else 1

        # 트랙 경로: 기본 흰색, 로컬 옐로 구간만 색 변경
        for i in range(len(sorted_buckets)):
            b1 = sorted_buckets[i]
            b2 = sorted_buckets[(i + 1) % len(sorted_buckets)]
            p1 = to_screen(*self._track_path[b1])
            p2 = to_screen(*self._track_path[b2])
            sector = self._sector_of_bucket(b1, total_buckets)
            is_yellow = sector < len(self._sector_flags) and self._sector_flags[sector]
            pen = QPen(YELLOW_FLAG_COLOR if is_yellow else TRACK_COLOR, 4)
            painter.setPen(pen)
            painter.drawLine(p1, p2)

        # 섹터 구분선: 트랙 진행 방향에 수직인 짧은 막대(tick)
        painter.setPen(QPen(SECTOR_LINE_COLOR, 3))
        tick_half_len = 10
        for label, boundary_bucket in (("S/F", 0), ("S2", total_buckets // 3), ("S3", (2 * total_buckets) // 3)):
            nearest = min(self._track_path.keys(), key=lambda b: abs(b - boundary_bucket))
            prev_b = sorted_buckets[(sorted_buckets.index(nearest) - 1) % len(sorted_buckets)]
            next_b = sorted_buckets[(sorted_buckets.index(nearest) + 1) % len(sorted_buckets)]
            p_prev = to_screen(*self._track_path[prev_b])
            p_next = to_screen(*self._track_path[next_b])
            p_here = to_screen(*self._track_path[nearest])

            # 진행 방향 벡터 -> 수직 벡터로 회전
            dx = p_next.x() - p_prev.x()
            dy = p_next.y() - p_prev.y()
            length = math.hypot(dx, dy) or 1.0
            perp_x = -dy / length
            perp_y = dx / length

            p_a = QPointF(p_here.x() + perp_x * tick_half_len, p_here.y() + perp_y * tick_half_len)
            p_b = QPointF(p_here.x() - perp_x * tick_half_len, p_here.y() - perp_y * tick_half_len)
            painter.drawLine(p_a, p_b)
            painter.drawText(p_here + QPointF(perp_x * (tick_half_len + 10), perp_y * (tick_half_len + 10)), label)

        # 차량 위치 (클래스별 색상, 내 차는 보라)
        for v in self._vehicles:
            frac = v["lap_dist_fraction"]
            bucket = int(frac * total_buckets) % total_buckets
            nearest = min(
                self._track_path.keys(),
                key=lambda b: min(abs(b - bucket), total_buckets - abs(b - bucket)),
            )
            point = to_screen(*self._track_path[nearest])

            color = QColor(PLAYER_MAP_COLOR) if v["is_player"] else QColor(v.get("class_color", "#90a4ae"))
            radius = 7 if v["is_player"] else 5
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(point, radius, radius)
            if v["is_player"]:
                painter.setPen(QColor("#ffffff"))
                painter.drawText(point + QPointF(8, -8), str(v["place"]))

    def _draw_overlays(self, painter: QPainter) -> None:
        w = self.width()
        h = self.height()
        font = painter.font()

        # 좌상단: 트랙명 + 세션베스트
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#e0e0e0"))
        painter.drawText(8, 16, self._track_name or "-")

        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QColor("#ce93d8"))
        best_text = format_laptime(self._session_best_s) if self._session_best_s else "--:--.---"
        painter.drawText(8, 32, f"SESSION BEST {best_text}")

        # 좌하단: 기온/트랙온도
        painter.setPen(QColor("#81d4fa"))
        ambient = self._weather.get("ambient_c")
        track_t = self._weather.get("track_c")
        ambient_text = f"AIR {ambient:.1f}°" if ambient is not None else "AIR --"
        track_text = f"TRACK {track_t:.1f}°" if track_t is not None else "TRACK --"
        painter.drawText(8, h - 20, ambient_text)
        painter.drawText(8, h - 6, track_text)

        # 우상단: 바람 컴퍼스
        cx, cy, r = w - 26, 26, 16
        painter.setPen(QPen(QColor("#616161"), 1))
        painter.drawEllipse(QPointF(cx, cy), r, r)
        wind_speed = self._weather.get("wind_speed", 0.0)
        angle = self._weather.get("wind_angle_deg", 0.0)
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(angle)
        painter.setPen(QPen(QColor("#ef5350"), 2))
        painter.drawLine(QPointF(0, 0), QPointF(0, -r + 3))
        painter.restore()
        painter.setPen(QColor("#9e9e9e"))
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(int(cx - 14), int(cy + r + 12), f"{wind_speed:.1f}")


class _TireCell(QWidget):
    """타이어 1개: 잔여%(상단) + 3분할 온도 미니바+평균온도(중단) + 압력psi(하단) + 전랩마모(최하단)"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._remaining_pct = 0.0
        self._inner_samples_c: list[float] = [0.0, 0.0, 0.0]
        self._optimal_c = 90.0
        self._pressure_psi = 0.0
        self._wear_used_pct = 0.0
        self._flat = False
        self.setMinimumSize(100, 175)

    def set_data(
        self,
        remaining_pct: float,
        inner_samples_c: list[float],
        optimal_c: float,
        pressure_psi: float,
        wear_used_pct: float,
        flat: bool,
    ) -> None:
        self._remaining_pct = remaining_pct
        self._inner_samples_c = inner_samples_c
        self._optimal_c = optimal_c
        self._pressure_psi = pressure_psi
        self._wear_used_pct = wear_used_pct
        self._flat = flat
        self.update()

    def _wear_color(self) -> QColor:
        if self._flat:
            return QColor("#616161")
        if self._remaining_pct > 60:
            return QColor("#43a047")
        if self._remaining_pct > 30:
            return QColor("#fdd835")
        return QColor("#e53935")

    def _temp_color(self, temp_c: float) -> QColor:
        if self._flat:
            return QColor("#616161")
        diff = temp_c - self._optimal_c
        if diff < -15:
            return QColor("#1e88e5")
        if diff > 15:
            return QColor("#e53935")
        return QColor("#43a047")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 레이아웃 구역 (겹치지 않도록 고정 높이 배정)
        top_h = 26        # 잔여% 숫자
        temp_row_h = 46   # 3분할 온도바 + 평균온도 숫자
        bottom_h = 40      # 압력 + 전랩마모

        # 상단: 잔여율 % (마모 상태 색상)
        painter.setPen(self._wear_color())
        font = painter.font()
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, 0, w, top_h, Qt.AlignCenter, f"{self._remaining_pct:.0f}%")

        # 중단: 좌/중/우 3분할 온도 미니바
        bar_top = top_h + 4
        bar_h = temp_row_h - 16
        bar_area_w = w - 24
        gap = 3
        bar_w = (bar_area_w - gap * 2) / 3
        max_scale = 130.0  # 표시용 상한 (색상은 optimal 기준으로 별도 판단)

        for i, temp_c in enumerate(self._inner_samples_c):
            ratio = max(0.08, min(1.0, temp_c / max_scale))
            seg_h = bar_h * ratio
            x = 12 + i * (bar_w + gap)
            y = bar_top + (bar_h - seg_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(self._temp_color(temp_c)))
            painter.drawRect(int(x), int(y), int(bar_w), int(seg_h))

        avg_c = sum(self._inner_samples_c) / len(self._inner_samples_c) if self._inner_samples_c else 0.0
        painter.setPen(QColor("#e0e0e0"))
        font.setPointSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(0, bar_top + bar_h + 2, w, 14, Qt.AlignCenter, f"{avg_c:.0f}°")

        # 하단: 압력(psi) + 전랩마모
        line_y = top_h + temp_row_h
        painter.setPen(QColor("#e0e0e0"))
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, line_y, w, 18, Qt.AlignCenter, f"{self._pressure_psi:.1f}")

        painter.setPen(QColor("#9e9e9e"))
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(0, line_y + 20, w, 14, Qt.AlignCenter, f"WEAR/LAP {self._wear_used_pct:.1f}%")


class TirePanel(QGroupBox):
    """타이어 상태: 마모도 게이지 중심 (이미지 레이아웃 참고, 온도/압력 겹침 수정)"""

    POSITIONS = {"FL": (0, 0), "FR": (0, 2), "RL": (1, 0), "RR": (1, 2)}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("TIRES", parent)
        grid = QGridLayout(self)
        self.cells: dict[str, _TireCell] = {}

        for name, (row, col) in self.POSITIONS.items():
            cell = _TireCell()
            grid.addWidget(cell, row, col)
            self.cells[name] = cell

        self.compound_label = QLabel("--")
        self.compound_label.setAlignment(Qt.AlignCenter)
        self.compound_label.setStyleSheet(
            "background-color: #fdd835; color: #212121; border-radius: 10px; padding: 2px 10px; font-weight: bold;"
        )
        grid.addWidget(self.compound_label, 0, 1, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)

    def update_data(self, data: dict) -> None:
        player = data.get("player") or {}
        wheels = player.get("wheels")
        wear_used = player.get("wear_used_last_lap") or [0, 0, 0, 0]
        if not wheels:
            return
        for i, wheel in enumerate(wheels):
            cell = self.cells.get(wheel["name"])
            if cell is None:
                continue
            cell.set_data(
                remaining_pct=wheel["remaining_pct"],
                inner_samples_c=wheel["inner_samples_c"],
                optimal_c=wheel["optimal_c"] or 90.0,
                pressure_psi=wheel["pressure_psi"],
                wear_used_pct=wear_used[i],
                flat=wheel["flat"],
            )

        front_c = player.get("front_compound") or "-"
        rear_c = player.get("rear_compound") or "-"
        self.compound_label.setText(front_c if front_c == rear_c else f"{front_c}/{rear_c}")


class FuelVEPanel(QGroupBox):
    """연료: FUEL(현재+바) + EST LAPS/REFUEL/LAST LAP FUEL + AVG 2/5/ALL 랩 사용량.
    VE(가상 에너지)는 하단에 간단히 별도 표시."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("FUEL / VE", parent)
        outer = QVBoxLayout(self)
        grid = QGridLayout()
        outer.addLayout(grid)

        def stat(title: str) -> QLabel:
            box = QLabel("--")
            box.setAlignment(Qt.AlignCenter)
            font = box.font()
            font.setPointSize(13)
            font.setBold(True)
            box.setFont(font)
            return box

        def title_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #9e9e9e; font-size: 10px;")
            return lbl

        grid.addWidget(title_label("FUEL"), 0, 0, 1, 2, Qt.AlignCenter)
        self.fuel_value = stat("")
        self.fuel_value.setStyleSheet("font-size: 20px; font-weight: bold;")
        grid.addWidget(self.fuel_value, 1, 0, 1, 2)

        self.fuel_bar = QProgressBar()
        self.fuel_bar.setRange(0, 100)
        self.fuel_bar.setTextVisible(False)
        self.fuel_bar.setStyleSheet("QProgressBar::chunk { background-color: #43a047; }")
        grid.addWidget(self.fuel_bar, 2, 0, 1, 2)

        rows = [
            ("EST LAPS", "AVG 2 LAPS"),
            ("REFUEL", "AVG 5 LAPS"),
            ("LAST LAP FUEL", "AVG ALL LAPS"),
        ]
        self.value_labels: dict[str, QLabel] = {}
        keys = [
            ("est_laps", "avg2"),
            ("refuel", "avg5"),
            ("last_lap", "avg_all"),
        ]
        for row_idx, ((left_title, right_title), (left_key, right_key)) in enumerate(zip(rows, keys), start=3):
            grid.addWidget(title_label(left_title), row_idx * 2, 0)
            grid.addWidget(title_label(right_title), row_idx * 2, 1)
            left_val = stat("")
            right_val = stat("")
            left_val.setStyleSheet("font-size: 13px; font-weight: bold;")
            right_val.setStyleSheet("font-size: 13px; font-weight: bold;")
            grid.addWidget(left_val, row_idx * 2 + 1, 0)
            grid.addWidget(right_val, row_idx * 2 + 1, 1)
            self.value_labels[left_key] = left_val
            self.value_labels[right_key] = right_val

        # VE(가상 에너지) - 간단 요약 한 줄
        ve_row = QHBoxLayout()
        ve_row.addWidget(QLabel("VE LEVEL"))
        self.ve_current = QLabel("--")
        self.ve_avg = QLabel("AVG -- ")
        self.ve_remaining = QLabel("EST -- LAPS")
        for w in (self.ve_current, self.ve_avg, self.ve_remaining):
            w.setStyleSheet("color: #ce93d8;")
            ve_row.addWidget(w)
        outer.addLayout(ve_row)

    def update_data(self, data: dict) -> None:
        player = data.get("player") or {}

        fuel_l = player.get("fuel_l", 0.0) or 0.0
        capacity = player.get("fuel_capacity_l") or 0.0
        self.fuel_value.setText(f"{fuel_l:.2f}")
        if capacity > 0:
            self.fuel_bar.setValue(int(max(0, min(100, (fuel_l / capacity) * 100))))
        else:
            self.fuel_bar.setValue(0)

        est_laps = player.get("fuel_laps_remaining")
        self.value_labels["est_laps"].setText(f"{est_laps:.1f}" if est_laps else "--")

        refuel = player.get("refuel_needed_l")
        self.value_labels["refuel"].setText(f"{refuel:.1f} L" if refuel is not None else "--")

        last_lap_fuel = player.get("fuel_used_last_lap")
        self.value_labels["last_lap"].setText(f"{last_lap_fuel:.2f} L" if last_lap_fuel else "--")

        avg2 = player.get("fuel_avg_usage_2")
        self.value_labels["avg2"].setText(f"{avg2:.2f} L" if avg2 else "--")

        avg5 = player.get("fuel_avg_usage")
        self.value_labels["avg5"].setText(f"{avg5:.2f} L" if avg5 else "--")

        avg_all = player.get("fuel_avg_usage_all")
        self.value_labels["avg_all"].setText(f"{avg_all:.2f} L" if avg_all else "--")

        ve = player.get("ve_frac") or 0
        self.ve_current.setText(f"{ve * 100:.1f}%")
        ve_avg = player.get("ve_avg_usage")
        self.ve_avg.setText(f"AVG {ve_avg * 100:.1f}%" if ve_avg else "AVG --")
        ve_remaining = player.get("ve_laps_remaining")
        self.ve_remaining.setText(f"EST {ve_remaining:.1f} LAPS" if ve_remaining else "EST -- LAPS")


class TimingPanel(QGroupBox):
    """랩타임: 현재 / 전랩 / 최근5랩평균 / 베스트"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("LAP TIMES", parent)
        grid = QGridLayout(self)

        labels = [("CURRENT", "current"), ("LAST", "last"), ("AVG (5 LAPS)", "avg"), ("BEST", "best")]
        self.value_labels: dict[str, QLabel] = {}
        for row, (title, key) in enumerate(labels):
            grid.addWidget(QLabel(title), row, 0)
            value_label = _value_label("--:--.---", point_size=15)
            grid.addWidget(value_label, row, 1)
            self.value_labels[key] = value_label

    def update_data(self, data: dict) -> None:
        player = data.get("player") or {}
        self.value_labels["current"].setText(format_laptime(player.get("current_lap_time_s")))
        self.value_labels["last"].setText(format_laptime(player.get("last_lap_s")))
        self.value_labels["avg"].setText(format_laptime(player.get("avg_lap_s")))
        self.value_labels["best"].setText(format_laptime(player.get("best_lap_s")))

    def set_current_laptime_text(self, text: str) -> None:
        """60Hz 보간 타이머가 '현재 랩' 라벨만 부드럽게 갱신할 때 사용"""
        self.value_labels["current"].setText(text)


class SectorPanel(QGroupBox):
    """섹터 타임: 전랩 vs 최근 5랩 평균 vs 델타"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("SECTORS (LAST vs AVG5)", parent)
        grid = QGridLayout(self)

        headers = ["", "LAST", "AVG5", "DELTA"]
        for col, text in enumerate(headers):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            grid.addWidget(label, 0, col)

        self.rows: dict[str, dict[str, QLabel]] = {}
        for row, key in enumerate(("S1", "S2", "S3"), start=1):
            grid.addWidget(QLabel(key), row, 0)
            last_label = QLabel("--.---")
            avg_label = QLabel("--.---")
            delta_label = QLabel("--")
            for col, label in enumerate((last_label, avg_label, delta_label), start=1):
                label.setAlignment(Qt.AlignCenter)
                grid.addWidget(label, row, col)
            self.rows[key] = {"last": last_label, "avg": avg_label, "delta": delta_label}

    def update_data(self, data: dict) -> None:
        player = data.get("player") or {}
        sectors = player.get("sectors") or {}
        last_s1, last_s2, last_s3 = sectors.get("last", (None, None, None))
        avg_s1, avg_s2, avg_s3 = sectors.get("avg5", (None, None, None))

        for key, last_val, avg_val in (
            ("S1", last_s1, avg_s1),
            ("S2", last_s2, avg_s2),
            ("S3", last_s3, avg_s3),
        ):
            row = self.rows[key]
            row["last"].setText(format_sector(last_val))
            row["avg"].setText(format_sector(avg_val))
            if last_val is not None and avg_val is not None:
                delta = last_val - avg_val
                row["delta"].setText(format_delta(delta))
                row["delta"].setStyleSheet("color: #e53935;" if delta > 0 else "color: #43a047;")
            else:
                row["delta"].setText("--")
                row["delta"].setStyleSheet("")


class GapPanel(QGroupBox):
    """앞/뒤 격차"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("GAP", parent)
        grid = QGridLayout(self)

        grid.addWidget(QLabel("GAP AHEAD"), 0, 0)
        self.ahead_label = _value_label("--", point_size=18)
        grid.addWidget(self.ahead_label, 0, 1)

        grid.addWidget(QLabel("GAP BEHIND"), 1, 0)
        self.behind_label = _value_label("--", point_size=18)
        grid.addWidget(self.behind_label, 1, 1)

        grid.addWidget(QLabel("POSITION"), 2, 0)
        self.place_label = _value_label("--", point_size=18)
        grid.addWidget(self.place_label, 2, 1)

    def update_data(self, data: dict) -> None:
        player = data.get("player") or {}
        self.ahead_label.setText(format_gap(player.get("gap_ahead")))
        self.behind_label.setText(format_gap(player.get("gap_behind")))
        place = player.get("place")
        total = player.get("num_vehicles")
        self.place_label.setText(f"{place} / {total}" if place else "--")


class OnboardPanel(QGroupBox):
    """온보드 정보: 이미지 레이아웃과 동일하게 가로 배치, 색상 라벨, 단일 값 표시
    (BRK BIAS 빨강 / TC SLIP·TC CUT·TC 파랑 / ABS 노랑 / MAP 초록)"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("ONBOARD", parent)
        layout = QHBoxLayout(self)

        specs = [
            ("BRK BIAS", "brake_bias", "#ef5350"),
            ("TC SLIP", "tc_slip", "#29b6f6"),
            ("TC CUT", "tc_cut", "#29b6f6"),
            ("TC", "tc", "#29b6f6"),
            ("ABS", "abs", "#fdd835"),
            ("MAP", "motor_map", "#66bb6a"),
        ]
        self.value_labels: dict[str, QLabel] = {}
        for title, key, color in specs:
            col = QVBoxLayout()
            title_label = QLabel(title)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
            value_label = _value_label("--", point_size=18)
            col.addWidget(title_label)
            col.addWidget(value_label)
            layout.addLayout(col)
            self.value_labels[key] = value_label

    def update_data(self, data: dict) -> None:
        onboard = (data.get("player") or {}).get("onboard") or {}

        def fmt(key: str, key_max: str, decimals: int = 0) -> str:
            v = onboard.get(key)
            vmax = onboard.get(key_max)
            if v is None or not vmax:  # max값이 0/None이면 해당 차량엔 적용 안되는 항목 (이미지의 '-' 표시)
                return "-"
            return f"{v:.{decimals}f}" if decimals else str(v)

        self.value_labels["tc"].setText(fmt("tc", "tc_max"))
        self.value_labels["tc_cut"].setText(fmt("tc_cut", "tc_cut_max"))
        self.value_labels["tc_slip"].setText(fmt("tc_slip", "tc_slip_max"))
        self.value_labels["abs"].setText(fmt("abs", "abs_max"))
        self.value_labels["motor_map"].setText(fmt("motor_map", "motor_map_max"))
        bias = onboard.get("brake_bias_front_pct")
        self.value_labels["brake_bias"].setText(f"{bias:.2f}" if bias is not None else "--")
