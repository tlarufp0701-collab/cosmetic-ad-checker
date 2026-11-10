"""링크에서 상세페이지 이미지를 가져와 검토용 구간으로 잘라 보관한다.

회사 PC 보안 프로그램(파일 업로드 차단) 때문에, 브라우저는 링크만 보내고
이미지는 서버가 직접 받아온다. 자른 이미지는 /tmp에 잠깐 보관하고
브라우저에는 작은 미리보기와 토큰만 돌려준다.
"""

import base64
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

Image.MAX_IMAGE_PIXELS = 120_000_000  # 아주 긴 상세페이지 통이미지 허용

CACHE_DIR = os.environ.get("SLICE_CACHE_DIR", "/tmp/slices")
CACHE_TTL = 2 * 3600
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_W, SLICE_H, OVERLAP = 1000, 1500, 120
MAX_LINKS = 5
MAX_CANDIDATES = 60
MAX_SLICES = 48
HTML_LIMIT = 8 * 1024 * 1024
IMG_LIMIT = 20 * 1024 * 1024

DETAIL_SELECTORS = [
    "#prdDetail", ".xans-product-detail", "#productDetail", ".product-detail", ".prd-detail",
    ".detail_cont", ".detailArea", ".goods_detail", ".goods-detail", "#goods_detail",
    ".se-main-container", "#detail", ".detail-content", ".product_detail", "#prd_detail",
]
IMG_ATTRS = ["ec-data-src", "data-src", "data-original", "data-lazy-src", "data-lazy", "data-url", "src"]
BLOCKED_HINT = {
    "coupang.com": "쿠팡은 외부에서 페이지를 가져오는 것을 막고 있어요.",
    "smartstore.naver.com": "네이버 스마트스토어는 상세페이지를 화면에서 따로 불러와서 링크로는 이미지가 안 잡힐 수 있어요.",
    "brand.naver.com": "네이버 브랜드스토어는 상세페이지를 화면에서 따로 불러와서 링크로는 이미지가 안 잡힐 수 있어요.",
}


class FetchError(Exception):
    pass


# ---------- 안전한 다운로드 ----------
def _check_host(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("http:// 또는 https:// 로 시작하는 주소를 넣어주세요.")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise FetchError(f"주소를 찾을 수 없어요: {p.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError("이 주소는 가져올 수 없어요.")


def _get(url: str, limit: int, timeout: int = 15, referer: str | None = None):
    """리다이렉트마다 주소를 확인하면서 받아온다. (content, content_type, final_url)"""
    headers = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    if referer:
        headers["Referer"] = referer
    for _ in range(6):
        _check_host(url)
        r = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location")
            r.close()
            if not loc:
                break
            url = urljoin(url, loc)
            continue
        if r.status_code >= 400:
            r.close()
            raise FetchError(f"페이지를 열 수 없어요 (응답 코드 {r.status_code}).")
        buf = io.BytesIO()
        for chunk in r.iter_content(65536):
            buf.write(chunk)
            if buf.tell() > limit:
                r.close()
                raise FetchError("파일이 너무 커요.")
        return buf.getvalue(), (r.headers.get("Content-Type") or "").lower(), url
    raise FetchError("리다이렉트가 너무 많아요.")


# ---------- 페이지 분석 ----------
def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _parse_page(html: bytes, base: str):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"]
    elif soup.title and soup.title.string:
        title = soup.title.string
    desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    desc = desc_tag.get("content", "") if desc_tag else ""

    roots = []
    for sel in DETAIL_SELECTORS:
        roots.extend(soup.select(sel))
    in_detail = bool(roots)

    def collect(nodes):
        out = []
        for root in nodes:
            for img in root.find_all("img"):
                for a in IMG_ATTRS:
                    v = (img.get(a) or "").strip()
                    if v and not v.startswith("data:"):
                        out.append(urljoin(base, v))
                        break
        return out

    urls = collect(roots) if roots else []
    if len(urls) < 1:
        in_detail = False
        urls = collect([soup])

    seen, ordered = set(), []
    for u in urls:
        if u not in seen and not u.lower().endswith(".svg"):
            seen.add(u)
            ordered.append(u)

    detail_text = _clean_text(" ".join(r.get_text(" ") for r in roots))[:2500] if roots else ""
    return _clean_text(title)[:150], _clean_text(desc)[:300], detail_text, ordered[:MAX_CANDIDATES], in_detail


def _open_image(data: bytes) -> Image.Image | None:
    try:
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            return bg
        return im.convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _download_images(urls: list[str], referer: str) -> list[Image.Image | None]:
    def one(u):
        try:
            data, ctype, _ = _get(u, IMG_LIMIT, timeout=15, referer=referer)
            if ctype and not ctype.startswith("image/") and "octet-stream" not in ctype:
                return None
            return _open_image(data)
        except Exception:  # noqa: BLE001
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(one, urls))


def _slice(im: Image.Image) -> list[Image.Image]:
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    if im.height <= SLICE_H * 1.25:
        return [im]
    parts, y = [], 0
    while y < im.height:
        h = min(SLICE_H, im.height - y)
        parts.append(im.crop((0, y, im.width, y + h)))
        if y + h >= im.height:
            break
        y += SLICE_H - OVERLAP
    return parts


def _thumb(im: Image.Image) -> str:
    t = im.copy()
    t.thumbnail((160, 214))
    buf = io.BytesIO()
    t.save(buf, "JPEG", quality=60)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _cleanup_cache() -> None:
    if not os.path.isdir(CACHE_DIR):
        return
    now = time.time()
    for name in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, name)
        try:
            if now - os.path.getmtime(path) > CACHE_TTL:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


# ---------- 공개 함수 ----------
def prepare_from_urls(urls: list[str]) -> dict:
    urls = [u for u in urls if u][:MAX_LINKS]
    if not urls:
        raise FetchError("링크를 넣어주세요.")
    _cleanup_cache()

    images: list[Image.Image] = []
    titles, texts, notes = [], [], []
    for url in urls:
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        host = (urlparse(url).hostname or "").lower()
        hint = next((v for k, v in BLOCKED_HINT.items() if host.endswith(k)), "")
        try:
            data, ctype, final = _get(url, max(HTML_LIMIT, IMG_LIMIT))
        except FetchError as e:
            notes.append(f"{host}: {e} {hint}".strip())
            continue
        except requests.RequestException:
            notes.append(f"{host}: 페이지에 연결하지 못했어요. {hint}".strip())
            continue

        if ctype.startswith("image/"):
            im = _open_image(data)
            if im:
                images.append(im)
            continue

        title, desc, detail_text, cand, in_detail = _parse_page(data, final)
        if title:
            titles.append(title)
        if detail_text:
            texts.append(detail_text)
        elif desc:
            texts.append(desc)
        min_w = 400 if in_detail else 600
        got = 0
        for im in _download_images(cand, final):
            if im is None or im.width < min_w or im.height < 200:
                continue
            images.append(im)
            got += 1
        if not got:
            notes.append(f"{host}: 상세 이미지를 찾지 못했어요. {hint}".strip())

    if not images:
        raise FetchError(" / ".join(notes) or "가져올 수 있는 이미지가 없어요.")

    token = uuid.uuid4().hex
    folder = os.path.join(CACHE_DIR, token)
    os.makedirs(folder, exist_ok=True)
    slices, idx = [], 0
    truncated = False
    for src_no, im in enumerate(images, start=1):
        parts = _slice(im)
        for p_no, part in enumerate(parts, start=1):
            if idx >= MAX_SLICES:
                truncated = True
                break
            part.save(os.path.join(folder, f"{idx:03d}.jpg"), "JPEG", quality=80, optimize=True)
            slices.append({
                "i": idx, "src": src_no,
                "part": f"{p_no}/{len(parts)}구간" if len(parts) > 1 else "",
                "thumb": _thumb(part),
            })
            idx += 1
        if truncated:
            break
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"count": idx, "created": time.time()}, f)

    if truncated:
        notes.append(f"이미지가 많아서 앞쪽 {MAX_SLICES}구간까지만 가져왔어요.")
    return {
        "token": token,
        "title": titles[0] if titles else "",
        "page_text": " / ".join(texts)[:2500],
        "source_count": len(images),
        "slices": slices,
        "notes": notes,
    }


def load_slice(token: str, i: int) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise FetchError("가져온 이미지 정보가 올바르지 않아요. 링크를 다시 가져와 주세요.")
    path = os.path.join(CACHE_DIR, token, f"{int(i):03d}.jpg")
    if not os.path.isfile(path):
        raise FetchError("가져온 이미지가 만료됐어요. 링크를 다시 가져와 주세요.")
    with open(path, "rb") as f:
        return f.read()
