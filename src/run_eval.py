"""평가셋 이미지를 Gemini 에 보내 Task B(정상/비정상 판정)를 받는다.

    python src/run_eval.py --set eval_smoke --limit 20 --balance --dry-run
    python src/run_eval.py --set eval_smoke --limit 20 --balance

결과는 data/processed/eval_runs/<run>.jsonl 에 한 줄 1건으로 쌓인다. 원본 응답 텍스트를
그대로 남기므로 나중에 프롬프트를 고쳐도 이전 결과와 비교할 수 있다.

안전장치:
    --dry-run       API 를 호출하지 않고 대상·예상 소모량만 출력
    --limit         호출 상한. 지정하지 않으면 세트 전체라 실수로 수천 건이 나간다
    --rpm           분당 호출 수 제한 (기본 10, 무료 티어 보수적으로)
    이미 결과가 있는 이미지는 건너뛴다 (중단 후 재실행 안전)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import gemini  # noqa: E402
import prompts  # noqa: E402

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    raise SystemExit("pandas 가 필요하다: pip install -r requirements.txt")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

EVALSET_DIR = config.PROCESSED_DIR / "evalset"
RUN_DIR = config.PROCESSED_DIR / "eval_runs"
IMAGE_DIR = config.RAW_DIR / "images"
SEED = 20260918  # make_evalset.py 와 같은 시드


def load_set(name: str) -> pd.DataFrame:
    path = EVALSET_DIR / f"{name}.csv"
    if not path.exists():
        raise SystemExit(f"{path} 가 없다. python src/make_evalset.py 를 먼저 돌려라.")
    df = pd.read_csv(path)
    df["image_path"] = df.image_file.map(lambda n: IMAGE_DIR / n)
    df["exists"] = df.image_path.map(lambda p: p.exists())
    return df


def pick(df: pd.DataFrame, limit: int | None, balance: bool, seed: int) -> pd.DataFrame:
    """평가 대상 선택. balance 면 정상/비정상 같은 수로."""
    available = df[df.exists].copy()
    if not balance:
        return available if limit is None else available.sample(
            n=min(limit, len(available)), random_state=seed
        ).sort_values("image_file")

    # is_normal 은 csv 에서 True/False/빈값(C·SO 계열)으로 들어온다
    normal = available[available.is_normal == True]  # noqa: E712
    abnormal = available[available.is_normal == False]  # noqa: E712
    half = (limit or (len(normal) + len(abnormal))) // 2
    half_n = min(half, len(normal))
    half_a = min(half, len(abnormal))
    if half_n != half_a:
        half_n = half_a = min(half_n, half_a)
    picked = pd.concat(
        [
            normal.sample(n=half_n, random_state=seed),
            abnormal.sample(n=half_a, random_state=seed),
        ]
    )
    return picked.sort_values(["situation_id", "image_file"])


def done_images(run_path: Path) -> set[str]:
    if not run_path.exists():
        return set()
    seen = set()
    for line in run_path.read_text(encoding="utf-8").splitlines():
        try:
            seen.add(json.loads(line)["image_file"])
        except (json.JSONDecodeError, KeyError):
            continue
    return seen


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gemini 로 Task B 판정 실행")
    parser.add_argument("--set", default="eval_smoke", help="평가셋 이름 (기본 eval_smoke)")
    parser.add_argument("--limit", type=int, default=20, help="최대 호출 수 (기본 20)")
    parser.add_argument("--balance", action="store_true", help="정상/비정상 같은 수로 뽑기")
    parser.add_argument("--run-name", default=None, help="결과 파일 이름 (기본: 세트_프롬프트버전)")
    parser.add_argument("--rpm", type=float, default=10.0, help="분당 호출 상한 (기본 10)")
    parser.add_argument("--model", default=None, help=f"모델 (기본 {gemini.DEFAULT_MODEL})")
    parser.add_argument("--long-edge", type=int, default=gemini.DEFAULT_LONG_EDGE, help="리사이즈 긴 변 (기본 1024)")
    parser.add_argument("--no-schema", action="store_true", help="구조화 출력 끄기 (자유형식 JSON 파싱 측정용)")
    parser.add_argument("--dry-run", action="store_true", help="호출하지 않고 대상만 출력")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    model = args.model or gemini.model_name()
    prompt = prompts.task_b(use_schema=not args.no_schema)
    run_name = args.run_name or f"{args.set}_{prompt['version']}"
    run_path = RUN_DIR / f"{run_name}.jsonl"

    df = load_set(args.set)
    missing = int((~df.exists).sum())
    print(f"[1/3] 평가셋 {args.set}: {len(df):,} 장 / 이미지 있음 {int(df.exists.sum()):,} / 없음 {missing:,}")
    if missing:
        print(f"      이미지 없는 항목은 건너뛴다 (python scripts/fetch_eval_images.py 로 확보)")

    targets = pick(df, args.limit, args.balance, args.seed)
    already = done_images(run_path)
    if already:
        targets = targets[~targets.image_file.isin(already)]
        print(f"      기존 결과 {len(already)} 건은 건너뛴다 ({run_path.name})")

    truth = targets.is_normal.map(prompts.truth_label)
    print(f"[2/3] 호출 대상 {len(targets)} 건")
    print(f"      정답 구성: 정상 {int((truth == '정상').sum())} / 비정상 {int((truth == '비정상').sum())} / 판정대상 아님 {int((truth == '').sum())}")
    print(f"      모델 {model} / 프롬프트 {prompt['version']} / 구조화출력 {'off' if args.no_schema else 'on'}")
    print(f"      리사이즈 긴 변 {args.long_edge}px / temperature 0 / RPM 상한 {args.rpm}")
    est_min = len(targets) / args.rpm if args.rpm else 0
    print(f"      예상 소요 {est_min:.1f} 분 (RPM 상한 기준) / 호출 {len(targets)} 회 = RPD 소모 {len(targets)}")

    if args.dry_run:
        print("\n[3/3] --dry-run 이므로 호출하지 않는다. 대상:")
        for row in targets.itertuples():
            print(f"      {row.image_file}  {row.situation_id}  정답={prompts.truth_label(row.is_normal) or '-'}")
        return

    if targets.empty:
        print("\n[3/3] 호출할 대상이 없다.")
        return

    key = gemini.api_key()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    usage = gemini.Usage()
    interval = 60.0 / args.rpm if args.rpm else 0.0
    n_fail = 0
    n_parse_fail = 0

    print(f"\n[3/3] 호출 시작 -> {run_path.relative_to(config.PROJECT_ROOT)}")
    with run_path.open("a", encoding="utf-8") as out:
        for i, row in enumerate(targets.itertuples(), 1):
            started = time.time()
            result = gemini.generate(
                Path(row.image_path),
                prompt,
                key=key,
                model=model,
                long_edge=args.long_edge,
                usage=usage,
            )
            record = {
                "image_file": row.image_file,
                "situation_id": row.situation_id,
                "situation_label": row.situation_label,
                "truth": prompts.truth_label(row.is_normal),
                "type_id": row.type_id,
                "device": int(row.device) if str(row.device).isdigit() else row.device,
                "eval_set": args.set,
                "model": model,
                "prompt_version": prompt["version"],
                "schema": not args.no_schema,
                "long_edge": args.long_edge,
                "ok": result.ok,
                "error": result.error,
                "finish_reason": result.finish_reason,
                "attempts": result.attempts,
                "elapsed_sec": round(result.elapsed, 2),
                "usage": result.usage,
                "raw_text": result.text,
                "parse_error": result.parse_error,
                "parsed": result.parsed,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()

            if not result.ok:
                n_fail += 1
                print(f"  ({i}/{len(targets)}) {row.image_file} 실패: {result.error}")
            else:
                if result.parse_error:
                    n_parse_fail += 1
                judgment = (result.parsed or {}).get("judgment", "?")
                conf = (result.parsed or {}).get("confidence", "?")
                mark = "O" if judgment == record["truth"] else "X"
                print(
                    f"  ({i}/{len(targets)}) {row.image_file[:34]:<34} "
                    f"정답={record['truth'] or '-':<4} 판정={judgment:<5} conf={conf} {mark} "
                    f"{result.elapsed:.1f}초"
                )

            if i < len(targets) and interval:
                time.sleep(max(0.0, interval - (time.time() - started)))

    print(f"\n{usage.summary()}")
    print(f"호출 실패 {n_fail} / JSON 파싱 실패 {n_parse_fail} / 총 {len(targets)}")
    print(f"결과: {run_path.relative_to(config.PROJECT_ROOT)}")
    print(f"채점: python src/score_eval.py --run {run_name}")


if __name__ == "__main__":
    main()
