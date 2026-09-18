# aihubshell 분할 파일 병합 버그 — 한글 파일명 + 로케일 미설정

AI-Hub 공식 CLI `aihubshell`로 「건설 현장 위험 상태 판단 데이터」(datasetkey 71407)의
라벨 226개를 내려받는 과정에서, 내려받은 데이터가 전부 빈 파일이 되고 원본까지
삭제되는 문제를 겪었다. 원인과 우회 방법을 기록한다.

| | |
| --- | --- |
| 도구 | `aihubshell version 25.09.19 v0.6` (231줄, md5 `266d09d0f1933df50416d076710f2efa`) |
| 받은 곳 | `https://api.aihub.or.kr/api/aihubshell.do` |
| 환경 | Windows 11, Git Bash (MSYS2), `LANG` 미설정 |
| 데이터 | datasetkey 71407, 라벨 filekey 226개 (Training 113 + Validation 113) |
| 발생 일자 | 2026-09-18 |

## 증상

`aihubshell -mode d`가 정상 종료(`Download successful.` → `병합이 완료 되었습니다.`)
했는데도 결과물이 비어 있었다.

- 병합된 zip **226개 전부 0바이트**
- 원본 분할 파일 `*.zip.part0` **전부 삭제됨**
- 내려받은 `download.tar`(519 MB)도 삭제됨
- `data/raw` 총 용량 555 MB → **144 KB**

로그에는 파일마다 `Merging TL_5대사고유형_추락_정상_Y-03.zip in ...` 이 정상 출력되어,
로그만 보면 성공한 것과 구별되지 않는다. **복구 불가 — 재다운로드가 유일한 방법이었다.**

## 원인

`aihubshell`의 `merge_parts()` 함수 (파일 127~145줄). 문제가 되는 세 줄:

```bash
# 133줄 — 파일명에 있는 특수문자 이스케이프 처리
escaped_prefix=$(printf '%q' "$prefix")

# 139줄 — 분할 파일을 찾아 병합
find "${target_dir}" -name "${escaped_prefix}.part*" -print0 | sort -zt'.' -k2V | xargs -0 cat > "${target_dir}/${prefix}"

# 142줄 — 병합 후 분할 파일 삭제
rm "${target_dir}/${prefix}".part*
```

bash의 `printf '%q'`는 **현재 로케일에서 출력 가능한 문자가 아니면 바이트 단위로
escape**한다. `LANG`이 비어 있으면 한글이 이렇게 바뀐다.

```
$ printf '%q\n' 'TL_5대사고유형_추락_정상_Y-03'          # LANG 미설정
$'TL_5\354\214\200\354\202\254\352\263\240...'          # ← find 에 넘어가는 값

$ LC_ALL=C.UTF-8 printf '%q\n' 'TL_5대사고유형_추락_정상_Y-03'
TL_5대사고유형_추락_정상_Y-03                            # ← 그대로 유지
```

이 escape된 문자열이 `find -name` 패턴으로 들어가므로 **한 건도 매치되지 않는다.**
그 결과:

1. `find`가 아무것도 출력하지 않음 → `xargs -0 cat`이 빈 입력을 받음
2. 리다이렉션 `> "${target_dir}/${prefix}"`은 먼저 평가되므로 **0바이트 파일이 생성됨**
3. 파이프라인이 성공(exit 0)으로 끝나 `set -e`도 걸리지 않고, 로그도 정상처럼 보임
4. **다음 줄 142번의 `rm`은 escape하지 않은 `${prefix}`에 쉘 glob을 쓰므로 정상 매치**
   → 원본 분할 파일이 지워짐
5. 루프 종료 후 `rm download.tar` (154줄)로 원본 tar까지 삭제

즉 **읽기용 경로(`find`)만 escape되고 삭제용 경로(`rm`)는 escape되지 않은 비대칭**이
데이터 소실의 직접 원인이다. 133줄의 주석("파일명에 있는 특수문자 이스케이프 처리")대로
`%q`는 셸 인자용 escape인데, `find -name`이 받는 것은 셸 인자가 아니라 glob 패턴이라
애초에 용도가 맞지 않는다.

139줄 바로 위 138줄에 주석 처리된 원래 구현이 남아 있는데, 그 쪽은 이 버그가 없다.

```bash
#cat "${target_dir}/${prefix}".part* > "${target_dir}/${prefix}"
```

## 재현 조건

네 가지가 겹칠 때만 발생한다.

1. 파일명에 한글(또는 현재 로케일로 표현할 수 없는 문자)이 포함된 데이터셋
2. `LANG`·`LC_ALL` 미설정 — Windows Git Bash 기본값, WSL도 설정 안 하면 해당
3. 파일이 분할 전송되어 `.part0` 병합 경로를 타는 경우
4. `-mode d` 다운로드

ASCII 파일명만 있는 데이터셋이나 UTF-8 로케일 환경에서는 재현되지 않는다.
Git Bash에서 `locale`은 `LC_CTYPE="C.UTF-8"`을 보여주지만 `LANG`이 비어 있으면
`printf '%q'`는 escape한다 — **`locale` 출력만 보고 안심하면 안 된다.**

## 우회

`scripts/download.sh`에서 두 가지를 적용했다.

### 1. 로케일 고정

```bash
export LC_ALL="${LC_ALL:-C.UTF-8}"
export LANG="${LANG:-C.UTF-8}"
```

`aihubshell`을 수정하지 않고 호출 환경만 바꾼다. 도구는 다시 내려받으면 원본으로
돌아가므로, 우회는 우리 래퍼에 두는 편이 안전하다.

### 2. 사후 검사 `verify_download()`

병합 결과를 신뢰하지 않고 직접 센다.

- 0바이트 zip 개수
- 남은 `*.part*` 개수
- 받은 zip 총 개수

하나라도 이상하면 경고와 함께 exit 1로 끝낸다. 도구가 성공을 보고해도 결과물이
비어 있을 수 있다는 것이 이 버그의 교훈이다.

## 영향

- 라벨 데이터 약 **519 MB 재다운로드** (압축 해제 시 JSON 약 2.23 GB)
- 이미지 원천데이터(253 GB)를 먼저 받았다면 손실 규모가 훨씬 컸다. 라벨만 먼저
  받는 순서를 택한 것이 결과적으로 피해를 줄였다.

## 교훈

1. **도구의 exit code와 로그를 결과물의 증거로 삼지 않는다.** 파일 개수와 크기를
   직접 센다.
2. **삭제가 포함된 파이프라인은 삭제 전에 검증한다.** 이 버그는 "병합 실패"가 아니라
   "병합 실패 + 원본 삭제"였기 때문에 복구가 불가능했다.
3. **비ASCII 파일명은 셸 스크립트에서 여전히 위험하다.** 로케일에 따라 같은 코드가
   다르게 동작한다.
