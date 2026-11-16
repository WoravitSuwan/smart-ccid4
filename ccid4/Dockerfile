FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Bangkok DJANGO_SETTINGS_MODULE=config.settings.prod \
    CCID4_DATA_DIR=/data HF_HUB_OFFLINE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui ffmpeg fonts-thai-tlwg fontconfig tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY fonts/ /usr/share/fonts/truetype/ccid4/
COPY deploy/fonts.conf /etc/fonts/conf.d/99-ccid4.conf
RUN fc-cache -f
WORKDIR /srv/ccid4
COPY requirements/ requirements/
ARG WITH_AI=false
RUN pip install --no-cache-dir -r requirements/base.txt \
 && if [ "$WITH_AI" = "true" ]; then pip install --no-cache-dir -r requirements/ai.txt; fi
COPY . .
RUN DJANGO_SECRET_KEY=build-only-placeholder-not-used-at-runtime-000 python manage.py collectstatic --noinput -v0
RUN useradd --system --home /data ccid4 && mkdir -p /data && chown ccid4 /data
USER ccid4
EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
