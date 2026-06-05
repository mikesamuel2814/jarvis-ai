FROM python:3.13-slim-bookworm

WORKDIR /opt/jarvis
RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV JARVIS_HOME=/home/kali/.jarvis \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Runtime user set in compose (UID/GID) so bind mounts stay owned by kali.
WORKDIR /home/kali/.jarvis
