"""Nautobot-side DiffSync subclasses with CRUD against firewall-models ORM.

These subclass the vendor-neutral models in ``firewall.py`` and add the
``create``/``update``/``delete`` machinery that diffsync calls when its
diff says a record should land in (or leave) Nautobot.

ORM imports happen **inside** each method, not at module level, so that
unit tests can import the module without a fully-bootstrapped Django.
This is the same pattern used by every nautobot-ssot integration.
"""

from __future__ import annotations

from typing import Any

from nautobot_ssot_fortinet.diffsync.models.firewall import (
    AddressObject,
    AddressObjectGroup,
    NATPolicy,
    NATPolicyRule,
    Policy,
    PolicyRule,
    ServiceObject,
    ServiceObjectGroup,
)


class NautobotAddressObject(AddressObject):
    """Nautobot-side AddressObject with FK dispatch on address_type.

    The Nautobot ``AddressObject`` has 4 mutually-exclusive nullable FKs
    (``prefix``, ``fqdn``, ``ip_range``, ``ip_address``). ``create`` picks
    one based on the DiffSync ``address_type`` discriminator and upserts
    the referenced object (``Prefix``, ``FQDN``, etc.) before linking.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.extras.models import Status
        from nautobot.ipam.models import IPAddress, Namespace, Prefix
        from nautobot_firewall_models.models import (
            FQDN,
            IPRange,
        )
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )

        active = Status.objects.get(name="Active")
        default_ns = Namespace.objects.get(name="Global")
        kwargs: dict[str, Any] = {
            "name": ids["name"],
            "description": attrs.get("original_name", "")
            + (": " + attrs["description"] if attrs.get("description") else ""),
            "status": active,
        }

        address_type = attrs["address_type"]
        value = attrs["value"]

        if address_type == "ipmask":
            prefix, _ = Prefix.objects.get_or_create(
                prefix=value,
                namespace=default_ns,
                defaults={"status": active},
            )
            kwargs["prefix"] = prefix
        elif address_type == "fqdn":
            fqdn_obj, _ = FQDN.objects.get_or_create(name=value, defaults={"status": active})
            kwargs["fqdn"] = fqdn_obj
        elif address_type == "iprange":
            start, end = value.split("-", 1)
            ip_range, _ = IPRange.objects.get_or_create(
                start_address=start,
                end_address=end,
                vrf=None,
                defaults={"status": active},
            )
            kwargs["ip_range"] = ip_range
        elif address_type == "ipaddress":
            # Single host — represent as /32 IPAddress under default Namespace's
            # /32 parent prefix. Nautobot 3.x requires IPAddress to belong to
            # a Prefix in the same Namespace.
            host_prefix, _ = Prefix.objects.get_or_create(
                prefix=f"{value}/32",
                namespace=default_ns,
                defaults={"status": active},
            )
            ip_addr, _ = IPAddress.objects.get_or_create(
                address=f"{value}/32",
                parent=host_prefix,
                defaults={"status": active},
            )
            kwargs["ip_address"] = ip_addr
        else:
            raise ValueError(f"Unsupported address_type: {address_type!r}")

        ORMAddressObject.objects.update_or_create(name=ids["name"], defaults=kwargs)
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot_firewall_models.models import AddressObject as ORMAddressObject

        try:
            orm_obj = ORMAddressObject.objects.get(name=self.name)
        except ORMAddressObject.DoesNotExist:
            return super().update(attrs)

        if "description" in attrs:
            orm_obj.description = (self.original_name + ": " if self.original_name else "") + attrs["description"]
        # Note: changing address_type or value would mean repointing FKs,
        # which is rare in practice. We don't implement FK churn in v1 —
        # if the source changes type, the user should delete + recreate.
        orm_obj.save()
        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import AddressObject as ORMAddressObject

        ORMAddressObject.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotAddressObjectGroup(AddressObjectGroup):
    """Nautobot-side AddressObjectGroup — resolve members by mangled name."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.extras.models import Status
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )
        from nautobot_firewall_models.models import (
            AddressObjectGroup as ORMGroup,
        )

        active = Status.objects.get(name="Active")
        members_qs = ORMAddressObject.objects.filter(name__in=attrs["members"])
        group, _ = ORMGroup.objects.update_or_create(
            name=ids["name"],
            defaults={
                "description": (attrs.get("original_name") or "")
                + (": " + attrs["description"] if attrs.get("description") else ""),
                "status": active,
            },
        )
        group.address_objects.set(members_qs)
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )
        from nautobot_firewall_models.models import (
            AddressObjectGroup as ORMGroup,
        )

        try:
            group = ORMGroup.objects.get(name=self.name)
        except ORMGroup.DoesNotExist:
            return super().update(attrs)

        if "members" in attrs:
            members_qs = ORMAddressObject.objects.filter(name__in=attrs["members"])
            group.address_objects.set(members_qs)
        if "description" in attrs:
            group.description = (self.original_name + ": " if self.original_name else "") + attrs["description"]
        group.save()
        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import AddressObjectGroup as ORMGroup

        ORMGroup.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotServiceObject(ServiceObject):
    """Nautobot-side ServiceObject — composite NK (ip_protocol, port, name)."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.extras.models import Status
        from nautobot_firewall_models.models import ServiceObject as ORMServiceObject

        active = Status.objects.get(name="Active")
        ORMServiceObject.objects.update_or_create(
            name=ids["name"],
            ip_protocol=ids["ip_protocol"],
            port=ids["port"],
            defaults={
                "description": attrs.get("description", "") or "",
                "status": active,
            },
        )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot_firewall_models.models import ServiceObject as ORMServiceObject

        try:
            orm_obj = ORMServiceObject.objects.get(name=self.name, ip_protocol=self.ip_protocol, port=self.port)
        except ORMServiceObject.DoesNotExist:
            return super().update(attrs)
        if "description" in attrs:
            orm_obj.description = attrs["description"] or ""
        orm_obj.save()
        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import ServiceObject as ORMServiceObject

        ORMServiceObject.objects.filter(name=self.name, ip_protocol=self.ip_protocol, port=self.port).delete()
        super().delete()
        return self


class NautobotServiceObjectGroup(ServiceObjectGroup):
    """Nautobot-side ServiceObjectGroup — resolve members by composite NK."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from django.db.models import Q
        from nautobot.extras.models import Status
        from nautobot_firewall_models.models import (
            ServiceObject as ORMServiceObject,
        )
        from nautobot_firewall_models.models import (
            ServiceObjectGroup as ORMGroup,
        )

        active = Status.objects.get(name="Active")
        members_query = Q()
        for proto, port, name in attrs["members"]:
            members_query |= Q(ip_protocol=proto, port=port, name=name)
        members_qs = (
            ORMServiceObject.objects.filter(members_query) if attrs["members"] else ORMServiceObject.objects.none()
        )

        group, _ = ORMGroup.objects.update_or_create(
            name=ids["name"],
            defaults={
                "description": (attrs.get("original_name") or "")
                + (": " + attrs["description"] if attrs.get("description") else ""),
                "status": active,
            },
        )
        group.service_objects.set(members_qs)
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from django.db.models import Q
        from nautobot_firewall_models.models import (
            ServiceObject as ORMServiceObject,
        )
        from nautobot_firewall_models.models import (
            ServiceObjectGroup as ORMGroup,
        )

        try:
            group = ORMGroup.objects.get(name=self.name)
        except ORMGroup.DoesNotExist:
            return super().update(attrs)

        if "members" in attrs:
            q = Q()
            for proto, port, name in attrs["members"]:
                q |= Q(ip_protocol=proto, port=port, name=name)
            members_qs = ORMServiceObject.objects.filter(q) if attrs["members"] else ORMServiceObject.objects.none()
            group.service_objects.set(members_qs)
        group.save()
        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import ServiceObjectGroup as ORMGroup

        ORMGroup.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotPolicy(Policy):
    """Nautobot-side Policy — empty container; rules attach in NautobotPolicyRule.

    Per FortiGate-VDOM we create exactly one Policy. PolicyRules link to it
    via Policy.policy_rules M2M, populated by NautobotPolicyRule.create.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.extras.models import Status
        from nautobot_firewall_models.models import Policy as ORMPolicy

        active = Status.objects.get(name="Active")
        ORMPolicy.objects.update_or_create(
            name=ids["name"],
            defaults={
                "description": attrs.get("description", "") or "",
                "status": active,
            },
        )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot_firewall_models.models import Policy as ORMPolicy

        try:
            orm_obj = ORMPolicy.objects.get(name=self.name)
        except ORMPolicy.DoesNotExist:
            return super().update(attrs)
        if "description" in attrs:
            orm_obj.description = attrs["description"] or ""
        orm_obj.save()
        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import Policy as ORMPolicy

        # Deleting the Policy automatically removes the M2M rows; the
        # PolicyRule records themselves are NOT cascade-deleted (they're
        # M2M-linked, not FK'd). DiffSync's delete ordering handles
        # PolicyRule deletion separately, so by the time we get here,
        # the rules should already be gone.
        ORMPolicy.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotPolicyRule(PolicyRule):
    """Nautobot-side PolicyRule with M2M resolution + parent-Policy linkage.

    The 12 source/destination M2M attrs are resolved on every create/update
    via name lookup. ``policy_name`` (the mangled parent Policy name) is
    used to add this rule to the parent's ``policy_rules`` M2M.
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.extras.models import Status
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )
        from nautobot_firewall_models.models import (
            AddressObjectGroup as ORMAddressGroup,
        )
        from nautobot_firewall_models.models import (
            Policy as ORMPolicy,
        )
        from nautobot_firewall_models.models import (
            PolicyRule as ORMPolicyRule,
        )
        from nautobot_firewall_models.models import (
            ServiceObject as ORMServiceObject,
        )
        from nautobot_firewall_models.models import (
            ServiceObjectGroup as ORMServiceGroup,
        )

        active = Status.objects.get(name="Active")

        # Build the rule with scalar fields first; M2Ms get set after save().
        # PolicyRule.name is NOT unique in firewall-models — use update_or_create
        # by name only since DiffSync ensures uniqueness via our mangled form.
        rule, _ = ORMPolicyRule.objects.update_or_create(
            name=ids["name"],
            defaults={
                "action": attrs["action"],
                "log": attrs["log"],
                "index": attrs["index"],
                "description": _build_rule_description(attrs),
                "status": active,
                "request_id": attrs.get("original_name", "")[:64],
            },
        )

        # Resolve and assign the 6 M2M relationships.
        _set_m2m_by_name(rule.source_addresses, ORMAddressObject, attrs["source_addresses"])
        _set_m2m_by_name(rule.source_address_groups, ORMAddressGroup, attrs["source_address_groups"])
        _set_m2m_by_name(rule.destination_addresses, ORMAddressObject, attrs["destination_addresses"])
        _set_m2m_by_name(rule.destination_address_groups, ORMAddressGroup, attrs["destination_address_groups"])
        _set_m2m_services(rule.destination_services, ORMServiceObject, attrs["destination_services"])
        _set_m2m_by_name(rule.destination_service_groups, ORMServiceGroup, attrs["destination_service_groups"])

        # Attach to the parent Policy.
        try:
            parent = ORMPolicy.objects.get(name=attrs["policy_name"])
            parent.policy_rules.add(rule)
        except ORMPolicy.DoesNotExist:
            if adapter.job:
                adapter.job.logger.warning(
                    f"PolicyRule {ids['name']!r} parent Policy "
                    f"{attrs['policy_name']!r} not found — rule created but unlinked"
                )

        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )
        from nautobot_firewall_models.models import (
            AddressObjectGroup as ORMAddressGroup,
        )
        from nautobot_firewall_models.models import (
            PolicyRule as ORMPolicyRule,
        )
        from nautobot_firewall_models.models import (
            ServiceObject as ORMServiceObject,
        )
        from nautobot_firewall_models.models import (
            ServiceObjectGroup as ORMServiceGroup,
        )

        try:
            rule = ORMPolicyRule.objects.get(name=self.name)
        except ORMPolicyRule.DoesNotExist:
            return super().update(attrs)

        scalar_changed = False
        for field in ("action", "log", "index"):
            if field in attrs:
                setattr(rule, field, attrs[field])
                scalar_changed = True

        # description gets rebuilt from any changed attr — easier than tracking
        # which sub-component triggered the change.
        if scalar_changed or "description" in attrs or "original_name" in attrs:
            merged = {**self.get_attrs(), **attrs}
            rule.description = _build_rule_description(merged)
            scalar_changed = True

        if scalar_changed:
            rule.save()

        # M2M churn: re-resolve and re-assign only the lists that changed.
        if "source_addresses" in attrs:
            _set_m2m_by_name(rule.source_addresses, ORMAddressObject, attrs["source_addresses"])
        if "source_address_groups" in attrs:
            _set_m2m_by_name(rule.source_address_groups, ORMAddressGroup, attrs["source_address_groups"])
        if "destination_addresses" in attrs:
            _set_m2m_by_name(rule.destination_addresses, ORMAddressObject, attrs["destination_addresses"])
        if "destination_address_groups" in attrs:
            _set_m2m_by_name(rule.destination_address_groups, ORMAddressGroup, attrs["destination_address_groups"])
        if "destination_services" in attrs:
            _set_m2m_services(rule.destination_services, ORMServiceObject, attrs["destination_services"])
        if "destination_service_groups" in attrs:
            _set_m2m_by_name(rule.destination_service_groups, ORMServiceGroup, attrs["destination_service_groups"])

        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import PolicyRule as ORMPolicyRule

        # firewall-models has a protect_on_delete signal that refuses to
        # delete a PolicyRule still attached to a Policy.policy_rules M2M.
        # Walk the reverse relation, detach from every Policy, *then* delete.
        for rule in ORMPolicyRule.objects.filter(name=self.name):
            for policy in rule.policies.all():
                policy.policy_rules.remove(rule)
            rule.delete()
        super().delete()
        return self


# ---- shared helpers for NautobotPolicyRule M2M resolution -----------------


def _build_rule_description(attrs: dict[str, Any]) -> str:
    """Compose a human-readable description from FortiOS source attrs.

    Format: ``"<original_name>: <free-text>"`` to match the convention
    used elsewhere in the integration, so the Nautobot adapter's
    ``_strip_original_name_prefix`` can recover the original.
    """
    parts = []
    orig = attrs.get("original_name") or ""
    free = attrs.get("description") or ""
    if orig:
        head = f"{orig}: " if free else orig
        parts.append(head + free)
    elif free:
        parts.append(free)
    return parts[0] if parts else ""


def _set_m2m_by_name(rel_manager, model_cls, names: list[str]) -> None:
    """Resolve a list of mangled names → ORM objects → set on the relation."""
    if not names:
        rel_manager.clear()
        return
    qs = model_cls.objects.filter(name__in=names)
    rel_manager.set(qs)


def _set_m2m_services(rel_manager, model_cls, nks: list) -> None:
    """Resolve a list of ``(ip_protocol, port, name)`` tuples → ServiceObjects."""
    from django.db.models import Q

    if not nks:
        rel_manager.clear()
        return
    q = Q()
    for proto, port, name in nks:
        q |= Q(ip_protocol=proto, port=port, name=name)
    qs = model_cls.objects.filter(q)
    rel_manager.set(qs)


class NautobotNATPolicy(NATPolicy):
    """Nautobot-side NATPolicy — singleton container; rules link via M2M."""

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.extras.models import Status
        from nautobot_firewall_models.models import NATPolicy as ORMNATPolicy

        active = Status.objects.get(name="Active")
        ORMNATPolicy.objects.update_or_create(
            name=ids["name"],
            defaults={
                "description": attrs.get("description", "") or "",
                "status": active,
            },
        )
        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot_firewall_models.models import NATPolicy as ORMNATPolicy

        try:
            orm_obj = ORMNATPolicy.objects.get(name=self.name)
        except ORMNATPolicy.DoesNotExist:
            return super().update(attrs)
        if "description" in attrs:
            orm_obj.description = attrs["description"] or ""
        orm_obj.save()
        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import NATPolicy as ORMNATPolicy

        ORMNATPolicy.objects.filter(name=self.name).delete()
        super().delete()
        return self


class NautobotNATPolicyRule(NATPolicyRule):
    """Nautobot-side NATPolicyRule with destination-only M2Ms + parent link.

    Mirrors ``NautobotPolicyRule`` but with the ``original_*`` /
    ``translated_*`` field shape that NATPolicyRule uses. Only destination
    side is populated (DNAT pattern from FortiOS VIPs).
    """

    @classmethod
    def create(cls, adapter, ids: dict[str, Any], attrs: dict[str, Any]):
        from nautobot.extras.models import Status
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )
        from nautobot_firewall_models.models import (
            NATPolicy as ORMNATPolicy,
        )
        from nautobot_firewall_models.models import (
            NATPolicyRule as ORMNATPolicyRule,
        )
        from nautobot_firewall_models.models import (
            ServiceObject as ORMServiceObject,
        )

        active = Status.objects.get(name="Active")

        rule, _ = ORMNATPolicyRule.objects.update_or_create(
            name=ids["name"],
            defaults={
                "log": attrs["log"],
                "index": attrs["index"],
                "description": _build_rule_description(attrs),
                "status": active,
                "request_id": attrs.get("original_name", "")[:64],
            },
        )

        _set_m2m_by_name(
            rule.original_destination_addresses,
            ORMAddressObject,
            attrs["original_destination_addresses"],
        )
        _set_m2m_by_name(
            rule.translated_destination_addresses,
            ORMAddressObject,
            attrs["translated_destination_addresses"],
        )
        _set_m2m_services(
            rule.original_destination_services,
            ORMServiceObject,
            attrs["original_destination_services"],
        )
        _set_m2m_services(
            rule.translated_destination_services,
            ORMServiceObject,
            attrs["translated_destination_services"],
        )

        try:
            parent = ORMNATPolicy.objects.get(name=attrs["nat_policy_name"])
            parent.nat_policy_rules.add(rule)
        except ORMNATPolicy.DoesNotExist:
            if adapter.job:
                adapter.job.logger.warning(
                    f"NATPolicyRule {ids['name']!r} parent NATPolicy "
                    f"{attrs['nat_policy_name']!r} not found — created but unlinked"
                )

        return super().create(adapter, ids, attrs)

    def update(self, attrs: dict[str, Any]):
        from nautobot_firewall_models.models import (
            AddressObject as ORMAddressObject,
        )
        from nautobot_firewall_models.models import (
            NATPolicyRule as ORMNATPolicyRule,
        )
        from nautobot_firewall_models.models import (
            ServiceObject as ORMServiceObject,
        )

        try:
            rule = ORMNATPolicyRule.objects.get(name=self.name)
        except ORMNATPolicyRule.DoesNotExist:
            return super().update(attrs)

        scalar_changed = False
        for field in ("log", "index"):
            if field in attrs:
                setattr(rule, field, attrs[field])
                scalar_changed = True
        if scalar_changed or "description" in attrs or "original_name" in attrs:
            merged = {**self.get_attrs(), **attrs}
            rule.description = _build_rule_description(merged)
            scalar_changed = True
        if scalar_changed:
            rule.save()

        if "original_destination_addresses" in attrs:
            _set_m2m_by_name(
                rule.original_destination_addresses,
                ORMAddressObject,
                attrs["original_destination_addresses"],
            )
        if "translated_destination_addresses" in attrs:
            _set_m2m_by_name(
                rule.translated_destination_addresses,
                ORMAddressObject,
                attrs["translated_destination_addresses"],
            )
        if "original_destination_services" in attrs:
            _set_m2m_services(
                rule.original_destination_services,
                ORMServiceObject,
                attrs["original_destination_services"],
            )
        if "translated_destination_services" in attrs:
            _set_m2m_services(
                rule.translated_destination_services,
                ORMServiceObject,
                attrs["translated_destination_services"],
            )

        return super().update(attrs)

    def delete(self):
        from nautobot_firewall_models.models import NATPolicyRule as ORMNATPolicyRule

        # Same protect_on_delete dance as PolicyRule — unlink from parent
        # NATPolicy first, then delete.
        for rule in ORMNATPolicyRule.objects.filter(name=self.name):
            for policy in rule.nat_policies.all():
                policy.nat_policy_rules.remove(rule)
            rule.delete()
        super().delete()
        return self
