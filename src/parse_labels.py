"""data/raw/ 밑의 라벨 JSON 을 전부 훑어 DataFrame 으로 만들고 분포를 뽑는다.

    python src/parse_labels.py                 # 캐시 있으면 재사용
    python src/parse_labels.py --force         # 다시 순회
    python src/parse_labels.py --limit 2000    # 앞 2000건만 (구조 확인용)
    python src/parse_labels.py --workers 8     # 병렬 파싱

산출물 (data/processed/):
    labels.parquet             이미지 1장 = 1행
    objects.parquet            어노테이션 1개 = 1행
    reports/summary.txt        한눈에 보는 요약 (파싱 -> 분포 -> 쌍 검증 -> 이상 징후)
    reports/*.csv              분포 집계 (코드값 옆에 tables.py 의 명칭 포함)
    reports/pair_check.csv     Y/N 쌍 수량 + 기준 2,000장과의 차이
    reports/missing_scenarios.csv  정의됐지만 데이터에 없는 시나리오
    reports/missing_class_hits.csv 원문 결번 코드가 등장한 경우
    reports/unknown_codes.csv  테이블에 없는 situation_id / class_id
    reports/mobile_subset.txt  device=4(휴대폰) 실사용 조건 서브셋 통계
    reports/object_pairs.csv   안전상태 내포 클래스 11쌍 등장 횟수
    reports/parse_errors.csv   읽기/파싱 실패 목록

JSON 22만 개 순회는 I/O 바운드다. 한 번 돌린 결과는 parquet(없으면 csv)으로
캐시하고, 이후 실행에서는 --force 없이는 재사용한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import schema  # noqa: E402  # 코드 테이블은 schema -> tables 로 흐른다

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    print("pandas 가 필요하다: pip install -r requirements.txt", file=sys.stderr)
    raise

try:
    from tqdm import tqdm
except ImportError:  # tqdm 없으면 자체 진행률로 대체
    tqdm = None  # type: ignore[assignment]

# Windows 기본 콘솔은 cp949 라 일부 문자에서 UnicodeEncodeError 가 난다.
# 리포트 내용보다 진행이 중요하니 인코딩 못하는 문자는 흘려보낸다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # 파이프로 연결된 경우 등
        pass


# ------------------------------------------------------------------ 파일 수집
def iter_json_files(raw_dir: Path) -> Iterator[Path]:
    """raw_dir 아래 모든 *.json. os.walk 가 rglob 보다 22만 건에서 빠르다."""
    for dirpath, _dirnames, filenames in os.walk(raw_dir):
        for name in filenames:
            if name.lower().endswith(".json"):
                yield Path(dirpath) / name


def collect_files(raw_dir: Path, limit: int | None = None) -> list[Path]:
    print(f"[1/4] JSON 파일 목록 수집: {raw_dir}")
    files: list[Path] = []
    for path in iter_json_files(raw_dir):
        files.append(path)
        if limit and len(files) >= limit:
            break
        if len(files) % 20000 == 0:
            print(f"      ... {len(files):,} 개", flush=True)
    print(f"      총 {len(files):,} 개")
    return files


# -------------------------------------------------------------------- 파싱
def _read_json(path: Path) -> dict[str, Any]:
    """AI-Hub JSON 은 UTF-8(BOM 포함)이 기본이지만 cp949 도 섞여 있다."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return json.loads(data.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("decode/parse 실패")


def parse_one(path_str: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    """한 파일 -> (label row, object rows, error). 예외를 던지지 않는다."""
    path = Path(path_str)
    try:
        doc = _read_json(path)
        if not isinstance(doc, dict):
            return None, [], "최상위가 객체가 아님"
        record = schema.LabelRecord.from_dict(doc, source_file=str(path))
        return record.to_row(), list(record.object_rows()), None
    except Exception as exc:  # noqa: BLE001 - 22만 건 중 한 건 때문에 멈추지 않는다
        return None, [], f"{type(exc).__name__}: {exc}"


def _progress(iterable, total: int, desc: str):
    if tqdm is not None:
        return tqdm(iterable, total=total, desc=desc, unit="file", ncols=80)

    def gen():
        step = max(total // 100, 1)
        for i, item in enumerate(iterable, 1):
            if i % step == 0 or i == total:
                pct = i * 100 // total if total else 100
                print(f"\r      {desc}: {i:,}/{total:,} ({pct}%)", end="", flush=True)
            yield item
        print()

    return gen()


def parse_all(
    files: list[Path], workers: int = 1
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    print(f"[2/4] JSON 파싱 (workers={workers})")
    rows: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    paths = [str(p) for p in files]
    if workers > 1 and len(paths) > 1000:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = pool.map(parse_one, paths, chunksize=256)
            for path_str, (row, obj_rows, err) in zip(
                paths, _progress(results, len(paths), "parse")
            ):
                if err:
                    errors.append({"source_file": path_str, "error": err})
                    continue
                if row:
                    rows.append(row)
                objects.extend(obj_rows)
    else:
        for path_str in _progress(iter(paths), len(paths), "parse"):
            row, obj_rows, err = parse_one(path_str)
            if err:
                errors.append({"source_file": path_str, "error": err})
                continue
            if row:
                rows.append(row)
            objects.extend(obj_rows)

    labels_df = pd.DataFrame(rows, columns=list(schema.LABEL_COLUMNS))
    objects_df = pd.DataFrame(objects, columns=list(schema.OBJECT_COLUMNS))
    errors_df = pd.DataFrame(errors, columns=["source_file", "error"])
    print(
        f"      라벨 {len(labels_df):,} 행 / 어노테이션 {len(objects_df):,} 행 "
        f"/ 실패 {len(errors_df):,} 건"
    )
    return labels_df, objects_df, errors_df


# ------------------------------------------------------------------- 캐시 I/O
def save_df(df: pd.DataFrame, parquet_path: Path, csv_path: Path) -> Path:
    """parquet 우선, pyarrow 없으면 csv 로 폴백."""
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(parquet_path, index=False)
        return parquet_path
    except Exception as exc:  # noqa: BLE001
        print(f"      parquet 저장 불가({type(exc).__name__}) -> csv 로 저장")
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        return csv_path


def load_df(parquet_path: Path, csv_path: Path) -> pd.DataFrame | None:
    if parquet_path.exists():
        try:
            return pd.read_parquet(parquet_path)
        except Exception as exc:  # noqa: BLE001
            print(f"      parquet 읽기 실패({type(exc).__name__}) -> csv 확인")
    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    return None


def write_report(df: pd.DataFrame, name: str) -> None:
    out = config.REPORT_DIR / f"{name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")  # 엑셀에서 한글 안 깨지게
    print(f"      - {out.relative_to(config.PROJECT_ROOT)}")


# -------------------------------------------------------------------- 집계
# 코드값만 있는 리포트는 읽을 수 없다. 모든 집계에 tables.py 의 명칭을 붙인다.
NONE_LABEL = "(없음)"


def _codes(series: pd.Series) -> pd.Series:
    """NaN/빈 문자열을 (없음) 으로 통일한 문자열 시리즈."""
    return series.astype("object").where(series.notna(), NONE_LABEL).replace("", NONE_LABEL)


def value_counts_report(
    df: pd.DataFrame,
    col: str,
    namer=None,
    name_col: str = "name",
) -> pd.DataFrame:
    """col 별 장수. namer 로 코드 -> 명칭 컬럼을 붙인다."""
    if col not in df.columns or df.empty:
        cols = [col, name_col, "n_images", "ratio"] if namer else [col, "n_images", "ratio"]
        return pd.DataFrame(columns=cols)
    counts = _codes(df[col]).value_counts()
    out = counts.rename_axis(col).reset_index(name="n_images")
    total = int(out["n_images"].sum()) or 1
    out["ratio"] = (out["n_images"] / total).round(5)
    if namer is not None:
        out.insert(
            1,
            name_col,
            out[col].map(lambda code: "" if code == NONE_LABEL else namer(code)),
        )
    return out


def _joined_unique(series: pd.Series) -> str:
    values = sorted({str(v) for v in series.dropna() if str(v)})
    return "|".join(values)


def scenario_report(df: pd.DataFrame) -> pd.DataFrame:
    """시나리오 ID(Y/N/C)별 장수 + 사고유형·공정·설명 명칭.

    type/process 는 tables.py 가 규정한 값을 정답으로 쓰고, 데이터에 실제로 들어
    있던 값은 data_* 컬럼에 따로 남긴다. 둘이 다르면 code_mismatch 로 표시된다.
    """
    columns = [
        "situation_id",
        "n_images",
        "diff_from_expected",
        "situation_type",
        "situation_type_ko",
        "type_id",
        "type_name",
        "process_id",
        "process_name",
        "description",
        "data_type_ids",
        "data_process_ids",
        "code_mismatch",
        "in_tables",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    grouped = (
        df.assign(_sid=_codes(df["situation_id"]))
        .groupby("_sid", dropna=False)
        .agg(
            n_images=("json_id", "size"),
            data_type_ids=("type_id", _joined_unique),
            data_process_ids=("process_id", _joined_unique),
        )
        .reset_index()
        .rename(columns={"_sid": "situation_id"})
    )

    sid = grouped["situation_id"]
    grouped["situation_type"] = sid.map(schema.situation_prefix)
    grouped["situation_type_ko"] = grouped["situation_type"].map(
        lambda p: schema.SITUATION_TYPE_KO.get(p or "", "")
    )
    grouped["type_id"] = sid.map(schema.scenario_type_id)
    grouped["type_name"] = grouped["type_id"].map(schema.accident_type_name)
    grouped["process_id"] = sid.map(schema.scenario_process_id)
    grouped["process_name"] = grouped["process_id"].map(schema.process_name)
    grouped["description"] = sid.map(schema.scenario_description)
    grouped["in_tables"] = sid.map(schema.is_known_situation)
    grouped["diff_from_expected"] = grouped["n_images"] - schema.EXPECTED_PER_SCENARIO
    # 기대값과 다른 type_ID 가 하나라도 섞여 있으면 불일치로 본다.
    grouped["code_mismatch"] = [
        bool(exp) and bool(got) and any(t != exp for t in got.split("|"))
        for exp, got in zip(grouped["type_id"], grouped["data_type_ids"])
    ]

    grouped["_num"] = sid.astype(str).str.extract(r"-(\d+)$")[0]
    grouped = grouped.sort_values(
        ["situation_type", "_num"], na_position="last"
    ).drop(columns=["_num"])
    return grouped[columns]


def pair_report(df: pd.DataFrame) -> pd.DataFrame:
    """Y-nn 과 N-nn 의 장수가 짝을 이루는지 + 각각 2,000장 기준과의 차이."""
    counts = _codes(df["situation_id"]).value_counts().to_dict() if not df.empty else {}
    expected = schema.EXPECTED_PER_SCENARIO
    rows = []
    for y_id, n_id in schema.SCENARIO_PAIRS:
        y_n = int(counts.get(y_id, 0))
        n_n = int(counts.get(n_id, 0))
        if y_n == 0 and n_n == 0:
            status = "둘 다 없음"
        elif y_n == 0 or n_n == 0:
            status = "한쪽만 있음"
        elif y_n == n_n:
            status = "일치"
        else:
            status = "개수 불일치"
        rows.append(
            {
                "pair": y_id.split("-", 1)[1],
                "y_id": y_id,
                "y_desc": schema.scenario_description(y_id),
                "y_n_images": y_n,
                "y_diff_from_expected": y_n - expected,
                "n_id": n_id,
                "n_desc": schema.scenario_description(n_id),
                "n_n_images": n_n,
                "n_diff_from_expected": n_n - expected,
                "diff": y_n - n_n,
                "type_id": schema.scenario_type_id(n_id),
                "type_name": schema.accident_type_name(schema.scenario_type_id(n_id)),
                "process_name": schema.process_name(schema.scenario_process_id(n_id)),
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def missing_scenario_report(df: pd.DataFrame) -> pd.DataFrame:
    """tables.py 에 정의된 105개 중 데이터에 아예 없는 situation_id."""
    present = set(_codes(df["situation_id"])) if not df.empty else set()
    rows = []
    for sid in schema.DEFINED_SITUATION_IDS:
        if sid in present:
            continue
        rows.append(
            {
                "situation_id": sid,
                "situation_type": schema.situation_prefix(sid),
                "type_id": schema.scenario_type_id(sid),
                "type_name": schema.accident_type_name(schema.scenario_type_id(sid)),
                "process_id": schema.scenario_process_id(sid),
                "process_name": schema.process_name(schema.scenario_process_id(sid)),
                "description": schema.scenario_description(sid),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "situation_id",
            "situation_type",
            "type_id",
            "type_name",
            "process_id",
            "process_name",
            "description",
        ],
    )


def class_freq_report(objects_df: pd.DataFrame) -> pd.DataFrame:
    """객체 클래스(class_ID) 등장 빈도 + 대분류·소분류·명칭."""
    columns = [
        "class_id",
        "class_group",
        "class_group_ko",
        "class_major",
        "class_minor",
        "class_name",
        "n_annotations",
        "n_images",
        "in_tables",
        "is_situation_tag",
        "is_missing_code",
    ]
    if objects_df.empty:
        return pd.DataFrame(columns=columns)

    obj = objects_df[objects_df["class_id"].notna()]
    out = (
        obj.groupby("class_id", dropna=False)
        .agg(n_annotations=("json_id", "size"), n_images=("json_id", "nunique"))
        .reset_index()
        .sort_values("n_annotations", ascending=False)
    )
    cid = out["class_id"]
    out["class_group"] = cid.map(schema.object_class_group)
    out["class_group_ko"] = out["class_group"].map(
        lambda g: schema.OBJECT_CLASS_GROUP_KO.get(g or "", "")
    )
    out["class_major"] = cid.map(schema.object_class_major)
    out["class_minor"] = cid.map(schema.object_class_minor)
    out["class_name"] = cid.map(schema.object_class_name)
    out["in_tables"] = cid.map(schema.is_known_class)
    out["is_situation_tag"] = cid.map(lambda c: schema.object_class_group(c) is None)
    out["is_missing_code"] = cid.map(lambda c: str(c).upper() in schema.MISSING_CLASS_IDS)
    return out[columns]


def missing_class_report(classes: pd.DataFrame) -> pd.DataFrame:
    """원문 결번(MISSING_CLASS_IDS)이 데이터에 등장하면 데이터 이상 신호."""
    if classes.empty:
        return pd.DataFrame(columns=["class_id", "n_annotations", "n_images", "note"])
    hits = classes[classes["is_missing_code"]].copy()
    hits["note"] = "원문 명세 결번인데 데이터에 등장함"
    return hits[["class_id", "n_annotations", "n_images", "note"]]


def unknown_codes_report(
    labels_df: pd.DataFrame, objects_df: pd.DataFrame
) -> pd.DataFrame:
    """tables.py 에 정의되지 않은 situation_id / class_id 를 모아 둔다."""
    rows: list[dict[str, Any]] = []

    if not labels_df.empty:
        for code, n in _codes(labels_df["situation_id"]).value_counts().items():
            if code == NONE_LABEL:
                rows.append({"kind": "situation_id", "code": "(비어 있음)", "count": int(n), "unit": "images", "note": "Situation_ID 누락"})
            elif not schema.is_known_situation(code):
                rows.append({"kind": "situation_id", "code": code, "count": int(n), "unit": "images", "note": "SCENARIOS 에 없음"})

    if not objects_df.empty:
        for code, n in _codes(objects_df["class_id"]).value_counts().items():
            if code == NONE_LABEL:
                rows.append({"kind": "class_id", "code": "(비어 있음)", "count": int(n), "unit": "annotations", "note": "class_ID 누락"})
                continue
            # 시나리오 ID 로 달린 bbox 는 정상이다. 객체도 시나리오도 아닌 값만 이상.
            if schema.is_known_class(code) or schema.is_known_situation(code):
                continue
            if str(code).upper() in schema.MISSING_CLASS_IDS:
                note = "원문 결번 코드"
                kind = "class_id(결번)"
            else:
                note = "OBJECT_CLASSES 에 없음"
                kind = "class_id"
            rows.append({"kind": kind, "code": code, "count": int(n), "unit": "annotations", "note": note})

    return pd.DataFrame(rows, columns=["kind", "code", "count", "unit", "note"])


def object_pairs_report(objects_df: pd.DataFrame) -> pd.DataFrame:
    """OBJECT_SAFETY_PAIRS 11쌍의 정상/비정상 클래스 등장 횟수."""
    columns = [
        "판정항목",
        "normal_class_id",
        "normal_name",
        "normal_n_annotations",
        "normal_n_images",
        "abnormal_class_id",
        "abnormal_name",
        "abnormal_n_annotations",
        "abnormal_n_images",
        "diff",
        "status",
    ]
    pairs = schema.safety_pairs()
    if not pairs:
        return pd.DataFrame(columns=columns)

    if objects_df.empty:
        ann_counts: dict[str, int] = {}
        img_counts: dict[str, int] = {}
    else:
        ann_counts = objects_df["class_id"].value_counts().to_dict()
        img_counts = (
            objects_df.dropna(subset=["class_id"])
            .groupby("class_id")["json_id"]
            .nunique()
            .to_dict()
        )

    rows = []
    for normal, abnormal, item in pairs:
        n_ann = int(ann_counts.get(normal, 0))
        a_ann = int(ann_counts.get(abnormal, 0))
        if n_ann == 0 and a_ann == 0:
            status = "둘 다 없음"
        elif n_ann == 0 or a_ann == 0:
            status = "한쪽만 있음"
        else:
            status = "양쪽 등장"
        rows.append(
            {
                "판정항목": item,
                "normal_class_id": normal,
                "normal_name": schema.object_class_name(normal),
                "normal_n_annotations": n_ann,
                "normal_n_images": int(img_counts.get(normal, 0)),
                "abnormal_class_id": abnormal,
                "abnormal_name": schema.object_class_name(abnormal),
                "abnormal_n_annotations": a_ann,
                "abnormal_n_images": int(img_counts.get(abnormal, 0)),
                "diff": n_ann - a_ann,
                "status": status,
            }
        )
    return pd.DataFrame(rows)[columns]


def shape_report(objects_df: pd.DataFrame) -> pd.DataFrame:
    if objects_df.empty:
        return pd.DataFrame(columns=["shape", "n_annotations"])
    return (
        _codes(objects_df["shape"])
        .value_counts()
        .rename_axis("shape")
        .reset_index(name="n_annotations")
    )


def mobile_subset_text(labels_df: pd.DataFrame) -> str:
    """device == 4(휴대폰) 서브셋 통계. 실사용 조건 평가셋 후보다."""
    total = len(labels_df)
    if total == 0:
        return "라벨이 없다.\n"

    mobile = labels_df[_codes(labels_df["device"]) == schema.DEVICE_PHONE]
    n = len(mobile)
    lines = [
        "실사용 조건 서브셋 (device=4 휴대폰)",
        "=" * 60,
        f"전체 이미지    : {total:,}",
        f"휴대폰 이미지  : {n:,} ({n / total * 100:.2f}%)",
        "",
    ]
    if n == 0:
        lines.append("휴대폰 촬영 이미지가 없다. 실사용 조건 평가셋을 다른 방식으로 구성해야 한다.")
        return "\n".join(lines) + "\n"

    situ = _codes(mobile["situation_type"]).value_counts()
    lines.append("상황 유형별:")
    for code, count in situ.items():
        ko = schema.SITUATION_TYPE_KO.get(str(code), "")
        lines.append(f"  {code} {ko:<10} {int(count):>8,}")

    types = _codes(mobile["type_id"]).value_counts()
    lines += ["", "사고유형별:"]
    for code, count in types.items():
        lines.append(f"  {code} {schema.accident_type_name(code):<12} {int(count):>8,}")

    lines += [
        "",
        f"등장 시나리오 {mobile['situation_id'].nunique():,} 종 / 정의된 "
        f"{len(schema.DEFINED_SITUATION_IDS)} 종",
        "",
        "시나리오별 (장수 내림차순):",
        f"  {'situation_id':<14}{'n':>8}  {'type':<8}{'process':<14}description",
    ]
    counts = _codes(mobile["situation_id"]).value_counts()
    for sid, count in counts.items():
        type_name = schema.accident_type_name(schema.scenario_type_id(sid))
        proc = schema.process_name(schema.scenario_process_id(sid))
        desc = schema.scenario_description(sid)
        lines.append(f"  {str(sid):<14}{int(count):>8,}  {type_name:<8}{proc:<14}{desc}")

    absent = [
        sid
        for sid in schema.DEFINED_SITUATION_IDS
        if sid not in set(counts.index)
    ]
    if absent:
        lines += [
            "",
            f"휴대폰 서브셋에 없는 시나리오 {len(absent)} 종:",
            "  " + ", ".join(absent),
        ]
    return "\n".join(lines) + "\n"


def build_summary(
    labels_df: pd.DataFrame,
    objects_df: pd.DataFrame,
    errors_df: pd.DataFrame,
    pairs: pd.DataFrame,
    classes: pd.DataFrame,
    missing_scenarios: pd.DataFrame,
    missing_classes: pd.DataFrame,
    unknown_codes: pd.DataFrame,
) -> tuple[str, dict[str, Any]]:
    total = len(labels_df)
    n_files = total + len(errors_df)

    def dist(col: str) -> dict[str, int]:
        if total == 0 or col not in labels_df.columns:
            return {}
        return {str(k): int(v) for k, v in _codes(labels_df[col]).value_counts().items()}

    situ = dist("situation_type")
    types = dist("type_id")
    processes = dist("process_id")
    devices = dist("device")
    site = dist("site_condition")
    weather = dist("weather")

    phone = int(devices.get(schema.DEVICE_PHONE, 0))
    object_classes = classes[~classes["is_situation_tag"]] if len(classes) else classes
    mismatched = pairs[pairs["status"].isin(["개수 불일치", "한쪽만 있음"])] if len(pairs) else pairs

    def pct(n: int) -> str:
        return f"{n / total * 100:5.2f}%" if total else "    -"

    L: list[str] = []
    # 1. 총 JSON 수 / 파싱 성공·실패
    L += [
        "SnapRA - AI-Hub 건설현장 위험 상태 판단 데이터 라벨 요약",
        "=" * 64,
        "[1] 파싱",
        f"  JSON 파일        {n_files:>9,}",
        f"  파싱 성공        {total:>9,}",
        f"  파싱 실패        {len(errors_df):>9,}",
        f"  어노테이션       {len(objects_df):>9,}",
        "",
    ]
    # 2. 사고유형별
    L.append("[2] 사고유형 (type_ID)")
    for code in list(schema.ACCIDENT_TYPE_KO) + [
        c for c in types if c not in schema.ACCIDENT_TYPE_KO
    ]:
        if code not in types:
            continue
        name = schema.accident_type_name(code) or "(미정의)"
        L.append(f"  {code} {name:<12} {types[code]:>9,}  {pct(types[code])}")
    L.append("")
    # 3. 정상 vs 비정상 vs 주의요망
    L.append("[3] 상황 유형 (정상 / 비정상 / 주의요망)")
    for code in ["Y", "N", "C", "SO"]:
        if code not in situ:
            continue
        name = schema.SITUATION_TYPE_KO.get(code, "(미정의)")
        L.append(f"  {code} {name:<12} {situ[code]:>9,}  {pct(situ[code])}")
    for code, n in situ.items():
        if code not in ("Y", "N", "C", "SO"):
            L.append(f"  {code} {'(미정의)':<12} {n:>9,}  {pct(n)}")
    L.append("")
    # 4. 공정별
    L.append("[4] 공정 (process_ID)")
    for code, n in sorted(processes.items(), key=lambda kv: -kv[1]):
        L.append(f"  {code} {schema.process_name(code) or '(미정의)':<16} {n:>9,}  {pct(n)}")
    L.append("")
    # 5. 촬영장비 — 휴대폰 강조
    L.append("[5] 촬영장비 (device)")
    for code, n in sorted(devices.items(), key=lambda kv: str(kv[0])):
        mark = "   <== 실사용 조건(서비스 입력과 동일)" if code == schema.DEVICE_PHONE else ""
        L.append(f"  {code} {schema.device_name(code) or '(미정의)':<12} {n:>9,}  {pct(n)}{mark}")
    L += [
        "",
        f"  >> 휴대폰 촬영 {phone:,} 장 ({phone / total * 100:.2f}% of {total:,})"
        if total
        else "  >> 휴대폰 촬영 0 장",
        "",
    ]
    # 6. 실내외 / 날씨
    L.append("[6] 촬영 환경")
    L.append("  실내/실외:")
    for code, n in sorted(site.items(), key=lambda kv: str(kv[0])):
        L.append(f"    {code} {schema.site_condition_name(code) or '(미정의)':<10} {n:>9,}  {pct(n)}")
    L.append("  날씨:")
    for code, n in sorted(weather.items(), key=lambda kv: str(kv[0])):
        L.append(f"    {code} {schema.weather_name(code) or '(미정의)':<10} {n:>9,}  {pct(n)}")
    L.append("")
    # 7. 객체 클래스 상위 20
    L.append("[7] 객체 클래스 상위 20종")
    if len(object_classes):
        for _, row in object_classes.head(20).iterrows():
            name = row["class_name"] or "(테이블에 없음)"
            L.append(
                f"  {row['class_id']:<8}{str(row['class_group_ko'] or ''):<8}"
                f"{name:<20}{int(row['n_annotations']):>9,}"
            )
    else:
        L.append("  (없음)")
    L.append("")
    # 8. pair_check 요약 — 불일치만
    n_match = int((pairs["status"] == "일치").sum()) if len(pairs) else 0
    L += [
        "[8] Y/N 쌍 검증",
        f"  정의된 쌍 {len(pairs)} 중 수량 일치 {n_match}, 문제 {len(mismatched)}",
    ]
    if len(mismatched):
        L.append(f"  {'pair':<6}{'Y':>8}{'N':>8}{'diff':>7}  상태 / 설명")
        for _, row in mismatched.iterrows():
            L.append(
                f"  {row['y_id']}/{row['n_id']:<4}{int(row['y_n_images']):>6,}"
                f"{int(row['n_n_images']):>8,}{int(row['diff']):>7,}  "
                f"{row['status']} / {row['n_desc']}"
            )
    else:
        L.append("  모든 쌍의 수량이 일치한다.")
    off_expected = (
        pairs[(pairs["y_diff_from_expected"] != 0) | (pairs["n_diff_from_expected"] != 0)]
        if len(pairs)
        else pairs
    )
    L.append(
        f"  기준 {schema.EXPECTED_PER_SCENARIO:,}장과 다른 시나리오가 있는 쌍: "
        f"{len(off_expected)} (상세: pair_check.csv)"
    )
    L.append("")
    # 9. 이상 징후
    L.append("[9] 이상 징후")
    anomalies = []
    if len(missing_classes):
        ids = ", ".join(f"{r.class_id}({int(r.n_annotations):,})" for r in missing_classes.itertuples())
        anomalies.append(f"결번 클래스 등장: {ids}")
    if len(unknown_codes):
        # 같은 코드가 situation_id 와 class_id 양쪽에 걸릴 수 있으니 종류를 붙여 구분한다.
        labeled = [
            f"{row.code}({row.kind})" for row in unknown_codes.itertuples()
        ]
        anomalies.append(
            f"미정의 코드 {len(unknown_codes)} 건 (unknown_codes.csv): "
            + ", ".join(labeled[:10])
            + (" ..." if len(labeled) > 10 else "")
        )
    if len(missing_scenarios):
        anomalies.append(
            f"데이터에 없는 시나리오 {len(missing_scenarios)}/{len(schema.DEFINED_SITUATION_IDS)} 종"
            f" (missing_scenarios.csv): "
            + ", ".join(missing_scenarios["situation_id"].head(15))
            + (" ..." if len(missing_scenarios) > 15 else "")
        )
    if len(errors_df):
        anomalies.append(f"파싱 실패 {len(errors_df):,} 건 (parse_errors.csv)")
    mismatch_codes = (
        int(labels_df.get("known_situation", pd.Series(dtype=bool)).eq(False).sum())
        if total
        else 0
    )
    if mismatch_codes:
        anomalies.append(f"tables.py 에 없는 situation_id 를 가진 이미지 {mismatch_codes:,} 장")
    if anomalies:
        for line in anomalies:
            L.append(f"  - {line}")
    else:
        L.append("  이상 없음")
    L += ["", "상세: data/processed/reports/ 의 csv 참고"]

    stats: dict[str, Any] = {
        "n_json_files": n_files,
        "n_images": total,
        "n_parse_errors": int(len(errors_df)),
        "n_annotations": int(len(objects_df)),
        "n_situation_ids": int(labels_df["situation_id"].nunique()) if total else 0,
        "n_defined_situation_ids": len(schema.DEFINED_SITUATION_IDS),
        "n_object_classes_seen": int(len(object_classes)),
        "n_object_classes_defined": len(schema.OBJECT_CLASSES),
        "expected_per_scenario": schema.EXPECTED_PER_SCENARIO,
        "by_accident_type": types,
        "by_situation_type": situ,
        "by_process": processes,
        "by_device": devices,
        "by_site_condition": site,
        "by_weather": weather,
        "phone_images": phone,
        "phone_ratio": round(phone / total, 5) if total else 0.0,
        "pairs_matched": n_match,
        "pairs_problem": int(len(mismatched)),
        "missing_scenarios": missing_scenarios["situation_id"].tolist(),
        "missing_class_hits": missing_classes["class_id"].tolist() if len(missing_classes) else [],
        "unknown_codes": unknown_codes["code"].tolist() if len(unknown_codes) else [],
    }
    return "\n".join(L), stats


# --------------------------------------------------------------------- main
def run(
    raw_dir: Path,
    force: bool = False,
    limit: int | None = None,
    workers: int = 1,
) -> None:
    config.ensure_dirs()

    labels_df = None if force else load_df(config.LABELS_CACHE_PARQUET, config.LABELS_CACHE_CSV)
    objects_df = None if force else load_df(config.OBJECTS_CACHE_PARQUET, config.OBJECTS_CACHE_CSV)
    errors_df = pd.DataFrame(columns=["source_file", "error"])

    if labels_df is not None and objects_df is not None:
        print(f"[1/4] 캐시 재사용: {config.LABELS_CACHE_PARQUET.name} ({len(labels_df):,} 행)")
        print("      다시 순회하려면 --force")
        print("[2/4] 파싱 생략")
        if config.PARSE_ERRORS_CSV.exists():
            errors_df = pd.read_csv(config.PARSE_ERRORS_CSV, dtype=str, keep_default_na=False)
    else:
        if not raw_dir.exists():
            raise SystemExit(
                f"{raw_dir} 가 없다. scripts/download.sh 로 데이터를 먼저 내려받아라."
            )
        files = collect_files(raw_dir, limit=limit)
        if not files:
            raise SystemExit(
                f"{raw_dir} 밑에 JSON 이 없다. zip 압축을 풀었는지 확인해라."
            )
        labels_df, objects_df, errors_df = parse_all(files, workers=workers)

        print("[3/4] 캐시 저장")
        saved = save_df(labels_df, config.LABELS_CACHE_PARQUET, config.LABELS_CACHE_CSV)
        print(f"      - {saved.relative_to(config.PROJECT_ROOT)}")
        saved = save_df(objects_df, config.OBJECTS_CACHE_PARQUET, config.OBJECTS_CACHE_CSV)
        print(f"      - {saved.relative_to(config.PROJECT_ROOT)}")

    print("[4/4] 분포 리포트 생성")
    scen = scenario_report(labels_df)
    pairs = pair_report(labels_df)
    classes = class_freq_report(objects_df)
    missing_scenarios = missing_scenario_report(labels_df)
    missing_classes = missing_class_report(classes)
    unknown_codes = unknown_codes_report(labels_df, objects_df)

    write_report(scen, "scenario_counts")
    write_report(
        value_counts_report(
            labels_df, "situation_type", lambda c: schema.SITUATION_TYPE_KO.get(c, "")
        ),
        "situation_type_counts",
    )
    write_report(
        value_counts_report(labels_df, "type_id", schema.accident_type_name),
        "accident_type_counts",
    )
    write_report(
        value_counts_report(labels_df, "process_id", schema.process_name), "process_counts"
    )
    write_report(value_counts_report(labels_df, "device", schema.device_name), "device_counts")
    write_report(
        value_counts_report(labels_df, "site_condition", schema.site_condition_name),
        "site_condition_counts",
    )
    write_report(value_counts_report(labels_df, "weather", schema.weather_name), "weather_counts")
    write_report(value_counts_report(labels_df, "location_id"), "location_counts")
    write_report(classes, "class_freq")
    write_report(object_pairs_report(objects_df), "object_pairs")
    write_report(shape_report(objects_df), "annotation_shape_counts")
    write_report(pairs, "pair_check")
    write_report(missing_scenarios, "missing_scenarios")
    if len(missing_classes):
        write_report(missing_classes, "missing_class_hits")
    if len(unknown_codes):
        write_report(unknown_codes, "unknown_codes")
    if len(errors_df):
        write_report(errors_df, "parse_errors")

    mobile_text = mobile_subset_text(labels_df)
    (config.REPORT_DIR / "mobile_subset.txt").write_text(mobile_text, encoding="utf-8")
    print(f"      - {(config.REPORT_DIR / 'mobile_subset.txt').relative_to(config.PROJECT_ROOT)}")

    text, stats = build_summary(
        labels_df,
        objects_df,
        errors_df,
        pairs,
        classes,
        missing_scenarios,
        missing_classes,
        unknown_codes,
    )
    (config.REPORT_DIR / "summary.txt").write_text(text + "\n", encoding="utf-8")
    (config.REPORT_DIR / "summary.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(text)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AI-Hub 라벨 JSON 파싱 및 분포 집계")
    parser.add_argument("--raw-dir", type=Path, default=config.RAW_DIR, help="JSON 루트 (기본 data/raw)")
    parser.add_argument("--force", action="store_true", help="캐시 무시하고 다시 순회")
    parser.add_argument("--limit", type=int, default=None, help="앞 N 개만 파싱 (구조 확인용)")
    parser.add_argument(
        "--workers",
        type=int,
        default=max(os.cpu_count() or 1, 1) // 2 or 1,
        help="병렬 파싱 프로세스 수 (기본: CPU 절반)",
    )
    args = parser.parse_args(argv)
    run(raw_dir=args.raw_dir, force=args.force, limit=args.limit, workers=max(args.workers, 1))


if __name__ == "__main__":
    main()
