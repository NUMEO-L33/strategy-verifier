FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV VERIFIER_STREAM_HOST=stream.binance.com:9443
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "verifier init --dir /data 2>/dev/null; verifier serve --dir /data"]
