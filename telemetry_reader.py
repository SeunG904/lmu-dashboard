"""
telemetry_reader.py

LMU 공유 메모리를 읽어서 대시보드가 쓰기 좋은 형태(dict)로 가공한다.

기능:
  1) 스탠딩(순위표, 압축형 - 타이어/연료/섹터/피트-아웃랩 포함)
  2) 트랙맵 (섹터 수직 구분선 + 로컬 옐로 + 클래스별 색상 위치)
  3) 타이어 (마모도, 표면/이너레이어/코어 온도, 전랩 마모도)
  4) 연료/VE (전랩 사용량, 평균 사용량, 예측 남은 랩 수)
  5) 랩타임 (현재/전랩/최근5랩평균/베스트) + 섹터 5랩 비교 + 베스트랩 델타
  6) 앞/뒤 격차
  7) 온보드 정보 (TC/ABS/브레이크밸런스/모터맵)
  8) 텔레메트리 그래프용 최근 N초 샘플 버퍼 (속도/페달/스티어링)
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from pathlib import Path

from vendor.pyLMUSharedMemory import lmu_data
from vendor.pyLMUSharedMemory.lmu_data import LMUConstants
from vendor.pyLMUSharedMemory.lmu_mmap import MMapControl

logger = logging.getLogger(__name__)

KELVIN_TO_CELSIUS = 273.15
KPA_TO_PSI = 0.14503773773
WHEEL_NAMES = ("FL", "FR", "RL", "RR")
TRACK_MAP_BUCKETS = 720  # 트랙 경로를 저장할 해상도 (0.5도 간격)
RECENT_LAPS = 5  # 평균/비교에 사용할 최근 랩 수
GRAPH_BUFFER_SECONDS = 12.0  # 그래프용 샘플을 보관할 최대 시간(초)
GRAPH_MAX_SAMPLES = 400  # 안전장치: 최대 샘플 개수

# 클래스명(문자열, 대소문자 무관 부분일치)에 따른 트랙맵 색상
CLASS_COLORS = {
    "hypercar": "#e53935",
    "lmp2": "#1e88e5",
    "lmp3": "#8e24aa",
    "gt3": "#43a047",
}
DEFAULT_CLASS_COLOR = "#90a4ae"
PLAYER_MAP_COLOR = "#ab47bc"  # 보라
TRACK_MAP_LOCK_COVERAGE = 0.95  # 이 비율 이상 기록되면 트랙맵을 고정(더 이상 갱신 안 함)
TRACK_MAP_SAVE_DIR = Path(__file__).resolve().parent / "track_maps"


def classify_color(vehicle_class: str) -> str:
    name = (vehicle_class or "").lower()
    for key, color in CLASS_COLORS.items():
        if key in name:
            return color
    return DEFAULT_CLASS_COLOR


def format_driver_short(name: str) -> str:
    """'Seunggyu Choi' -> 'S.CHOI' 형식으로 축약"""
    parts = (name or "").strip().split()
    if not parts:
        return "-"
    if len(parts) == 1:
        return parts[0].upper()
    return f"{parts[0][0].upper()}.{parts[-1].upper()}"


def format_laptime(seconds: float | None) -> str:
    """초 단위 랩타임을 M:SS.mmm 형식 문자열로 변환. 유효하지 않으면 '--:--.---'"""
    if seconds is None or seconds <= 0 or seconds > 3600:
        return "--:--.---"
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:06.3f}"


def format_sector(seconds: float | None) -> str:
    """섹터 타임을 SS.mmm 형식으로. 유효하지 않으면 '--.---'"""
    if seconds is None or seconds <= 0 or seconds > 600:
        return "--.---"
    return f"{seconds:06.3f}"


def format_minutes_seconds(seconds: float | None) -> str:
    """초 단위 시간을 MM:SS 형식으로 (연료 소진 예상시간 등에 사용). 유효하지 않으면 '--:--'"""
    if seconds is None or seconds < 0 or seconds > 86400:
        return "--:--"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def format_gap(seconds: float | None) -> str:
    """격차(초)를 +12.345 형태 문자열로. 유효하지 않으면 '--'"""
    if seconds is None:
        return "--"
    return f"+{seconds:.3f}"


def format_delta(seconds: float | None) -> str:
    """델타(초)를 +/-1.234 형태로. 유효하지 않으면 '--'"""
    if seconds is None:
        return "--"
    sign = "+" if seconds >= 0 else ""
    return f"{sign}{seconds:.3f}"


class TelemetryReader:
    """LMU 공유 메모리 읽기 + 랩 단위 파생 데이터 계산 + 그래프 샘플 버퍼링"""

    def __init__(self) -> None:
        self._mmap = MMapControl(LMUConstants.LMU_SHARED_MEMORY_FILE, lmu_data.LMUObjectOut)

        # 트랙맵: bucket index -> (x, z)
        self._track_path: dict[int, tuple[float, float]] = {}
        self._track_locked = False
        self._loaded_track_name: str | None = None

        # 랩 경계 감지 및 전랩 파생값 계산용 상태 (플레이어)
        self._prev_lap_number: int | None = None
        self._lap_start_remaining: list[float] | None = None
        self._lap_start_fuel: float | None = None
        self._lap_start_ve: float | None = None
        self._last_wear_used: list[float] = [0.0, 0.0, 0.0, 0.0]
        self._fuel_usage_history: deque[float] = deque(maxlen=RECENT_LAPS)
        self._fuel_usage_history_2: deque[float] = deque(maxlen=2)
        self._fuel_usage_history_all: deque[float] = deque(maxlen=500)
        self._ve_usage_history: deque[float] = deque(maxlen=RECENT_LAPS)
        self._lap_time_history: deque[float] = deque(maxlen=RECENT_LAPS)
        self._sector_history: deque[tuple[float, float, float]] = deque(maxlen=RECENT_LAPS)
        self._last_fuel_used: float | None = None
        self._last_ve_used: float | None = None

        # 피트/아웃랩 상태 추적 (차량 id 기준)
        self._pit_state: dict[int, dict] = {}

        # 텔레메트리 그래프 샘플 버퍼: (timestamp, speed, throttle, brake, clutch, steering)
        self._graph_samples: deque[tuple[float, float, float, float, float, float]] = deque(
            maxlen=GRAPH_MAX_SAMPLES
        )

    # -- 메모리 매핑 관리 -------------------------------------------------

    def open(self) -> None:
        self._mmap.create(access_mode=0)

    def close(self) -> None:
        if self._mmap is not None:
            self._mmap.close()

    def update(self) -> None:
        self._mmap.update()

    def is_connected(self) -> bool:
        try:
            return bool(self._mmap.data.generic.gameVersion)
        except (AttributeError, ValueError):
            return False

    # -- 내부 유틸 ---------------------------------------------------------

    def _track_map_file(self, track_name: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in track_name) or "unknown"
        return TRACK_MAP_SAVE_DIR / f"{safe_name}.json"

    def _try_load_track_map(self, track_name: str) -> bool:
        """저장된 트랙맵이 있으면 불러와서 즉시 고정 상태로 설정. 성공 시 True"""
        path = self._track_map_file(track_name)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._track_path = {int(k): tuple(v) for k, v in raw.items()}
            self._track_locked = True
            logger.info("track map loaded from %s (%d points)", path, len(self._track_path))
            return True
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("failed to load track map %s: %s", path, exc)
            return False

    def _save_track_map(self, track_name: str) -> None:
        """현재 트랙맵을 디스크에 저장 (다음 실행부터 영구적으로 재사용)"""
        try:
            TRACK_MAP_SAVE_DIR.mkdir(parents=True, exist_ok=True)
            path = self._track_map_file(track_name)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({str(k): list(v) for k, v in self._track_path.items()}, f)
            logger.info("track map saved to %s (%d points)", path, len(self._track_path))
        except OSError as exc:
            logger.warning("failed to save track map: %s", exc)

    def _ensure_track_map_loaded(self, track_name: str) -> None:
        """트랙명이 바뀌면(첫 확인 포함) 저장된 맵이 있는지 한 번 확인해서 불러온다"""
        if not track_name or track_name == self._loaded_track_name:
            return
        self._loaded_track_name = track_name
        # 새 트랙으로 전환 시 이전 트랙 데이터는 초기화
        self._track_path = {}
        self._track_locked = False
        self._try_load_track_map(track_name)

    def _record_track_point(self, fraction: float, x: float, z: float, track_name: str = "") -> None:
        if self._track_locked:
            return
        bucket = int(fraction * TRACK_MAP_BUCKETS) % TRACK_MAP_BUCKETS
        self._track_path[bucket] = (x, z)
        if len(self._track_path) / TRACK_MAP_BUCKETS >= TRACK_MAP_LOCK_COVERAGE:
            self._track_locked = True
            if track_name:
                self._save_track_map(track_name)

    def _handle_lap_change(
        self,
        lap_number: int,
        remaining_pct: list[float],
        fuel_l: float,
        ve_frac: float,
        last_lap_s: float | None,
        last_sector1: float | None,
        last_sector2: float | None,
    ) -> None:
        if self._prev_lap_number is None:
            self._prev_lap_number = lap_number
            self._lap_start_remaining = remaining_pct
            self._lap_start_fuel = fuel_l
            self._lap_start_ve = ve_frac
            return

        if lap_number != self._prev_lap_number and lap_number > self._prev_lap_number:
            if self._lap_start_remaining is not None:
                self._last_wear_used = [
                    max(0.0, start - now) for start, now in zip(self._lap_start_remaining, remaining_pct)
                ]
            if self._lap_start_fuel is not None:
                used = self._lap_start_fuel - fuel_l
                if used > 0:
                    self._last_fuel_used = used
                    self._fuel_usage_history.append(used)
                    self._fuel_usage_history_2.append(used)
                    self._fuel_usage_history_all.append(used)
            if self._lap_start_ve is not None:
                used = self._lap_start_ve - ve_frac
                if used > 0:
                    self._last_ve_used = used
                    self._ve_usage_history.append(used)

            if last_lap_s and last_lap_s > 0:
                self._lap_time_history.append(last_lap_s)

            if last_lap_s and last_lap_s > 0 and last_sector1 and last_sector2 and last_sector2 > last_sector1:
                s1 = last_sector1
                s2 = last_sector2 - last_sector1
                s3 = last_lap_s - last_sector2
                if s1 > 0 and s2 > 0 and s3 > 0:
                    self._sector_history.append((s1, s2, s3))

            self._lap_start_remaining = remaining_pct
            self._lap_start_fuel = fuel_l
            self._lap_start_ve = ve_frac
            self._prev_lap_number = lap_number

    def _update_pit_state(self, vehicle_id: int, in_pits: bool, laps: int) -> str:
        """차량별 피트/아웃랩 상태를 추적해서 'PIT' / 'OUT' / '' 반환"""
        state = self._pit_state.setdefault(vehicle_id, {"was_in_pits": in_pits, "out_lap": False, "last_laps": laps})

        if in_pits:
            state["was_in_pits"] = True
            state["out_lap"] = False
            return "PIT"

        if state["was_in_pits"] and not in_pits:
            # 방금 피트를 빠져나옴 -> 아웃랩 시작
            state["out_lap"] = True
        state["was_in_pits"] = False

        if state["out_lap"]:
            if laps > state["last_laps"]:
                # 한 바퀴를 완주했으니 아웃랩 종료
                state["out_lap"] = False
            else:
                state["last_laps"] = laps
                return "OUT"

        state["last_laps"] = laps
        return ""

    @staticmethod
    def _avg(values: deque[float]) -> float | None:
        return sum(values) / len(values) if values else None

    def _avg_sectors(self) -> tuple[float | None, float | None, float | None]:
        if not self._sector_history:
            return None, None, None
        s1 = sum(s[0] for s in self._sector_history) / len(self._sector_history)
        s2 = sum(s[1] for s in self._sector_history) / len(self._sector_history)
        s3 = sum(s[2] for s in self._sector_history) / len(self._sector_history)
        return s1, s2, s3

    def graph_samples(self, seconds: float = GRAPH_BUFFER_SECONDS) -> list[tuple[float, float, float, float, float, float]]:
        """최근 `seconds`초 동안의 그래프 샘플 반환 (현재 시각 기준 상대 초 단위로 변환)"""
        if not self._graph_samples:
            return []
        now = self._graph_samples[-1][0]
        cutoff = now - seconds
        return [(t - now, spd, thr, brk, clu, steer) for (t, spd, thr, brk, clu, steer) in self._graph_samples if t >= cutoff]

    # -- 스냅샷 --------------------------------------------------------

    def snapshot(self) -> dict:
        data = self._mmap.data
        telemetry = data.telemetry
        scoring_info = data.scoring.scoringInfo

        if not self.is_connected() or telemetry.activeVehicles == 0:
            return {"connected": False}

        idx = telemetry.playerVehicleIdx
        if idx < 0 or idx >= LMUConstants.MAX_MAPPED_VEHICLES:
            return {"connected": False}

        veh = telemetry.telemInfo[idx]
        track_length = scoring_info.mLapDist or 1.0

        # 텔레메트리 배열을 mID 기준으로 매핑 (스탠딩에서 상대차량 타이어/연료 조회용)
        telem_by_id: dict[int, object] = {}
        for i in range(telemetry.activeVehicles):
            t = telemetry.telemInfo[i]
            telem_by_id[t.mID] = t

        # --- 전체 차량 스코어링 정보 수집 ---
        num_vehicles = scoring_info.mNumVehicles
        vehicles = []
        gap_ahead_by_place: dict[int, float] = {}
        for i in range(num_vehicles):
            vs = data.scoring.vehScoringInfo[i]
            place = vs.mPlace
            gap_ahead_by_place[place] = vs.mTimeBehindNext

            status = self._update_pit_state(vs.mID, bool(vs.mInPits), vs.mTotalLaps)

            # 상대 차량 타이어/연료/VE: telemInfo에 값이 채워져 있으면 사용, 아니면 None
            tire_avg_remaining = None
            fuel_l_other = None
            ve_pct_other = None
            t = telem_by_id.get(vs.mID)
            if t is not None:
                wears = [w.mWear for w in t.mWheels]
                # 4바퀴 전부 0이면 데이터가 채워지지 않은 것으로 판단 (원격 차량 등)
                if any(w > 0.0 for w in wears):
                    tire_avg_remaining = (sum(wears) / 4.0) * 100.0
                if t.mFuel > 0.0 or t.mFuelCapacity > 0.0:
                    fuel_l_other = t.mFuel
                if t.mVirtualEnergy > 0.0:
                    ve_pct_other = t.mVirtualEnergy * 100.0

            vehicle_class = vs.mVehicleClass.decode(errors="ignore").strip()
            driver_name = vs.mDriverName.decode(errors="ignore").strip()

            vehicles.append(
                {
                    "id": vs.mID,
                    "place": place,
                    "driver_name": driver_name,
                    "driver_name_short": format_driver_short(driver_name),
                    "vehicle_name": vs.mVehicleName.decode(errors="ignore").strip(),
                    "vehicle_class": vehicle_class,
                    "class_color": classify_color(vehicle_class),
                    "laps": vs.mTotalLaps,
                    "in_pits": bool(vs.mInPits),
                    "status": status,
                    "is_player": bool(vs.mIsPlayer),
                    "lap_dist_fraction": (vs.mLapDist / track_length) % 1.0,
                    "sector": vs.mSector,
                    "gap_to_leader": vs.mTimeBehindLeader,
                    "gap_ahead": vs.mTimeBehindNext,
                    "best_lap_s": vs.mBestLapTime,
                    "last_lap_s": vs.mLastLapTime,
                    "last_sector1": vs.mLastSector1,
                    "last_sector2": vs.mLastSector2,
                    "cur_sector1": vs.mCurSector1,
                    "cur_sector2": vs.mCurSector2,
                    "tire_avg_remaining_pct": tire_avg_remaining,
                    "fuel_l": fuel_l_other,
                    "ve_pct": ve_pct_other,
                }
            )
        vehicles.sort(key=lambda v: (v["place"] if v["place"] else 999))

        for v in vehicles:
            v["gap_behind"] = gap_ahead_by_place.get(v["place"] + 1)

        player_scoring = next((v for v in vehicles if v["id"] == veh.mID), None)

        # --- 타이어 ---
        # mWear = 잔여 비율(1.0=새 타이어, 0.0=완전 마모)로 확인됨
        # 이너레이어 온도가 실제 게임 표시값과 정확히 일치하는 것으로 확인되어 이를 기준값으로 사용
        remaining_pct_list = []
        wheels = []
        for name, w in zip(WHEEL_NAMES, veh.mWheels):
            inner_samples_c = [t - KELVIN_TO_CELSIUS for t in w.mTireInnerLayerTemperature]
            surface_avg_c = (sum(w.mTemperature) / 3.0) - KELVIN_TO_CELSIUS
            core_c = w.mTireCarcassTemperature - KELVIN_TO_CELSIUS
            remaining_pct = w.mWear * 100.0
            remaining_pct_list.append(remaining_pct)
            wheels.append(
                {
                    "name": name,
                    "inner_samples_c": inner_samples_c,  # [좌, 중, 우] 3개 샘플 (게임 표시값과 일치)
                    "inner_avg_c": sum(inner_samples_c) / 3.0,
                    "surface_temp_c": surface_avg_c,
                    "core_temp_c": core_c,
                    "optimal_c": w.mOptimalTemp,
                    "remaining_pct": remaining_pct,
                    "pressure_kpa": w.mPressure,
                    "pressure_psi": w.mPressure * KPA_TO_PSI,
                    "flat": bool(w.mFlat),
                }
            )

        # --- 연료 / VE ---
        fuel_l = veh.mFuel
        ve_frac = veh.mVirtualEnergy

        lap_number = veh.mLapNumber
        last_lap_s = player_scoring["last_lap_s"] if player_scoring else None
        last_sector1 = player_scoring["last_sector1"] if player_scoring else None
        last_sector2 = player_scoring["last_sector2"] if player_scoring else None

        self._handle_lap_change(
            lap_number, remaining_pct_list, fuel_l, ve_frac, last_lap_s, last_sector1, last_sector2
        )

        # --- 트랙맵: 저장된 맵이 있으면 로드, 없으면 계속 기록 (피트인/트랙컷 시 기록 생략) ---
        track_name_now = veh.mTrackName.decode(errors="ignore").strip()
        self._ensure_track_map_loaded(track_name_now)
        if player_scoring is not None and not player_scoring["in_pits"] and not bool(veh.mLapInvalidated):
            self._record_track_point(player_scoring["lap_dist_fraction"], veh.mPos.x, veh.mPos.z, track_name_now)

        avg_fuel_usage = self._avg(self._fuel_usage_history)
        avg_fuel_usage_2 = self._avg(self._fuel_usage_history_2)
        avg_fuel_usage_all = self._avg(self._fuel_usage_history_all)
        avg_ve_usage = self._avg(self._ve_usage_history)
        laps_remaining_fuel = (fuel_l / avg_fuel_usage) if avg_fuel_usage else None
        laps_remaining_ve = (ve_frac / avg_ve_usage) if avg_ve_usage else None
        avg_lap_time = self._avg(self._lap_time_history)
        avg_s1, avg_s2, avg_s3 = self._avg_sectors()

        # 연료 소진 예상 시간(초) = 예상 남은 랩 수 * 평균 랩타임
        fuel_time_remaining_s = None
        if laps_remaining_fuel is not None and avg_lap_time:
            fuel_time_remaining_s = laps_remaining_fuel * avg_lap_time

        # 완주까지 필요한 리필량 = (남은 레이스 랩 수 * 평균 사용량) - 현재 연료
        # mMaxLaps가 0 이하이면 랩 기반 레이스가 아니거나(시간제 등) 알 수 없는 경우
        refuel_needed_l = None
        max_laps = scoring_info.mMaxLaps
        if max_laps and max_laps > 0 and avg_fuel_usage:
            laps_left_in_race = max(0, max_laps - lap_number)
            needed = laps_left_in_race * avg_fuel_usage - fuel_l
            refuel_needed_l = max(0.0, needed)

        # 세션 베스트(전체 차량 중 최고 베스트랩)
        valid_bests = [v["best_lap_s"] for v in vehicles if v["best_lap_s"] and v["best_lap_s"] > 0]
        session_best_s = min(valid_bests) if valid_bests else None

        # 날씨/바람
        ambient_c = scoring_info.mAmbientTemp
        track_c = scoring_info.mTrackTemp
        wind = scoring_info.mWind
        wind_speed = (wind.x ** 2 + wind.z ** 2) ** 0.5
        wind_angle_deg = math.degrees(math.atan2(wind.x, wind.z)) if wind_speed > 0.001 else 0.0

        current_lap_time = None
        cur_s1 = cur_s2 = cur_s3 = None
        if player_scoring is not None:
            current_lap_time = scoring_info.mCurrentET - veh.mLapStartET
            sector_now = player_scoring["sector"]
            c1 = player_scoring["cur_sector1"]
            c2 = player_scoring["cur_sector2"]
            if sector_now == 1:
                cur_s1 = current_lap_time
            elif sector_now == 2:
                cur_s1 = c1 if c1 and c1 > 0 else None
                cur_s2 = (current_lap_time - c1) if c1 and c1 > 0 else None
            elif sector_now == 0:
                cur_s1 = c1 if c1 and c1 > 0 else None
                cur_s2 = (c2 - c1) if c1 and c2 and c2 > c1 else None
                cur_s3 = (current_lap_time - c2) if c2 and c2 > 0 else None

        last_s1 = last_s2 = last_s3 = None
        if last_lap_s and last_sector1 and last_sector2 and last_sector2 > last_sector1:
            last_s1 = last_sector1
            last_s2 = last_sector2 - last_sector1
            last_s3 = last_lap_s - last_sector2

        # --- 온보드 정보 ---
        onboard = {
            "tc": veh.mTC,
            "tc_max": veh.mTCMax,
            "tc_cut": veh.mTCCut,
            "tc_cut_max": veh.mTCCutMax,
            "tc_slip": veh.mTCSlip,
            "tc_slip_max": veh.mTCSlipMax,
            "abs": veh.mABS,
            "abs_max": veh.mABSMax,
            "brake_bias_front_pct": (1.0 - veh.mRearBrakeBias) * 100.0,
            "motor_map": veh.mMotorMap,
            "motor_map_max": veh.mMotorMapMax,
        }

        # --- 그래프 샘플 기록 & 라이브 값 ---
        speed_kmh = ((veh.mLocalVel.x ** 2 + veh.mLocalVel.y ** 2 + veh.mLocalVel.z ** 2) ** 0.5) * 3.6
        steering_frac = veh.mFilteredSteering  # -1.0(좌) ~ +1.0(우)
        # 비주얼(콕핏 3D 모델) 휠 회전 범위 사용. mPhysicalSteeringWheelRange(실제 휠베이스 설정값)는
        # 실측(292~293도)과 달라서(랙비율 차이로 물리 휠과 비주얼 휠 회전각이 다름) 이걸로 교체함.
        wheel_range_deg = veh.mVisualSteeringWheelRange or veh.mPhysicalSteeringWheelRange or 900.0
        front_compound = veh.mFrontTireCompoundName.decode(errors="ignore").strip()
        rear_compound = veh.mRearTireCompoundName.decode(errors="ignore").strip()

        self._graph_samples.append(
            (
                time.monotonic(),
                speed_kmh,
                veh.mFilteredThrottle,
                veh.mFilteredBrake,
                veh.mFilteredClutch,
                veh.mFilteredSteering,
            )
        )

        return {
            "connected": True,
            "track_name": veh.mTrackName.decode(errors="ignore").strip(),
            "track_length": track_length,
            "sector_flags": list(scoring_info.mSectorFlag),
            "track_path": dict(self._track_path),
            "vehicles": vehicles,
            "session_best_s": session_best_s,
            "weather": {
                "ambient_c": ambient_c,
                "track_c": track_c,
                "wind_speed": wind_speed,
                "wind_angle_deg": wind_angle_deg,
            },
            "player": {
                "lap_dist_fraction": player_scoring["lap_dist_fraction"] if player_scoring else 0.0,
                "place": player_scoring["place"] if player_scoring else None,
                "num_vehicles": num_vehicles,
                "gap_ahead": player_scoring["gap_ahead"] if player_scoring else None,
                "gap_behind": player_scoring["gap_behind"] if player_scoring else None,
                "lap_number": lap_number,
                "speed_kmh": speed_kmh,
                "steering_frac": steering_frac,
                "wheel_range_deg": wheel_range_deg,
                "front_compound": front_compound,
                "rear_compound": rear_compound,
                "wheels": wheels,
                "wear_used_last_lap": list(self._last_wear_used),
                "fuel_l": fuel_l,
                "fuel_capacity_l": veh.mFuelCapacity,
                "fuel_used_last_lap": self._last_fuel_used,
                "fuel_avg_usage": avg_fuel_usage,
                "fuel_avg_usage_2": avg_fuel_usage_2,
                "fuel_avg_usage_all": avg_fuel_usage_all,
                "fuel_laps_remaining": laps_remaining_fuel,
                "fuel_time_remaining_s": fuel_time_remaining_s,
                "refuel_needed_l": refuel_needed_l,
                "ve_frac": ve_frac,
                "ve_used_last_lap": self._last_ve_used,
                "ve_avg_usage": avg_ve_usage,
                "ve_laps_remaining": laps_remaining_ve,
                "current_lap_time_s": current_lap_time,
                "last_lap_s": last_lap_s,
                "best_lap_s": player_scoring["best_lap_s"] if player_scoring else None,
                "avg_lap_s": avg_lap_time,
                "delta_best_s": veh.mDeltaBest,
                "sectors": {
                    "current": (cur_s1, cur_s2, cur_s3),
                    "last": (last_s1, last_s2, last_s3),
                    "avg5": (avg_s1, avg_s2, avg_s3),
                },
                "onboard": onboard,
            },
        }
