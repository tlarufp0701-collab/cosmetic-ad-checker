# 화장품 광고 리스크 검토기

광고 소재·상세페이지 이미지(또는 문구)를 올리면 식약처 화장품 표시·광고 기준으로
오인 소지가 있는 표현을 찾아 **위험도·근거·수정 문구**를 알려주는 웹앱입니다.
분석은 Google Gemini 무료 티어를 사용합니다.

## 검토 기준
- 화장품법 제13조(부당한 표시·광고 금지), 시행규칙 별표5
- 화장품 표시·광고 실증에 관한 규정
- 식약처 「화장품 표시·광고 관리 지침」(2025.1 개정 금지표현 포함)
- 메타 광고 정책 일부(Before/After, 개인 특성 지목 등)

기준 문구는 `src/prompt.py`의 `RULES`에 있습니다. 기준을 바꾸려면 이 부분만 고치고 push하면 됩니다.

## 로컬 실행
```bash
cp .env.example .env   # GEMINI_API_KEY 입력
pip install -r requirements.txt python-dotenv
py app.py
```

## Render 배포
1. 이 저장소를 Render에 연결: **New → Blueprint**(또는 Web Service) → 저장소 선택
2. `render.yaml`이 자동으로 Docker 설정을 잡습니다
3. **Environment**에서 `GEMINI_API_KEY` 입력 → Deploy
4. `https://cosmetic-ad-checker-xxxx.onrender.com` 주소가 생성됩니다

## 알아둘 점
- 긴 상세페이지는 브라우저에서 1,500px 단위로 잘라 8장씩 묶어 보냅니다.
- 공개 사이트라 IP당 시간당 30회로 제한합니다(`RATE_LIMIT_PER_HOUR`).
- 무료 한도는 블로그 생성기와 같은 키를 쓰면 함께 차감됩니다.
- Google 무료 티어는 입력 내용을 모델 개선에 활용할 수 있습니다. 미공개 소재는 주의하세요.
- 무료 플랜은 15분간 접속이 없으면 잠들고, 다음 접속 시 1분 정도 걸립니다.
- AI 참고 검토이며 최종 판단은 식약처 기준·대한화장품협회 광고자문을 따르세요.
