FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fdmigrate ./fdmigrate

# State (SQLite DB), reports and logs are written under /data so they can be
# mounted as a volume and survive container restarts (resume-on-crash).
VOLUME ["/data"]
ENV FDMIG_STATE_DIR=/data

ENTRYPOINT ["python", "-m", "fdmigrate"]
CMD ["--help"]
