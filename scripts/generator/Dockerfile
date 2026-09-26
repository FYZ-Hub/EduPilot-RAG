# 一次性演示语料生成器环境（不加入 docker-compose 长期服务）
#
# 构建：
#   docker build -f scripts/generator/Dockerfile -t campus-rag-generator:1.0.0 .
# 运行（生成阶段禁止联网）：
#   docker run --rm --network none -v "<repo>:/work" -w /work campus-rag-generator:1.0.0 \
#     python scripts/generate_demo_corpus.py --output .tmp/demo-generated --seed 20260925
# 基础镜像固定到官方 tag + 不可变 digest（digest 取自本机已拉取镜像的 RepoDigests）
FROM python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d

# 固定 locale、时区与哈希种子，保证生成结果可复现
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONHASHSEED=0 \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /work

COPY scripts/generator/requirements.txt /tmp/generator-requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/generator-requirements.txt

CMD ["python", "scripts/generate_demo_corpus.py", "--help"]
