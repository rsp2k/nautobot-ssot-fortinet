# Uninstall

## Remove the integration from Nautobot

Remove the package and the `nautobot_ssot_fortinet` entry from your `PLUGINS` list:

```python
# nautobot_config.py
PLUGINS = [
    "nautobot_ssot",
    "nautobot_firewall_models",
    # "nautobot_ssot_fortinet",   # ← remove
]
```

Then:

```bash
pip uninstall nautobot-ssot-fortinet
sudo systemctl restart nautobot nautobot-worker
```

## What stays behind

The integration writes to existing Nautobot models — it doesn't have its own database schema. So uninstalling the package leaves all synced records (AddressObjects, Policies, WirelessNetworks, etc.) intact in their respective Nautobot tables.

If you want to scrub the synced records too:

1. They're identifiable by the mangled name prefix `<hostname>__<vdom>__`. Pick the prefix(es) for each FortiGate you synced.
2. The Django shell deletes records by prefix:

```python
# nautobot-server shell_plus
from nautobot_firewall_models.models import (
    AddressObject,
    AddressObjectGroup,
    Policy,
    NATPolicy,
    ServiceObjectGroup,
)
from nautobot.wireless.models import WirelessNetwork, RadioProfile

prefix = "fgt-edge1__root__"

# Order matters: rules → policies → groups → leaves
for policy in Policy.objects.filter(name__startswith=prefix):
    for rule in policy.policy_rules.all():
        policy.policy_rules.remove(rule)
        rule.delete()
Policy.objects.filter(name__startswith=prefix).delete()

for npol in NATPolicy.objects.filter(name__startswith=prefix):
    for r in npol.nat_policy_rules.all():
        npol.nat_policy_rules.remove(r)
        r.delete()
NATPolicy.objects.filter(name__startswith=prefix).delete()

ServiceObjectGroup.objects.filter(name__startswith=prefix).delete()
AddressObjectGroup.objects.filter(name__startswith=prefix).delete()
AddressObject.objects.filter(name__startswith=prefix).delete()

WirelessNetwork.objects.filter(name__startswith=prefix).delete()
RadioProfile.objects.filter(name__startswith=prefix).delete()
```

`ServiceObject` records have a composite natural key without the hostname prefix — they may be shared across FortiGates. Decide per-deployment whether to scrub them.

## Credentials

The integration's credentials live in Nautobot's `Secret` + `SecretsGroup` + `ExternalIntegration` machinery. Uninstalling the integration leaves these records in place. To remove them, delete the corresponding records in:

- **Extensibility → External Integrations**
- **Secrets → Secrets Groups**
- **Secrets → Secrets**
