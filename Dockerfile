# meow-now: one small Python image, no system audio libraries needed.
# The browser sends 16-bit PCM WAV, so decoding is the stdlib `wave` module.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY meow_now ./meow_now
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080
COPY --from=build /install /usr/local
RUN useradd --system --uid 10001 --no-create-home meow
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8080'), timeout=4)"]
CMD ["meow-now", "serve"]
