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
                "status": r.get("status") or ("judged" if r.get("ok") else "error"),
                "attempts": r.get("attempts"),
                "retry_waits": r.get("retry_waits"),
                "model_responded": r.get("model_responded"),
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
                # Interactions API 의 usage 필드명 (2026-10 기준)
                "prompt_tokens": usage.get("total_input_tokens"),
                "output_tokens": usage.get("total_output_tokens"),
                "thought_tokens": usage.get("total_thought_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "model": r.get("model"),
                "prompt_version": r.get("prompt_version"),
            }
        )
    df = pd.DataFrame(rows)
    # 같은 이미지가 여러 번 있으면 마지막 결과를 쓴다
    return df.drop_duplicates(subset=["image_file"], keep="last").reset_index(drop=True)


def pair_analysis(df: pd.DataFrame) -> tuple[list[dict], dict]:
    """Y-nn / N-nn 쌍 단위 분석.

    쌍은 양쪽이 모두 판정을 받아야 비교에 쓸 수 있다. 503 으로 한쪽이 error 가 되면
    남은 한쪽의 정답 여부는 아무것도 말해주지 않으므로 모수에서 뺀다.
    """
    by_num: dict[str, dict[str, pd.Series]] = {}
    for row in df.itertuples():
        sid = str(row.situation_id)
        if "-" not in sid or sid[0] not in ("Y", "N"):
            continue
        by_num.setdefault(sid.split("-", 1)[1], {})[sid[0]] = row

    pairs = []
    for number in sorted(by_num):
        side = by_num[number]
        y, n = side.get("Y"), side.get("N")
        entry: dict = {
            "pair": number,
            "y_id": f"Y-{number}",
            "n_id": f"N-{number}",
            "y_status": getattr(y, "status", "missing") if y is not None else "missing",
            "n_status": getattr(n, "status", "missing") if n is not None else "missing",
            "y_judgment": getattr(y, "judgment", "") if y is not None else "",
            "n_judgment": getattr(n, "judgment", "") if n is not None else "",
            "y_image": getattr(y, "image_file", "") if y is not None else "",
            "n_image": getattr(n, "image_file", "") if n is not None else "",
            "y_conf": getattr(y, "confidence", None) if y is not None else None,
            "n_conf": getattr(n, "confidence", None) if n is not None else None,
        }
        y_ok = entry["y_status"] == "judged" and entry["y_judgment"] in prompts.JUDGMENTS
        n_ok = entry["n_status"] == "judged" and entry["n_judgment"] in prompts.JUDGMENTS

        if y_ok and n_ok:
            entry["completeness"] = "양쪽 judged"
            yj, nj = entry["y_judgment"], entry["n_judgment"]
            if yj == prompts.TRUTH_NORMAL and nj == prompts.TRUTH_ABNORMAL:
                entry["result"] = "구분"          # 정상/비정상을 정확히 가름
            elif yj == nj:
                entry["result"] = f"쏠림({yj})"    # 양쪽을 같게 판정
            elif yj == prompts.TRUTH_ABNORMAL and nj == prompts.TRUTH_NORMAL:
                entry["result"] = "뒤집힘"         # 정반대로 판정
            else:
                entry["result"] = f"부분판정({yj}/{nj})"  # 한쪽이 판단불가 등
        elif y_ok or n_ok:
            entry["completeness"] = "한쪽만"
            entry["result"] = "-"
        else:
            entry["completeness"] = "양쪽 실패"
            entry["result"] = "-"
        pairs.append(entry)

    complete = [p for p in pairs if p["completeness"] == "양쪽 judged"]
    stats = {
        "pairs_total": len(pairs),
        "pairs_complete": len(complete),
        "pairs_partial": sum(1 for p in pairs if p["completeness"] == "한쪽만"),
        "pairs_failed": sum(1 for p in pairs if p["completeness"] == "양쪽 실패"),
        "distinguished": sum(1 for p in complete if p["result"] == "구분"),
        "skewed": sum(1 for p in complete if p["result"].startswith("쏠림")),
        "flipped": sum(1 for p in complete if p["result"] == "뒤집힘"),
        "partial_judgment": sum(1 for p in complete if p["result"].startswith("부분판정")),
    }
    return pairs, stats


def score(df: pd.DataFrame) -> tuple[str, dict]:
    total = len(df)
    n_call_fail = int((df.status == "error").sum())
    n_parse_fail = int((df.status == "parse_error").sum())
    n_judged_status = int((df.status == "judged").sum())

    judged = df[df.ok & df.judgment.isin(prompts.JUDGMENTS)]
    gradable = judged[judged.truth.isin([prompts.TRUTH_NORMAL, prompts.TRUTH_ABNORMAL])]
    decided = gradable[gradable.judgment != prompts.JUDGMENT_UNKNOWN]
    correct = decided[decided.judgment == decided.truth]

    models_used = sorted({m for m in df.model_responded.dropna() if m})
    L = [
        f"채점 — {df.prompt_version.dropna().iloc[0] if df.prompt_version.notna().any() else '?'}"
        f" / {df.model.dropna().iloc[0] if df.model.notna().any() else '?'}",
        "=" * 64,
        "[1] 실행 상태 — 인프라 실패와 모델 판정을 분리한다",
        f"  총 건수                      {total:>5}",
        f"  호출 실패 (503 등 인프라)    {n_call_fail:>5}",
        f"  JSON 파싱 실패               {n_parse_fail:>5}",
        f"  판정 획득 (judged)           {n_judged_status:>5}   <- 이 안에 '판단불가' 포함",
        "",
        f"  응답한 모델: {', '.join(models_used) if models_used else '(없음)'}"
        + ("   ** 모델 혼용 **" if len(models_used) > 1 else "   (혼용 없음)"),
        "",
        "[2] 판정 분포 (judged 건만)",
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

    # ---- 재시도 분포 ----
    attempts = pd.to_numeric(df.attempts, errors="coerce").dropna()
    if len(attempts):
        L += ["", "[6] 재시도 분포 (attempts = 최초 시도 + 재시도)"]
        for value, count in attempts.value_counts().sort_index().items():
            label = "재시도 없음" if value == 1 else f"재시도 {int(value) - 1}회"
            L.append(f"  attempts={int(value)}  {label:<12} {int(count):>4} 건")
        L.append(f"  재시도를 거친 건: {int((attempts > 1).sum())} / {len(attempts)}")
    waits = [w for ws in df.retry_waits.dropna() if isinstance(ws, list) for w in ws]
    if waits:
        L.append(f"  재시도 대기: 총 {sum(waits):.0f}초 / 건당 {sorted(set(waits))} (Retry-After 존중)")

    # ---- 쌍 무결성 ----
    pairs, pstats = pair_analysis(df)
    if pairs:
        L += [
            "",
            "[7] Y/N 쌍 무결성 — 쌍은 양쪽이 모두 judged 여야 비교에 쓸 수 있다",
            f"  전체 {pstats['pairs_total']}쌍 중"
            f"  양쪽 judged {pstats['pairs_complete']} / 한쪽만 {pstats['pairs_partial']}"
            f" / 양쪽 실패 {pstats['pairs_failed']}",
        ]
        if pstats["pairs_complete"]:
            n = pstats["pairs_complete"]
            L += [
                "",
                f"  [쌍 단위 집계 — 모수는 완전한 쌍 {n}쌍]",
                f"    구분   {pstats['distinguished']:>3} / {n}   (Y=정상, N=비정상 으로 정확히 가름)",
                f"    쏠림   {pstats['skewed']:>3} / {n}   (양쪽을 같게 판정)",
                f"    뒤집힘 {pstats['flipped']:>3} / {n}   (정반대로 판정)",
                f"    부분판정 {pstats['partial_judgment']:>3} / {n}   (한쪽이 판단불가)",
            ]
        else:
            L.append("    완전한 쌍이 0 이므로 쌍 단위 집계를 낼 수 없다.")

        L += ["", "  쌍별 상세:", f"    {'쌍':<5}{'Y 판정':<10}{'N 판정':<10}{'상태':<12}결과"]
        for p in pairs:
            L.append(
                f"    {p['pair']:<5}{str(p['y_judgment'] or p['y_status']):<10}"
                f"{str(p['n_judgment'] or p['n_status']):<10}{p['completeness']:<12}{p['result']}"
            )

        incomplete = [p for p in pairs if p["completeness"] != "양쪽 judged"]
        if incomplete:
            L += ["", f"  [재실행 대상] 불완전한 쌍 {len(incomplete)}개의 이미지:"]
            for p in incomplete:
                for side in ("y", "n"):
                    if p[f"{side}_status"] != "judged" and p[f"{side}_image"]:
                        L.append(f"    data/raw/images/{p[f'{side}_image']}  ({p[f'{side}_id']}, {p[f'{side}_status']})")

    if n_call_fail:
        L += ["", "[8] 호출 실패 상세 (인프라)"]
        for row in df[df.status == "error"].itertuples():
            L.append(f"  {row.image_file} ({row.situation_id}): attempts={row.attempts} {str(row.error)[:160]}")
    if n_parse_fail:
        L += ["", "[9] 파싱 실패 상세 (raw 응답 앞 200자)"]
        for row in df[df.parse_error.notna()].itertuples():
            L.append(f"  {row.image_file}: {row.parse_error}")
            L.append(f"    {str(row.raw_text)[:200]!r}")

    stats = {
        "total": total,
        "call_fail": n_call_fail,
        "parse_fail": n_parse_fail,
        "judged": n_judged_status,
        "models_responded": models_used,
        **{f"pair_{k}": v for k, v in pstats.items()},
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
