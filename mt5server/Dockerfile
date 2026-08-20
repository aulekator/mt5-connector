# Stage 1: Base image with apt packages
FROM ghcr.io/linuxserver/baseimage-selkies:arch-2026-08-07-ls182 AS base

ENV TITLE=MetaTrader
ENV WINEARCH=win64
ENV WINEPREFIX="/config/.wine"
ENV DISPLAY=:0
ENV SELKIES_IS_MANUAL_RESOLUTION_MODE=true
ENV SELKIES_MANUAL_WIDTH=1920
ENV SELKIES_MANUAL_HEIGHT=1080
# Ensure the directory exists with correct permissions
RUN mkdir -p /config/.wine && \
    chown -R abc:abc /config/.wine && \
    chmod -R 755 /config/.wine

# Update package lists and upgrade packages
RUN pacman -Suy --noconfirm
# Install required packages
RUN pacman -Sy --noconfirm \
    dos2unix \
    python-pip \
    python-websockets \
    wget \
    python-pyxdg \
    wine \
    imagemagick

# Stage 2: Final image
FROM base

# Copy the scripts directory and convert start.sh to Unix format
COPY app /app
COPY scripts /scripts
COPY root /
COPY mt5ticks /mt5ticks
RUN dos2unix /scripts/*.sh && \
    chmod +x /scripts/*.sh

RUN touch /var/log/mt5_setup.log && \
    chown abc:abc /var/log/mt5_setup.log && \
    chmod 644 /var/log/mt5_setup.log

EXPOSE 3000 5000 5001 8001 9000 18812
ENV MT5_API_PORT=5000
ENV WS_PORT=9000
VOLUME /config
