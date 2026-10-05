"""위험성(빈도 × 강도) 산정 규칙.

위험성평가표의 숫자는 임의로 정하면 문서 전체가 신뢰를 잃는다. 그래서 이 파일은
규칙과 근거만 담고, 모든 값이 어디서 나왔는지 산정 근거 문자열로 되돌려준다.

────────────────────────────────────────────────────────────────────────
척도: 3 x 3 (빈도 1~3 x 강도 1~3 = 위험성 1~9)
────────────────────────────────────────────────────────────────────────
KRAS(위험성평가 지원시스템)의 기본 척도가 3x3 이고, 5x5 를 쓰려면 작업 높이·중량·
노출 시간 같은 정량 정보가 필요하다. 우리 입력은 사진 1장과 시나리오 ID 뿐이라
5단계를 지탱할 근거가 없다. 과도한 해상도는 없는 정밀도를 꾸며내는 것이므로 3x3 을 쓴다.

────────────────────────────────────────────────────────────────────────
강도(S) — 재해가 일어났을 때의 중대성. 사고유형으로 결정한다
────────────────────────────────────────────────────────────────────────
**본 데이터셋은 5대 중대재해 유형만 포함하므로 강도(S)는 전 유형 3으로 균일하다.
등급 변별은 빈도(F)에서 발생한다. 경미 재해 유형이 포함된 일반 현장으로 확장하면
강도가 변별력을 갖는다.**

이 사실을 숨기지 않는다. 강도가 상수인 표를 "강도도 평가했다"고 내놓으면 심사에서
바로 드러난다. 다섯 유형 전부 산업안전보건법상 중대재해(사망 1명 이상 등)로 이어질
수 있어 3 이 되는 것이고, 그래서 변별은 빈도가 담당한다.

  추락 3 — 건설업 사고사망 1위 유형(떨어짐). 고소에서의 추락은 사망에 직결된다.
  협착 3 — 중장비 끼임은 사망·절단으로 이어진다. 2차 방어수단이 없다.
  전도 3 — 중장비·가설물 전도는 깔림 사망으로 이어진다.
  화재 3 — 화상·질식. 밀폐·실내에서는 다수 피해가 발생한다.
  낙하 3 — 산안법상 '물체에 맞음'도 중대재해 유형이다. 안전모·방호선반이 2차
           방어로 작동한다는 이유로 2 를 주는 안을 검토했으나 채택하지 않았다.
           (1) 안전 문서에서 위험을 낮게 잡는 방향의 오류는 허용되기 어렵다.
               안전모가 있어도 중량물 낙하는 사망 사고다.
           (2) 2차 방어수단의 존재를 강도 감점 근거로 쓰려면 그 완화 효과를 측정한
               자료가 있어야 한다. 지금 우리에겐 없다.
           (3) "왜 낙하만 낮은가"에 답할 수 없다.
           이 논리는 아래 confidence 를 점수에서 뺀 것과 같은 방향이다.

한계: 작업 높이와 중량 정보가 입력에 없어 강도를 더 세분할 수 없다. 사진만으로
2 층과 10 층을 구분하지 않으므로, 고층 작업이 많은 현장은 강도를 상향 조정해야 한다.
SEVERITY_BY_TYPE 은 설정으로 남겨 두었으니 현장 성격에 맞게 조정할 수 있다.

────────────────────────────────────────────────────────────────────────
빈도(F) — 그 상태가 재해로 이어질 가능성. 시나리오 설명 문구로 결정한다
────────────────────────────────────────────────────────────────────────
tables.SCENARIOS 의 N-nn 설명은 "무엇이 어떻게 잘못되었는지"를 일정한 어법으로
적고 있다(미배치 / 불량 / 위험 반경 내 작업 ...). 50종 전수를 확인해 세 갈래로
갈랐고, 아래 키워드로 기계적으로 분류한다.

  F=3 상시·즉시 노출 (26종)
      · 방호장치·신호수 부재: "미배치", "미설치", "미사용"
        → 없으면 작업 시간 전체가 노출 구간이다. 방어가 0 이다.
      · 근로자가 이미 위험구역 안: "위험 반경 내", "동시작업", "최상단에서 작업",
        "열려 있는"
        → 추가 조건 없이 현재 상태에서 바로 재해가 가능하다.
      · 불안정 승강설비: "간이 사다리"
        → 비계 발판 위 사다리는 디딤면 자체가 불안정해 올라서는 즉시 노출된다.

  F=2 조건부 노출 (24종)
      · 설비 불량·미준수: "불량", "미흡", "미준수", "부적절"
        → 설비가 존재해 부분적으로 기능한다. 하중·각도 등 특정 조건에서 실패한다.
      · 불안정 적재·설치: "과적재", "적치", "적재물 위", "단부 위", "위험물 배치"
        → 붕괴·낙하하려면 외력이나 추가 동작이 필요하다.
      · 가연물·열원 근접: "인화성", "난로", "산소절단기"
        → 착화원이 실제로 작동해야 재해가 된다.

  F=1 은 규칙으로 부여하지 않는다. 이 데이터의 N 시나리오는 모두 "이미 발생한
  불안전 상태"를 찍은 것이므로 가능성이 낮다고 볼 근거가 없다. 분류가 안 되는
  문구는 F=2(기본값)로 두고 unmatched 로 표시해 사람이 확인하게 한다.

────────────────────────────────────────────────────────────────────────
confidence 는 위험성 점수에 넣지 않는다 (설계 판단)
────────────────────────────────────────────────────────────────────────
VLM 의 confidence 를 빈도에 곱하자는 접근을 검토했으나 채택하지 않았다. 둘은 다른
것을 재는 값이다.

  · confidence = "모델이 이 상태를 맞게 봤는가"의 불확실성 (측정의 신뢰도)
  · 빈도       = "이 상태가 재해로 이어질 가능성" (위험의 속성)

곱하면 "모델이 확신하지 못하니 위험이 낮다"는 결론이 나온다. 안전 문서에서 이 방향의
오류는 허용되지 않는다. 확신이 없을 때 필요한 것은 점수를 깎는 게 아니라 사람이
현장에서 확인하는 것이다. 그래서 confidence 는 신뢰도 등급과 '검토필요' 플래그로
분리해 내보낸다. 위험성 점수는 상태가 사실이라는 가정 위에서 산정한다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schema  # noqa: E402  (tables.py 를 단일 출처로 읽는다)

SCALE = 3  # 3 x 3

# ---------------------------------------------------------------- 강도
# 5대 중대재해 유형이라 전부 3 이다(균일). 변별은 빈도가 담당한다.
# 경미 재해 유형이 포함된 일반 현장으로 확장하면 이 표가 변별력을 갖는다.
SEVERITY_BY_TYPE: dict[str, int] = {
    "A": 3,  # 추락
    "B": 3,  # 낙하 — 2 안을 검토했으나 기각 (모듈 docstring 참고)
    "C": 3,  # 협착
    "D": 3,  # 화재
    "E": 3,  # 전도
}
SEVERITY_DEFAULT = 2  # F(주의요망)·G(안전보조장비) 등 5대 유형이 아닌 경우

SEVERITY_REASON: dict[str, str] = {
    "A": "건설업 사고사망 1위 유형(떨어짐). 고소 추락은 사망 직결",
    "B": "산안법상 '물체에 맞음' 중대재해 유형. 중량물 낙하는 보호구로 막히지 않음",
    "C": "중장비 끼임은 사망·절단으로 직결, 2차 방어수단 없음",
    "D": "화상·질식. 실내·밀폐 공간에서는 다수 피해",
    "E": "중장비·가설물 전도는 깔림 사망으로 직결",
}

# 강도가 상수임을 리포트에 그대로 싣기 위한 문장
SEVERITY_UNIFORM_NOTE = (
    "본 데이터셋은 5대 중대재해 유형만 포함하므로 강도(S)는 전 유형 3으로 균일하다. "
    "등급 변별은 빈도(F)에서 발생한다. 경미 재해 유형이 포함된 일반 현장으로 "
    "확장하면 강도가 변별력을 갖는다."
)

# ---------------------------------------------------------------- 빈도
# (키워드, 빈도, 분류명, 근거)
FREQUENCY_RULES: tuple[tuple[tuple[str, ...], int, str, str], ...] = (
    (
        ("미배치", "미설치", "미사용"),
        3,
        "방호장치·신호수 부재",
        "방호수단이 없어 작업 시간 전체가 노출 구간",
    ),
    (
        ("위험 반경 내", "동시작업", "최상단에서 작업", "열려 있는"),
        3,
        "근로자가 위험구역 내",
        "추가 조건 없이 현재 상태에서 재해 가능",
    ),
    (
        ("간이 사다리",),
        3,
        "불안정 승강설비 사용",
        "비계 발판 위 사다리는 디딤면이 불안정해 올라서는 즉시 추락에 노출",
    ),
    (
        ("불량", "미흡", "미준수", "부적절"),
        2,
        "설비 불량·기준 미준수",
        "설비가 존재해 부분 기능. 하중·각도 등 특정 조건에서 실패",
    ),
    (
        ("과적재", "적치", "적재물 위", "단부 위", "위험물 배치"),
        2,
        "불안정 적재·설치",
        "붕괴·낙하에 외력이나 추가 동작이 필요",
    ),
    (
        ("인화성", "난로", "산소절단기"),
        2,
        "가연물·열원 근접",
        "착화원이 실제로 작동해야 재해로 진행",
    ),
)
FREQUENCY_DEFAULT = 2
FREQUENCY_DEFAULT_REASON = "설명 문구가 규칙에 걸리지 않음 — 사람이 확인 필요"

# ------------------------------------------------------- 등급 / 조치 기한
# KRAS 3x3 기준. 1~2 낮음 / 3~4 보통 / 6~9 높음
GRADE_BANDS: tuple[tuple[int, str, str], ...] = (
    (6, "높음", "즉시 개선 (3일 내)"),
    (3, "보통", "계획 개선 (30일 내)"),
    (0, "낮음", "현행 유지 (수시 점검)"),
)

# confidence -> 신뢰도 등급. 검토필요 경계는 0.7
CONFIDENCE_BANDS: tuple[tuple[float, str], ...] = ((0.8, "상"), (0.6, "중"), (0.0, "하"))
REVIEW_THRESHOLD = 0.7


@dataclass
class RiskScore:
    frequency: int
    severity: int
    risk: int
    grade: str
    action_deadline: str
    frequency_class: str
    frequency_reason: str
    severity_reason: str
    rule_matched: bool
    confidence_grade: str
    needs_review: bool
    review_reason: str

    @property
    def formula(self) -> str:
        return f"{self.frequency}×{self.severity}={self.risk} ({self.grade})"

    @property
    def basis(self) -> str:
        """산정 근거 한 줄. 표에 그대로 싣는다."""
        return (
            f"빈도 {self.frequency}={self.frequency_class}: {self.frequency_reason} / "
            f"강도 {self.severity}: {self.severity_reason}"
        )


def severity(type_id: str | None) -> tuple[int, str]:
    key = (type_id or "").strip().upper()
    if key in SEVERITY_BY_TYPE:
        return SEVERITY_BY_TYPE[key], SEVERITY_REASON[key]
    name = schema.accident_type_name(key) or "미분류"
    return SEVERITY_DEFAULT, f"5대 사고유형이 아님({name}) — 기본값 적용"


def frequency(description: str | None) -> tuple[int, str, str, bool]:
    """시나리오 설명 문구로 빈도를 정한다. (빈도, 분류명, 근거, 규칙적용여부)."""
    text = description or ""
    for keywords, value, label, reason in FREQUENCY_RULES:
        hit = next((k for k in keywords if k in text), None)
        if hit:
            return value, label, f'{reason} (키워드 "{hit}")', True
    return FREQUENCY_DEFAULT, "미분류", FREQUENCY_DEFAULT_REASON, False


def grade(risk: int) -> tuple[str, str]:
    for threshold, label, deadline in GRADE_BANDS:
        if risk >= threshold:
            return label, deadline
    return GRADE_BANDS[-1][1], GRADE_BANDS[-1][2]


def confidence_grade(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "미상"
    for threshold, label in CONFIDENCE_BANDS:
        if number >= threshold:
            return label
    return "하"


def score(
    situation_id: str | None,
    *,
    confidence: object = None,
    judgment: str | None = None,
    type_id_override: str | None = None,
) -> RiskScore:
    """시나리오 ID 하나에 대한 위험성 산정.

    type_id 와 설명은 tables.py 에서 끌어온다. 데이터의 type_ID 가 테이블과 다르면
    type_id_override 로 넘길 수 있다(실측에서는 불일치가 없었다).
    """
    type_id = type_id_override or schema.scenario_type_id(situation_id)
    description = schema.scenario_description(situation_id)

    freq, freq_class, freq_reason, matched = frequency(description)
    sev, sev_reason = severity(type_id)
    risk = freq * sev
    grade_label, deadline = grade(risk)

    conf_grade = confidence_grade(confidence)
    review_reasons: list[str] = []
    if judgment == "판단불가":
        review_reasons.append("모델이 판단불가로 응답")
    try:
        if confidence is not None and float(confidence) < REVIEW_THRESHOLD:  # type: ignore[arg-type]
            review_reasons.append(f"confidence {float(confidence):.2f} < {REVIEW_THRESHOLD}")
    except (TypeError, ValueError):
        review_reasons.append("confidence 값 없음")
    if not matched:
        review_reasons.append("빈도 규칙 미적용")

    return RiskScore(
        frequency=freq,
        severity=sev,
        risk=risk,
        grade=grade_label,
        action_deadline=deadline,
        frequency_class=freq_class,
        frequency_reason=freq_reason,
        severity_reason=sev_reason,
        rule_matched=matched,
        confidence_grade=conf_grade,
        needs_review=bool(review_reasons),
        review_reason="; ".join(review_reasons),
    )


def rule_table() -> list[dict[str, object]]:
    """규칙을 표로 — 보고서 부록과 검토용."""
    rows: list[dict[str, object]] = []
    for keywords, value, label, reason in FREQUENCY_RULES:
        rows.append(
            {
                "축": "빈도",
                "값": value,
                "분류": label,
                "키워드": ", ".join(keywords),
                "근거": reason,
            }
        )
    rows.append(
        {"축": "빈도", "값": FREQUENCY_DEFAULT, "분류": "미분류(기본)", "키워드": "-", "근거": FREQUENCY_DEFAULT_REASON}
    )
    for code, value in SEVERITY_BY_TYPE.items():
        rows.append(
            {
                "축": "강도",
                "값": value,
                "분류": f"{code} {schema.accident_type_name(code)}",
                "키워드": "-",
                "근거": SEVERITY_REASON[code],
            }
        )
    return rows


def coverage() -> dict[str, object]:
    """N 시나리오 50종에 규칙이 얼마나 적용되는지 — 규칙 품질 점검용."""
    matched: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for sid in schema.DEFINED_SITUATION_IDS:
        if not sid.startswith("N-"):
            continue
        freq, label, _reason, ok = frequency(schema.scenario_description(sid))
        if ok:
            matched.setdefault(f"F={freq} {label}", []).append(sid)
        else:
            unmatched.append(sid)
    return {
        "총": sum(len(v) for v in matched.values()) + len(unmatched),
        "적용": {k: len(v) for k, v in sorted(matched.items())},
        "미적용": unmatched,
    }


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    print("위험성 산정 규칙 (3x3)")
    print("=" * 72)
    for row in rule_table():
        print(f"  {row['축']} {row['값']}  {row['분류']:<22} {row['근거']}")

    cov = coverage()
    print(f"\nN 시나리오 {cov['총']}종 규칙 적용 현황")
    for label, count in cov["적용"].items():  # type: ignore[union-attr]
        print(f"  {label:<28} {count:>3}종")
    print(f"  미적용 {len(cov['미적용'])}종 {cov['미적용']}")  # type: ignore[arg-type]

    print("\n예시")
    for sid in ("N-03", "N-19", "N-11", "N-33", "N-50"):
        s = score(sid, confidence=0.85)
        print(f"  {sid} {schema.scenario_description(sid)}")
        print(f"      {s.formula}  {s.action_deadline}")
        print(f"      {s.basis}")
