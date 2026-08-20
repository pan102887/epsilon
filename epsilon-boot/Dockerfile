FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
EXPOSE 7777
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

ARG APP_USER=appuser
ARG WORK_SPACE=/home/appuser/app
WORKDIR ${WORK_SPACE}

ENV PATH="${WORK_SPACE}/.venv/bin:$PATH" \
	VIRTUAL_ENV="${WORK_SPACE}/.venv"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tzdata \
       fonts-noto-cjk \
    && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash ${APP_USER} \
	&& chown -R ${APP_USER}:${APP_USER} /home/${APP_USER}
USER ${APP_USER}
COPY --chown=${APP_USER}:${APP_USER} epsilon-boot/pyproject.toml epsilon-boot/uv.lock ./
RUN uv sync --frozen --no-install-project
COPY --chown=${APP_USER}:${APP_USER} epsilon-boot/ .
RUN uv sync --frozen
ENTRYPOINT [".venv/bin/python3", "main.py"]
