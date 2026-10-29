FROM python:3.12-slim
WORKDIR /app
COPY memory_service.py /app/
RUN useradd --uid 10001 --create-home tracememory && mkdir /app/data && chown 10001:10001 /app/data
USER 10001:10001
ENV MEMORY_HOST=0.0.0.0 MEMORY_PORT=18086 MEMORY_DB=/app/data/memory.sqlite3 PYTHONUNBUFFERED=1
EXPOSE 18086
CMD ["python", "memory_service.py"]
