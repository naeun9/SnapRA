"""평가셋 이미지를 zip 하나씩 받아 필요한 것만 추출하고 zip 은 버린다.

    python scripts/fetch_eval_images.py --dry-run   # 계획만 출력 (다운로드 없음)
    python scripts/fetch_eval_images.py             # 실행
    python scripts/fetch_eval_images.py --only-zip VS_주의요망작업_C-01.zip

왜 순차 스트리밍인가:
    Validation 원천 이미지는 zip 113개 27 GB 인데 실제로 필요한 이미지는 8,194장
    (약 10 GB)이다. 전부 받아 놓고 푸는 방식은 27 + 10 GB 를 동시에 요구해서
    디스크가 버티지 못한다. zip 하나(최대 408 MB)를 받아 필요한 이미지만 꺼내고
    바로 지우면 순간 최대 사용량이 한 자리 GB 로 떨어진다.

중단/재시작:
    진행 상태를 data/raw/images/_progress.json 에 남긴다. 다시 실행하면 이미 끝낸
    zip 은 건너뛴다. 실패한 zip 도 기록하고 계속 진행하며, 마지막에 요약한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402

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

EVALSET_DIR = config.PROCESSED_DIR / "evalset"
IMAGE_DIR = config.RAW_DIR / "images"
PROGRESS_FILE = IMAGE_DIR / "_progress.json"
FILEKEY_FILE = ROOT / "filekeys_validation_source.json"
DOWNLOAD_SH = ROOT / "scripts" / "download.sh"

# 한 zip(최대 408 MB) + 추출분 + 여유. 이보다 적으면 시작하지 않는다.
MIN_FREE_GB = 3.0
DATASETKEY = "71407"


# ------------------------------------------------------------------ 진행 상태
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print("  진행 파일을 읽을 수 없다. 처음부터 시작한다.", file=sys.stderr)
    return {"done": {}, "failed": {}}


def save_progress(progress: dict) -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(
        json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ------------------------------------------------------------------ 준비
def wanted_images() -> pd.DataFrame:
    """3개 평가셋의 합집합. zip 이름과 이미지 파일명만 남긴다."""
    frames = []
    for csv_path in sorted(EVALSET_DIR.glob("eval_*.csv")):
        frames.append(pd.read_csv(csv_path, usecols=["image_file", "source_zip"]))
        print(f"      {csv_path.name}: {len(frames[-1]):,} 장")
    if not frames:
        raise SystemExit(
            f"{EVALSET_DIR} 에 eval_*.csv 가 없다. python src/make_evalset.py 를 먼저 돌려라."
        )
    union = pd.concat(frames).drop_duplicates(subset=["image_file"])
    return union[union.source_zip.notna() & (union.source_zip != "")]


def filekeys() -> dict[str, dict]:
    if not FILEKEY_FILE.exists():
        raise SystemExit(
            f"{FILEKEY_FILE.name} 이 없다. bash scripts/download.sh files {DATASETKEY} 로 "
            "파일 목록을 받고 filekey 를 정리해라."
        )
    data = json.loads(FILEKEY_FILE.read_text(encoding="utf-8"))
    return {e["name"]: e for e in data["files"]}


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1024**3


def size_mb(text: str) -> float:
    """'408 MB' -> 408.0"""
    import re

    m = re.match(r"([\d.]+)\s*([KMGT]?B)", str(text))
    if not m:
        return 0.0
    value, unit = m.groups()
    return float(value) * {"B": 1 / 1048576, "KB": 1 / 1024, "MB": 1, "GB": 1024}[unit]


# ------------------------------------------------------------------ 처리
def download_zip(filekey: str) -> Path | None:
    """download.sh get 으로 zip 1개를 받는다. 받은 zip 경로를 돌려준다."""
    before = {p for p in config.RAW_DIR.rglob("*.zip")}
    result = subprocess.run(
        ["bash", str(DOWNLOAD_SH), "get", DATASETKEY, filekey],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        errors="replace",
    )
    after = {p for p in config.RAW_DIR.rglob("*.zip")}
    new = sorted(after - before)
    if not new:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        print(f"      다운로드 실패: {' / '.join(tail) if tail else '새 zip 없음'}")
        return None
    if len(new) > 1:
        print(f"      경고: zip 이 {len(new)} 개 생겼다. 첫 번째만 처리한다.")
    return new[0]


def extract_wanted(zip_path: Path, wanted: set[str]) -> tuple[int, list[str]]:
    """zip 에서 원하는 이미지만 꺼낸다. (추출 수, 못 찾은 파일명)."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    found = 0
    seen: set[str] = set()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename.replace("\\", "/")).name
            if name not in wanted:
                continue
            seen.add(name)
            target = IMAGE_DIR / name
            if target.exists() and target.stat().st_size == info.file_size:
                found += 1
                continue
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            found += 1
    return found, sorted(wanted - seen)


def process_one(
    zip_name: str, entry: dict, wanted: set[str], progress: dict, dry_run: bool
) -> None:
    mb = size_mb(entry["size"])
    print(f"  [{zip_name}] {mb:.0f} MB / 필요 이미지 {len(wanted)} 장", flush=True)
    if dry_run:
        return

    if free_gb(config.RAW_DIR) < MIN_FREE_GB:
        raise SystemExit(
            f"디스크 여유가 {free_gb(config.RAW_DIR):.1f} GB 뿐이다 "
            f"(최소 {MIN_FREE_GB} GB 필요). 공간을 확보하고 다시 실행해라. "
            "진행 상태는 저장돼 있어 이어서 받는다."
        )

    started = time.time()
    zip_path = download_zip(entry["filekey"])
    if zip_path is None:
        progress["failed"][zip_name] = "다운로드 실패"
        save_progress(progress)
        return

    try:
        extracted, missing = extract_wanted(zip_path, wanted)
    except (zipfile.BadZipFile, OSError) as exc:
        progress["failed"][zip_name] = f"{type(exc).__name__}: {exc}"
        save_progress(progress)
        print(f"      추출 실패: {exc}")
        zip_path.unlink(missing_ok=True)
        return

    # zip 은 바로 버린다. 이게 이 스크립트의 존재 이유다.
    zip_path.unlink(missing_ok=True)
    # aihubshell 이 만든 빈 디렉터리 정리
    for parent in list(zip_path.parents):
        if parent == config.RAW_DIR:
            break
        try:
            parent.rmdir()
        except OSError:
            break

    elapsed = time.time() - started
    progress["done"][zip_name] = {
        "extracted": extracted,
        "wanted": len(wanted),
        "seconds": round(elapsed, 1),
    }
    if missing:
        progress["done"][zip_name]["missing"] = missing[:20]
    save_progress(progress)
    mark = "" if not missing else f"  (zip 에 없던 파일 {len(missing)} 장)"
    print(f"      추출 {extracted}/{len(wanted)} 장 / {elapsed:.0f}초{mark}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="평가셋 이미지만 순차 스트리밍으로 확보")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력, 다운로드 안 함")
    parser.add_argument("--only-zip", action="append", help="이 zip 만 처리 (여러 번 지정 가능)")
    parser.add_argument("--retry-failed", action="store_true", help="실패 기록된 zip 을 다시 시도")
    args = parser.parse_args(argv)

    print("[1/3] 평가셋에서 필요한 이미지 목록")
    union = wanted_images()
    by_zip: dict[str, set[str]] = {}
    for zip_name, group in union.groupby("source_zip"):
        by_zip[str(zip_name)] = set(group.image_file)
    print(f"      합집합 {len(union):,} 장 / zip {len(by_zip)} 개")

    keys = filekeys()
    unknown = [z for z in by_zip if z not in keys]
    if unknown:
        raise SystemExit(f"파일 목록에 없는 zip: {unknown[:5]}")

    progress = load_progress()
    if args.retry_failed:
        for zip_name in list(progress["failed"]):
            progress["failed"].pop(zip_name, None)

    targets = sorted(by_zip)
    if args.only_zip:
        targets = [z for z in targets if z in set(args.only_zip)]
    todo = [z for z in targets if z not in progress["done"]]

    total_mb = sum(size_mb(keys[z]["size"]) for z in todo)
    done_images = sum(v.get("extracted", 0) for v in progress["done"].values())
    print("[2/3] 계획")
    print(f"      처리할 zip {len(todo)} / 전체 {len(targets)} (완료 {len(progress['done'])})")
    print(f"      다운로드 총량 {total_mb / 1024:.2f} GB (한 번에 최대 {max([size_mb(keys[z]['size']) for z in todo], default=0):.0f} MB)")
    print(f"      추출 예상 {sum(len(by_zip[z]) for z in todo):,} 장 (이미 {done_images:,} 장 확보)")
    for speed in (3.5, 7.0):
        print(f"      예상 소요 {total_mb / speed / 60:.0f} 분 @ {speed} MB/s")
    print(f"      디스크 여유 {free_gb(config.RAW_DIR):.1f} GB / 최소 요구 {MIN_FREE_GB} GB")
    print(f"      보관 위치 {IMAGE_DIR.relative_to(config.PROJECT_ROOT)}")
    if progress["failed"]:
        print(f"      이전 실패 {len(progress['failed'])} 건 (--retry-failed 로 재시도)")

    if args.dry_run:
        print("\n[3/3] --dry-run 이므로 여기서 멈춘다.")
        print("      zip 별 필요 장수:")
        for zip_name in todo[:10]:
            print(f"        {len(by_zip[zip_name]):>4} 장  {size_mb(keys[zip_name]['size']):>5.0f} MB  {zip_name}")
        if len(todo) > 10:
            print(f"        ... 외 {len(todo) - 10} 개")
        return

    if not todo:
        print("\n[3/3] 처리할 zip 이 없다. 이미 전부 끝났다.")
    else:
        print("\n[3/3] 순차 처리 시작 (Ctrl+C 로 중단해도 이어서 받을 수 있다)")
        started = time.time()
        for i, zip_name in enumerate(todo, 1):
            print(f"\n  ({i}/{len(todo)}) 경과 {(time.time() - started) / 60:.0f}분", flush=True)
            process_one(zip_name, keys[zip_name], by_zip[zip_name], progress, args.dry_run)

    # ------------------------------------------------------------- 요약
    have = {p.name for p in IMAGE_DIR.glob("*.jpg")} if IMAGE_DIR.exists() else set()
    want = set(union.image_file)
    stored = sum(p.stat().st_size for p in IMAGE_DIR.glob("*.jpg")) if IMAGE_DIR.exists() else 0
    print("\n" + "=" * 60)
    print("요약")
    print(f"  확보 이미지   {len(have & want):,} / {len(want):,} 장")
    print(f"  보관 용량     {stored / 1024**3:.2f} GB")
    print(f"  처리 완료 zip {len(progress['done'])} / {len(targets)}")
    if progress["failed"]:
        print(f"  실패 zip {len(progress['failed'])} 개:")
        for zip_name, reason in list(progress["failed"].items())[:10]:
            print(f"    {zip_name}: {reason}")
        print("  재시도: python scripts/fetch_eval_images.py --retry-failed")
    missing = want - have
    if missing:
        print(f"  아직 없는 이미지 {len(missing):,} 장 (예: {sorted(missing)[:3]})")
    else:
        print("  평가셋 이미지 전부 확보됨")


if __name__ == "__main__":
    main()
