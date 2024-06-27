#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# SPDX-License-Identifier: Apache-2.0
"""A charm of the s3 integrator service."""

import logging
from typing import Dict, List, Optional

import ops
import ops.charm
import ops.framework
import ops.lib
import ops.main
import ops.model
from charms.data_platform_libs.v0.object_storage import (
    AzureStorageProvider,
    CredentialRequestedEvent,
)
from ops.charm import ActionEvent, ConfigChangedEvent, RelationChangedEvent, StartEvent
from ops.model import ActiveStatus, BlockedStatus

from constants import (
    AZURE_MANDATORY_OPTIONS,
    AZURE_OPTIONS,
    AZURE_RELATION_NAME,
    KEYS_LIST,
    PEER_RELATION_NAME,
)

logger = logging.getLogger(__name__)


class ObjectStorageIntegratorCharm(ops.charm.CharmBase):
    """Charm for s3 integrator service."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.azure_provider = AzureStorageProvider(self, AZURE_RELATION_NAME)

        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

        self.framework.observe(
            self.azure_provider.on.credentials_requested, self._on_azure_credentials_requested
        )

        self.framework.observe(
            self.on[PEER_RELATION_NAME].relation_changed, self._on_peer_relation_changed
        )

        # actions
        self.framework.observe(
            self.on.sync_azure_credentials_action, self._on_sync_azure_credentials
        )

        self.framework.observe(
            self.on.get_azure_credentials_action, self.on_get_credentials_action
        )
        self.framework.observe(
            self.on.get_azure_connection_info_action, self.on_get_connection_info_action
        )

    def _on_azure_credentials_requested(self, event: CredentialRequestedEvent):
        """Handle the `credential-requested` event for azure storage."""
        if not self.unit.is_leader():
            return
        relation_id = event.relation.id

        container_name = self.get_secret("app", "container") or event.container

        logger.debug(f"Desired container name: {container_name}")
        assert container_name is not None
        # if bucket name is already specified ignore the one provided by the requirer app
        if self.get_secret("app", container_name) is None:
            self.set_secret("app", "container", container_name)

        desired_configuration = {}
        # collect all configuration options
        for option in AZURE_OPTIONS:
            if self.get_secret("app", option) is not None:
                desired_configuration[option] = self.get_secret("app", option)

        # update connection parameters in the relation data bug
        self.azure_provider.update_connection_info(relation_id, desired_configuration)

    @property
    def app_peer_data(self) -> Dict:
        """Application peer relation data object."""
        relation = self.model.get_relation(PEER_RELATION_NAME)
        if not relation:
            return {}

        return relation.data[self.app]

    @property
    def unit_peer_data(self) -> Dict:
        """Peer relation data object."""
        relation = self.model.get_relation(PEER_RELATION_NAME)
        if relation is None:
            return {}

        return relation.data[self.unit]

    def _on_start(self, _: StartEvent) -> None:
        """Handle the charm startup event."""
        missing_options = self.get_missing_parameters()
        logger.info(f"Missing options: {missing_options}")
        if missing_options:
            self.unit.status = ops.model.BlockedStatus(f"Missing parameters: {missing_options}")

    def _on_config_changed(self, _: ConfigChangedEvent) -> None:  # noqa: C901
        """Event handler for configuration changed events."""
        # Only execute in the unit leader
        if not self.unit.is_leader():
            return
        logger.debug(f"Current configuration: {self.config}")
        # store updates from config and apply them.
        update_config = {}

        # iterate over the option and check for updates
        for option in AZURE_OPTIONS:
            # option possibly removed from the config
            # (e.g. 'juju config --reset <option>' or 'juju config <option>=""')
            if option not in self.config or self.config[option] == "":
                if option in KEYS_LIST:
                    logger.debug("Secret parameter %s not stored inside config.", option)
                    continue
                # reset previous config value if present
                if self.get_secret("app", option) is not None:
                    self.set_secret("app", option, None)
                    update_config.update({option: ""})
                # skip in case of default value
                continue

            update_config.update({option: str(self.config[option])})
            self.set_secret("app", option, str(self.config[option]))

        if len(self.azure_provider.relations) > 0:
            for relation in self.azure_provider.relations:
                self.azure_provider.update_connection_info(relation.id, update_config)

    def get_secret(self, scope: str, key: str) -> Optional[str]:
        """Get secret from the secret storage."""
        if scope == "unit":
            return self.unit_peer_data.get(key, None)
        elif scope == "app":
            return self.app_peer_data.get(key, None)
        else:
            raise RuntimeError("Unknown secret scope.")

    def set_secret(self, scope: str, key: str, value: Optional[str]) -> None:
        """Set secret in the secret storage."""
        if scope == "unit":
            if not value:
                del self.unit_peer_data[key]
                return
            self.unit_peer_data.update({key: value})
        elif scope == "app":
            if not value:
                del self.app_peer_data[key]
                return
            self.app_peer_data.update({key: value})
        else:
            raise RuntimeError("Unknown secret scope.")

    def get_missing_parameters(self) -> List[str]:
        """Returns the missing mandatory parameters that are not stored in the peer relation."""
        missing_options = []
        for config_option in AZURE_MANDATORY_OPTIONS:
            if not self.get_secret("app", config_option):
                missing_options.append(config_option)
        return missing_options

    def _on_sync_azure_credentials(self, event: ops.charm.ActionEvent) -> None:
        """Handle a user synchronizing their Azure credentials to the charm."""
        # only leader can write the new access and secret key into peer relation.
        if not self.unit.is_leader():
            event.fail("The action can be run only on leader unit.")
            return
        # read parameters from the event
        secret_key = event.params["secret-key"]
        # set parameters in the secrets
        self.set_secret("app", "secret-key", secret_key)
        # update relation data if the relation is present
        if len(self.azure_provider.relations) > 0:
            for relation in self.azure_provider.relations:
                self.azure_provider.set_secret_key(relation.id, secret_key)
        credentials = {"ok": "Credentials successfully updated."}
        event.set_results(credentials)

    def _on_peer_relation_changed(self, _: RelationChangedEvent) -> None:
        """Handle the peer relation changed event."""
        # Check if mandatory configuration options are present and change the status
        missing_options = self.get_missing_parameters()
        logger.info(f"Missing options: {missing_options}")
        if missing_options:
            self.unit.status = BlockedStatus(f"Missing parameters: {missing_options}")
            return
        self.unit.status = ActiveStatus()

    @property
    def _peers(self):
        """Retrieve the peer relation."""
        return self.model.get_relation(PEER_RELATION_NAME)

    def on_get_credentials_action(self, event: ActionEvent):
        """Handle the action `get-credential`."""
        secret_key = self.get_secret("app", "secret-key")
        if secret_key is None:
            event.fail("Credentials are not set!")
            return
        credentials = {"ok": "Credentials are configured."}
        event.set_results(credentials)

    def on_get_connection_info_action(self, event: ActionEvent):
        """Handle the action `get connection info`."""
        current_configuration = {}
        for option in AZURE_OPTIONS:
            if self.get_secret("app", option) is not None:
                if option in KEYS_LIST:
                    current_configuration[option] = "************"  # Hide keys from configuration
                else:
                    current_configuration[option] = self.get_secret("app", option)

        # emit event fail if no option is set in the charm
        if len(current_configuration) == 0:
            event.fail("Credentials are not set!")
            return

        event.set_results(current_configuration)


if __name__ == "__main__":
    ops.main.main(ObjectStorageIntegratorCharm)
