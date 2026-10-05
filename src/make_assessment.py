"""Task C — Task B 판정 결과를 모아 위험성평가표 초안을 만든다.

    python src/make_assessment.py --run eval_smoke_task_b.v1
    python src/make_assessment.py --run ... --profile site.json --merge
    python src/make_assessment.py --demo          # 가짜 Task B 결과로 샘플 출력

핵심 — Y/N 쌍이 그대로 "현재 상태 → 목표 상태"가 된다:

    N-03 (개구부 덮개 설치 불량)  판정
      유해위험요인 = SCENARIOS["N-03"] 설명
      개선대책     = SCENARIOS["Y-03"] 설명        <- tables.scenario_pair() 로 유도
      사고유형·공정 = SCENARIOS["N-03"] 의 type_id / process_id

문구를 새로 짓지 않는다. 모든 문장은 tables.py 에 있는 것을 그대로 쓰고, 모델이
생성한 것은 "현재 안전조치"(Task B 의 observed)뿐이다. 그래서 평가표의 어느 칸이
사람이 정의한 것이고 어느 칸이 모델 출력인지 구분된다.

산출물 (data/processed/assessment/):
    <run>_assessment.csv    평가표 (엑셀에서 바로 열림)
    <run>_assessment.txt    사람이 읽는 표 + 산정 규칙 부록
    <run>_rules.csv         위험성 산정 규칙표 (검토용)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import risk_scoring  # noqa: E402
import schema  # noqa: E402

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    raise SystemExit("pandas 가 필요하다: pip install -r requirements.txt")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

RUN_DIR = config.PROCESSED_DIR / "eval_runs"
OUT_DIR = config.PROCESSED_DIR / "assessment"

# 사업장 프로파일 최소 필드. 지금은 표 머리글과 담당자 칸을 채우는 데만 쓴다.
DEFAULT_PROFILE: dict[str, Any] = {
    "site_name": "(사업장명 미입력)",
    "industry": "건설업",
    "process": "",          # 비우면 시나리오의 공정을 행별로 쓴다
    "worker_count": None,
    "assessor": "(평가자 미입력)",
    "assessed_on": "",      # 비우면 오늘
}

# 평가표 열 순서. KRAS 서식에 맞추는 작업은 나중이고, 지금은 구조만 잡는다.
COLUMNS = [
    "번호",
    "공정",
    "유해위험요인",
    "사고유형",
    "현재 안전조치",
    "빈도",
    "강도",
    "위험성",
    "위험등급",
    "산정근거",
    "개선대책",
    "담당",
    "기한",
    "근거",
    "신뢰도",
    "검토필요",
    "검토사유",
    "사진",
    "판정source",
]


def load_profile(path: Path | None) -> dict[str, Any]:
    profile = dict(DEFAULT_PROFILE)
    if path:
        if not path.exists():
            raise SystemExit(f"{path} 가 없다.")
        profile.update(json.loads(path.read_text(encoding="utf-8")))
    if not profile.get("assessed_on"):
        profile["assessed_on"] = date.today().isoformat()
    return profile


def load_judgments(run: str) -> list[dict[str, Any]]:
    """run_eval.py 가 남긴 jsonl 을 읽는다."""
    path = RUN_DIR / f"{run}.jsonl"
    if not path.exists():
        available = sorted(p.stem for p in RUN_DIR.glob("*.jsonl"))
        raise SystemExit(f"{path} 가 없다. 있는 실행: {available or '(없음)'}")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def demo_judgments() -> list[dict[str, Any]]:
    """API 호출 없이 구조를 보기 위한 가짜 Task B 결과.

    observed 문구만 사람이 쓴 것이고(모델이 낼 자리), 나머지는 실제 코드값이다.
    """
    samples = [
        ("N-03", 0.92, "콘크리트 슬래브에 개구부가 있고 덮개가 한쪽으로 밀려 개구부 일부가 열려 있다. 주변에 안전난간은 없다."),
        ("N-19", 0.88, "이동식 크레인이 자재를 인양 중이고 붐 아래에 작업자 2명이 서 있다. 신호수로 보이는 인원은 없다."),
        ("N-33", 0.95, "용접기가 가동 중이며 불꽃이 발생하고 있다. 반경 5m 내에 소화기가 보이지 않는다."),
        ("N-11", 0.61, "시스템 비계 작업발판 위에 각재와 공구가 쌓여 있고 일부가 발판 바깥으로 나와 있다."),
        ("N-48", 0.74, "오거 크레인이 천공 작업 중이고 선회 반경 내에 작업자가 있다. 신호수는 확인되지 않는다."),
        ("N-05", 0.45, "건물 외벽 단부에 안전난간 지주는 세워져 있으나 중간대가 일부 누락돼 있다. 화질이 낮아 전체 구간은 확인 어렵다."),
    ]
    records = []
    for i, (sid, conf, observed) in enumerate(samples):
        records.append(
            {
                "image_file": f"DEMO_{sid}_{i:03d}.jpg",
                "situation_id": sid,
                "truth": "비정상",
                "type_id": schema.scenario_type_id(sid),
                "ok": True,
                "parsed": {
                    "judgment": "비정상",
                    "confidence": conf,
                    "observed": observed,
                    "reason": f"{schema.scenario_description(sid)} 상태로 확인된다.",
                    "hazard_type": schema.accident_type_name(schema.scenario_type_id(sid)),
                    "missing_measure": "",
                },
                "model": "(demo)",
                "prompt_version": "(demo)",
            }
        )
    # 판단불가 1건 — 검토필요 행이 어떻게 나오는지 보기 위해
    records.append(
        {
            "image_file": "DEMO_N-14_900.jpg",
            "situation_id": "N-14",
            "truth": "비정상",
            "type_id": schema.scenario_type_id("N-14"),
            "ok": True,
            "parsed": {
                "judgment": "판단불가",
                "confidence": 0.3,
                "observed": "외벽 비계가 보이지만 역광으로 방지망 설치 상태를 확인할 수 없다.",
                "reason": "방지망 유무를 판별할 수 없다.",
                "hazard_type": "낙하",
                "missing_measure": "",
            },
            "model": "(demo)",
            "prompt_version": "(demo)",
        }
    )
    return records


def build_rows(
    records: list[dict[str, Any]],
    profile: dict[str, Any],
    include_unknown: bool = True,
) -> pd.DataFrame:
    """판정 결과 -> 평가표 행."""
    rows: list[dict[str, Any]] = []
    for record in records:
        if not record.get("ok"):
            continue
        parsed = record.get("parsed") or {}
        judgment = parsed.get("judgment", "")
        if judgment == "정상":
            continue  # 정상은 평가표에 올리지 않는다 (개선할 것이 없다)
        if judgment == "판단불가" and not include_unknown:
            continue
        if judgment not in ("비정상", "판단불가"):
            continue

        situation_id = record.get("situation_id") or ""
        category = schema.situation_category(situation_id)
        if category != schema.CATEGORY_SCENARIO or not situation_id.startswith("N-"):
            # Y/C/SO 는 개선대책을 끌어낼 쌍이 없다. 별도 처리 대상.
            continue

        # ---- 핵심: N-nn -> Y-nn 으로 목표 상태를 끌어온다 ----
        pair_id = schema.counterpart(situation_id)  # tables.scenario_pair() 위임
        hazard = schema.scenario_description(situation_id)
        measure = schema.scenario_description(pair_id) if pair_id else ""

        type_id = schema.scenario_type_id(situation_id)
        process_id = schema.scenario_process_id(situation_id)
        risk = risk_scoring.score(
            situation_id,
            confidence=parsed.get("confidence"),
            judgment=judgment,
        )

        rows.append(
            {
                "공정": profile.get("process") or schema.process_name(process_id),
                "유해위험요인": hazard,
                "사고유형": schema.accident_type_name(type_id),
                "현재 안전조치": parsed.get("observed", ""),
                "빈도": risk.frequency,
                "강도": risk.severity,
                "위험성": risk.risk,
                "위험등급": risk.grade if judgment == "비정상" else "확인필요",
                "산정근거": risk.basis,
                "개선대책": measure,
                "담당": profile.get("assessor", ""),
                "기한": risk.action_deadline if judgment == "비정상" else "현장 확인 후 확정",
                # TODO: 산업안전보건기준에 관한 규칙 조항 매핑. 지금은 시나리오 ID 와 유형만.
                "근거": f"{situation_id} / {schema.accident_type_name(type_id)} / 목표상태 {pair_id or '-'}"
                        f" (산안법 조항 매핑 TODO)",
                "신뢰도": risk.confidence_grade,
                "검토필요": "Y" if risk.needs_review else "",
                "검토사유": risk.review_reason,
                "사진": record.get("image_file", ""),
                "판정source": f"{record.get('model', '')} / {record.get('prompt_version', '')}",
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    # 위험성 높은 것부터. 같은 점수면 시나리오 순서.
    df = df.sort_values(["위험성", "유해위험요인"], ascending=[False, True]).reset_index(drop=True)
    df.insert(0, "번호", range(1, len(df) + 1))
    return df[COLUMNS]


def merge_by_hazard(df: pd.DataFrame) -> pd.DataFrame:
    """같은 유해위험요인이 여러 장에서 나오면 1행으로 합친다.

    실제 평가표는 사진 단위가 아니라 위험요인 단위로 쓴다. 사진 목록과 건수는
    남겨서 근거를 잃지 않는다.
    """
    if df.empty:
        return df
    grouped = (
        df.groupby(["유해위험요인", "공정", "사고유형"], sort=False)
        .agg(
            빈도=("빈도", "first"),
            강도=("강도", "first"),
            위험성=("위험성", "first"),
            위험등급=("위험등급", "first"),
            산정근거=("산정근거", "first"),
            개선대책=("개선대책", "first"),
            담당=("담당", "first"),
            기한=("기한", "first"),
            근거=("근거", "first"),
            판정건수=("사진", "size"),
            현재_안전조치=("현재 안전조치", lambda s: " / ".join(dict.fromkeys(s))),
            신뢰도=("신뢰도", lambda s: ", ".join(sorted(set(s)))),
            검토필요=("검토필요", lambda s: "Y" if (s == "Y").any() else ""),
            검토사유=("검토사유", lambda s: "; ".join(dict.fromkeys(x for x in s if x))),
            사진=("사진", lambda s: ", ".join(s)),
            판정source=("판정source", "first"),
        )
        .reset_index()
        .rename(columns={"현재_안전조치": "현재 안전조치"})
    )
    grouped = grouped.sort_values(["위험성", "유해위험요인"], ascending=[False, True]).reset_index(drop=True)
    grouped.insert(0, "번호", range(1, len(grouped) + 1))
    cols = [c for c in COLUMNS if c in grouped.columns]
    extra = [c for c in grouped.columns if c not in cols]
    return grouped[cols + extra]


def render_text(df: pd.DataFrame, profile: dict[str, Any], run: str) -> str:
    L = [
        "위험성평가표 (초안)",
        "=" * 100,
        f"사업장   : {profile['site_name']}",
        f"업종     : {profile['industry']}",
        f"인원     : {profile.get('worker_count') or '(미입력)'}",
        f"평가자   : {profile['assessor']}",
        f"평가일   : {profile['assessed_on']}",
        f"판정근거 : {run}",
        "",
        "본 표는 VLM 판정 결과로 만든 초안이다. 유해위험요인·개선대책 문구는 AI-Hub",
        "건설 현장 위험 상태 판단 데이터의 시나리오 정의를 그대로 인용했고, '현재",
        "안전조치' 칸만 모델이 사진에서 관찰한 내용이다. 검토필요 표시가 있는 행은",
        "반드시 현장 확인 후 확정해야 한다.",
        "",
    ]
    if df.empty:
        L.append("비정상 판정이 없어 평가표에 올릴 행이 없다.")
        return "\n".join(L) + "\n"

    # 컬럼명에 공백이 있어 itertuples 속성 접근이 깨진다. dict 로 꺼낸다.
    for row in df.to_dict("records"):
        L += [
            "-" * 100,
            f"[{row['번호']}] 위험성 {row['위험성']} ({row['위험등급']})"
            f"  빈도 {row['빈도']} × 강도 {row['강도']}"
            + ("   ** 검토필요 **" if row.get("검토필요") == "Y" else ""),
            f"  공정         : {row['공정']}",
            f"  유해위험요인 : {row['유해위험요인']}  [{row['사고유형']}]",
            f"  현재 안전조치: {row['현재 안전조치']}",
            f"  개선대책     : {row['개선대책']}",
            f"  담당 / 기한  : {row['담당']} / {row['기한']}",
            f"  산정근거     : {row['산정근거']}",
            f"  근거         : {row['근거']}",
            f"  신뢰도       : {row['신뢰도']}"
            + (f"  ({row['검토사유']})" if row.get("검토사유") else ""),
        ]
        count = row.get("판정건수")
        L.append(
            f"  사진         : {row.get('사진', '')}"
            + (f" ({count}건)" if count else "")
        )

    L += ["-" * 100, ""]
    grade_counts = df.위험등급.value_counts()
    L.append("등급 분포: " + " / ".join(f"{k} {v}건" for k, v in grade_counts.items()))
    L.append(f"검토필요: {int((df.검토필요 == 'Y').sum())}건 / 전체 {len(df)}건")
    L += ["", "[부록] 위험성 산정 규칙 (3×3)", ""]
    for rule in risk_scoring.rule_table():
        L.append(f"  {rule['축']} {rule['값']}  {rule['분류']:<24} {rule['근거']}")
    L += [
        "",
        "  · confidence 는 위험성 점수에 넣지 않는다. 측정 신뢰도와 위험 속성은 다른 축이고,",
        "    곱하면 '확신이 없으니 위험이 낮다'는 잘못된 결론이 나온다. 대신 신뢰도 등급과",
        "    검토필요 플래그로 분리한다.",
        "  · 작업 높이·중량 정보가 입력에 없어 강도를 더 세분할 수 없다. 고층 작업이 많은",
        "    현장은 강도를 상향 조정해야 한다.",
        "  · 산업안전보건기준에 관한 규칙 조항 매핑은 아직 없다(근거 칸 TODO).",
    ]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Task B 판정 -> 위험성평가표 초안")
    parser.add_argument("--run", default=None, help="eval_runs/<run>.jsonl")
    parser.add_argument("--demo", action="store_true", help="가짜 판정 결과로 샘플 생성")
    parser.add_argument("--profile", type=Path, default=None, help="사업장 프로파일 json")
    parser.add_argument("--merge", action="store_true", help="같은 유해위험요인을 1행으로 합치기")
    parser.add_argument("--no-unknown", action="store_true", help="판단불가 건을 표에서 제외")
    parser.add_argument("--xlsx", action="store_true", help="xlsx 로도 저장 (openpyxl 필요)")
    args = parser.parse_args(argv)

    if not args.run and not args.demo:
        raise SystemExit("--run 또는 --demo 중 하나가 필요하다.")

    run = args.run or "demo"
    records = demo_judgments() if args.demo else load_judgments(args.run)
    profile = load_profile(args.profile)
    if args.demo:
        # 프로파일을 따로 주지 않았으면 데모용 값으로 채운다
        if profile["site_name"] == DEFAULT_PROFILE["site_name"]:
            profile["site_name"] = "샘플 현장 (demo)"
        if profile["assessor"] == DEFAULT_PROFILE["assessor"]:
            profile["assessor"] = "안전관리자"
        if profile["worker_count"] is None:
            profile["worker_count"] = 12

    print(f"[1/3] 판정 {len(records)} 건 읽음 ({run})")
    df = build_rows(records, profile, include_unknown=not args.no_unknown)
    if args.merge:
        before = len(df)
        df = merge_by_hazard(df)
        print(f"[2/3] 평가표 {before} 행 -> 위험요인 단위 {len(df)} 행으로 병합")
    else:
        print(f"[2/3] 평가표 {len(df)} 행 (판정 1건 = 1행)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / f"{run}_assessment.csv"
    txt_path = OUT_DIR / f"{run}_assessment.txt"
    rules_path = OUT_DIR / f"{run}_rules.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(render_text(df, profile, run), encoding="utf-8")
    pd.DataFrame(risk_scoring.rule_table()).to_csv(rules_path, index=False, encoding="utf-8-sig")

    if args.xlsx:
        try:
            df.to_excel(OUT_DIR / f"{run}_assessment.xlsx", index=False)
            print(f"      - {run}_assessment.xlsx")
        except ImportError:
            print("      xlsx 저장 생략: pip install openpyxl 필요")

    print("[3/3] 저장")
    for path in (csv_path, txt_path, rules_path):
        print(f"      - {path.relative_to(config.PROJECT_ROOT)}")

    if not df.empty:
        print()
        print(render_text(df, profile, run))


if __name__ == "__main__":
    main()
