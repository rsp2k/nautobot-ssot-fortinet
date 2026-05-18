"""FortiGate target wireless model — wtp-profile create paths (v2.2).

Three branches in ``FortiGateRadioProfile.create()``:

1. Missing ``original_profile_name`` → skip with warning, no API call.
2. Parent wtp-profile already in target store (sibling exists)
   → partial ``radio-N`` PUT against existing profile.
3. Parent wtp-profile absent from target store
   → sibling aggregation: collect all RadioProfiles for the same
   ``original_profile_name`` from the SOURCE adapter, build a combined
   POST payload, and create the whole wtp-profile.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nautobot_ssot_fortinet.diffsync.adapters.fortigate_wireless_target import (
    FortiGateWirelessTargetAdapter,
)
from nautobot_ssot_fortinet.diffsync.models.fortigate_target_wireless import (
    FortiGateRadioProfile,
)


def _rp_attrs(
    *,
    name: str,
    original_profile_name: str,
    radio_index: int,
    frequency: str = "5GHz",
    tx_power_min: int = 5,
    tx_power_max: int = 17,
    allowed_channel_list: list[int] | None = None,
    regulatory_domain: str = "US",
) -> dict:
    """Build a full DiffSync RadioProfile attrs dict (minus name = ids)."""
    return {
        "frequency": frequency,
        "tx_power_min": tx_power_min,
        "tx_power_max": tx_power_max,
        "allowed_channel_list": allowed_channel_list or [36, 40, 44, 48],
        "regulatory_domain": regulatory_domain,
        "original_profile_name": original_profile_name,
        "radio_index": radio_index,
        "vdom": "root",
        "hostname": "fgt-test",
    }


def _make_target_adapter() -> FortiGateWirelessTargetAdapter:
    """Build an empty target adapter without going through .load()."""
    a = FortiGateWirelessTargetAdapter.__new__(FortiGateWirelessTargetAdapter)
    # DiffSync.Adapter.__init__ wires up the empty stores; we re-init manually
    # because the parent FortiGateWirelessAdapter.__init__ wants a client.
    from diffsync import Adapter

    Adapter.__init__(a)
    a.client = MagicMock()
    a.job = MagicMock()
    a.hostname = "fgt-test"
    a.vdom = "root"
    a.name_prefix = "fgt-test__root"
    return a


# ---------------------------------------------------------------------------
# Branch 1: missing original_profile_name → warn + skip
# ---------------------------------------------------------------------------


def test_create_skips_when_original_profile_name_missing():
    target = _make_target_adapter()
    attrs = _rp_attrs(name="orphan", original_profile_name="", radio_index=1)
    attrs["original_profile_name"] = ""  # explicit empty

    result = FortiGateRadioProfile.create(target, ids={"name": "orphan"}, attrs=attrs)

    # Warning emitted, no FortiOS calls
    target.job.logger.warning.assert_called_once()
    target.client.cmdb.wireless_controller.wtp_profile.create.assert_not_called()
    target.client.cmdb.wireless_controller.wtp_profile.update.assert_not_called()
    assert result.name == "orphan"


# ---------------------------------------------------------------------------
# Branch 2: parent exists on target → per-radio partial update
# ---------------------------------------------------------------------------


def test_create_does_partial_update_when_target_sibling_exists():
    target = _make_target_adapter()

    # Sibling already in target store (radio-1 of "office-tri")
    sibling = FortiGateRadioProfile(
        name="fgt-test__root__office-tri__radio-1",
        **_rp_attrs(
            name="fgt-test__root__office-tri__radio-1",
            original_profile_name="office-tri",
            radio_index=1,
            frequency="2.4GHz",
        ),
    )
    target.add(sibling)

    # New radio-2 arriving for the same parent
    new_attrs = _rp_attrs(
        name="fgt-test__root__office-tri__radio-2",
        original_profile_name="office-tri",
        radio_index=2,
        frequency="5GHz",
    )

    FortiGateRadioProfile.create(
        target,
        ids={"name": "fgt-test__root__office-tri__radio-2"},
        attrs=new_attrs,
    )

    # Should have called UPDATE with just the radio-2 subfield
    wtp = target.client.cmdb.wireless_controller.wtp_profile
    wtp.update.assert_called_once()
    call_kwargs = wtp.update.call_args.kwargs
    assert call_kwargs["uid"] == "office-tri"
    assert "radio-2" in call_kwargs["data"]
    assert "radio-1" not in call_kwargs["data"]
    # No full-profile create call
    wtp.create.assert_not_called()


# ---------------------------------------------------------------------------
# Branch 3a: parent absent, no source_adapter → warn + skip
# ---------------------------------------------------------------------------


def test_create_warns_when_source_adapter_unavailable():
    target = _make_target_adapter()
    # No source_adapter attribute set on the target

    attrs = _rp_attrs(
        name="fgt-test__root__lobby__radio-1",
        original_profile_name="lobby",
        radio_index=1,
    )

    FortiGateRadioProfile.create(
        target,
        ids={"name": "fgt-test__root__lobby__radio-1"},
        attrs=attrs,
    )

    target.job.logger.warning.assert_called_once()
    target.client.cmdb.wireless_controller.wtp_profile.create.assert_not_called()
    target.client.cmdb.wireless_controller.wtp_profile.update.assert_not_called()


# ---------------------------------------------------------------------------
# Branch 3b: parent absent, source has siblings → aggregated wtp-profile POST
# ---------------------------------------------------------------------------


def test_create_aggregates_siblings_from_source():
    target = _make_target_adapter()
    source = _make_target_adapter()  # same shape works as a source stand-in
    target.source_adapter = source

    # Source has TWO RadioProfiles for "lobby" (radio-1 and radio-2)
    rp1 = FortiGateRadioProfile(
        name="fgt-test__root__lobby__radio-1",
        **_rp_attrs(
            name="fgt-test__root__lobby__radio-1",
            original_profile_name="lobby",
            radio_index=1,
            frequency="2.4GHz",
            tx_power_min=3,
            tx_power_max=15,
            allowed_channel_list=[1, 6, 11],
        ),
    )
    rp2 = FortiGateRadioProfile(
        name="fgt-test__root__lobby__radio-2",
        **_rp_attrs(
            name="fgt-test__root__lobby__radio-2",
            original_profile_name="lobby",
            radio_index=2,
            frequency="5GHz",
            tx_power_min=5,
            tx_power_max=17,
            allowed_channel_list=[36, 40, 44, 48],
        ),
    )
    source.add(rp1)
    source.add(rp2)

    # DiffSync orchestration would call create() for radio-1 first (the
    # alphabetically first one). At that point target store is empty for
    # this profile, so we go down the aggregation path.
    FortiGateRadioProfile.create(
        target,
        ids={"name": "fgt-test__root__lobby__radio-1"},
        attrs=rp1.get_attrs(),
    )

    wtp = target.client.cmdb.wireless_controller.wtp_profile
    wtp.create.assert_called_once()
    payload = wtp.create.call_args.kwargs["data"]

    # Combined payload: parent name + platform-mode default + BOTH radios
    assert payload["name"] == "lobby"
    assert payload["platform-mode"] == "FortiAP-tunnel-mode"
    assert "radio-1" in payload
    assert "radio-2" in payload
    assert "2 radios" in payload["comment"]
    # No update call (we POSTed the whole profile)
    wtp.update.assert_not_called()


def test_create_aggregates_single_radio_when_only_one_sibling():
    """Edge case: profile with just one radio still works via aggregation."""
    target = _make_target_adapter()
    source = _make_target_adapter()
    target.source_adapter = source

    only_rp = FortiGateRadioProfile(
        name="fgt-test__root__guest__radio-1",
        **_rp_attrs(
            name="fgt-test__root__guest__radio-1",
            original_profile_name="guest",
            radio_index=1,
            frequency="2.4GHz",
        ),
    )
    source.add(only_rp)

    FortiGateRadioProfile.create(
        target,
        ids={"name": "fgt-test__root__guest__radio-1"},
        attrs=only_rp.get_attrs(),
    )

    wtp = target.client.cmdb.wireless_controller.wtp_profile
    wtp.create.assert_called_once()
    payload = wtp.create.call_args.kwargs["data"]
    assert payload["name"] == "guest"
    assert "radio-1" in payload
    assert "radio-2" not in payload
    assert "1 radios" in payload["comment"]
