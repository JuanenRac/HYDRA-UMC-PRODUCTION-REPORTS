# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - Container Build: Dockerfile
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
# The KPI/OEE reporting API as a long-running service, like the rest of the family. It has no runtime dependency beyond
# the standard library. The health check only opens the port: GET /health is closed unless the operator opens it.
#
# Inside a container the service must listen on every interface to be reached through a published port, and it refuses to do
# that without a bearer token: pass it as PRODUCTION_REPORTS_TOKEN. Point it at the data lake with --datalake-url. Events that
# are accepted live in memory unless --events-file names a file in the /data volume. Publish the port on the loopback of the
# host (-p 127.0.0.1:8099:8099) unless something with its own authentication sits in front.
#
#   docker build -t hydra-umc-production-reports .
#   docker run --rm -e PRODUCTION_REPORTS_TOKEN='a-secret-of-16-or-more-characters' -p 127.0.0.1:8099:8099 \
#       -v production-reports-data:/data hydra-umc-production-reports --datalake-url http://datalake:8095

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md CHANGELOG.md LICENSE.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --system --uid 10001 hydra && mkdir /data && chown hydra /data
USER hydra
VOLUME ["/data"]

EXPOSE 8099
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8099), timeout=3).close()"]
ENTRYPOINT ["hydra-umc-production-reports"]
CMD ["--addr", "0.0.0.0", "--port", "8099"]
