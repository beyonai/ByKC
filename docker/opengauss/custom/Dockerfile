ARG OPENGAUSS_BASE_IMAGE=opengauss/opengauss-server:7.0.0-RC1
ARG OPENGAUSS_SOURCE_REF=7.0.0-RC1
ARG OPENGAUSS_SOURCE_SHA256=
ARG OPENGAUSS_SOURCE_URL=https://codeload.github.com/opengauss-mirror/openGauss-server/tar.gz/refs/heads/${OPENGAUSS_SOURCE_REF}
FROM ${OPENGAUSS_BASE_IMAGE} AS source
ARG OPENGAUSS_SOURCE_REF
ARG OPENGAUSS_SOURCE_SHA256
ARG OPENGAUSS_SOURCE_URL

USER root
ENV OPENGAUSS_SOURCE_DIR=/tmp/openGauss-server

RUN dnf install -y --nogpgcheck tar gzip wget \
    && dnf clean all

WORKDIR /tmp

RUN wget -O openGauss-server.tar.gz "${OPENGAUSS_SOURCE_URL}" \
    && if [ -n "${OPENGAUSS_SOURCE_SHA256}" ]; then \
         echo "${OPENGAUSS_SOURCE_SHA256}  openGauss-server.tar.gz" | sha256sum -c -; \
       fi \
    && mkdir -p "${OPENGAUSS_SOURCE_DIR}" \
    && tar -xzf openGauss-server.tar.gz -C "${OPENGAUSS_SOURCE_DIR}" --strip-components=1

FROM ${OPENGAUSS_BASE_IMAGE} AS builder
USER root
ENV GAUSSHOME=/usr/local/opengauss
ENV PATH=${GAUSSHOME}/bin:${PATH}
ENV OPENGAUSS_SOURCE_DIR=/tmp/openGauss-server
ENV OPENGAUSS_SERVER_INCLUDE_DIR=${GAUSSHOME}/include/postgresql/server

RUN dnf install -y --nogpgcheck gcc gcc-c++ make tar gzip wget perl which diffutils openssl-devel libaio-devel \
    && dnf clean all

COPY --from=source ${OPENGAUSS_SOURCE_DIR} ${OPENGAUSS_SOURCE_DIR}
RUN cp -r ${OPENGAUSS_SOURCE_DIR}/src/include/* ${OPENGAUSS_SERVER_INCLUDE_DIR}/
RUN perl -0pi -e 's/double mcvsum;\n        double mcvsel;/double mcvsum;\n        double mcvsel;\n        bool inmcv = false;\n        double lastcommon = 0.0;/g; s/mcvsel = mcv_selectivity\(&vardata, &contproc, constval, varonleft, &mcvsum\);/mcvsel = mcv_selectivity\(&vardata, &contproc, constval, varonleft, &mcvsum, InvalidOid, \&inmcv, \&lastcommon\);/g' ${OPENGAUSS_SOURCE_DIR}/contrib/ltree/ltree_op.cpp
RUN perl -0pi -e 's/PG_CPPFLAGS = -DLOWER_NODE/PG_CPPFLAGS = -DLOWER_NODE\nexclude_option = -fPIE\noverride CPPFLAGS := \$(filter-out \$(exclude_option),\$(CPPFLAGS))\noverride CFLAGS := \$(filter-out \$(exclude_option),\$(CFLAGS)) -fPIC\noverride CXXFLAGS := \$(filter-out \$(exclude_option),\$(CXXFLAGS)) -fPIC/' ${OPENGAUSS_SOURCE_DIR}/contrib/ltree/Makefile

WORKDIR ${OPENGAUSS_SOURCE_DIR}/contrib/ltree
RUN make USE_PGXS=1 PG_CONFIG=${GAUSSHOME}/bin/pg_config \
        PG_CPPFLAGS="-I/usr/include -I${OPENGAUSS_SERVER_INCLUDE_DIR}/access/obs -DLOWER_NODE -DPGXC" \
    && make USE_PGXS=1 PG_CONFIG=${GAUSSHOME}/bin/pg_config \
        PG_CPPFLAGS="-I/usr/include -I${OPENGAUSS_SERVER_INCLUDE_DIR}/access/obs -DLOWER_NODE -DPGXC" \
        install

WORKDIR ${OPENGAUSS_SOURCE_DIR}/contrib/pg_trgm
RUN make USE_PGXS=1 PG_CONFIG=${GAUSSHOME}/bin/pg_config \
        PG_CPPFLAGS="-I/usr/include -I${OPENGAUSS_SERVER_INCLUDE_DIR}/access/obs -DPGXC" \
    && make USE_PGXS=1 PG_CONFIG=${GAUSSHOME}/bin/pg_config \
        PG_CPPFLAGS="-I/usr/include -I${OPENGAUSS_SERVER_INCLUDE_DIR}/access/obs -DPGXC" \
        install

FROM ${OPENGAUSS_BASE_IMAGE}

USER root
ENV GAUSSHOME=/usr/local/opengauss
ENV PATH=${GAUSSHOME}/bin:${PATH}

COPY --from=builder /usr/local/opengauss/share/postgresql/extension/ltree* /usr/local/opengauss/share/postgresql/extension/
COPY --from=builder /usr/local/opengauss/share/postgresql/extension/pg_trgm* /usr/local/opengauss/share/postgresql/extension/
COPY --from=builder /usr/local/opengauss/lib/postgresql/ltree.so /usr/local/opengauss/lib/postgresql/
COPY --from=builder /usr/local/opengauss/lib/postgresql/pg_trgm.so /usr/local/opengauss/lib/postgresql/

USER omm
