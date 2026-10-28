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


def pick(
    df: pd.DataFrame,
    limit: int | None,
    balance: bool,
    seed: int,
    by_type: bool = False,
) -> pd.DataFrame:
    """평가 대상 선택.

    balance   정상/비정상 같은 수로
    by_type   5대 사고유형(A~E)에 고르게 — 유형별로 정상/비정상 반반
              한 유형에 몰려 있으면 유형별 성능 비교가 안 되므로 스모크에 쓴다.
    """
    available = df[df.exists].copy()
    if not balance:
        return available if limit is None else available.sample(
            n=min(limit, len(available)), random_state=seed
        ).sort_values("image_file")

    # is_normal 은 csv 에서 True/False/빈값(C·SO 계열)으로 들어온다
    normal = available[available.is_normal == True]  # noqa: E712
    abnormal = available[available.is_normal == False]  # noqa: E712

    if not by_type:
        half = (limit or (len(normal) + len(abnormal))) // 2
        half_n = half_a = min(half, len(normal), len(abnormal))
        picked = pd.concat(
            [
                normal.sample(n=half_n, random_state=seed),
                abnormal.sample(n=half_a, random_state=seed),
            ]
        )
        return picked.sort_values(["situation_id", "image_file"])

    # 5대 사고유형 x 정상/비정상 = 10칸. limit 를 10칸에 고르게 나눈다.
    types = [t for t in schema.MAJOR_ACCIDENT_TYPES if (available.type_id == t).any()]
    if not types:
        raise SystemExit("5대 사고유형(A~E) 이미지가 없다.")
    per_cell = max((limit or len(available)) // (len(types) * 2), 1)

    frames = []
    for type_id in types:
        for pool in (normal, abnormal):
            cell = pool[pool.type_id == type_id]
            if cell.empty:
                continue
            # 같은 시나리오만 뽑히지 않게 시나리오를 먼저 섞고 1장씩 돌아가며 뽑는다
            shuffled = cell.sample(frac=1.0, random_state=seed)
            shuffled = shuffled.assign(
                _rank=shuffled.groupby("situation_id").cumcount()
            ).sort_values(["_rank", "situation_id"])
            frames.append(shuffled.head(per_cell).drop(columns=["_rank"]))
    picked = pd.concat(frames) if frames else available.iloc[0:0]
    return picked.sort_values(["type_id", "situation_id", "image_file"])


def done_images(run_path: Path) -> set[str]:
    """이미 '성공'한 이미지만 건너뛴다.

    503 같은 인프라 실패로 끝난 레코드는 판정을 얻지 못한 것이므로 다시 호출해야
    한다. 실패까지 건너뛰면 재실행해도 영영 비어 있는 칸이 남는다.
    """
    if not run_path.exists():
        return set()
    seen = set()
    for line in run_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("ok") and record.get("image_file"):
            seen.add(record["image_file"])
    return seen


def determinism_check(args, model: str, prompt: dict, run_path: Path) -> None:
    """같은 이미지를 1회 더 호출해 기존 jsonl 결과와 비교한다.

    Interactions API 의 seed 가 결정성을 보장한다고 문서가 명시하지 않는다.
    보장되지 않으면 그 사실 자체가 결과보고서에 적어야 할 한계다.
    """
    image_file = args.determinism_check
    previous = None
    if run_path.exists():
        for line in run_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("image_file") == image_file and rec.get("ok"):
                previous = rec
    if previous is None:
        raise SystemExit(f"{run_path.name} 에 {image_file} 의 성공 결과가 없다.")

    path = IMAGE_DIR / image_file
    print(f"[결정성 확인] {image_file}")
    print(f"  seed={config.EVAL_SEED} / thinking={config.EVAL_THINKING_LEVEL} / model={model}")
    usage = gemini.Usage()
    result = gemini.generate(path, prompt, key=gemini.api_key(), model=model,
                             long_edge=args.long_edge, usage=usage)
    if not result.ok:
        print(f"  재호출 실패: {result.error}")
        return

    before, after = previous.get("raw_text", ""), result.text
    same_raw = before.strip() == after.strip()
    pb, pa = previous.get("parsed") or {}, result.parsed or {}
    same_judgment = pb.get("judgment") == pa.get("judgment")
    same_conf = pb.get("confidence") == pa.get("confidence")

    print(f"\n  1회차 judgment={pb.get('judgment')} conf={pb.get('confidence')}")
    print(f"  2회차 judgment={pa.get('judgment')} conf={pa.get('confidence')}")
    print(f"\n  raw_text 완전 일치 : {same_raw}")
    print(f"  judgment 일치      : {same_judgment}")
    print(f"  confidence 일치    : {same_conf}")
    if not same_raw:
        print("\n  --- 1회차 observed ---")
        print(f"  {pb.get('observed', '')}")
        print("  --- 2회차 observed ---")
        print(f"  {pa.get('observed', '')}")

    out = run_path.with_name(run_path.stem + ".determinism.json")
    out.write_text(
        json.dumps(
            {
                "image_file": image_file,
                "seed": config.EVAL_SEED,
                "model_requested": model,
                "model_responded_1": previous.get("model_responded"),
                "model_responded_2": result.model_responded,
                "same_raw_text": same_raw,
                "same_judgment": same_judgment,
                "same_confidence": same_conf,
                "first": pb,
                "second": pa,
                "first_raw": before,
                "second_raw": after,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  기록 -> {out.name}")
    print(f"  {usage.summary()}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gemini 로 Task B 판정 실행")
    parser.add_argument("--set", default="eval_smoke", help="평가셋 이름 (기본 eval_smoke)")
    parser.add_argument("--limit", type=int, default=20, help="최대 호출 수 (기본 20)")
    parser.add_argument("--balance", action="store_true", help="정상/비정상 같은 수로 뽑기")
    parser.add_argument(
        "--by-type",
        action="store_true",
        help="5대 사고유형(A~E)에 고르게 분배 (--balance 와 함께 쓴다)",
    )
    parser.add_argument("--run-name", default=None, help="결과 파일 이름 (기본: 세트_프롬프트버전)")
    parser.add_argument("--rpm", type=float, default=10.0, help="분당 호출 상한 (기본 10)")
    parser.add_argument("--model", default=None, help=f"모델 (기본 {gemini.DEFAULT_MODEL})")
    parser.add_argument("--long-edge", type=int, default=gemini.DEFAULT_LONG_EDGE, help="리사이즈 긴 변 (기본 1024)")
    parser.add_argument("--no-schema", action="store_true", help="구조화 출력 끄기 (자유형식 JSON 파싱 측정용)")
    parser.add_argument("--dry-run", action="store_true", help="호출하지 않고 대상만 출력")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=gemini.DEFAULT_MAX_RETRIES,
        help=f"호출당 최대 시도 수 (기본 {gemini.DEFAULT_MAX_RETRIES}, 최초 시도 포함)",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=5,
        help="연속 호출 실패가 이 수에 도달하면 중단 (기본 5). 0 이면 끝까지 진행",
    )
    parser.add_argument(
        "--determinism-check",
        metavar="IMAGE_FILE",
        default=None,
        help="이 이미지를 1회 더 호출해 기존 결과와 같은 응답인지 확인 (호출 1건)",
    )
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)

    model = args.model or gemini.model_name()
    prompt = prompts.task_b(use_schema=not args.no_schema)
    run_name = args.run_name or f"{args.set}_{prompt['version']}"
    run_path = RUN_DIR / f"{run_name}.jsonl"

    if args.determinism_check:
        determinism_check(args, model, prompt, run_path)
        return

    df = load_set(args.set)
    missing = int((~df.exists).sum())
    print(f"[1/3] 평가셋 {args.set}: {len(df):,} 장 / 이미지 있음 {int(df.exists.sum()):,} / 없음 {missing:,}")
    if missing:
        print(f"      이미지 없는 항목은 건너뛴다 (python scripts/fetch_eval_images.py 로 확보)")

    targets = pick(df, args.limit, args.balance, args.seed, by_type=args.by_type)
    already = done_images(run_path)
    if already:
        targets = targets[~targets.image_file.isin(already)]
        print(f"      기존 결과 {len(already)} 건은 건너뛴다 ({run_path.name})")

    truth = targets.is_normal.map(prompts.truth_label)
    print(f"[2/3] 호출 대상 {len(targets)} 건")
    print(f"      정답 구성: 정상 {int((truth == '정상').sum())} / 비정상 {int((truth == '비정상').sum())} / 판정대상 아님 {int((truth == '').sum())}")
    if len(targets):
        by_type_counts = targets.groupby("type_id").size()
        print(
            "      사고유형: "
            + " / ".join(
                f"{t} {schema.accident_type_name(t)} {n}" for t, n in by_type_counts.items()
            )
        )
        print(f"      시나리오 {targets.situation_id.nunique()} 종 / 촬영장비 "
              + ", ".join(f"{k}={v}" for k, v in targets.device.astype(str).value_counts().sort_index().items()))
    print(f"      모델 {model} / 프롬프트 {prompt['version']} / 구조화출력 {'off' if args.no_schema else 'on'}")
    print(f"      리사이즈 긴 변 {args.long_edge}px / seed 고정 / RPM 상한 {args.rpm}")
    print(f"      호출당 최대 시도 {args.max_retries} / 연속 실패 {args.max_consecutive_errors}건이면 중단")
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

    # 설정 스냅샷. 스모크와 본평가의 설정이 달라지면 비교가 무효가 되므로 남긴다.
    meta_path = RUN_DIR / f"{run_name}.meta.json"
    snapshot = gemini.config_snapshot(
        model,
        prompt,
        long_edge=args.long_edge,
        seed=config.EVAL_SEED,
        thinking_level=config.EVAL_THINKING_LEVEL,
        max_output_tokens=config.EVAL_MAX_OUTPUT_TOKENS,
    )
    snapshot.update(
        {
            "run_name": run_name,
            "eval_set": args.set,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_images_planned": len(targets),
            "n_images_already_done": len(already),
            "rpm_limit": args.rpm,
            "max_retries": args.max_retries,
            "sampling": {
                "limit": args.limit,
                "balance": args.balance,
                "by_type": args.by_type,
                "seed": args.seed,
            },
        }
    )
    meta_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"      설정 스냅샷 -> {meta_path.name}")
    usage = gemini.Usage()
    interval = 60.0 / args.rpm if args.rpm else 0.0
    n_fail = 0
    n_parse_fail = 0
    consecutive_errors = 0
    stopped_early = False

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
                max_retries=args.max_retries,
                usage=usage,
            )
            # 503 등 인프라 실패와 모델의 "판단불가"는 전혀 다른 것이다.
            # status 로 한 칸에 섞이지 않게 분리해 둔다.
            parsed = result.parsed or {}
            if not result.ok:
                status = "error"          # 호출 자체가 실패 (인프라)
            elif result.parse_error:
                status = "parse_error"    # 응답은 왔는데 JSON 이 아님
            else:
                status = "judged"         # 판정을 받음 (판단불가도 여기에 포함)

            record = {
                "image_file": row.image_file,
                "situation_id": row.situation_id,
                "situation_label": row.situation_label,
                "truth": prompts.truth_label(row.is_normal),
                "type_id": row.type_id,
                "device": int(row.device) if str(row.device).isdigit() else row.device,
                "eval_set": args.set,
                "status": status,
                "judgment": parsed.get("judgment"),  # 판단불가는 여기, 인프라 실패는 status
                "model": model,                      # 요청한 모델
                "model_responded": result.model_responded,  # 응답이 밝힌 모델 (혼용 검사용)
                "prompt_version": prompt["version"],
                "seed": config.EVAL_SEED,
                "thinking_level": config.EVAL_THINKING_LEVEL,
                "max_output_tokens": config.EVAL_MAX_OUTPUT_TOKENS,
                "schema": not args.no_schema,
                "long_edge": args.long_edge,
                "ok": result.ok,
                "error": result.error,
                "finish_reason": result.finish_reason,
                "attempts": result.attempts,
                "retry_waits": result.retry_waits,
                "retry_after_seen": result.retry_after_seen,
                "quota_headers": result.quota_headers,
                "elapsed_sec": round(result.elapsed, 2),
                "usage": result.usage,
                "raw_text": result.text,
                "parse_error": result.parse_error,
                "parsed": result.parsed,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()

            retry_note = f" 재시도{result.attempts - 1}" if result.attempts > 1 else ""
            if not result.ok:
                n_fail += 1
                consecutive_errors += 1
                print(f"  ({i}/{len(targets)}) {row.image_file} 호출실패{retry_note}: {result.error[:120]}")
            else:
                if result.parse_error:
                    n_parse_fail += 1
                judgment = (result.parsed or {}).get("judgment", "?")
                conf = (result.parsed or {}).get("confidence", "?")
                consecutive_errors = 0
                mark = "O" if judgment == record["truth"] else "X"
                print(
                    f"  ({i}/{len(targets)}) {row.image_file[:34]:<34} "
                    f"정답={record['truth'] or '-':<4} 판정={judgment:<5} conf={conf} {mark} "
                    f"{result.elapsed:.1f}초{retry_note}"
                )

            # 503 이 이어지면 재시도로 한도만 태운다. 일정 수 연속 실패하면 멈춘다.
            if args.max_consecutive_errors and consecutive_errors >= args.max_consecutive_errors:
                stopped_early = True
                print(
                    f"\n  !! 연속 호출 실패 {consecutive_errors}건 — 중단한다."
                    f" 남은 {len(targets) - i}건은 나중에 재실행하면 이어서 처리된다."
                )
                break

            if i < len(targets) and interval:
                time.sleep(max(0.0, interval - (time.time() - started)))

    print(f"\n{usage.summary()}")
    print(f"호출 실패(인프라) {n_fail} / JSON 파싱 실패 {n_parse_fail} / 총 {len(targets)}")
    if stopped_early:
        print("중단됨: 연속 실패 한계 도달. 같은 명령을 다시 실행하면 성공분은 건너뛰고 이어서 돈다.")
    print(f"결과: {run_path.relative_to(config.PROJECT_ROOT)}")
    print(f"채점: python src/score_eval.py --run {run_name}")


if __name__ == "__main__":
    main()
