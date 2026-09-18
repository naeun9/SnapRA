"""src/tables.py 코드 테이블의 개수·정합성 검증.

pytest 가 있으면:   pytest -q
없어도 단독 실행:   python tests/test_tables.py

tables.py 는 위험성평가 정확도 측정의 정답지 역할을 한다. 여기서 한 건이라도
틀어지면 이후 모든 집계가 조용히 틀어지므로, 개수까지 못 박아 둔다.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

try:
    import tables  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        f"src/tables.py 가 없다 ({SRC}). 코드 테이블의 단일 출처이므로 이 파일이 "
        "있어야 검증할 수 있다."
    ) from exc

# 원문 명세 기준 개수
N_NORMAL = 50        # Y-01 ~ Y-50
N_ABNORMAL = 50      # N-01 ~ N-50
N_CAUTION = 5        # C-01 ~ C-05
N_OBJECT_CLASSES = 73
N_BY_GROUP = {"WO": 23, "SO": 45, "DO": 5}
N_SAFETY_PAIRS = 11


def _ids(prefix: str) -> list[str]:
    return sorted(k for k in tables.SCENARIOS if str(k).upper().startswith(prefix + "-"))


def _class_ids(prefix: str) -> list[str]:
    return sorted(k for k in tables.OBJECT_CLASSES if str(k).upper().startswith(prefix + "-"))


# --------------------------------------------------------------- 시나리오
def test_scenario_counts() -> None:
    assert len(_ids("Y")) == N_NORMAL, f"정상 시나리오 {len(_ids('Y'))}개 (기대 {N_NORMAL})"
    assert len(_ids("N")) == N_ABNORMAL, f"비정상 시나리오 {len(_ids('N'))}개 (기대 {N_ABNORMAL})"
    assert len(_ids("C")) == N_CAUTION, f"주의요망 {len(_ids('C'))}개 (기대 {N_CAUTION})"
    assert len(tables.SCENARIOS) == N_NORMAL + N_ABNORMAL + N_CAUTION


def test_scenario_pairs_complete() -> None:
    """Y-nn 과 N-nn 은 완전 쌍이어야 한다. 짝 없는 시나리오가 있으면 실패."""
    numbers_y = {k.split("-", 1)[1] for k in _ids("Y")}
    numbers_n = {k.split("-", 1)[1] for k in _ids("N")}
    only_y = sorted(numbers_y - numbers_n)
    only_n = sorted(numbers_n - numbers_y)
    assert not only_y, f"N 짝이 없는 Y 시나리오: {only_y}"
    assert not only_n, f"Y 짝이 없는 N 시나리오: {only_n}"


def test_scenario_entry_shape() -> None:
    """모든 항목이 (type_id, process_id, 설명) 3-튜플이고 설명이 비어 있지 않다."""
    bad_shape = [k for k, v in tables.SCENARIOS.items() if not isinstance(v, (tuple, list)) or len(v) < 3]
    assert not bad_shape, f"모양이 다른 시나리오: {bad_shape[:10]}"

    empty_desc = [k for k, v in tables.SCENARIOS.items() if not str(v[2]).strip()]
    assert not empty_desc, f"설명이 빈 시나리오: {empty_desc[:10]}"

    unknown_type = sorted(
        {str(v[0]) for v in tables.SCENARIOS.values()} - set(tables.ACCIDENT_TYPES)
    )
    assert not unknown_type, f"ACCIDENT_TYPES 에 없는 type_id: {unknown_type}"

    unknown_process = sorted(
        {str(v[1]) for v in tables.SCENARIOS.values()} - set(tables.PROCESSES)
    )
    assert not unknown_process, f"PROCESSES 에 없는 process_id: {unknown_process}"


# ------------------------------------------------------------ 객체 클래스
def test_object_class_count() -> None:
    assert len(tables.OBJECT_CLASSES) == N_OBJECT_CLASSES, (
        f"객체 클래스 {len(tables.OBJECT_CLASSES)}종 (기대 {N_OBJECT_CLASSES})"
    )


def test_object_class_count_by_group() -> None:
    for prefix, expected in N_BY_GROUP.items():
        got = len(_class_ids(prefix))
        assert got == expected, f"{prefix} {got}종 (기대 {expected})"


def test_object_class_entry_shape() -> None:
    """(대분류, 소분류, 명칭) 3-튜플이고 명칭이 비어 있지 않다."""
    bad = [
        k
        for k, v in tables.OBJECT_CLASSES.items()
        if not isinstance(v, (tuple, list)) or len(v) < 3 or not str(v[2]).strip()
    ]
    assert not bad, f"모양이 다르거나 명칭이 빈 클래스: {bad[:10]}"


def test_missing_class_ids_are_gaps() -> None:
    """결번은 말 그대로 결번이므로 OBJECT_CLASSES 에 있어서는 안 된다."""
    present = [c for c in tables.MISSING_CLASS_IDS if c in tables.OBJECT_CLASSES]
    assert not present, f"결번인데 테이블에 정의된 class_id: {present}"


# ------------------------------------------------------------- 안전쌍
def test_safety_pairs_count() -> None:
    assert len(tables.OBJECT_SAFETY_PAIRS) == N_SAFETY_PAIRS, (
        f"안전쌍 {len(tables.OBJECT_SAFETY_PAIRS)}쌍 (기대 {N_SAFETY_PAIRS})"
    )


def test_safety_pair_class_ids_exist() -> None:
    """모든 쌍의 class_id 가 OBJECT_CLASSES 에 존재해야 한다."""
    unknown: list[str] = []
    for entry in tables.OBJECT_SAFETY_PAIRS:
        normal, abnormal = entry[0], entry[1]
        for cid in (normal, abnormal):
            if cid not in tables.OBJECT_CLASSES:
                unknown.append(str(cid))
    assert not unknown, f"OBJECT_CLASSES 에 없는 class_id: {unknown}"


def test_safety_pairs_have_judgement_item() -> None:
    missing = [
        entry[0] for entry in tables.OBJECT_SAFETY_PAIRS if len(entry) < 3 or not str(entry[2]).strip()
    ]
    assert not missing, f"판정항목이 빈 쌍: {missing}"


# --------------------------------------------------------------- 코드값
def test_code_tables() -> None:
    assert tables.EXPECTED_PER_SCENARIO == 2000
    assert len(tables.PROCESSES) == 17, f"공정 {len(tables.PROCESSES)}종 (기대 17)"
    assert set(tables.SITE_CONDITIONS) == {"1", "2"}
    assert set(tables.WEATHER) == {"1", "2", "3", "4"}
    assert set(tables.DEVICES) == {"1", "2", "3", "4", "5"}
    assert set("ABCDEFG") <= set(tables.ACCIDENT_TYPES)
    assert {"Y", "N", "C"} <= set(tables.SITUATION_PREFIX)


# -------------------------------------------------- schema.py 연동 확인
def test_schema_reexports_tables() -> None:
    """schema.py 가 테이블을 복제하지 않고 tables.py 를 그대로 쓰는지."""
    import schema

    assert schema.SCENARIOS is tables.SCENARIOS
    assert schema.OBJECT_CLASSES is tables.OBJECT_CLASSES
    assert len(schema.SCENARIO_PAIRS) == N_NORMAL
    assert len(schema.safety_pairs()) == N_SAFETY_PAIRS
    sample = next(iter(tables.SCENARIOS))
    assert schema.scenario_description(sample) == str(tables.SCENARIOS[sample][2]).strip()


def _main() -> int:
    """pytest 없이도 돌도록 직접 수집해 실행한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    tests = sorted(
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    )
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}\n      {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {name}")
    print("-" * 52)
    print(f"{len(tests) - failed}/{len(tests)} 통과")
    if failed == 0:
        print(
            f"시나리오 {len(tables.SCENARIOS)}개 "
            f"(Y {len(_ids('Y'))} / N {len(_ids('N'))} / C {len(_ids('C'))}), "
            f"객체 클래스 {len(tables.OBJECT_CLASSES)}종, "
            f"안전쌍 {len(tables.OBJECT_SAFETY_PAIRS)}쌍"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
