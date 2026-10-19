"""Shared base entity: one device per student."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import YclassCoordinator


class YclassEntity(CoordinatorEntity[YclassCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: YclassCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            manufacturer="Vklass",
            model="Göteborgs Stad",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://app.vklass.se/Calendar/Schedule",
        )
