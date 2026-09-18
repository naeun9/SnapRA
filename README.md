# SnapRA — 건설현장 사진 기반 위험성평가 초안 생성

소규모 건설현장에는 안전 전담자가 없어 법정 위험성평가 문서를 작성하기 어렵다.
이미 찍어둔 현장 사진을 넣으면 VLM 이 위험요인을 찾아 위험성평가표 초안을 만들어주는
서비스를 목표로 한다.

모델을 학습시키지 않는다. VLM 에 프롬프팅해 판정시키고, AI-Hub 공개 데이터셋의 라벨을
정답지로 삼아 정확도를 측정하는 방식이다.

**현재 단계: 데이터 확보와 구조 파악.** 웹앱·학습 코드는 아직 없다.

## 사용 데이터

| 데이터셋 | 역할 | datasetkey |
| --- | --- | --- |
| AI-Hub 건설현장 위험 상태 판단 데이터 | 주력 (이미지 226,000장 / JSON 226,000개) | `.env` 에 직접 기입 |
| AI-Hub 공사현장 안전장비 인식 이미지 | 보조 (45클래스, 보호구 착용/미착용) | 163 |

주력 데이터의 구성:

- 정상 시나리오 `Y-01`~`Y-50`, 비정상 시나리오 `N-01`~`N-50` — 같은 번호끼리 완전 쌍, 각 2,000장
  - 예) `Y-03` 개구부 덮개 설치 ↔ `N-03` 개구부 덮개 설치 불량
  - 예) `Y-33` 용접기 옆 소화기 배치 ↔ `N-33` 소화기 미배치
- 주의요망작업 `C-01`~`C-05`
- 객체 클래스 73종 (`WO` 이동객체 / `SO` 정적객체 / `DO` 영역객체)
- 5대 사고유형: 추락(A) 낙하(B) 협착(C) 화재(D) 전도(E)

## 디렉터리

```
.
├── .env.example          환경변수 자리표시자 (실제 값은 .env 에)
├── requirements.txt
├── scripts/download.sh   aihubshell 래퍼 (목록 조회 / 선택 다운로드)
├── src/tables.py         코드 테이블 단일 출처 (시나리오 105개, 객체 클래스 73종, 공정 17종 …)
├── src/config.py         경로·환경변수
├── src/schema.py         JSON 어노테이션 스키마 — 테이블은 tables.py 에서 읽어 온다
├── src/parse_labels.py   JSON 일괄 파싱 → DataFrame → 분포 리포트
├── src/make_evalset.py   Validation 분할에서 VLM 평가셋 생성
├── scripts/fetch_eval_images.py  평가셋 이미지만 순차 스트리밍으로 확보
├── tests/test_tables.py  tables.py 개수·정합성 검증
├── tests/make_fixture.py 실데이터 없이 돌려보는 가짜 라벨 생성기
├── docs/dataset-card.md  실측 데이터 카드 (결과보고서 인용용)
├── docs/aihubshell-bug.md  AI-Hub CLI 병합 버그 기록
└── data/
    ├── raw/              내려받은 원본 (git 제외)
    └── processed/        캐시·리포트 (git 제외)
```

`data/`, `.env`, `*.zip` 은 모두 `.gitignore` 에 들어 있다. API 키는 코드에 하드코딩하지 않고
`.env` 에서만 읽는다.

## 셋업

### 1. Python 의존성

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash (WSL/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

Python 3.10 이상. `pyarrow` 가 없으면 parquet 대신 csv 로 자동 폴백하고, `tqdm` 이 없으면
자체 진행률 출력으로 대체하므로 둘은 없어도 돌아간다.

### 2. 환경변수

```bash
cp .env.example .env
```

`.env` 를 열어 값을 채운다.

| 키 | 설명 |
| --- | --- |
| `AIHUB_API_KEY` | AI-Hub 로그인 > 마이페이지 > API 키 발급 |
| `AIHUB_DATASETKEY_RISK` | 건설현장 위험 상태 판단 데이터의 datasetkey — `download.sh list` 로 찾는다 |
| `AIHUB_DATASETKEY_EQUIP` | 공사현장 안전장비 인식 이미지 (163) |

AI-Hub 는 데이터 이용 신청이 **승인된 계정**만 다운로드가 된다.

### 3. Windows 에서 셸 스크립트 실행하기

`scripts/download.sh` 는 bash 스크립트다. PowerShell/cmd 에서는 실행되지 않으므로 아래 중 하나를 쓴다.

**방법 A — Git Bash (권장, 별도 설치 부담 없음)**

Git for Windows 를 설치하면 탐색기 우클릭 메뉴에 "Git Bash Here" 가 생긴다.
프로젝트 폴더에서 Git Bash 를 열고:

```bash
cd "/c/Users/<사용자명>/OneDrive/바탕 화면/대학교/2026 하반기 AI 경진대회/SnapRA"
bash scripts/download.sh list
```

경로에 공백과 한글이 있으므로 반드시 따옴표로 감싼다. 드라이브는 `C:\` 대신 `/c/` 로 쓴다.

**방법 B — WSL**

```bash
wsl
cd "/mnt/c/Users/<사용자명>/OneDrive/바탕 화면/대학교/2026 하반기 AI 경진대회/SnapRA"
sudo apt-get install -y curl unzip
bash scripts/download.sh list
```

WSL 쪽이 대용량 다운로드와 압축 해제가 빠르지만, `/mnt/c` 를 경유하는 파일 I/O 는 느리다.
22만 개 JSON 파싱은 Windows 쪽 Python 으로 돌리는 편이 낫다.

**콘솔 한글 깨짐**: PowerShell 에서 Python 출력이 깨지면 `chcp 65001` 을 먼저 실행하거나
`$env:PYTHONIOENCODING="utf-8"` 을 설정한다. 리포트 파일 자체는 UTF-8(BOM)로 저장되므로
엑셀에서 바로 열린다.

## download.sh 사용법

`aihubshell` 이 없으면 `https://api.aihub.or.kr/api/aihubshell.do` 에서 자동으로 받아
프로젝트 루트에 `chmod +x` 로 저장한다 (git 제외 대상).

```bash
# 1) 전체 데이터셋 목록 → list.txt
bash scripts/download.sh list
grep 건설현장 list.txt        # datasetkey 확인 후 .env 에 기입

# 2) 데이터셋의 파일 트리 → files_<key>.txt
bash scripts/download.sh files 163
bash scripts/download.sh files          # 인자 생략 시 .env 의 AIHUB_DATASETKEY_RISK

# 3) 선택 다운로드 → data/raw/
bash scripts/download.sh get 163 1,2,3
bash scripts/download.sh get 163 all    # 전체. 수백 GB 니 주의
```

조회 결과를 터미널이 아니라 파일로 받는 이유는 한글 깨짐 때문이다. 파일이 깨져 보이면:

```bash
iconv -f EUC-KR -t UTF-8 list.txt > list.utf8.txt
```

**먼저 라벨(JSON)만 받는 것을 권한다.** 이미지 226,000장을 다 받지 않아도 분포 파악과
평가셋 설계는 라벨만으로 가능하다. `files_<key>.txt` 에서 라벨 쪽 filekey 만 골라 받으면 된다.

**분할 파일**: AI-Hub 는 대용량을 `.part` 로 쪼개 내려준다. 합친 뒤 풀어야 한다.

```bash
cd data/raw
cat <name>.zip.part* > <name>.zip
unzip -O cp949 <name>.zip        # 한글 파일명 보존
```

`unzip` 이 없으면 스크립트가 경고만 하고 다운로드는 계속한다. 그 경우 7-Zip 이나 탐색기로 직접 푼다.

## 라벨 파싱

```bash
python src/parse_labels.py                # 캐시 있으면 재사용
python src/parse_labels.py --force        # 다시 순회
python src/parse_labels.py --limit 2000   # 앞 2000건만 (구조 확인용)
python src/parse_labels.py --workers 8    # 병렬 파싱 (기본: CPU 절반)
```

22만 건 순회는 I/O 바운드라 시간이 걸린다. 진행률이 표시되고, 결과는
`data/processed/labels.parquet` 과 `objects.parquet` 으로 캐시되어 다음 실행에서 재사용된다.
깨진 JSON 한 건 때문에 전체가 멈추지 않고 `reports/parse_errors.csv` 에 기록된다.

산출물 (`data/processed/`):

| 파일 | 내용 |
| --- | --- |
| `labels.parquet` | 이미지 1장 = 1행 |
| `objects.parquet` | 어노테이션 1개 = 1행 |
| `reports/summary.txt` / `summary.json` | 전체 요약 (휴대폰 촬영 비율, Y/N 쌍 검증 결과 등) |
| `reports/scenario_counts.csv` | 시나리오 ID(Y/N/C)별 장수 |
| `reports/pair_check.csv` | Y-nn ↔ N-nn 장수 일치 여부 검증 |
| `reports/accident_type_counts.csv` | 사고유형(type_ID)별 분포 |
| `reports/process_counts.csv` | 공정(process_ID)별 분포 |
| `reports/device_counts.csv` | 촬영장비별 분포 — 휴대폰(4)이 몇 장인지 |
| `reports/site_condition_counts.csv` / `weather_counts.csv` | 실내외 / 날씨별 분포 |
| `reports/class_freq.csv` | 객체 클래스 등장 빈도 (대분류·소분류·명칭 포함) |
| `reports/object_pairs.csv` | 안전상태가 내포된 클래스 11쌍의 정상/비정상 등장 횟수 |
| `reports/mobile_subset.txt` | `device=4`(휴대폰) 실사용 조건 서브셋 통계 |
| `reports/missing_scenarios.csv` | 정의된 105개 중 데이터에 없는 시나리오 |
| `reports/missing_class_hits.csv` | 원문 결번 코드(SO-10, SO-29, DO-05)가 등장한 경우 |
| `reports/unknown_codes.csv` | 테이블에 없는 situation_id / class_id |
| `reports/annotation_shape_counts.csv` | polygon / bbox 등 어노테이션 형태 분포 |

촬영장비 분포를 특히 눈여겨볼 것. 서비스 입력은 현장 작업자의 휴대폰 사진이므로,
휴대폰(`device=4`) 촬영분이 평가셋의 현실성을 좌우한다. 드론·CCTV 시점 이미지에서 잘 맞는
프롬프트가 휴대폰 사진에서도 맞는다는 보장은 없다.

## 평가셋

라벨 파싱이 끝나면 Validation 분할(22,600장)에서만 평가셋을 뽑는다. Training 180,800장은
쓰지 않으므로 "학습에 사용되지 않은 데이터로 평가했다"가 데이터 분할로 보장된다.

```bash
python src/make_evalset.py -n 50 --mobile-all   # 시드 20260918 고정
```

| 세트 | 장수 | 종수 | 용도 |
| --- | --- | --- | --- |
| `eval_main` | 5,650 | 113 | 종당 50장. 주 평가셋 |
| `eval_mobile` | 3,426 | 25 | Validation 휴대폰 전수. 실사용 조건 |
| `eval_smoke` | 226 | 113 | 파이프라인 점검용 |

### 이미지 확보

원천 이미지는 Validation 만 27 GB 인데 실제로 필요한 것은 합집합 8,194장(약 10 GB)이다.
zip 하나(최대 408 MB)씩 받아 필요한 이미지만 꺼내고 zip 은 바로 버린다.

```bash
python scripts/fetch_eval_images.py --dry-run   # 계획만
python scripts/fetch_eval_images.py             # 실행 (중단해도 이어서 받는다)
```

진행 상태는 `data/raw/images/_progress.json` 에 남아 재시작 시 끝난 zip 을 건너뛴다.
Training 원천데이터(226 GB)는 받지 않는다.

## 코드 테이블

`src/tables.py` 가 코드 테이블의 **단일 출처**다. `schema.py` 는 테이블을 정의하지 않고
tables.py 를 읽어 쓰기 좋은 형태로 노출만 한다 (같은 표를 두 군데 두지 않는다).
따라서 `src/tables.py` 없이는 `schema.py` / `parse_labels.py` 가 동작하지 않는다.

| 이름 | 내용 |
| --- | --- |
| `SCENARIOS` | `{situation_id: (type_id, process_id, 설명)}` · Y-01~50 / N-01~50 / C-01~05 |
| `OBJECT_CLASSES` | `{class_id: (대분류, 소분류, 명칭)}` · WO 23 + SO 45 + DO 5 = 73종 |
| `PROCESSES`, `ACCIDENT_TYPES`, `WEATHER`, `DEVICES`, `SITE_CONDITIONS`, `SITUATION_PREFIX` | 코드값 매핑 |
| `OBJECT_SAFETY_PAIRS` | 클래스명에 안전상태가 내포된 11쌍 (정상, 비정상, 판정항목) |
| `MISSING_CLASS_IDS` | 원문 결번 (SO-10, SO-29, DO-05) |
| `EXPECTED_PER_SCENARIO` | 시나리오당 기대 장수 (2,000) |
| `scenario_pair()` / `is_normal()` / `describe()` | Y↔N 짝 찾기 / 정상 판정 / "사고유형 · 공정 · 설명" 한 줄 설명 |

짝 찾기·정상 판정·설명 생성은 전부 이 세 함수에 위임한다 (`schema.py` 가 같은 로직을
따로 구현하지 않는다). `describe()` 는 모르는 코드면 코드 자체를 돌려주므로,
`schema.describe()` 가 그 경우를 빈 문자열로 바꿔 리포트에 코드가 두 번 찍히지 않게 한다.

테이블이 틀어지면 이후 집계가 조용히 틀어지므로 개수를 테스트로 못 박아 두었다.

```bash
python tests/test_tables.py    # pytest 없어도 단독 실행
pytest -q                      # pytest 가 있으면
```

검증 항목: 시나리오 Y 50 / N 50 / C 5, 객체 클래스 73종(WO 23 / SO 45 / DO 5),
짝 없는 Y/N 시나리오 없음, `OBJECT_SAFETY_PAIRS` 의 모든 class_id 가 `OBJECT_CLASSES` 에 존재,
결번 코드가 테이블에 정의돼 있지 않음, 공정 17종.

## 실데이터 없이 파이프라인 돌려보기

AI-Hub 다운로드를 기다리는 동안에도 파싱·집계 경로를 검증할 수 있다.

```bash
python tests/make_fixture.py                                   # data/fixture_raw/ 에 가짜 라벨 생성
python src/parse_labels.py --raw-dir data/fixture_raw --force   # 리포트까지 생성
```

픽스처는 tables.py 의 실제 코드를 써서 만들고, 이상 징후 감지 경로를 확인하려고
결번 class_id·미정의 코드·type_ID 불일치·깨진 JSON 도 섞어 넣는다. 생성 위치는 `data/`
밑이라 git 에 올라가지 않는다.

---

본 프로젝트는 AI-Hub에서 제공하는 인공지능 학습용 데이터를 활용합니다.
데이터 출처: 한국지능정보사회진흥원
