FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir Telethon==1.42.0
WORKDIR /app
COPY mtproto_login.py /app/mtproto_login.py
ENTRYPOINT ["python", "/app/mtproto_login.py"]
