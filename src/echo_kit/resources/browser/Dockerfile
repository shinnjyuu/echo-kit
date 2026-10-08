ARG BASE_IMAGE=mcr.microsoft.com/playwright:v1.63.0-noble
FROM ${BASE_IMAGE}
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/echo-browser
RUN npm install --omit=dev --no-audit --no-fund playwright-core@1.63.0
COPY server.cjs ./server.cjs
ENV DISPLAY=:99
ENTRYPOINT ["node", "/opt/echo-browser/server.cjs"]
