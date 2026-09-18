"""가짜 라벨 JSON 을 만들어 parse_labels.py 를 실데이터 없이 검증한다.

    python tests/make_fixture.py                 # data/fixture_raw/ 에 생성
    python src/parse_labels.py --raw-dir data/fixture_raw --force

시나리오 ID·사고유형·공정·객체 클래스는 tables.py 에서 실제 코드를 뽑아 쓴다.
이상 징후 감지 경로까지 확인하려고 아래 비정상 케이스도 같이 만든다.

    - 원문 결번 class_id (MISSING_CLASS_IDS)
    - 테이블에 없는 class_id / situation_id
    - 시나리오가 규정한 type_ID 와 다른 값
    - 깨진 JSON, 빈 JSON

생성 위치는 data/ 밑이라 git 에 올라가지 않는다.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import schema  # noqa: E402  (tables.py 를 단일 출처로 읽는다)

DEFAULT_OUT = ROOT / "data" / "fixture_raw"


def _doc(
    situation_id: str,
    index: int,
    class_ids: list[str],
    device: str,
    type_id: str | None = None,
    process_id: str | None = None,
) -> dict:
    """AI-Hub 라벨 JSON 한 건의 모양을 그대로 흉내낸다."""
    type_id = type_id or schema.scenario_type_id(situation_id) or "A"
    process_id = process_id or schema.scenario_process_id(situation_id) or "a"
    base = f"H-220706_B16_{situation_id}_{index:03d}"
    return {
        schema.K_RAW: {
            "Raw_data_ID": base,
            "Location_ID": random.choice([1, 3, 16, 22]),
            "type_ID": type_id,
            "Type_Description": schema.accident_type_name(type_id),
            "process_ID": process_id,
            "Resolution": "1920, 1080",
            "Date": "2022-07-06",
            "Main_class_ID": class_ids[0],
            "Main_item_ID": "AT",
            "Situation_ID": situation_id,
            "GPS": "37.288079, 126.940177",
            "Site_Condition": random.choice(["1", "2"]),
            "Weather_Information": random.choice(["1", "2", "3", "4"]),
            "Wind_Speed": str(random.randint(0, 9)),
            "device": device,
        },
        schema.K_SOURCE: {
            "Source_Data": f"{base}_0008",
            "Extraction_Time": "00:00:06.10",
            "File_extension": "jpg",
        },
        schema.K_LEARNING: {
            "Path": f"./5대사고유형/{situation_id}/",
            "Json_Data_ID": f"{base}_0008",
            "Annotations": [
                {
                    "class_ID": cid,
                    "type": "polygon",
                    "item_ID": "ATO",
                    "value": [[random.randint(0, 1900), random.randint(0, 1000)] for _ in range(4)],
                }
                for cid in class_ids
            ]
            # 시나리오 ID 로 달리는 상황 bbox
            + [{"class_ID": situation_id, "type": "bbox", "value": [10, 10, 200, 200]}],
        },
    }


def build(
    out_dir: Path,
    per_scenario: int,
    n_pairs: int,
    anomalies: bool,
    seed: int = 7,
) -> int:
    random.seed(seed)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    class_ids = list(schema.OBJECT_CLASSES)
    if not class_ids:
        raise SystemExit("OBJECT_CLASSES 가 비어 있다. src/tables.py 를 확인해라.")

    situations: list[str] = []
    for y_id, n_id in schema.SCENARIO_PAIRS[:n_pairs]:
        situations += [y_id, n_id]
    situations += [sid for sid in schema.DEFINED_SITUATION_IDS if sid.startswith("C-")][:1]

    n = 0
    devices = ["1", "2", "3", "4", "4", "5"]  # 휴대폰(4) 비중을 조금 높게
    for situation_id in situations:
        sub = out_dir / situation_id
        sub.mkdir(parents=True, exist_ok=True)
        # 쌍마다 장수를 살짝 다르게 해 pair_check 불일치도 만들어 둔다.
        count = per_scenario - (1 if situation_id.startswith("N-") else 0)
        for i in range(max(count, 1)):
            doc = _doc(
                situation_id,
                i,
                random.sample(class_ids, k=min(3, len(class_ids))),
                random.choice(devices),
            )
            (sub / f"{situation_id}_{i:03d}.json").write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8"
            )
            n += 1

    if anomalies:
        bad = out_dir / "_anomaly"
        bad.mkdir(parents=True, exist_ok=True)
        normal_sid = situations[1] if len(situations) > 1 else situations[0]

        cases: dict[str, dict] = {}
        missing = sorted(schema.MISSING_CLASS_IDS)
        if missing:
            cases["missing_code.json"] = _doc(normal_sid, 900, [missing[0], class_ids[0]], "4")
        cases["unknown_class.json"] = _doc(normal_sid, 901, ["ZZ-99", class_ids[0]], "4")
        cases["unknown_situation.json"] = _doc("N-77", 902, [class_ids[0]], "4", type_id="A")
        cases["type_mismatch.json"] = _doc(
            situations[0],
            903,
            [class_ids[0]],
            "4",
            type_id="E" if schema.scenario_type_id(situations[0]) != "E" else "A",
        )
        for name, doc in cases.items():
            (bad / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            n += 1

        (bad / "broken.json").write_text("{not json", encoding="utf-8")
        (bad / "empty.json").write_text("{}", encoding="utf-8")
        n += 2

    return n


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="parse_labels.py 검증용 가짜 라벨 생성")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="생성 위치 (기본 data/fixture_raw)")
    parser.add_argument("--per-scenario", type=int, default=5, help="시나리오당 장수")
    parser.add_argument("--pairs", type=int, default=3, help="사용할 Y/N 쌍 개수")
    parser.add_argument("--no-anomalies", action="store_true", help="이상 케이스 생성 생략")
    args = parser.parse_args(argv)

    n = build(
        out_dir=args.out,
        per_scenario=args.per_scenario,
        n_pairs=args.pairs,
        anomalies=not args.no_anomalies,
    )
    print(f"{args.out} 에 JSON {n} 개 생성")
    print(f"검증: python src/parse_labels.py --raw-dir {args.out} --force")


if __name__ == "__main__":
    main()
