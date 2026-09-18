"""data/raw 의 zip 을 한글 파일명 보존해서 푼다.

    python scripts/extract_zips.py [대상_디렉터리]

AI-Hub zip 은 파일명이 cp949 로 들어 있는 경우가 많다. zipfile 은 UTF-8 플래그가
없는 이름을 cp437 로 디코드하므로 그대로 풀면 이름이 깨진다. cp437 로 되돌린 뒤
cp949 로 다시 디코드해 쓴다.

각 zip 은 자기 위치에 같은 이름의 폴더로 풀고, 성공하면 zip 을 지운다(용량 절약).
이미 푼 zip 은 건너뛴다.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def fix_name(info: zipfile.ZipInfo) -> str:
    """cp437 로 잘못 디코드된 이름을 cp949 로 되돌린다."""
    if info.flag_bits & 0x800:  # UTF-8 플래그가 있으면 그대로 믿는다
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for encoding in ("cp949", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def extract_one(zip_path: Path, delete_zip: bool = True) -> tuple[int, str | None]:
    """zip 하나를 같은 이름의 폴더로 푼다. (푼 파일 수, 오류)."""
    out_dir = zip_path.with_suffix("")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            n = 0
            for info in zf.infolist():
                name = fix_name(info).replace("\\", "/")
                # zip slip 방지: 절대경로/상위경로 참조는 버린다
                parts = [p for p in name.split("/") if p not in ("", ".", "..")]
                if not parts:
                    continue
                target = out_dir.joinpath(*parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                n += 1
    except (zipfile.BadZipFile, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"

    if delete_zip:
        try:
            zip_path.unlink()
        except OSError as exc:
            return n, f"zip 삭제 실패: {exc}"
    return n, None


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("data/raw")
    if not root.exists():
        print(f"{root} 가 없다.", file=sys.stderr)
        return 1

    zips = sorted(root.rglob("*.zip"))
    if not zips:
        print(f"{root} 에 zip 이 없다. 이미 풀었는지 확인해라.")
        return 0

    empty = [z for z in zips if z.stat().st_size == 0]
    if empty:
        print(f"경고: 0바이트 zip {len(empty)} 개. 병합이 실패한 파일이다:", file=sys.stderr)
        for z in empty[:5]:
            print(f"  {z.relative_to(root)}", file=sys.stderr)
        print("  다시 내려받아야 한다.", file=sys.stderr)
        return 1

    total_files = 0
    errors: list[tuple[Path, str]] = []
    for i, zip_path in enumerate(zips, 1):
        n, err = extract_one(zip_path)
        total_files += n
        if err:
            errors.append((zip_path, err))
        if i % 10 == 0 or i == len(zips):
            print(f"  {i}/{len(zips)} zip 해제 / 파일 {total_files:,} 개", flush=True)

    print(f"\n완료: zip {len(zips) - len(errors)}/{len(zips)} 개, 파일 {total_files:,} 개")
    if errors:
        print(f"실패 {len(errors)} 건:", file=sys.stderr)
        for path, err in errors[:10]:
            print(f"  {path.name}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
