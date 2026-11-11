# ot-sim device image for the mine OT demo.
# Builds ONLY the upstream ot-sim pieces this demo needs (CPU, logic, Modbus,
# message bus, DNP3 outstation) from a pinned upstream commit, plus a small
# read-only DNP3 class-0 poll tool (mine-dnp3-poll) that replaces the upstream
# pydnp3 test master (pydnp3 does not build on Debian Bookworm).
# Upstream: https://github.com/patsec/ot-sim  (GPL-3.0)

ARG OTSIM_REPO=https://github.com/patsec/ot-sim.git
ARG OTSIM_REF=9dc33047ff9fb474e61c8931c7dac7bf98df38ef
ARG GO_VERSION=1.25.4

# apt via flaky proxies/mirrors: retry the whole update+install a few times
FROM debian:bookworm AS build
ARG OTSIM_REPO
ARG OTSIM_REF
ARG GO_VERSION
ENV DEBIAN_FRONTEND=noninteractive
RUN for i in 1 2 3 4 5 6 7 8; do \
      apt-get -o Acquire::Retries=3 update; \
      apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        build-essential cmake git ca-certificates wget pkg-config \
        libboost-dev libczmq-dev libxml2-dev libzmq3-dev && break; \
      echo "apt failed (attempt $i), retrying"; sleep 6; \
    done && command -v cmake && rm -rf /var/lib/apt/lists/*
RUN wget -q -O /tmp/go.tgz https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz \
 && tar -C /usr/local -xzf /tmp/go.tgz && rm /tmp/go.tgz
ENV PATH=/usr/local/go/bin:$PATH

RUN git clone ${OTSIM_REPO} /src/ot-sim \
 && git -C /src/ot-sim checkout ${OTSIM_REF} \
 && git -C /src/ot-sim submodule update --init --recursive -- \
      src/c++/deps/cppzmq src/c++/deps/fmt src/c++/deps/json src/c++/deps/opendnp3 \
 && git -C /src/ot-sim log -1 --format='ot-sim %H %ci' > /src/OTSIM_VERSION

# Go modules: CPU, logic, Modbus (the only Go modules this demo uses)
RUN cd /src/ot-sim/src/go \
 && for m in cpu logic modbus; do go build -o /usr/local/bin/ot-sim-$m-module ./cmd/ot-sim-$m-module || exit 1; done

# C message bus + C++ DNP3 outstation module + our read-only DNP3 poll tool
COPY dnp3-poll /src/ot-sim/src/c++/cmd/mine-dnp3-poll
RUN mv /src/ot-sim/.git /src/ot-sim.git-off \
 && echo 'add_subdirectory(cmd/mine-dnp3-poll)' >> /src/ot-sim/src/c++/CMakeLists.txt \
 && cmake -S /src/ot-sim -B /build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /build -j"$(nproc)" --target install \
 && cp /src/OTSIM_VERSION /usr/local/share/OTSIM_VERSION

FROM debian:bookworm-slim AS prod
ENV DEBIAN_FRONTEND=noninteractive
RUN for i in 1 2 3 4 5 6 7 8; do \
      apt-get -o Acquire::Retries=3 update; \
      apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        libczmq4 libsodium23 libxml2 libzmq5 && break; \
      echo "apt failed (attempt $i), retrying"; sleep 6; \
    done && dpkg -s libzmq5 >/dev/null && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /usr/local/lib /usr/local/lib
COPY --from=build /usr/local/share/OTSIM_VERSION /usr/local/share/OTSIM_VERSION
RUN ldconfig
ARG OTSIM_REF
LABEL org.opencontainers.image.title="mine-ot-sim device (patsec/ot-sim)" \
      org.opencontainers.image.source="https://github.com/patsec/ot-sim" \
      org.opencontainers.image.revision="${OTSIM_REF}" \
      org.opencontainers.image.licenses="GPL-3.0-only"
WORKDIR /
CMD ["ot-sim-cpu-module", "/etc/ot-sim/config.xml"]
