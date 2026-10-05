"""VLM 프롬프트 정의.

과제 구분 (이 프로젝트에서 쓰는 이름):
    Task A  관찰      사진에 보이는 객체·상황을 사실만 기술       (미구현)
    Task B  판정      안전 상태가 정상인지 비정상인지 판정        <- 지금 구현
    Task C  평가표    위험성평가표 초안 생성                     (미구현)

Task B 가 평가의 핵심이다. AI-Hub 데이터의 Y-nn / N-nn 쌍이 같은 작업을 안전/불량
상태로 찍은 대조쌍이므로, 정답이 쌍으로 주어지는 유일한 지표이기 때문이다.

프롬프트를 고칠 때는 PROMPT_VERSION 을 올린다. 결과 jsonl 에 버전이 기록되므로
나중에 어느 프롬프트로 뽑은 숫자인지 추적할 수 있다.
"""

from __future__ import annotations

PROMPT_VERSION = "task_b.v1"

# 판정 라벨. 데이터의 Y/N 과 대응시키려면 이 문자열을 그대로 받아야 한다.
JUDGMENT_NORMAL = "정상"
JUDGMENT_ABNORMAL = "비정상"
JUDGMENT_UNKNOWN = "판단불가"
JUDGMENTS = (JUDGMENT_NORMAL, JUDGMENT_ABNORMAL, JUDGMENT_UNKNOWN)

# 5대 사고유형 + 해당 없음. tables.ACCIDENT_TYPES 의 한글명과 맞춰 둔다.
HAZARD_TYPES = ("추락", "낙하", "협착", "화재", "전도", "해당 없음")

TASK_B_SYSTEM = """당신은 건설현장 안전점검을 수행하는 산업안전 전문가다.
현장 사진 한 장을 보고 그 장면의 안전 상태가 정상인지 비정상인지 판정한다.

판정 기준:
- 정상: 필요한 안전조치(안전난간, 덮개, 방호장치, 보호구, 소화기, 신호수 배치 등)가
  갖춰져 있고 위험요인이 관리되고 있다.
- 비정상: 안전조치가 없거나 불량하거나, 작업 방식 자체가 위험하다.
- 판단불가: 화질·각도·가림 때문에 안전조치의 유무를 확인할 수 없다.

반드시 지킬 것:
1. 사진에서 실제로 보이는 것만 근거로 삼는다. 보이지 않는 것을 추측하지 않는다.
2. observed 에는 눈에 보이는 사실만 적는다. 판단이나 추측을 섞지 않는다.
3. reason 에는 그 사실이 왜 정상/비정상인지 적는다.
4. 확인할 수 없으면 비정상으로 몰지 말고 판단불가를 쓴다.
5. confidence 는 판정의 확신도다. 애매하면 낮게 준다. 전부 높게 주면 쓸모가 없다."""

TASK_B_USER = """이 건설현장 사진의 안전 상태를 판정하라.

- judgment: "정상", "비정상", "판단불가" 중 하나
- confidence: 0.0 ~ 1.0 (판정 확신도)
- observed: 사진에 보이는 사실 (한국어 2문장 이내)
- reason: 판정 근거 (한국어 2문장 이내)
- hazard_type: 관련 사고유형 — "추락", "낙하", "협착", "화재", "전도", "해당 없음" 중 하나
- missing_measure: 비정상이면 빠진 안전조치, 아니면 빈 문자열"""

# Gemini responseSchema. 구조화 출력을 쓰면 JSON 파싱 실패가 거의 사라진다.
# 자유 형식에서의 파싱 안정성을 보려면 run_eval.py --no-schema 로 끈다.
TASK_B_SCHEMA = {
    "type": "object",
    "properties": {
        "judgment": {"type": "string", "enum": list(JUDGMENTS)},
        "confidence": {"type": "number"},
        "observed": {"type": "string"},
        "reason": {"type": "string"},
        "hazard_type": {"type": "string", "enum": list(HAZARD_TYPES)},
        "missing_measure": {"type": "string"},
    },
    "required": ["judgment", "confidence", "observed", "reason", "hazard_type"],
    "propertyOrdering": [
        "judgment",
        "confidence",
        "observed",
        "reason",
        "hazard_type",
        "missing_measure",
    ],
}

# 스키마를 끌 때는 JSON 으로만 답하라고 본문에서 지시해야 한다.
TASK_B_JSON_HINT = """

다른 설명 없이 아래 형식의 JSON 객체만 출력하라.
{"judgment": "...", "confidence": 0.0, "observed": "...", "reason": "...", "hazard_type": "...", "missing_measure": "..."}"""


def task_b(use_schema: bool = True) -> dict:
    """Task B 프롬프트 묶음.

    연출 촬영 데이터라는 사실을 프롬프트에 알리지 않는다. 모델이 장면을 그대로
    판정하는지, 아니면 "훈련용 사진 같다"는 식으로 빗겨나가는지를 먼저 관찰하기
    위해서다. 관찰 결과에 따라 v2 에서 지시를 추가할지 결정한다.
    """
    return {
        "version": PROMPT_VERSION,
        "system": TASK_B_SYSTEM,
        "user": TASK_B_USER if use_schema else TASK_B_USER + TASK_B_JSON_HINT,
        "schema": TASK_B_SCHEMA if use_schema else None,
    }


# 정답(Y/N) 과 모델 판정을 맞춰 보기 위한 매핑
TRUTH_NORMAL = JUDGMENT_NORMAL
TRUTH_ABNORMAL = JUDGMENT_ABNORMAL


def truth_label(is_normal: object) -> str:
    """labels 의 is_normal(True/False/None) -> 정답 라벨."""
    if is_normal is True:
        return TRUTH_NORMAL
    if is_normal is False:
        return TRUTH_ABNORMAL
    return ""  # C/SO 계열은 정상/비정상 판정 대상이 아니다
