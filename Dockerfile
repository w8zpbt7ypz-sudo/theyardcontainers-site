FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=8000 DB_PATH=/data/yards.db
WORKDIR /app
COPY . /app
RUN rm -f /app/yards.db && mkdir -p /data && useradd -r app && chown -R app /app /data && chmod +x /app/entrypoint.sh
# Starts as root only to chown the mounted disk, then drops to user 'app' (see entrypoint.sh)
ENTRYPOINT ["/app/entrypoint.sh"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8000'),timeout=4)"
CMD ["python3", "server.py"]
