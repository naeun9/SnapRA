"""run_eval.py 결과를 채점한다.

    python src/score_eval.py --run eval_smoke_task_b.v1
    python src/score_eval.py --run ... --rows      # 20건 원본 표까지 출력

정답은 시나리오 ID 의 Y/N 이다(tables.is_normal 기준). C/SO 계열은 정상/비정상 판정
대상이 아니므로 정확도 계산에서 제외하고 따로 센다.

출력:
    reports/<run>_score.txt   채점 요약
    reports/<run>_rows.csv    건별 결과 (정답, 판정, conf, observed, reason)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
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

RUN_DIR = config.PROCESSED_DIR / "eval_runs"


def load_run(name: str) -> pd.DataFrame:
    path = RUN_DIR / f"{name}.jsonl"
    if not path.exists():
        available = sorted(p.stem for p in RUN_DIR.glob("*.jsonl"))
        raise SystemExit(
            f"{path} 가 없다. 있는 실행: {available or '(없음)'}"
        )
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not records:
        raise SystemExit(f"{path} 에 읽을 수 있는 레코드가 없다.")

    rows = []
    for r in records:
        parsed = r.get("parsed") or {}
        usage = r.get("usage") or {}
        rows.append(
            {
                "image_file": r.get("image_file"),
                "situation_id": r.get("situation_id"),
                "situation_label": r.get("situation_label"),
                "truth": r.get("truth") or "",
                "type_id": r.get("type_id"),
                "device": r.get("device"),
                "ok": bool(r.get("ok")),
                "error": r.get("error"),
                "parse_error": r.get("parse_error"),
                "judgment": parsed.get("judgment", ""),
                "confidence": parsed.get("confidence"),
                "observed": parsed.get("observed", ""),
                "reason": parsed.get("reason", ""),
                "hazard_type": parsed.get("hazard_type", ""),
                "missing_measure": parsed.get("missing_measure", ""),
                "raw_text": r.get("raw_text", ""),
                "elapsed_sec": r.get("elapsed_sec"),
                "prompt_tokens": usage.get("promptTokenCount"),
                "output_tokens": usage.get("candidatesTokenCount"),
                "total_tokens": usage.get("totalTokenCount"),
                "model": r.get("model"),
                "prompt_version": r.get("prompt_version"),
            }
        )
    df = pd.DataFrame(rows)
    # 같은 이미지가 여러 번 있으면 마지막 결과를 쓴다
    return df.drop_duplicates(subset=["image_file"], keep="last").reset_index(drop=True)


def score(df: pd.DataFrame) -> tuple[str, dict]:
    total = len(df)
    n_call_fail = int((~df.ok).sum())
    n_parse_fail = int(df.parse_error.notna().sum())

    judged = df[df.ok & df.judgment.isin(prompts.JUDGMENTS)]
    gradable = judged[judged.truth.isin([prompts.TRUTH_NORMAL, prompts.TRUTH_ABNORMAL])]
    decided = gradable[gradable.judgment != prompts.JUDGMENT_UNKNOWN]
    correct = decided[decided.judgment == decided.truth]

    L = [
        f"채점 — {df.prompt_version.dropna().iloc[0] if df.prompt_version.notna().any() else '?'}"
        f" / {df.model.dropna().iloc[0] if df.model.notna().any() else '?'}",
        "=" * 64,
        "[1] 실행 상태",
        f"  총 건수            {total:>5}",
        f"  호출 실패          {n_call_fail:>5}",
        f"  JSON 파싱 실패     {n_parse_fail:>5}  ({n_parse_fail / total * 100:.1f}%)" if total else "",
        f"  판정 획득          {len(judged):>5}",
        "",
        "[2] 판정 분포",
    ]
    dist = Counter(judged.judgment)
    for label in prompts.JUDGMENTS:
        n = dist.get(label, 0)
        share = n / len(judged) * 100 if len(judged) else 0
        L.append(f"  {label:<8} {n:>5}  ({share:5.1f}%)")
    if len(judged):
        unknown_rate = dist.get(prompts.JUDGMENT_UNKNOWN, 0) / len(judged) * 100
        L.append(f"  판단불가 비율 {unknown_rate:.1f}%")

    L += ["", "[3] 정확도 (Y/N 정답이 있는 건만)"]
    if len(decided):
        acc = len(correct) / len(decided) * 100
        L.append(f"  채점 가능 {len(gradable)} 중 판정 {len(decided)} / 정답 {len(correct)} -> 정확도 {acc:.1f}%")
        for truth_label in (prompts.TRUTH_NORMAL, prompts.TRUTH_ABNORMAL):
            sub = decided[decided.truth == truth_label]
            if len(sub):
                hit = int((sub.judgment == truth_label).sum())
                L.append(f"  정답 {truth_label}: {hit}/{len(sub)} ({hit / len(sub) * 100:.1f}%)")
        L += ["", "  혼동행렬 (행=정답, 열=판정)"]
        matrix = pd.crosstab(gradable.truth, gradable.judgment)
        for line in matrix.to_string().splitlines():
            L.append("  " + line)
    else:
        L.append("  채점 가능한 건이 없다")

    excluded = judged[~judged.truth.isin([prompts.TRUTH_NORMAL, prompts.TRUTH_ABNORMAL])]
    if len(excluded):
        L.append(f"\n  판정 대상 아님(C/SO 계열) {len(excluded)} 건은 정확도에서 제외")

    L += ["", "[4] confidence 분포"]
    conf = pd.to_numeric(judged.confidence, errors="coerce").dropna()
    if len(conf):
        L.append(f"  고유값 {conf.nunique()} 개 / 최소 {conf.min():.2f} / 중위 {conf.median():.2f} / 최대 {conf.max():.2f} / 평균 {conf.mean():.3f}")
        buckets = pd.cut(conf, [0, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0], include_lowest=True)
        for interval, n in buckets.value_counts().sort_index().items():
            if n:
                L.append(f"    {str(interval):<14} {n:>4}")
        if conf.nunique() <= 2:
            L.append("  >> 고유값이 2개 이하다. confidence 가 사실상 상수이므로 신뢰도로 쓸 수 없다.")
        if len(decided):
            conf_decided = pd.to_numeric(decided.confidence, errors="coerce")
            hit = decided.judgment == decided.truth
            if conf_decided.notna().any():
                L.append(
                    f"  정답 평균 {conf_decided[hit].mean():.3f} / 오답 평균 {conf_decided[~hit].mean():.3f}"
                    " (차이가 없으면 confidence 로 걸러낼 수 없다)"
                )
    else:
        L.append("  confidence 값이 없다")

    L += ["", "[5] 사고유형 / 비용"]
    if len(judged):
        hz = Counter(judged.hazard_type)
        L.append("  모델이 답한 hazard_type: " + ", ".join(f"{k} {v}" for k, v in hz.most_common()))
    tokens = pd.to_numeric(df.total_tokens, errors="coerce").dropna()
    secs = pd.to_numeric(df.elapsed_sec, errors="coerce").dropna()
    if len(tokens):
        L.append(f"  토큰 합계 {int(tokens.sum()):,} / 장당 평균 {tokens.mean():.0f}")
    if len(secs):
        L.append(f"  장당 소요 평균 {secs.mean():.1f}초 / 최대 {secs.max():.1f}초 / 합계 {secs.sum() / 60:.1f}분")

    if n_call_fail:
        L += ["", "[6] 호출 실패 상세"]
        for row in df[~df.ok].itertuples():
            L.append(f"  {row.image_file}: {row.error}")
    if n_parse_fail:
        L += ["", "[7] 파싱 실패 상세 (raw 응답 앞 200자)"]
        for row in df[df.parse_error.notna()].itertuples():
            L.append(f"  {row.image_file}: {row.parse_error}")
            L.append(f"    {str(row.raw_text)[:200]!r}")

    stats = {
        "total": total,
        "call_fail": n_call_fail,
        "parse_fail": n_parse_fail,
        "judgment_dist": dict(dist),
        "gradable": len(gradable),
        "decided": len(decided),
        "correct": len(correct),
        "accuracy": round(len(correct) / len(decided), 4) if len(decided) else None,
        "confidence_unique": int(conf.nunique()) if len(conf) else 0,
        "tokens_total": int(tokens.sum()) if len(tokens) else 0,
    }
    return "\n".join(line for line in L if line is not None), stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Task B 실행 결과 채점")
    parser.add_argument("--run", required=True, help="실행 이름 (eval_runs/<run>.jsonl)")
    parser.add_argument("--rows", action="store_true", help="건별 표를 콘솔에도 출력")
    args = parser.parse_args(argv)

    df = load_run(args.run)
    text, stats = score(df)

    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORT_DIR / f"{args.run}_score.txt").write_text(text + "\n", encoding="utf-8")
    cols = [
        "image_file", "situation_id", "truth", "judgment", "confidence",
        "hazard_type", "observed", "reason", "missing_measure",
        "situation_label", "device", "elapsed_sec", "total_tokens", "parse_error", "error",
    ]
    rows_path = config.REPORT_DIR / f"{args.run}_rows.csv"
    df[[c for c in cols if c in df.columns]].to_csv(rows_path, index=False, encoding="utf-8-sig")

    print(text)
    print(f"\n저장: {rows_path.relative_to(config.PROJECT_ROOT)}")
    print(f"      {(config.REPORT_DIR / f'{args.run}_score.txt').relative_to(config.PROJECT_ROOT)}")
    print(f"요약(json): {json.dumps(stats, ensure_ascii=False)}")

    if args.rows:
        print("\n건별 결과")
        for row in df.itertuples():
            mark = "O" if row.judgment == row.truth else ("-" if not row.truth else "X")
            print(
                f"\n[{mark}] {row.image_file}  {row.situation_id}  정답={row.truth or '-'}  "
                f"판정={row.judgment}  conf={row.confidence}  유형={row.hazard_type}"
            )
            print(f"    observed: {row.observed}")
            print(f"    reason  : {row.reason}")
            if row.missing_measure:
                print(f"    missing : {row.missing_measure}")


if __name__ == "__main__":
    main()
