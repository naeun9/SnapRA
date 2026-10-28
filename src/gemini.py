"""Gemini Interactions API 얇은 래퍼 (REST).

google-genai SDK 없이 requests 만 쓴다. 필요한 기능이 호출 하나뿐이고, 의존성을
늘리지 않는 편이 재현에 유리하다.

────────────────────────────────────────────────────────────────────────
2026-10 기준 API 형태 (문서·실측 확인)
────────────────────────────────────────────────────────────────────────
엔드포인트는 모델별 경로가 아니라 단일 엔드포인트이고, 모델은 바디에 넣는다.

    POST https://generativelanguage.googleapis.com/v1beta/interactions
    x-goog-api-key: <KEY>          <- 쿼리파라미터(?key=)가 아니라 헤더
    {
      "model": "gemini-3.8-flash",
      "input": [ {"type":"text","text":"..."},
                 {"type":"image","data":"<base64>","mime_type":"image/jpeg"} ],
      "system_instruction": "...",
      "response_format": {"type":"text","mime_type":"application/json","schema": {...}},
      "generation_config": {"seed": 20260918, "thinking_level": "low"}
    }

응답(실측):

    {"id":..., "status":"completed", "model":...,
     "usage": {"total_tokens":115, "total_input_tokens":12, "total_output_tokens":2,
               "total_thought_tokens":101, "input_tokens_by_modality":[...]},
     "steps": [ {"type":"thought","signature":"..."},
                {"type":"model_output","content":[{"type":"text","text":"정상"}]} ]}

    -> 본문은 steps 중 type=="model_output" 의 content[].text 를 이어붙인다.
    -> thought 스텝은 서명만 오고 내용은 없다. 토큰은 total_thought_tokens 로 과금된다.

에러: {"error": {"message": "...", "code": "service_unavailable"}}  (code 가 문자열)

temperature 는 이 API 의 generation_config 에 없다. 재현성은 seed 로 확보한다.
같은 사진에 같은 판정이 나와야 평가가 의미 있으므로 seed 를 고정한다.

무료 티어 주의:
    무료 티어는 입력·출력이 Google 제품 개선에 사용된다(공식 가격 문서의
    "Content used to improve our products: Yes"). 우리가 보내는 것은 AI-Hub
    공개 데이터셋 이미지라 문제가 없지만, 현장 사진을 받는 서비스로 확장할 때는
    유료 티어로 올려야 한다.

한도:
    RPM/TPM/RPD 는 계정·모델별로 다르고 공식 문서가 더 이상 고정 수치를 싣지 않는다
    (AI Studio 대시보드에서 확인). 그래서 이 래퍼는 호출 간 간격을 호출자가 제어하게
    두고, 429·503 을 만나면 Retry-After 를 따라 물러난다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config

try:
    import requests
except ImportError:  # pragma: no cover
    raise SystemExit("requests 가 필요하다: pip install -r requirements.txt")

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    raise SystemExit("Pillow 가 필요하다: pip install -r requirements.txt")

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_MODEL = "gemini-3.8-flash"  # 2026-10 기준 무료 티어 멀티모달 최신 Flash
DEFAULT_LONG_EDGE = 1024  # 긴 변 기준 리사이즈
RETRY_STATUS = (429, 500, 502, 503, 504)

# 503 한 번이 무료 티어 일일 요청 한도를 소모하는지는 확인되지 않았다(응답에 한도
# 정보가 없다). 소모한다면 재시도 1회가 한도 1건이므로 4회는 너무 비싸다. 확인될
# 때까지 보수적으로 2회만 시도한다 (최초 1 + 재시도 1).
DEFAULT_MAX_RETRIES = 2

# 호출 설정의 단일 출처는 config.py 다. 여기서는 기본값으로만 끌어 쓴다.
DEFAULT_SEED = config.EVAL_SEED
DEFAULT_THINKING = config.EVAL_THINKING_LEVEL
DEFAULT_MAX_OUTPUT_TOKENS = config.EVAL_MAX_OUTPUT_TOKENS

# 503 은 같은 모델로 재시도만 한다. 다른 모델로 폴백하지 않는다 - 서로 다른 모델의
# 판정을 한 평가 결과에 섞으면 그 수치는 의미가 없다. 끝까지 실패하면 error 로 남긴다.
ALLOW_MODEL_FALLBACK = False


@dataclass
class Usage:
    """토큰 사용량 누적. 한도 소모량을 눈으로 보기 위한 것."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0
    retries: int = 0
    seconds: float = 0.0

    def add(self, usage: dict[str, Any], elapsed: float) -> None:
        self.calls += 1
        self.input_tokens += int(usage.get("total_input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("total_output_tokens", 0) or 0)
        self.thought_tokens += int(usage.get("total_thought_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0)
        self.seconds += elapsed

    def summary(self) -> str:
        avg = self.seconds / self.calls if self.calls else 0.0
        return (
            f"호출 {self.calls} / 재시도 {self.retries} / "
            f"입력 {self.input_tokens:,} + 출력 {self.output_tokens:,} + "
            f"사고 {self.thought_tokens:,} = {self.total_tokens:,} 토큰 / "
            f"평균 {avg:.1f}초"
        )


@dataclass
class Result:
    """한 번의 호출 결과. 원본 응답을 그대로 들고 있는다."""

    ok: bool
    text: str = ""
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    error: str | None = None
    elapsed: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""  # 이 API 의 status ("completed" 등)
    http_status: int = 0
    attempts: int = 1
    model_responded: str = ""  # 응답이 실제로 밝힌 모델명. 혼용 여부 전수 검사용
    quota_headers: dict[str, str] = field(default_factory=dict)  # 한도 관련 응답 헤더
    retry_waits: list[float] = field(default_factory=list)  # 재시도 대기 초
    retry_after_seen: list[str] = field(default_factory=list)  # 서버가 준 Retry-After


def api_key() -> str:
    """.env 또는 환경변수에서 키를 읽는다. 코드에 하드코딩하지 않는다."""
    import config

    key = config.get_env("GEMINI_API_KEY")
    if not key:
        raise SystemExit(
            "GEMINI_API_KEY 가 비어 있다.\n"
            "  1) https://aistudio.google.com/apikey 에서 키 발급\n"
            "  2) .env 의 GEMINI_API_KEY= 뒤에 붙여넣기 (.env 는 git 제외)"
        )
    return key


def model_name() -> str:
    """GEMINI_MODEL 이 비어 있으면(키만 있고 값이 없으면) 기본 모델을 쓴다."""
    import config

    return config.get_env("GEMINI_MODEL").strip() or DEFAULT_MODEL


# ------------------------------------------------------------------ 이미지
def load_image_part(path: Path, long_edge: int = DEFAULT_LONG_EDGE) -> tuple[dict, int]:
    """이미지를 긴 변 long_edge 로 줄여 input 파트로 만든다.

    원본은 1920x1080 이라 그대로 보내면 한도를 빠르게 먹는다. 1024 로 줄이면
    전송량이 약 1/8 (1.1MB -> 140KB)이 되고, 안전설비 유무 판단에 필요한 해상도는
    남는다.
    """
    with Image.open(path) as img:
        img = img.convert("RGB")
        width, height = img.size
        scale = long_edge / max(width, height)
        if scale < 1:
            img = img.resize(
                (round(width * scale), round(height * scale)), Image.LANCZOS
            )
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=90, optimize=True)
    payload = buffer.getvalue()
    part = {
        "type": "image",
        "data": base64.b64encode(payload).decode("ascii"),
        "mime_type": "image/jpeg",
    }
    return part, len(payload)


# ------------------------------------------------------------------ 호출
def build_body(
    prompt: dict[str, Any],
    model: str,
    image_part: dict | None = None,
    seed: int = DEFAULT_SEED,
    thinking_level: str = DEFAULT_THINKING,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Interactions API 요청 바디. 텍스트 전용 호출은 image_part=None."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt["user"]}]
    if image_part:
        parts.append(image_part)

    body: dict[str, Any] = {
        "model": model,
        "input": parts,
        "generation_config": {
            "seed": seed,
            "thinking_level": thinking_level,
            "max_output_tokens": max_output_tokens,
        },
    }
    if prompt.get("system"):
        body["system_instruction"] = prompt["system"]
    if prompt.get("schema"):
        body["response_format"] = {
            "type": "text",
            "mime_type": "application/json",
            "schema": prompt["schema"],
        }
    elif prompt.get("expect_json", True):
        body["response_format"] = {"type": "text", "mime_type": "application/json"}
    return body


def extract_text(payload: dict[str, Any]) -> str:
    """steps 에서 model_output 의 텍스트만 이어붙인다 (thought 는 건너뛴다)."""
    chunks: list[str] = []
    for step in payload.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for item in step.get("content") or []:
            if item.get("type") == "text" and item.get("text"):
                chunks.append(item["text"])
    return "".join(chunks)


def call(
    body: dict[str, Any],
    *,
    key: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = 180,
    usage: Usage | None = None,
) -> Result:
    """Interactions API 호출 + 재시도. 이미지 유무와 무관하게 공용."""
    last_error = ""
    status = 0
    last_quota_headers: dict[str, str] = {}
    retry_waits: list[float] = []
    retry_after_seen: list[str] = []
    for attempt in range(1, max_retries + 1):
        started = time.time()
        try:
            response = requests.post(
                API_URL,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                data=json.dumps(body),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_retries:
                if usage:
                    usage.retries += 1
                wait = _backoff(attempt)
                retry_waits.append(round(wait, 1))
                time.sleep(wait)
                continue
            return Result(
                ok=False, error=last_error, attempts=attempt,
                retry_waits=retry_waits, retry_after_seen=retry_after_seen,
            )

        elapsed = time.time() - started
        status = response.status_code

        if status in RETRY_STATUS and attempt < max_retries:
            if usage:
                usage.retries += 1
            # 서버가 Retry-After 를 주면 그 값을 따른다. 무시하고 즉시 재시도하면
            # 한도만 갉아먹는다.
            header = response.headers.get("Retry-After")
            retry_after_seen.append(header or "(없음)")
            last_quota_headers = _quota_headers(response)
            last_error = f"HTTP {status}: {response.text[:500]}"
            wait = _retry_after(response, attempt)
            retry_waits.append(round(wait, 1))
            time.sleep(wait)
            continue

        if status != 200:
            return Result(
                ok=False,
                error=f"HTTP {status}: {response.text[:500]}",
                http_status=status,
                elapsed=elapsed,
                attempts=attempt,
                retry_waits=retry_waits,
                retry_after_seen=retry_after_seen,
                quota_headers=_quota_headers(response),
            )

        payload = response.json()
        meta = payload.get("usage", {}) or {}
        responded = str(payload.get("model", ""))
        if usage:
            usage.add(meta, elapsed)

        text = extract_text(payload)
        if not text:
            return Result(
                ok=False,
                error=f"model_output 없음 (status={payload.get('status')}): "
                f"{json.dumps(payload, ensure_ascii=False)[:300]}",
                usage=meta,
                elapsed=elapsed,
                finish_reason=str(payload.get("status", "")),
                http_status=status,
                attempts=attempt,
                model_responded=responded,
                retry_waits=retry_waits,
                retry_after_seen=retry_after_seen,
            )

        parsed, parse_error = _parse_json(text)
        return Result(
            ok=True,
            text=text,
            parsed=parsed,
            parse_error=parse_error,
            elapsed=elapsed,
            usage=meta,
            finish_reason=str(payload.get("status", "")),
            http_status=status,
            attempts=attempt,
            model_responded=responded,
            retry_waits=retry_waits,
            retry_after_seen=retry_after_seen,
        )

    return Result(
        ok=False,
        error=last_error or f"HTTP {status}: 재시도 {max_retries}회 초과",
        http_status=status,
        attempts=max_retries,
        retry_waits=retry_waits,
        retry_after_seen=retry_after_seen,
        quota_headers=last_quota_headers,
    )


def generate(
    image_path: Path,
    prompt: dict[str, Any],
    *,
    key: str,
    model: str = DEFAULT_MODEL,
    long_edge: int = DEFAULT_LONG_EDGE,
    seed: int = DEFAULT_SEED,
    thinking_level: str = DEFAULT_THINKING,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = 180,
    usage: Usage | None = None,
) -> Result:
    """이미지 1장 + 프롬프트 -> 판정 JSON."""
    part, _ = load_image_part(image_path, long_edge)
    body = build_body(
        prompt, model, part, seed=seed, thinking_level=thinking_level,
        max_output_tokens=max_output_tokens,
    )
    return call(body, key=key, max_retries=max_retries, timeout=timeout, usage=usage)


def ping(key: str, model: str = DEFAULT_MODEL) -> Result:
    """인증 확인용 최소 호출 (텍스트만). 이미지 문제와 인증 문제를 분리한다."""
    prompt = {
        "user": "'정상'이라는 단어만 출력하라.",
        "system": "",
        "schema": None,
        "expect_json": False,
    }
    body = build_body(prompt, model, None)
    body.pop("response_format", None)
    return call(body, key=key, max_retries=DEFAULT_MAX_RETRIES)


def config_snapshot(
    model: str,
    prompt: dict[str, Any],
    *,
    long_edge: int = DEFAULT_LONG_EDGE,
    seed: int = DEFAULT_SEED,
    thinking_level: str = DEFAULT_THINKING,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """이번 실행에 쓴 호출 설정 전체. 결과 파일 옆에 남겨 비교 가능하게 한다."""
    schema = prompt.get("schema")
    schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True) if schema else ""
    return {
        "api_url": API_URL,
        "model_requested": model,
        "model_fallback_allowed": ALLOW_MODEL_FALLBACK,
        "prompt_version": prompt.get("version"),
        "system_instruction_sha256": hashlib.sha256(
            (prompt.get("system") or "").encode("utf-8")
        ).hexdigest()[:16],
        "user_prompt_sha256": hashlib.sha256(
            (prompt.get("user") or "").encode("utf-8")
        ).hexdigest()[:16],
        "response_format": "json_schema" if schema else "json_freeform",
        "response_schema_sha256": hashlib.sha256(schema_json.encode("utf-8")).hexdigest()[:16]
        if schema
        else "",
        "seed": seed,
        "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
        "image_long_edge": long_edge,
        "image_format": "JPEG q90",
    }


# 한도·과금 관련으로 알려진 헤더 이름들. 있으면 그대로 기록하고, 없으면 비워 둔다.
QUOTA_HEADER_HINTS = (
    "retry-after",
    "x-ratelimit",
    "ratelimit",
    "x-goog-quota",
    "quota",
)


def _quota_headers(response: "requests.Response") -> dict[str, str]:
    return {
        k: v
        for k, v in response.headers.items()
        if any(hint in k.lower() for hint in QUOTA_HEADER_HINTS)
    }


def _backoff(attempt: int) -> float:
    return min(2**attempt + random.uniform(0, 1), 60.0)


def _retry_after(response: "requests.Response", attempt: int) -> float:
    """429·503 은 서버가 알려주는 대기 시간을 우선 따른다."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), 120.0)
        except ValueError:
            pass
    return _backoff(attempt)


def _parse_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """응답 본문에서 JSON 객체를 꺼낸다. 코드펜스나 잡설이 섞여도 시도한다."""
    raw = (text or "").strip()
    if not raw:
        return None, "빈 응답"

    candidates = [raw]
    if raw.startswith("```"):
        stripped = raw.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        candidates.append(stripped.strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value, None
        return None, f"최상위가 객체가 아님: {type(value).__name__}"
    return None, "JSON 파싱 실패"
