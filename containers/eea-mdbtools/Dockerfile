FROM docker.io/library/debian@sha256:e9606f88b5f49b14d013d5c6d54ac7e11a48e13a6ec4c99d952330d03ddc703f

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV TZ=UTC

RUN set -eux; \
    test "$(dpkg --print-architecture)" = 'arm64'; \
    rm -f /etc/apt/sources.list.d/debian.sources; \
    printf '%s\n' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260610T000000Z/ trixie main' \
      > /etc/apt/sources.list; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      mdbtools=1.0.1-0.1; \
    test "$(mdb-export --version)" = 'mdbtools v1.0.1'; \
    mkdir -p /opt/tool-manifest; \
    dpkg-query -W -f='${binary:Package}\t${Version}\n' \
      | LC_ALL=C sort > /opt/tool-manifest/packages.tsv; \
    sha256sum /usr/bin/mdb-* \
      | LC_ALL=C sort > /opt/tool-manifest/binaries.sha256; \
    rm -rf /var/lib/apt/lists/*
