FROM docker.1ms.run/library/ubuntu:24.04
EXPOSE 7777
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

ARG APP_USER=appuser
ARG WORK_SPACE=/home/appuser/app
ARG RUST_VERSION=1.93.0
ARG RUST_ARCHIVE_SHA256=b9d9f01a96a2542852ccfddd82194276ba1c86bc76353309ff636b737fc0a772
WORKDIR ${WORK_SPACE}

ENV PIPX_BIN_DIR=/usr/local/bin \
	PIPX_HOME=/usr/local/pipx \
	PATH="${WORK_SPACE}/.venv/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH" \
	VIRTUAL_ENV="${WORK_SPACE}/.venv"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tzdata \
       fonts-noto-cjk \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone
RUN apt-get install -y build-essential ca-certificates curl python3 pipx xz-utils \
	&& rm -rf /var/lib/apt/lists/*

# uv 依赖 Rust 编译环境，从公开发行源下载并校验固定版本。
RUN curl -fsSL \
		"https://static.rust-lang.org/dist/rust-${RUST_VERSION}-x86_64-unknown-linux-gnu.tar.xz" \
		-o /tmp/rust.tar.xz \
	&& echo "${RUST_ARCHIVE_SHA256}  /tmp/rust.tar.xz" | sha256sum -c - \
	&& mkdir -p /tmp/rust \
	&& tar -xf /tmp/rust.tar.xz --strip-components=1 -C /tmp/rust \
	&& /tmp/rust/install.sh \
	&& rm -rf /tmp/rust /tmp/rust.tar.xz


RUN pipx install uv

# 先单独复制 .python-version 并安装 Python 解释器，
# 使解释器层独立缓存——依赖变更时不会重新下载 Python。
COPY epsilon-boot/.python-version ./
RUN uv python install

RUN useradd --create-home --shell /bin/bash ${APP_USER} \
	&& chown -R ${APP_USER}:${APP_USER} ${WORK_SPACE}
# 切换到非 root 用户后再创建虚拟环境，确保文件权限正确
USER ${APP_USER}
COPY --chown=${APP_USER}:${APP_USER} epsilon-boot/pyproject.toml epsilon-boot/uv.lock ./
RUN uv sync --frozen
COPY epsilon-boot/ .
ENTRYPOINT ["sh", "-c", ".venv/bin/python3 main.py"]
