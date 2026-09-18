#!/usr/bin/env bash
# AI-Hub aihubshell 래퍼.
#   ./scripts/download.sh list
#   ./scripts/download.sh files <datasetkey>
#   ./scripts/download.sh get   <datasetkey> <filekey[,filekey,...]>
#
# Windows Git Bash / WSL / macOS / Linux 공통으로 동작하도록 bashism 을 최소화했다.
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
RAW_DIR="$ROOT_DIR/data/raw"
SHELL_BIN="$ROOT_DIR/aihubshell"
SHELL_URL="https://api.aihub.or.kr/api/aihubshell.do"

# aihubshell 25.09.19 v0.6 의 merge_parts() 는 분할 파일 prefix 를 printf '%q' 로
# 이스케이프한 뒤 find -name 에 넘긴다. LANG 이 비어 있으면 bash 가 한글을
# $'\354\240\225...' 형태로 escape 하므로 find 가 한 건도 못 찾고, 0바이트 파일을
# 만든 다음 줄의 rm 이 part 파일을 지워 버린다 (데이터 소실).
# UTF-8 로케일에서는 %q 가 한글을 그대로 두므로 반드시 고정해서 실행한다.
export LC_ALL="${LC_ALL:-C.UTF-8}"
export LANG="${LANG:-C.UTF-8}"

log()  { printf '%s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat >&2 <<'USAGE'
사용법:
  ./scripts/download.sh list
      전체 데이터셋 목록을 list.txt 로 저장한다.

  ./scripts/download.sh files <datasetkey>
      해당 데이터셋의 파일 트리를 files_<datasetkey>.txt 로 저장한다.
      datasetkey 를 생략하면 .env 의 AIHUB_DATASETKEY_RISK 를 쓴다.

  ./scripts/download.sh get <datasetkey> <filekey[,filekey,...]>
      선택한 파일만 data/raw/ 밑으로 내려받는다.
      filekey 에 all 을 주면 데이터셋 전체(수백 GB)를 받는다. 주의.

  ./scripts/download.sh unzip
      data/raw/ 의 zip 을 한글 파일명 보존해서 푼다.

예시:
  ./scripts/download.sh list
  ./scripts/download.sh files 163
  ./scripts/download.sh get 163 1,2,3
USAGE
}

# ---------------------------------------------------------------- .env 로드
load_env() {
  env_file="$ROOT_DIR/.env"
  if [ ! -f "$env_file" ]; then
    die ".env 가 없다. 'cp .env.example .env' 후 AIHUB_API_KEY 를 채워라."
  fi
  # KEY=VALUE 형식만 읽는다. 주석/빈 줄/따옴표/CRLF 처리 포함.
  while IFS= read -r line || [ -n "$line" ]; do
    line=$(printf '%s' "$line" | tr -d '\r')
    case "$line" in
      ''|'#'*) continue ;;
      *'='*) ;;
      *) continue ;;
    esac
    key=${line%%=*}
    val=${line#*=}
    key=$(printf '%s' "$key" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^export[[:space:]]*//')
    val=$(printf '%s' "$val" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"\(.*\)"$/\1/; s/^'\''\(.*\)'\''$/\1/')
    case "$key" in
      AIHUB_*) eval "$key=\$val"; export "$key" ;;
    esac
  done < "$env_file"

  : "${AIHUB_API_KEY:=}"
  : "${AIHUB_DATASETKEY_RISK:=}"
  : "${AIHUB_DATASETKEY_EQUIP:=}"

  if [ -z "$AIHUB_API_KEY" ]; then
    log ""
    log "AIHUB_API_KEY 가 비어 있다."
    log "  1) AI-Hub 로그인 > 마이페이지 > API 키 발급"
    log "  2) $ROOT_DIR/.env 의 AIHUB_API_KEY= 뒤에 값을 붙여넣기"
    log ""
    exit 1
  fi
  # aihubshell 구버전은 환경변수 AIHUB_APIKEY 를 참조한다. 둘 다 세팅해 둔다.
  AIHUB_APIKEY="$AIHUB_API_KEY"
  export AIHUB_APIKEY
}

# ------------------------------------------------------- aihubshell 준비
ensure_shell() {
  if [ ! -x "$SHELL_BIN" ]; then
    if [ -f "$SHELL_BIN" ]; then
      chmod +x "$SHELL_BIN"
    else
      command -v curl >/dev/null 2>&1 || die "curl 이 없다. Git Bash 또는 WSL 에서 실행해라."
      log "aihubshell 을 내려받는다: $SHELL_URL"
      curl -fsSL -o "$SHELL_BIN" "$SHELL_URL" || die "aihubshell 다운로드 실패"
      chmod +x "$SHELL_BIN"
      log "저장 완료: $SHELL_BIN"
    fi
  fi
}

check_unzip() {
  command -v unzip >/dev/null 2>&1 && return 0
  log ""
  log "경고: unzip 이 없다. 내려받은 zip 을 자동으로 풀 수 없다."
  log "  - Git Bash: 7-Zip 설치 후 'C:\Program Files\7-Zip' 을 PATH 에 추가하거나"
  log "    탐색기에서 직접 압축 해제"
  log "  - WSL/Ubuntu: sudo apt-get install -y unzip"
  log ""
  return 0
}

run_shell() {
  # aihubshell 은 stdout 으로 결과를 뿌린다. 터미널 한글 깨짐을 피하려고 파일로 받는다.
  "$SHELL_BIN" -aihubapikey "$AIHUB_API_KEY" "$@"
}

note_encoding() {
  out="$1"
  log "저장: $out"
  log "한글이 깨져 보이면: iconv -f EUC-KR -t UTF-8 '$out' > '${out%.txt}.utf8.txt'"
}

# ------------------------------------------------------------- 서브커맨드
cmd_list() {
  out="$ROOT_DIR/list.txt"
  log "전체 데이터셋 목록 조회 중..."
  run_shell -mode l > "$out" 2>&1 || die "목록 조회 실패. $out 을 확인해라."
  note_encoding "$out"
  log "예: grep 건설현장 '$out'"
}

cmd_files() {
  key="${1:-$AIHUB_DATASETKEY_RISK}"
  [ -n "$key" ] || die "datasetkey 가 없다. 인자로 주거나 .env 의 AIHUB_DATASETKEY_RISK 를 채워라."
  out="$ROOT_DIR/files_$key.txt"
  log "데이터셋 $key 의 파일 트리 조회 중..."
  run_shell -mode l -datasetkey "$key" > "$out" 2>&1 || die "파일 목록 조회 실패. $out 을 확인해라."
  note_encoding "$out"
  log "filekey 를 골라: ./scripts/download.sh get $key <filekey,filekey,...>"
}

cmd_get() {
  key="${1:-}"
  filekeys="${2:-}"
  [ -n "$key" ] || { usage; die "datasetkey 를 지정해라."; }
  [ -n "$filekeys" ] || { usage; die "filekey 를 지정해라. 전체는 all."; }

  check_unzip
  mkdir -p "$RAW_DIR"
  log "데이터셋 $key / filekey $filekeys 다운로드 -> $RAW_DIR"
  # aihubshell 은 현재 디렉터리에 내려받는다. 서브셸에서 cd 한다.
  ( cd "$RAW_DIR" && "$SHELL_BIN" -aihubapikey "$AIHUB_API_KEY" -mode d -datasetkey "$key" -filekey "$filekeys" ) \
    || die "다운로드 실패"
  verify_download
}

# 병합 결과를 믿지 않는다. 0바이트 zip 이나 남은 part 가 있으면 크게 알린다.
verify_download() {
  zips=$(find "$RAW_DIR" -name '*.zip' 2>/dev/null | wc -l | tr -d ' ')
  empty=$(find "$RAW_DIR" -name '*.zip' -size 0 2>/dev/null | wc -l | tr -d ' ')
  parts=$(find "$RAW_DIR" -name '*.part*' 2>/dev/null | wc -l | tr -d ' ')

  log ""
  log "zip $zips 개 / 0바이트 $empty 개 / 남은 part $parts 개"

  if [ "$empty" -gt 0 ]; then
    log ""
    log "경고: 0바이트 zip 이 $empty 개다. aihubshell 의 분할 파일 병합이 실패했다."
    log "  한글 파일명 + 비UTF-8 로케일 조합에서 생기는 문제로, 이 스크립트는"
    log "  LC_ALL=C.UTF-8 을 고정하므로 정상이라면 나오지 않는다."
    log "  0바이트 파일을 지우고 다시 받아라 (part 파일은 이미 삭제되어 복구 불가)."
    return 1
  fi

  if [ "$parts" -gt 0 ]; then
    log ""
    log "병합되지 않은 분할 파일이 남았다. 직접 합쳐라:"
    log "  cd data/raw && cat <name>.zip.part* > <name>.zip"
    return 1
  fi

  log "다음: bash scripts/download.sh unzip"
  return 0
}

# zip 안의 한글 파일명이 깨지지 않게 푼다. AI-Hub zip 은 cp949 이름이 섞여 있다.
cmd_unzip() {
  [ -d "$RAW_DIR" ] || die "$RAW_DIR 가 없다."
  py=$(command -v python || command -v python3) || die "python 이 필요하다."
  log "data/raw 의 zip 을 푼다 (한글 파일명 보존)"
  "$py" "$SCRIPT_DIR/extract_zips.py" "$RAW_DIR"
}

# ------------------------------------------------------------------- main
sub="${1:-}"
[ $# -gt 0 ] && shift || true

case "$sub" in
  list)  load_env; ensure_shell; cmd_list ;;
  files) load_env; ensure_shell; cmd_files "${1:-}" ;;
  get)   load_env; ensure_shell; cmd_get "${1:-}" "${2:-}" ;;
  unzip) cmd_unzip ;;
  ""|-h|--help|help) usage; exit 0 ;;
  *)     usage; die "알 수 없는 서브커맨드: $sub" ;;
esac
