# Aplikacja + ffmpeg + faster-whisper. Obraz bazowy z bibliotekami CUDA/cuDNN pozwala
# transkrybować na karcie NVIDIA; bez karty ten sam obraz działa na CPU.
FROM nvcr.io/nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY examples ./examples

ENV EDITORIAL_DB=/app/data/editorial.db \
    EDITORIAL_MEDIA_DIR=/app/data/media \
    HF_HOME=/app/models

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
