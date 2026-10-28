"""경로 및 환경변수 설정.

API 키는 코드에 절대 하드코딩하지 않는다. .env 에서만 읽는다.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 경로
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# parse_labels.py 산출물
LABELS_CACHE_PARQUET = PROCESSED_DIR / "labels.parquet"
LABELS_CACHE_CSV = PROCESSED_DIR / "labels.csv"
OBJECTS_CACHE_PARQUET = PROCESSED_DIR / "objects.parquet"
OBJECTS_CACHE_CSV = PROCESSED_DIR / "objects.csv"
REPORT_DIR = PROCESSED_DIR / "reports"
PARSE_ERRORS_CSV = REPORT_DIR / "parse_errors.csv"

ENV_FILE = PROJECT_ROOT / ".env"

# ---------------------------------------------------------- 평가 호출 설정
# 한 곳에 모아 둔다. 스모크와 본평가의 설정이 달라지면 비교가 무효가 되므로,
# 실행할 때마다 결과 파일 옆에 .meta.json 으로 스냅샷을 남긴다.
#
# EVAL_SEED: Interactions API 에는 temperature 가 없고 generation_config.seed 로
#   재현성을 요구한다. make_evalset.py 의 표본 추출 시드와 같은 값을 쓴다.
#   주의: seed 고정이 결정성을 "보장"한다고 문서가 명시하지 않는다. 실제로 같은
#   응답이 나오는지는 run_eval.py --determinism-check 로 확인한다.
EVAL_SEED = 20260918

# 판정 과제에 긴 사고는 필요 없다. minimal/low/medium/high 중 low.
EVAL_THINKING_LEVEL = "low"

# 응답 상한. 우리 스키마는 200 토큰이면 충분하고, 상한을 명시해야 설정이 고정된다.
EVAL_MAX_OUTPUT_TOKENS = 1024


def ensure_dirs() -> None:
    for d in (RAW_DIR, PROCESSED_DIR, REPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------ 환경변수
def load_env(env_file: Path = ENV_FILE) -> None:
    """.env 를 os.environ 에 올린다. python-dotenv 가 없으면 직접 파싱한다."""
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_file, override=False)
        return
    except ImportError:
        pass

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_env(name: str, default: str = "") -> str:
    load_env()
    return os.environ.get(name, default)


def require_env(name: str) -> str:
    value = get_env(name)
    if not value:
        raise RuntimeError(
            f"{name} 이(가) 비어 있다. .env.example 을 .env 로 복사한 뒤 값을 채워라."
        )
    return value


# 편의 접근자 — import 시점에 읽지 않는다(키가 없어도 import 는 되어야 하므로).
def aihub_api_key() -> str:
    return require_env("AIHUB_API_KEY")


def datasetkey_risk() -> str:
    return require_env("AIHUB_DATASETKEY_RISK")


def datasetkey_equip() -> str:
    return get_env("AIHUB_DATASETKEY_EQUIP", "163")
