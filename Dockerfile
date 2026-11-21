FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY starlinkleo ./starlinkleo
RUN pip install --no-cache-dir .
ENV STARLINK_DB_PATH=/data/starlinkleo.db PORT=8080
VOLUME /data
EXPOSE 8080
CMD ["starlinkleo"]
