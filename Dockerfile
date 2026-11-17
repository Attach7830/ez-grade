FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GRADER_DATA_DIR=/data

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 grader \
    && useradd --uid 10001 --gid grader --no-create-home --shell /usr/sbin/nologin grader \
    && mkdir /data \
    && chown grader:grader /data

COPY app ./app
COPY examples ./examples
USER grader:grader
EXPOSE 8766
CMD ["python", "-m", "app"]
