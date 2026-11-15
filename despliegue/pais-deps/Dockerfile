# Centinela en modo PAIS (GQ LAB). Se construye desde la raíz del repo:
#   docker build -f despliegue/pais-deps/Dockerfile -t gq-centinela-pais:1.0 .
FROM python:3.12-slim
ARG VOZ=1
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 CENTINELA_PROVEEDOR=pais CENTINELA_CONFIG_LOCAL=/datos/config.local.yaml
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN grep -v -E "^(pytest|mlx-whisper)" requirements.txt > /tmp/req.txt \
    && if [ "$VOZ" != "1" ]; then grep -v -E "kokoro|whisper|huggingface|numpy" /tmp/req.txt > /tmp/r2 && mv /tmp/r2 /tmp/req.txt; fi \
    && pip install -r /tmp/req.txt
COPY . .
RUN chmod +x despliegue/pais-deps/entrypoint.sh
EXPOSE 8300
ENTRYPOINT ["despliegue/pais-deps/entrypoint.sh"]
