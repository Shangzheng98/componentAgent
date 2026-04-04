FROM python:3.13-slim

WORKDIR /app

# 先复制依赖定义，利用 Docker 层缓存
COPY pyproject.toml ./

# 创建最小 src 包以满足 editable install
RUN mkdir -p src && touch src/__init__.py && \
    pip install --no-cache-dir -e . && \
    rm -rf src

# 复制实际源码
COPY src/ ./src/

# 创建非 root 用户
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --no-create-home appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "src", "--transport", "http", "--port", "8000"]
