FROM docker.io/library/python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
LABEL org.opencontainers.image.source="https://github.com/neboman11/overlordbot-local" org.opencontainers.image.licenses="AGPL-3.0-or-later"
COPY LICENSE /usr/share/licenses/overlordbot-local/LICENSE
RUN apt-get update && apt-get install -y --no-install-recommends libopus0 ca-certificates && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home bot
WORKDIR /app
COPY overlord/ ./overlord/
COPY data/ ./data/
COPY fixtures/ ./fixtures/
COPY scripts/replay_tacview.py ./scripts/replay_tacview.py
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
USER 10001
EXPOSE 8080 8081 42674
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('HTTP_PORT','8081')+'/readyz',timeout=2)"
ENTRYPOINT ["python", "-m", "overlord"]
CMD ["brain"]
