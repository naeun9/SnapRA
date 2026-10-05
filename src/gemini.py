"""Gemini API 얇은 래퍼 (REST).

google-genai SDK 없이 requests 만 쓴다. 필요한 기능이 generateContent 호출 하나뿐이고,
의존성을 늘리지 않는 편이 재현에 유리하다.

무료 티어 주의:
    무료 티어는 입력·출력이 Google 제품 개선에 사용된다(공식 가격 문서의
    "Content used to improve our products: Yes"). 우리가 보내는 것은 AI-Hub
    공개 데이터셋 이미지라 문제가 없지만, 현장 사진을 받는 서비스로 확장할 때는
    유료 티어로 올려야 한다.

한도:
    RPM/TPM/RPD 는 계정·모델별로 다르고 공식 문서가 더 이상 고정 수치를 싣지 않는다
    (AI Studio 대시보드에서 확인). 그래서 이 래퍼는 호출 간 간격을 직접 제어하고
    429 를 만나면 Retry-After 를 따라 물러난다.
"""

from __future__ import annotations

import base64
import io
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    raise SystemExit("requests 가 필요하다: pip install -r requirements.txt")

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    raise SystemExit("Pillow 가 필요하다: pip install -r requirements.txt")

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.8-flash"  # 2026-10 기준 무료 티어 멀티모달 최신 Flash
DEFAULT_LONG_EDGE = 1024  # 긴 변 기준 리사이즈
RETRY_STATUS = (429, 500, 502, 503, 504)


@dataclass
class Usage:
    """토큰 사용량 누적. 한도 소모량을 눈으로 보기 위한 것."""

    calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    retries: int = 0
    seconds: float = 0.0

    def add(self, meta: dict[str, Any], elapsed: float) -> None:
        self.calls += 1
        self.prompt_tokens += int(meta.get("promptTokenCount", 0) or 0)
        self.output_tokens += int(meta.get("candidatesTokenCount", 0) or 0)
        self.total_tokens += int(meta.get("totalTokenCount", 0) or 0)
        self.seconds += elapsed

    def summary(self) -> str:
        avg = self.seconds / self.calls if self.calls else 0.0
        return (
            f"호출 {self.calls} / 재시도 {self.retries} / "
            f"입력 {self.prompt_tokens:,} + 출력 {self.output_tokens:,} = "
            f"{self.total_tokens:,} 토큰 / 평균 {avg:.1f}초"
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
    finish_reason: str = ""
    http_status: int = 0
    attempts: int = 1


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
    """이미지를 긴 변 long_edge 로 줄여 inline_data 파트로 만든다.

    원본은 1920x1080 이라 그대로 보내면 한도를 빠르게 먹는다. 1024 로 줄이면
    전송량이 약 1/3 이 되고, 안전설비 유무 판단에 필요한 해상도는 남는다.
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
        "inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(payload).decode("ascii"),
        }
    }
    return part, len(payload)


# ------------------------------------------------------------------ 호출
def generate(
    image_path: Path,
    prompt: dict[str, Any],
    *,
    key: str,
    model: str = DEFAULT_MODEL,
    long_edge: int = DEFAULT_LONG_EDGE,
    temperature: float = 0.0,
    max_retries: int = 4,
    timeout: int = 120,
    usage: Usage | None = None,
) -> Result:
    """이미지 1장 + 프롬프트 -> 판정 JSON.

    temperature 0 으로 고정한다. 같은 사진에 같은 판정이 나와야 평가가 의미 있다.
    """
    part, _ = load_image_part(image_path, long_edge)
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [part, {"text": prompt["user"]}]}],
        "generationConfig": {"temperature": temperature},
    }
    if prompt.get("system"):
        body["systemInstruction"] = {"parts": [{"text": prompt["system"]}]}
    if prompt.get("schema"):
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = prompt["schema"]
    elif prompt.get("expect_json", True):
        body["generationConfig"]["responseMimeType"] = "application/json"

    url = f"{API_BASE}/{model}:generateContent"
    last_error = ""
    status = 0
    for attempt in range(1, max_retries + 1):
        started = time.time()
        try:
            response = requests.post(
                url,
                headers={
                    "x-goog-api-key": key,
                    "Content-Type": "application/json",
                },
                data=json.dumps(body),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_retries:
                if usage:
                    usage.retries += 1
                time.sleep(_backoff(attempt))
                continue
            return Result(ok=False, error=last_error, attempts=attempt)

        elapsed = time.time() - started
        status = response.status_code

        if status in RETRY_STATUS and attempt < max_retries:
            if usage:
                usage.retries += 1
            time.sleep(_retry_after(response, attempt))
            continue

        if status != 200:
            detail = response.text[:500]
            return Result(
                ok=False,
                error=f"HTTP {status}: {detail}",
                http_status=status,
                elapsed=elapsed,
                attempts=attempt,
            )

        payload = response.json()
        meta = payload.get("usageMetadata", {}) or {}
        if usage:
            usage.add(meta, elapsed)

        candidates = payload.get("candidates") or []
        if not candidates:
            # 안전 필터 등으로 후보가 없을 수 있다. 원본을 남긴다.
            return Result(
                ok=False,
                error=f"후보 없음: {json.dumps(payload.get('promptFeedback', {}), ensure_ascii=False)[:300]}",
                usage=meta,
                elapsed=elapsed,
                http_status=status,
                attempts=attempt,
            )

        candidate = candidates[0]
        text = "".join(
            p.get("text", "") for p in (candidate.get("content", {}).get("parts") or [])
        )
        parsed, parse_error = _parse_json(text)
        return Result(
            ok=True,
            text=text,
            parsed=parsed,
            parse_error=parse_error,
            elapsed=elapsed,
            usage=meta,
            finish_reason=candidate.get("finishReason", ""),
            http_status=status,
            attempts=attempt,
        )

    return Result(
        ok=False,
        error=last_error or f"HTTP {status}: 재시도 {max_retries}회 초과",
        http_status=status,
        attempts=max_retries,
    )


def _backoff(attempt: int) -> float:
    return min(2**attempt + random.uniform(0, 1), 60.0)


def _retry_after(response: "requests.Response", attempt: int) -> float:
    """429 는 서버가 알려주는 대기 시간을 우선 따른다."""
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
