FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

# 이미지 여러 장 분석 + 재시도까지 감안해 타임아웃을 넉넉히 잡습니다.
CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 240 --workers 1 --threads 8
