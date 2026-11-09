import os
import time
import traceback
from collections import defaultdict, deque

from flask import Flask, jsonify, render_template, request

from src.analyzer import analyze
from src.cases import CASES, case_brief, fallback_cases

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 요청 1건 최대 25MB

MAX_IMAGES_PER_REQUEST = 10

# 공개 사이트라 무료 한도를 한 사람이 다 쓰지 않도록 IP당 시간당 횟수를 제한합니다.
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_HOUR", "30"))
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return True
    q.append(now)
    return False


def _attach_cases(result: dict) -> dict:
    """AI가 고른 사례 id를 사례집의 실제 내용으로 바꿔 붙인다 (AI가 사례 내용을 지어내지 않도록)."""
    for f in result.get("findings") or []:
        ids = f.get("similar_cases") or []
        if not isinstance(ids, list):
            ids = [ids]
        briefs = [b for b in (case_brief(i) for i in ids[:5]) if b and not b["product"].startswith("(") and not b["company"].startswith("(")][:2]
        if not briefs and f.get("category") != "플랫폼 정책":
            briefs = fallback_cases(f)
        f["similar_cases"] = briefs
    return result


@app.route("/cases")
def cases_page():
    return render_template("cases.html", cases=CASES)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True) or {}
    images = data.get("images") or []
    copy = (data.get("copy") or "").strip()

    if not images and not copy:
        return jsonify({"error": "이미지를 올리거나 광고 문구를 붙여넣어 주세요."}), 400
    if len(images) > MAX_IMAGES_PER_REQUEST:
        return jsonify({"error": f"한 번에 이미지는 {MAX_IMAGES_PER_REQUEST}개까지 보낼 수 있습니다."}), 400
    if _rate_limited(_client_ip()):
        return jsonify({"error": "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."}), 429

    try:
        return jsonify(_attach_cases(analyze(data)))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"error": "검토 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}), 500


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "이미지 용량이 너무 큽니다. 장수를 줄여 다시 시도해주세요."}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    if host == "127.0.0.1":
        print(f"\n  화장품 광고 리스크 검토기\n  브라우저에서 http://127.0.0.1:{port} 접속\n")
    app.run(host=host, port=port, debug=False)
