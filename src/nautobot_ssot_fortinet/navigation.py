"""Nautobot navigation entries for the Fortinet SSoT app (v3.1+)."""

from __future__ import annotations

from nautobot.apps.ui import NavMenuAddButton, NavMenuGroup, NavMenuItem, NavMenuTab

menu_items = (
    NavMenuTab(
        name="Plugins",
        groups=(
            NavMenuGroup(
                name="Fortinet SSoT",
                weight=1000,
                items=(
                    NavMenuItem(
                        link="plugins:nautobot_ssot_fortinet:fortinetstaticroute_list",
                        name="Static Routes",
                        weight=100,
                        permissions=["nautobot_ssot_fortinet.view_fortinetstaticroute"],
                        buttons=(
                            NavMenuAddButton(
                                link="plugins:nautobot_ssot_fortinet:fortinetstaticroute_add",
                                permissions=["nautobot_ssot_fortinet.add_fortinetstaticroute"],
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
)
