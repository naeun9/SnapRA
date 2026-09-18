"""AI-Hub `건설현장 위험 상태 판단 데이터` 어노테이션 스키마.

JSON 한 건 = 이미지 한 장의 라벨. 최상위 키에 마침표가 붙어 있다는 점에 주의
("Raw_Data_Info." 처럼). 원본 키를 그대로 상수로 두고 파싱한다.

값이 비거나 키가 없는 필드가 섞여 있으므로 모든 파서는 관대하게 동작한다.
파싱 실패는 예외 대신 None 으로 흘린다 — 22만 건을 훑는 중에 한 건 때문에
멈추면 곤란하기 때문.

코드 테이블(시나리오 105개, 객체 클래스 73종, 공정, 사고유형, 날씨, 촬영장비,
실내외, 결번, 안전쌍)의 단일 출처는 tables.py 다. 이 파일은 테이블을 정의하지
않고 tables.py 를 읽어 쓰기 좋은 형태로 노출하는 역할만 한다. Enum 으로 남은
것들은 ID 문법(접두어 체계)이라 데이터가 아니라 구조에 속한다.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable

try:
    import tables
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "src/tables.py 가 필요하다. 코드 테이블의 단일 출처이므로 이 파일 없이는 "
        "schema.py 를 쓸 수 없다."
    ) from exc

# ---------------------------------------------------------------- 최상위 키
K_RAW = "Raw_Data_Info."
K_SOURCE = "Source_Data_Info."
K_LEARNING = "Learning_Data_Info."


# ====================================================== 코드 문법 (구조 정의)
class SituationType(str, Enum):
    """Situation_ID 접두어."""

    NORMAL = "Y"          # 정상
    ABNORMAL = "N"        # 비정상
    CAUTION = "C"         # 주의요망
    SAFETY_EQUIP = "SO"   # 안전보조장비


class AccidentType(str, Enum):
    """type_ID — 5대 사고유형 + 보조 분류."""

    FALL = "A"          # 추락
    DROP = "B"          # 낙하
    PINCH = "C"         # 협착
    FIRE = "D"          # 화재
    OVERTURN = "E"      # 전도
    CAUTION = "F"       # 주의요망
    SAFETY_EQUIP = "G"  # 안전보조장비


# 5대 사고유형만 따로 (정확도 평가 대상)
MAJOR_ACCIDENT_TYPES: tuple[str, ...] = ("A", "B", "C", "D", "E")


class SiteCondition(str, Enum):
    INDOOR = "1"   # 실내
    OUTDOOR = "2"  # 실외


class Weather(str, Enum):
    CLEAR = "1"   # 맑음
    CLOUDY = "2"  # 흐림
    RAIN = "3"    # 비
    SNOW = "4"    # 눈


class Device(str, Enum):
    GOPRO = "1"      # 고프로
    DRONE = "2"      # 드론
    CAMCORDER = "3"  # 캠코더
    PHONE = "4"      # 휴대폰
    CCTV = "5"       # 이동식 CCTV


# 서비스 입력은 현장 작업자가 휴대폰으로 찍은 사진이다. 휴대폰(4) 촬영분이
# 평가셋에 몇 장이나 있는지가 핵심 지표.
DEVICE_PHONE = "4"


class ObjectClassGroup(str, Enum):
    """class_ID 접두어 — 객체 클래스 73종의 대분류."""

    MOVING = "WO"  # 이동객체
    STATIC = "SO"  # 정적객체
    AREA = "DO"    # 영역객체


OBJECT_GROUP_PREFIXES: frozenset[str] = frozenset(g.value for g in ObjectClassGroup)


class AnnotationShape(str, Enum):
    POLYGON = "polygon"
    BBOX = "bbox"
    POLYLINE = "polyline"
    POINT = "point"


# =============================================== tables.py 에서 가져오는 테이블
def _pick_name(value: Any) -> str:
    """{code: "명칭"} 과 {code: (..., "명칭")} 양쪽을 받아 명칭만 뽑는다."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (tuple, list)):
        for item in reversed(value):
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ""
    return str(value)


def _name_map(mapping: Any) -> dict[str, str]:
    """코드 테이블을 {문자열 코드: 명칭} 으로 정규화. 키가 int 여도 str 로 통일."""
    if not isinstance(mapping, dict):
        return {}
    return {str(code).strip(): _pick_name(value) for code, value in mapping.items()}


# 코드값 -> 한글 명칭. 전부 tables.py 가 출처다.
SITUATION_TYPE_KO: dict[str, str] = _name_map(tables.SITUATION_PREFIX)
ACCIDENT_TYPE_KO: dict[str, str] = _name_map(tables.ACCIDENT_TYPES)
SITE_CONDITION_KO: dict[str, str] = _name_map(tables.SITE_CONDITIONS)
WEATHER_KO: dict[str, str] = _name_map(tables.WEATHER)
DEVICE_KO: dict[str, str] = _name_map(tables.DEVICES)
PROCESS_KO: dict[str, str] = _name_map(tables.PROCESSES)

# 원본 테이블은 그대로 재노출한다 (복제하지 않는다).
SCENARIOS: dict[str, tuple] = tables.SCENARIOS
OBJECT_CLASSES: dict[str, tuple] = tables.OBJECT_CLASSES
MISSING_CLASS_IDS: frozenset[str] = frozenset(
    str(c).strip() for c in tables.MISSING_CLASS_IDS
)
EXPECTED_PER_SCENARIO: int = int(tables.EXPECTED_PER_SCENARIO)
OBJECT_SAFETY_PAIRS = tables.OBJECT_SAFETY_PAIRS

# 대분류 명칭은 OBJECT_CLASSES 의 첫 항목에서 유도한다.
OBJECT_CLASS_GROUP_KO: dict[str, str] = {}
for _cid, _entry in OBJECT_CLASSES.items():
    _prefix = str(_cid).split("-", 1)[0].upper()
    if _prefix in OBJECT_GROUP_PREFIXES and _prefix not in OBJECT_CLASS_GROUP_KO:
        _major = _entry[0] if isinstance(_entry, (tuple, list)) and _entry else ""
        if isinstance(_major, str) and _major.strip():
            OBJECT_CLASS_GROUP_KO[_prefix] = _major.strip()

# 정의된 시나리오 ID 전체 / Y-N 쌍. 데이터가 아니라 tables.py 에서 유도한다.
DEFINED_SITUATION_IDS: tuple[str, ...] = tuple(SCENARIOS)
SCENARIO_PAIRS: list[tuple[str, str]] = [
    (sid, f"N-{sid.split('-', 1)[1]}")
    for sid in sorted(SCENARIOS)
    if sid.startswith("Y-") and f"N-{sid.split('-', 1)[1]}" in SCENARIOS
]


# ----------------------------------------------------------- 조회 헬퍼
def object_class_group(class_id: str | None) -> str | None:
    """'SO-03' -> 'SO'. 시나리오 ID(N-12 등)가 들어오면 None."""
    if not isinstance(class_id, str) or not class_id:
        return None
    prefix = class_id.split("-", 1)[0].upper()
    return prefix if prefix in OBJECT_GROUP_PREFIXES else None


def object_class_entry(class_id: str | None) -> tuple | None:
    """(대분류, 소분류, 명칭). 테이블에 없으면 None."""
    if not isinstance(class_id, str):
        return None
    entry = OBJECT_CLASSES.get(class_id.strip().upper())
    return entry if isinstance(entry, (tuple, list)) else None


def _entry_at(entry: tuple | None, index: int) -> str:
    if not entry or len(entry) <= index:
        return ""
    value = entry[index]
    return value.strip() if isinstance(value, str) else str(value)


def object_class_major(class_id: str | None) -> str:
    return _entry_at(object_class_entry(class_id), 0)


def object_class_minor(class_id: str | None) -> str:
    return _entry_at(object_class_entry(class_id), 1)


def object_class_name(class_id: str | None) -> str:
    """객체 클래스 명칭. 테이블에 없으면 빈 문자열."""
    return _pick_name(object_class_entry(class_id))


def is_known_class(class_id: str | None) -> bool:
    return object_class_entry(class_id) is not None


_SITUATION_RE = re.compile(r"^(SO|Y|N|C)-?(\d+)?$", re.IGNORECASE)


def situation_prefix(situation_id: str | None) -> str | None:
    """'N-12' -> 'N'. pandas NaN 처럼 문자열이 아닌 값도 그냥 None 으로 흘린다."""
    if not isinstance(situation_id, str) or not situation_id:
        return None
    m = _SITUATION_RE.match(situation_id.strip())
    if not m:
        return None
    return m.group(1).upper()


def counterpart(situation_id: str | None) -> str | None:
    """Y-03 <-> N-03. 쌍이 없는 코드면 None."""
    if not isinstance(situation_id, str) or "-" not in situation_id:
        return None
    prefix = situation_prefix(situation_id)
    if prefix not in ("Y", "N"):
        return None
    number = situation_id.split("-", 1)[1]
    return f"{'N' if prefix == 'Y' else 'Y'}-{number}"


def scenario_entry(situation_id: str | None) -> tuple | None:
    """(type_id, process_id, 설명). 테이블에 없으면 None."""
    if not isinstance(situation_id, str):
        return None
    entry = SCENARIOS.get(situation_id.strip().upper())
    return entry if isinstance(entry, (tuple, list)) else None


def scenario_description(situation_id: str | None) -> str:
    """시나리오 설명. 테이블에 없으면 빈 문자열."""
    return _pick_name(scenario_entry(situation_id))


def scenario_type_id(situation_id: str | None) -> str:
    """tables.py 가 규정한 사고유형. 데이터의 type_ID 와 다르면 이상 신호."""
    return _entry_at(scenario_entry(situation_id), 0)


def scenario_process_id(situation_id: str | None) -> str:
    return _entry_at(scenario_entry(situation_id), 1)


def is_known_situation(situation_id: str | None) -> bool:
    return scenario_entry(situation_id) is not None


def accident_type_name(type_id: str | None) -> str:
    return ACCIDENT_TYPE_KO.get(str(type_id).strip(), "") if type_id else ""


def process_name(process_id: str | None) -> str:
    return PROCESS_KO.get(str(process_id).strip(), "") if process_id else ""


def device_name(device: str | None) -> str:
    return DEVICE_KO.get(str(device).strip(), "") if device else ""


def weather_name(code: str | None) -> str:
    return WEATHER_KO.get(str(code).strip(), "") if code else ""


def site_condition_name(code: str | None) -> str:
    return SITE_CONDITION_KO.get(str(code).strip(), "") if code else ""


def safety_pairs() -> list[tuple[str, str, str]]:
    """OBJECT_SAFETY_PAIRS 를 (정상 class_id, 비정상 class_id, 판정항목) 으로 정규화.

    테이블이 class_id 대신 명칭을 담고 있어도 명칭->ID 로 되돌려 준다.
    """
    by_name = {
        object_class_name(cid): cid for cid in OBJECT_CLASSES if object_class_name(cid)
    }

    def resolve(token: Any) -> str:
        text = token.strip() if isinstance(token, str) else str(token)
        if text.upper() in OBJECT_CLASSES:
            return text.upper()
        return by_name.get(text, text)

    out: list[tuple[str, str, str]] = []
    for entry in OBJECT_SAFETY_PAIRS or []:
        if isinstance(entry, dict):
            items = [entry.get("정상"), entry.get("비정상"), entry.get("판정항목")]
        elif isinstance(entry, (tuple, list)):
            items = list(entry) + [""] * (3 - len(entry))
        else:
            continue
        normal, abnormal, item = items[0], items[1], items[2]
        out.append(
            (
                resolve(normal),
                resolve(abnormal),
                item.strip() if isinstance(item, str) else str(item or ""),
            )
        )
    return out


# ================================================================ dataclass
def _s(value: Any) -> str | None:
    """코드값을 문자열로 정규화. 2 든 "2" 든 "2" 로 통일한다."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


def _i(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@dataclass
class RawDataInfo:
    Raw_data_ID: str | None = None
    Location_ID: int | None = None
    type_ID: str | None = None
    Type_Description: str | None = None
    process_ID: str | None = None
    Resolution: str | None = None
    Date: str | None = None
    Main_class_ID: str | None = None
    Main_item_ID: str | None = None
    Situation_ID: str | None = None
    GPS: str | None = None
    Site_Condition: str | None = None
    Weather_Information: str | None = None
    Wind_Speed: str | None = None
    device: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "RawDataInfo":
        d = d or {}
        return cls(
            Raw_data_ID=_s(d.get("Raw_data_ID")),
            Location_ID=_i(d.get("Location_ID")),
            type_ID=_s(d.get("type_ID")),
            Type_Description=_s(d.get("Type_Description")),
            process_ID=_s(d.get("process_ID")),
            Resolution=_s(d.get("Resolution")),
            Date=_s(d.get("Date")),
            Main_class_ID=_s(d.get("Main_class_ID")),
            Main_item_ID=_s(d.get("Main_item_ID")),
            Situation_ID=_s(d.get("Situation_ID")),
            GPS=_s(d.get("GPS")),
            Site_Condition=_s(d.get("Site_Condition")),
            Weather_Information=_s(d.get("Weather_Information")),
            Wind_Speed=_s(d.get("Wind_Speed")),
            device=_s(d.get("device")),
        )

    # ---- 코드값 -> 한글 ----
    @property
    def accident_type_ko(self) -> str | None:
        return ACCIDENT_TYPE_KO.get(self.type_ID or "")

    @property
    def situation_type(self) -> str | None:
        return situation_prefix(self.Situation_ID)

    @property
    def situation_type_ko(self) -> str | None:
        return SITUATION_TYPE_KO.get(self.situation_type or "")

    @property
    def site_condition_ko(self) -> str | None:
        return SITE_CONDITION_KO.get(self.Site_Condition or "")

    @property
    def weather_ko(self) -> str | None:
        return WEATHER_KO.get(self.Weather_Information or "")

    @property
    def device_ko(self) -> str | None:
        return DEVICE_KO.get(self.device or "")

    @property
    def resolution_wh(self) -> tuple[int, int] | None:
        if not self.Resolution:
            return None
        parts = [p.strip() for p in self.Resolution.replace("x", ",").split(",")]
        if len(parts) != 2:
            return None
        w, h = _i(parts[0]), _i(parts[1])
        return (w, h) if w and h else None

    @property
    def gps_latlon(self) -> tuple[float, float] | None:
        if not self.GPS:
            return None
        parts = [p.strip() for p in self.GPS.split(",")]
        if len(parts) != 2:
            return None
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None


@dataclass
class SourceDataInfo:
    Source_Data: str | None = None
    Extraction_Time: str | None = None
    File_extension: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "SourceDataInfo":
        d = d or {}
        return cls(
            Source_Data=_s(d.get("Source_Data")),
            Extraction_Time=_s(d.get("Extraction_Time")),
            File_extension=_s(d.get("File_extension")),
        )


@dataclass
class Annotation:
    """Annotations 배열의 한 원소.

    class_ID 는 객체 클래스(WO/SO/DO-nn)일 수도, 시나리오 ID(N-12)일 수도 있다.
    시나리오 ID 로 달린 bbox 가 그 장면의 위험 상황 영역이다.
    """

    class_ID: str | None = None
    type: str | None = None
    item_ID: str | None = None
    value: Any = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Annotation":
        d = d or {}
        return cls(
            class_ID=_s(d.get("class_ID")),
            type=_s(d.get("type")),
            item_ID=_s(d.get("item_ID")),
            value=d.get("value"),
        )

    @property
    def group(self) -> str | None:
        """'WO' / 'SO' / 'DO'. 시나리오 어노테이션이면 None."""
        return object_class_group(self.class_ID)

    @property
    def is_situation(self) -> bool:
        """시나리오 ID(Y/N/C-nn)로 달린 어노테이션인지."""
        return self.group is None and situation_prefix(self.class_ID) is not None


@dataclass
class LearningDataInfo:
    Path: str | None = None
    Json_Data_ID: str | None = None
    Annotations: list[Annotation] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LearningDataInfo":
        d = d or {}
        anns = d.get("Annotations") or []
        return cls(
            Path=_s(d.get("Path")),
            Json_Data_ID=_s(d.get("Json_Data_ID")),
            Annotations=[Annotation.from_dict(a) for a in anns if isinstance(a, dict)],
        )


@dataclass
class LabelRecord:
    """JSON 파일 한 건 전체."""

    raw: RawDataInfo = field(default_factory=RawDataInfo)
    source: SourceDataInfo = field(default_factory=SourceDataInfo)
    learning: LearningDataInfo = field(default_factory=LearningDataInfo)
    source_file: str | None = None  # 원본 JSON 경로 (디버깅용)

    @classmethod
    def from_dict(
        cls, d: dict[str, Any], source_file: str | None = None
    ) -> "LabelRecord":
        return cls(
            raw=RawDataInfo.from_dict(d.get(K_RAW)),
            source=SourceDataInfo.from_dict(d.get(K_SOURCE)),
            learning=LearningDataInfo.from_dict(d.get(K_LEARNING)),
            source_file=source_file,
        )

    # ---- 집계용 평탄화 ----
    def to_row(self) -> dict[str, Any]:
        """DataFrame 한 행. 어노테이션은 개수/클래스 목록으로 요약한다."""
        r = self.raw
        classes = [a.class_ID for a in self.learning.Annotations if a.class_ID]
        object_classes = [c for c in classes if object_class_group(c)]
        res = r.resolution_wh
        return {
            "json_id": self.learning.Json_Data_ID or self.source.Source_Data,
            "raw_data_id": r.Raw_data_ID,
            "source_file": self.source_file,
            "path": self.learning.Path,
            "situation_id": r.Situation_ID,
            "situation_type": r.situation_type,
            "situation_type_ko": r.situation_type_ko,
            "scenario_desc": scenario_description(r.Situation_ID),
            "known_situation": is_known_situation(r.Situation_ID),
            "type_id": r.type_ID,
            "type_ko": r.accident_type_ko or r.Type_Description,
            "process_id": r.process_ID,
            "process_ko": process_name(r.process_ID),
            # tables.py 가 규정한 값. 데이터와 다르면 코드 불일치 신호다.
            "expected_type_id": scenario_type_id(r.Situation_ID),
            "expected_process_id": scenario_process_id(r.Situation_ID),
            "location_id": r.Location_ID,
            "device": r.device,
            "device_ko": r.device_ko,
            "site_condition": r.Site_Condition,
            "site_condition_ko": r.site_condition_ko,
            "weather": r.Weather_Information,
            "weather_ko": r.weather_ko,
            "wind_speed": r.Wind_Speed,
            "date": r.Date,
            "gps": r.GPS,
            "main_class_id": r.Main_class_ID,
            "main_item_id": r.Main_item_ID,
            "width": res[0] if res else None,
            "height": res[1] if res else None,
            "file_ext": self.source.File_extension,
            "extraction_time": self.source.Extraction_Time,
            "n_annotations": len(self.learning.Annotations),
            "n_object_annotations": len(object_classes),
            "class_ids": "|".join(sorted(set(object_classes))),
        }

    def object_rows(self) -> Iterable[dict[str, Any]]:
        """클래스 등장 빈도 집계용 롱 포맷."""
        json_id = self.learning.Json_Data_ID or self.source.Source_Data
        for a in self.learning.Annotations:
            yield {
                "json_id": json_id,
                "situation_id": self.raw.Situation_ID,
                "type_id": self.raw.type_ID,
                "class_id": a.class_ID,
                "class_group": a.group,
                "class_group_ko": OBJECT_CLASS_GROUP_KO.get(a.group or ""),
                "class_major": object_class_major(a.class_ID),
                "class_minor": object_class_minor(a.class_ID),
                "class_name": object_class_name(a.class_ID),
                "known_class": is_known_class(a.class_ID),
                "item_id": a.item_ID,
                "shape": a.type,
                "is_situation": a.is_situation,
            }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# DataFrame 컬럼 순서 고정 (빈 데이터에서도 스키마가 유지되도록)
LABEL_COLUMNS: tuple[str, ...] = tuple(LabelRecord().to_row().keys())
OBJECT_COLUMNS: tuple[str, ...] = (
    "json_id",
    "situation_id",
    "type_id",
    "class_id",
    "class_group",
    "class_group_ko",
    "class_major",
    "class_minor",
    "class_name",
    "known_class",
    "item_id",
    "shape",
    "is_situation",
)
