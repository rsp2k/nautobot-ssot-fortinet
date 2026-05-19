.PHONY: help build up down restart logs logs-web ps shell nbshell seed sync-status check-navs clean

DC := docker compose
WEB := nautobot-web

help:
	@echo "nautobot-ssot-fortinet — dev stack"
	@echo ""
	@echo "Stack:"
	@echo "  make build       Build the dev image (run after src/ structural changes)"
	@echo "  make up          Start postgres + redis + web + worker"
	@echo "  make down        Stop containers (data persists in volumes)"
	@echo "  make restart     Restart only the web container (after code changes)"
	@echo "  make logs        Tail logs for the whole stack"
	@echo "  make logs-web    Tail only nautobot-web"
	@echo "  make ps          Container status"
	@echo "  make clean       DESTRUCTIVE — drop all volumes (wipes DB)"
	@echo ""
	@echo "Workflows:"
	@echo "  make shell       Bash shell on web container"
	@echo "  make nbshell     nautobot-server shell_plus"
	@echo "  make seed        Create dev SecretsGroup + ExternalIntegration 'fgt-dev'"
	@echo "  make check-navs  Verify SSoT + Firewall + Wireless navs all load"
	@echo "  make sync-status List recent Sync runs (read from DB)"

build:
	$(DC) build

up:
	$(DC) up -d
	@echo ""
	@echo "Stack starting. First boot ~60s for migrations + superuser."
	@echo "URL:   https://$$(grep ^DOMAIN .env | cut -d= -f2)/"
	@echo "Tail:  make logs-web"

down:
	$(DC) down

restart:
	$(DC) restart $(WEB)

logs:
	$(DC) logs -f --tail=100

logs-web:
	$(DC) logs -f --tail=100 $(WEB)

ps:
	$(DC) ps

shell:
	$(DC) exec $(WEB) bash

nbshell:
	$(DC) exec $(WEB) nautobot-server shell_plus

seed:
	@# django-extensions' `runscript` only discovers scripts in
	@# <INSTALLED_APP>/scripts/ — our /opt/nautobot/jobs/dev_scripts/
	@# bind-mount isn't a Python package of any installed app. So exec
	@# the file directly inside a shell_plus Django context.
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/seed_dev_data.py').read()); run()"

e2e-firewall:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_firewall_sync.py').read()); run()"

e2e-wireless:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_wireless_sync.py').read()); run()"

e2e-live-firewall:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_live_firewall.py').read()); run()"

e2e-live-wireless:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_live_wireless.py').read()); run()"

e2e-push-validate:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_validate.py').read()); run()"

e2e-push-wtp:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_wtp_profile.py').read()); run()"

e2e-push-policy:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_policy.py').read()); run()"

e2e-push-nat:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_nat.py').read()); run()"

e2e-push-address:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_address.py').read()); run()"

e2e-push-addrgrp:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_addrgrp.py').read()); run()"

e2e-push-service:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_service.py').read()); run()"

e2e-push-svcgrp:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_svcgrp.py').read()); run()"

e2e-push-vap:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_push_vap.py').read()); run()"

e2e-push-all: e2e-push-address e2e-push-addrgrp e2e-push-service e2e-push-svcgrp e2e-push-policy e2e-push-nat e2e-push-wtp e2e-push-vap

# Job lifecycle integration test (v2.10+) — closes the test gap that allowed
# v2.9's "AttributeError: 'ObjectVar' object has no attribute 'name'" bug.
# Exercises every SSoT Job's run() path with dryrun=True.
e2e-jobs-lifecycle:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_jobs_lifecycle.py').read()); run()"

# Module-import + Job-instantiation smoke test (v3.4.1+) — closes the test
# gap that allowed v3.2.1's "All groups defined in a tab must be an instance
# of NavMenuGroup" bug. Exercises real Nautobot import-time + constructor-
# time validation against every navigation/views/urls/forms/filters/tables/
# jobs/models module in this app. Non-zero exit on any failure — safe to
# wire into CI.
smoke-test:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load --command "exec(open('/opt/nautobot/jobs/dev_scripts/e2e_smoke_test.py').read()); run()"

check-navs:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load -c "\
from django.apps import apps; \
from django.urls import reverse; \
plugin_apps = [a.name for a in apps.get_app_configs() if a.name.startswith('nautobot_')]; \
print('Loaded Nautobot apps:'); \
[print(f'  - {a}') for a in sorted(plugin_apps)]; \
print(); \
print('Models present:'); \
print(f'  firewall AddressObject: {apps.get_model(\"nautobot_firewall_models\", \"AddressObject\")}'); \
print(f'  wireless WirelessNetwork: {apps.get_model(\"wireless\", \"WirelessNetwork\")}'); \
print(f'  ssot Sync: {apps.get_model(\"nautobot_ssot\", \"Sync\")}'); \
print(f'  our plugin config: {apps.get_app_config(\"nautobot_ssot_fortinet\").verbose_name}'); \
"

sync-status:
	$(DC) exec $(WEB) nautobot-server shell_plus --quiet-load -c "from nautobot_ssot.models import Sync; [print(f'{s.start_time}  {s.dry_run=}  {s.diff_summary}') for s in Sync.objects.order_by('-start_time')[:10]]"

clean:
	@echo "WARNING: this will delete all dev data."
	@printf "Type 'yes' to confirm: " && read confirm && [ "$$confirm" = "yes" ]
	$(DC) down -v
