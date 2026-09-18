"""VLM 평가셋을 만든다. Validation 분할만 쓴다.

    python src/make_evalset.py              # N=10 (종당), 기본 3세트
    python src/make_evalset.py -n 20
    python src/make_evalset.py --mobile-all  # eval_mobile 을 전수로

왜 Validation 만 쓰는가:
    AI-Hub 원본은 Training 180,800 / Validation 22,600 으로 나뉘어 있다(Test 10% 는
    비공개). 평가셋을 Validation 에서만 뽑으면 "학습에 쓰이지 않은 데이터로 평가했다"가
    데이터 분할 자체로 보장된다. 우리는 모델을 학습시키지 않지만, 공개 베이스라인
    (mAP 79.6%)과 같은 조건에서 비교하려면 같은 분할 규칙을 지키는 편이 낫다.

세트:
    eval_main    113종 x N장   주 평가셋
    eval_mobile  device=4 만   실사용 조건 (현장 작업자 휴대폰 사진)
    eval_smoke   113종 x 2장   파이프라인 점검용

산출물 (data/processed/evalset/):
    <세트>.csv   이미지 파일명, 코드값, JSON 경로
    <세트>.txt   구성 통계 (종별 장수, 정상/비정상, 장비 분포)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import schema  # noqa: E402

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    print("pandas 가 필요하다: pip install -r requirements.txt", file=sys.stderr)
    raise

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

SEED = 20260918  # 고정 시드. 바꾸면 평가셋이 달라지므로 기록해 둔다.
EVALSET_DIR = config.PROCESSED_DIR / "evalset"

# csv 에 남길 컬럼. 이미지 파일명과 JSON 경로가 있으면 나중에 이미지만 붙이면 된다.
COLUMNS = [
    "image_file",
    "json_file",
    "situation_id",
    "situation_label",
    "category",
    "is_normal",
    "type_id",
    "type_name",
    "process_id",
    "process_name",
    "device",
    "device_ko",
    "site_condition_ko",
    "weather_ko",
    "location_id",
    "source_zip",
]


def load_labels() -> pd.DataFrame:
    df = None
    if config.LABELS_CACHE_PARQUET.exists():
        df = pd.read_parquet(config.LABELS_CACHE_PARQUET)
    elif config.LABELS_CACHE_CSV.exists():
        df = pd.read_csv(config.LABELS_CACHE_CSV, dtype=str, keep_default_na=False)
    if df is None or df.empty:
        raise SystemExit(
            "labels 캐시가 없다. 먼저 python src/parse_labels.py --force 를 돌려라."
        )
    return df


def add_split(df: pd.DataFrame) -> pd.DataFrame:
    """Training / Validation 을 붙인다.

    이 정보는 JSON 내용이 아니라 파일이 놓인 폴더(01-1.정식개방데이터/Validation/...)
    에서만 알 수 있으므로 source_file 경로로 판정한다.
    """
    path = df["source_file"].fillna("").astype(str)
    df = df.copy()
    df["split"] = ["Validation" if "Validation" in p else "Training" for p in path]
    # 원천 이미지 zip 이름: 라벨 zip 의 TL_/VL_ 접두어만 다르다.
    #   .../VL_5대사고유형_추락_정상_Y-01/H-....json -> VS_5대사고유형_추락_정상_Y-01.zip
    def zip_name(p: str) -> str:
        parent = Path(p).parent.name  # 압축을 푼 폴더명 = zip 이름(확장자 제외)
        if parent.startswith("VL_"):
            return f"VS_{parent[3:]}.zip"
        if parent.startswith("TL_"):
            return f"TS_{parent[3:]}.zip"
        return ""

    df["source_zip"] = [zip_name(p) for p in path]
    return df


def to_eval_columns(df: pd.DataFrame) -> pd.DataFrame:
    """평가셋 csv 컬럼으로 정리. 이미지 파일명은 JSON 파일명에서 유도한다."""
    out = pd.DataFrame(index=df.index)
    json_paths = df["source_file"].fillna("").astype(str)
    # 이미지 파일명은 JSON 내용에서 만든다 (Json_Data_ID + File_extension).
    # 실측 확인: json_id 는 JSON 파일명 stem 과 203,400건 전부 일치, 확장자는 전부 jpg.
    out["image_file"] = [
        f"{jid}.{ext or 'jpg'}" if jid else Path(p).stem + ".jpg"
        for jid, ext, p in zip(df["json_id"], df["file_ext"].fillna("jpg"), json_paths)
    ]
    out["json_file"] = [
        str(Path(p).relative_to(config.RAW_DIR)) if p and Path(p).is_absolute() else p
        for p in json_paths
    ]
    out["situation_id"] = df["situation_id"]
    out["situation_label"] = df["situation_id"].map(schema.situation_label)
    out["category"] = df["situation_id"].map(schema.situation_category)
    out["is_normal"] = df["situation_id"].map(schema.is_normal)
    out["type_id"] = df["type_id"]
    out["type_name"] = df["type_id"].map(schema.accident_type_name)
    out["process_id"] = df["process_id"]
    out["process_name"] = df["process_id"].map(schema.process_name)
    out["device"] = df["device"]
    out["device_ko"] = df["device"].map(schema.device_name)
    out["site_condition_ko"] = df.get("site_condition_ko")
    out["weather_ko"] = df.get("weather_ko")
    out["location_id"] = df.get("location_id")
    out["source_zip"] = df["source_zip"]
    return out[COLUMNS]


def stratified(df: pd.DataFrame, n_per: int, seed: int = SEED) -> pd.DataFrame:
    """situation_id 별로 최대 n_per 장씩. 장수가 부족한 종은 있는 만큼.

    종마다 같은 시드를 쓰므로 n 을 늘리면 기존 표본을 포함하지는 않는다.
    세트를 바꿀 때는 시드와 n 을 같이 기록해 둘 것.
    """
    picks = [
        group.sample(n=min(n_per, len(group)), random_state=seed)
        for _, group in df.groupby("situation_id", sort=True)
    ]
    if not picks:
        return df.iloc[0:0]
    return (
        pd.concat(picks)
        .sort_values(["situation_id", "image_file"])
        .reset_index(drop=True)
    )


def describe_set(name: str, eval_df: pd.DataFrame, note: str = "") -> str:
    """세트 구성 통계를 사람이 읽을 형태로."""
    L = [
        f"평가셋 {name}",
        "=" * 64,
        f"총 {len(eval_df):,} 장 / 시나리오 {eval_df.situation_id.nunique()} 종",
        f"시드 {SEED} (고정) · Validation 분할만 사용",
    ]
    if note:
        L.append(note)

    counts = eval_df.situation_id.value_counts()
    L += [
        "",
        f"종당 장수: 최소 {counts.min()} / 최대 {counts.max()} / 평균 {counts.mean():.1f}",
        "",
        "정상/비정상:",
    ]
    normal = eval_df.is_normal.map(
        lambda v: "정상(Y)" if v is True else ("비정상(N)" if v is False else "판정 없음(C/SO)")
    )
    for label, n in normal.value_counts().items():
        L.append(f"  {label:<16} {n:>6,}  ({n / len(eval_df) * 100:5.2f}%)")

    L += ["", "사고유형:"]
    for code, n in eval_df.type_id.value_counts().sort_index().items():
        L.append(f"  {code} {schema.accident_type_name(code):<12} {n:>6,}")

    L += ["", "촬영장비:"]
    for code, n in eval_df.device.value_counts().sort_index().items():
        mark = "   <== 실사용 조건" if str(code) == schema.DEVICE_PHONE else ""
        L.append(f"  {code} {schema.device_name(code):<12} {n:>6,}{mark}")

    L += ["", "실내외 / 날씨:"]
    for col in ("site_condition_ko", "weather_ko"):
        vals = ", ".join(f"{k} {v:,}" for k, v in eval_df[col].value_counts().items())
        L.append(f"  {col}: {vals}")

    L += [
        "",
        f"필요한 원천 zip {eval_df.source_zip.nunique()} 개:",
    ]
    for zip_name, n in eval_df.source_zip.value_counts().sort_index().items():
        L.append(f"  {zip_name:<50} {n:>5} 장")

    L += ["", "시나리오별:", f"  {'situation_id':<14}{'n':>5}  설명"]
    label_by_id = dict(zip(eval_df.situation_id, eval_df.situation_label))
    for sid, n in counts.sort_index().items():
        L.append(f"  {sid:<14}{n:>5}  {label_by_id.get(sid, '')}")
    return "\n".join(L) + "\n"


def write_set(name: str, eval_df: pd.DataFrame, note: str = "") -> None:
    EVALSET_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = EVALSET_DIR / f"{name}.csv"
    txt_path = EVALSET_DIR / f"{name}.txt"
    eval_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    txt_path.write_text(describe_set(name, eval_df, note), encoding="utf-8")
    print(
        f"  {name:<12} {len(eval_df):>6,} 장 / {eval_df.situation_id.nunique():>3} 종 "
        f"/ zip {eval_df.source_zip.nunique():>3} 개  -> {csv_path.name}, {txt_path.name}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validation 분할에서 VLM 평가셋 생성")
    parser.add_argument("-n", "--per-scenario", type=int, default=10, help="종당 장수 (기본 10)")
    parser.add_argument("--smoke-per-scenario", type=int, default=2, help="smoke 세트 종당 장수")
    parser.add_argument(
        "--mobile-all",
        action="store_true",
        help="eval_mobile 을 층화 표본 대신 Validation 휴대폰 전수로",
    )
    parser.add_argument("--seed", type=int, default=SEED, help=f"난수 시드 (기본 {SEED})")
    args = parser.parse_args(argv)

    df = add_split(load_labels())
    val = df[df.split == "Validation"]
    print(f"[1/3] Validation {len(val):,} 장 (Training {len(df) - len(val):,} 장은 사용하지 않음)")
    if val.empty:
        raise SystemExit("Validation 데이터가 없다. data/raw 에 VL_* 가 풀려 있는지 확인해라.")

    per_code = val.situation_id.value_counts()
    short = per_code[per_code < args.per_scenario]
    print(f"      시나리오 {len(per_code)} 종 / 종당 {per_code.min()}~{per_code.max()} 장")
    if len(short):
        print(f"      경고: 요청한 {args.per_scenario} 장보다 적은 종 {len(short)} 개 — 있는 만큼만 뽑는다")

    eval_val = to_eval_columns(val)
    print("[2/3] 세트 생성")
    write_set("eval_main", stratified(eval_val, args.per_scenario, args.seed))

    mobile = eval_val[eval_val.device == schema.DEVICE_PHONE]
    if mobile.empty:
        print("  eval_mobile   건너뜀 — Validation 에 휴대폰 촬영분이 없다")
    else:
        n_codes = mobile.situation_id.nunique()
        if args.mobile_all:
            note = f"Validation 휴대폰 전수 ({n_codes} 종에만 존재)"
            write_set("eval_mobile", mobile.sort_values(["situation_id", "image_file"]).reset_index(drop=True), note)
        else:
            note = (
                f"Validation 휴대폰은 {n_codes} 종에만 존재한다 (전체 113 종 중). "
                f"전수는 {len(mobile):,} 장 — 전수로 쓰려면 --mobile-all"
            )
            write_set("eval_mobile", stratified(mobile, args.per_scenario, args.seed), note)

    write_set("eval_smoke", stratified(eval_val, args.smoke_per_scenario, args.seed))

    print("[3/3] 완료")
    print(f"      {EVALSET_DIR.relative_to(config.PROJECT_ROOT)}")
    print("      이미지는 아직 없다. csv 의 source_zip 이 받아야 할 원천 zip 이다.")


if __name__ == "__main__":
    main()
