# Server only: no pygame, no SDL, no system libraries.
FROM python:3.12-slim

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY server/ ./server/
COPY shared/ ./shared/
# db.py applies these on startup; without them the schema is never created.
COPY migrations/ ./migrations/

EXPOSE 8765

# --origin is set per deployment; without it any page may open a socket.
ENTRYPOINT ["python", "-m", "server.main"]
CMD ["--host", "0.0.0.0", "--port", "8765"]
