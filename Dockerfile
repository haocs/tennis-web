FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TENNIS_LIBRARY_ROOT=/library TENNIS_DATA_ROOT=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && groupadd -g 10001 courtside && useradd -u 10001 -g courtside -M courtside && mkdir /data && chown courtside:courtside /data
COPY --chown=courtside:courtside app.py storage.py state.py manage.py practices.py ./
COPY --chown=courtside:courtside templates ./templates
COPY --chown=courtside:courtside static ./static
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120", "--worker-tmp-dir", "/tmp", "--access-logfile", "-", "--error-logfile", "-", "app:create_app()"]
