# syntax=docker/dockerfile:1
#
# Headless Certum SimplySign signer for Windows Authenticode signing in CI.
#
# Bakes in everything the release sign jobs need: SimplySign Desktop (~313 MB
# installer from Certum's slow EU host), the X/Qt/PKCS#11 runtime stack, jsign,
# and osslsigncode, so a sign run pulls one image instead of downloading and
# apt-installing every time. Built by certum-signer-image.yml and consumed BY
# DIGEST from electron.yml / windows-installer.yml / windows-signing-test.yml —
# bumping this image is always an explicit digest change in those workflows.
#
# Contains NO secrets: credentials only ever enter at job runtime as env vars
# from the windows-signing environment. Adapted from ReactiveUI's signer image
# (github.com/reactiveui/actions-common), with two deviations: sha256 pins on
# both downloads (Certum publishes no checksums, so we pin first-download
# hashes), and a mingw stage compiling the smoke-test PE from source.

# --- stage 1: tiny throwaway PE for the signing smoke test -------------------
FROM ubuntu:24.04 AS testpe
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc-mingw-w64-x86-64 \
    && rm -rf /var/lib/apt/lists/*
COPY hello.c /tmp/hello.c
RUN x86_64-w64-mingw32-gcc -O2 -s -o /tmp/hello.exe /tmp/hello.c

# --- stage 2: the signer -----------------------------------------------------
FROM ubuntu:24.04

# SimplySign Desktop Linux (Ubuntu) installer. No Renovate/Dependabot
# datasource exists for Certum's file host; bump the version AND the sha256
# manually when Certum ships a new build, then re-run the smoke test before
# any release uses the new digest (the login automation's click coordinates
# are validated against this exact version).
ARG SIMPLYSIGN_VERSION=2.9.14-9.4.3.0
ARG SIMPLYSIGN_SHA256=e462dc83679f9987d4e8419c9b4db30e11884bffd875e85e142ac77aad7233e8
# renovate: datasource=maven depName=net.jsign:jsign
# 7.4 is the version the ReactiveUI signing flow was validated against; 7.5
# exists — bump deliberately, together with a smoke-test run.
ARG JSIGN_VERSION=7.4
ARG JSIGN_SHA256=2abf2ade9ea322acc2d60c24794eadc465ff9380938fca4c932d09e0b25f1c28

ENV DEBIAN_FRONTEND=noninteractive

# Headless X (Xvfb + fluxbox), UI automation (xdotool/wmctrl), screenshots for
# login diagnostics (imagemagick), PKCS#11 tooling, Java for jsign,
# osslsigncode for signature verification, and the Qt5/WebKit + system libs
# SimplySign needs (list from ReactiveUI's validated image).
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11-utils xdotool wmctrl fluxbox imagemagick \
      opensc opensc-pkcs11 osslsigncode default-jre-headless \
      unzip openssl ca-certificates curl python3 procps \
      libx11-6 libxext6 libxcomposite1 libxi6 libice6 libsm6 libxrender1 \
      libxcb1 libxau6 libpcsclite1 libglx-mesa0 libgl1 libxslt1.1 \
      libxkbcommon0 libxcb-xinerama0 libfontconfig1 libdbus-1-3 \
      libpulse-mainloop-glib0 libpulse0 libasound2t64 \
      libqt5webkit5 libqt5multimedia5 libqt5multimediawidgets5 libqt5quick5 \
      libqt5printsupport5 libqt5positioning5 libqt5sensors5 libqt5opengl5 \
      libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
      libwoff1 libharfbuzz0b libhyphen0 libwebp7 libwebpdemux2 \
      libnss3 libxtst6 libxss1 libxrandr2 libxdamage1 libxfixes3 libxcursor1 \
      libegl1 libgbm1 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

# Install SimplySign Desktop. The bundled SimplySignDesktop_start wrapper
# hardcodes /opt/SimplySignDesktop/SimplySignDesktop, so the dist MUST live
# there. Extract to a temp dir, then move the dist into place and drop the
# installer + temp to keep the layer smaller.
RUN curl -fsSL -o /tmp/ss.bin "https://files.certum.eu/software/SimplySignDesktop/Linux-Ubuntu/${SIMPLYSIGN_VERSION}/SimplySignDesktop-${SIMPLYSIGN_VERSION}-x86_64-prod-ubuntu.bin" \
    && echo "${SIMPLYSIGN_SHA256}  /tmp/ss.bin" | sha256sum -c - \
    && mkdir -p /tmp/ss-extract \
    && (yes yes | sh /tmp/ss.bin --target /tmp/ss-extract || true) \
    && mv "$(find /tmp/ss-extract -maxdepth 1 -type d -name 'SSD-*-dist' | head -1)" /opt/SimplySignDesktop \
    && rm -rf /tmp/ss.bin /tmp/ss-extract \
    && test -x /opt/SimplySignDesktop/SimplySignDesktop

# jsign (Authenticode signing over PKCS#11).
RUN curl -fsSL -o /opt/jsign.jar "https://repo1.maven.org/maven2/net/jsign/jsign/${JSIGN_VERSION}/jsign-${JSIGN_VERSION}.jar" \
    && echo "${JSIGN_SHA256}  /opt/jsign.jar" | sha256sum -c -

COPY --from=testpe /tmp/hello.exe /opt/test/hello.exe

# Contract consumed by the certum-sign action: use these baked-in paths.
#
# USER=root: SimplySignDesktop reads $USER at startup and segfaults (NULL
# deref in its bundled OpenSSL) when it is unset. Container jobs don't set
# $USER, so bake a default in — without it the login window never appears.
ENV CERTUM_PREBUILT=1 \
    USER=root \
    SS_DIST=/opt/SimplySignDesktop \
    JSIGN_JAR=/opt/jsign.jar \
    LD_LIBRARY_PATH=/opt/SimplySignDesktop

LABEL org.opencontainers.image.title="Certum SimplySign signer" \
      org.opencontainers.image.description="SimplySign Desktop + jsign + osslsigncode for headless Windows Authenticode signing" \
      org.opencontainers.image.source="https://github.com/pikselkroken/pixlstash"
