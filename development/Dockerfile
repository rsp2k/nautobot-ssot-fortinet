# Dev image: Nautobot 3.1 + nautobot-ssot + nautobot-firewall-models + our plugin.
#
# Build context is the repo root (see development/docker-compose.yml). We
# pip install -e /opt/plugin at build time so the editable install metadata
# is registered, then bind-mount the host's src/ over /opt/plugin/src at
# runtime for hot-reload. Restart nautobot-web to pick up code changes.

FROM ghcr.io/nautobot/nautobot:3.1-py3.12

USER 0

RUN pip install --no-cache-dir \
    nautobot-ssot==4.2.2 \
    nautobot-firewall-models==3.0.0 \
    fortigate-api==2.0.8

# Source baked in for the editable install; bind-mount overlays src/ at runtime.
COPY pyproject.toml /opt/plugin/
COPY README.md /opt/plugin/
COPY src /opt/plugin/src
RUN pip install --no-cache-dir -e "/opt/plugin[dev]"

USER nautobot
