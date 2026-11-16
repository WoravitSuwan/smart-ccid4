FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Bangkok CCID4_DATA_DIR=/data
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libreoffice-writer-nogui fonts-thai-tlwg fontconfig tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY deploy/99-ccid4-thai.conf /etc/fonts/conf.d/99-ccid4-thai.conf
RUN fc-cache -f
WORKDIR /srv/ccid4
COPY requirements.txt requirements-asr.txt ./
ARG WITH_ASR=false
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_ASR" = "true" ]; then pip install --no-cache-dir -r requirements-asr.txt; fi
COPY app ./app
RUN useradd --system --home /data ccid4 && mkdir -p /data && chown ccid4 /data
USER ccid4
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
