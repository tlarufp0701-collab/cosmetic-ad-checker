import base64
import json
import os
import time

from google import genai
from google.genai.types import GenerateContentConfig, Part

from .prompt import build_prompt

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

_MAX_RETRIES = 4
_RETRY_DELAYS = [3, 8, 15]


def _is_retryable(error: Exception) -> bool:
    text = str(error)
    return "503" in text or "UNAVAILABLE" in text or "429" in text or "RESOURCE_EXHAUSTED" in text


def _friendly_error(error: Exception | None) -> str:
    text = str(error or "")
    if "503" in text or "UNAVAILABLE" in text:
        return "지금 AI 서버가 혼잡합니다. 1~2분 뒤에 다시 시도해주세요."
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "오늘 무료 사용 한도를 다 썼습니다. 내일 다시 시도하거나 관리자에게 문의해주세요."
    if "API key" in text or "API_KEY" in text or "401" in text or "403" in text:
        return "API 키가 올바르지 않습니다. 관리자에게 문의해주세요."
    return f"검토에 실패했습니다: {text[:200]}"


def _decode_image(data_url: str) -> bytes:
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


def _parse(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("AI 응답을 읽지 못했습니다. 다시 시도해주세요.")
    return json.loads(text[start : end + 1])


def analyze(data: dict) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("서버에 GEMINI_API_KEY가 설정되어 있지 않습니다.")

    contents = [build_prompt(data)]
    for raw in data.get("image_bytes") or [_decode_image(img) for img in (data.get("images") or [])]:
        contents.append(Part.from_bytes(data=raw, mime_type="image/jpeg"))

    client = genai.Client(api_key=GEMINI_API_KEY)
    last_error = None
    response = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            break
        except Exception as e:  # noqa: BLE001
            last_error = e
            if not _is_retryable(e) or attempt == _MAX_RETRIES - 1:
                raise RuntimeError(_friendly_error(e)) from e
            time.sleep(_RETRY_DELAYS[attempt])
    if response is None:
        raise RuntimeError(_friendly_error(last_error))

    try:
        return _parse(response.text)
    except json.JSONDecodeError as e:
        raise RuntimeError("AI 응답 형식이 올바르지 않습니다. 다시 시도해주세요.") from e
