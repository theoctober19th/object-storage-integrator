#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# SPDX-License-Identifier: Apache-2.0
"""A charm of the s3 integrator service."""

import logging
from typing import Dict, List

import ops
import ops.charm
import ops.framework
import ops.lib
import ops.main
import ops.model
from charms.data_platform_libs.v0.data_interfaces import DataPeer
from charms.data_platform_libs.v0.object_storage import (
    AzureStorageProvides,
    CredentialRequestedEvent,
)
from ops.charm import ActionEvent, ConfigChangedEvent, StartEvent
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

    SECRET_FIELDS = ["secret-key"]

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.azure_provider = AzureStorageProvides(self, AZURE_RELATION_NAME)
        self.data_peer = DataPeer(
            self, PEER_RELATION_NAME, additional_secret_fields=self.SECRET_FIELDS
        )

        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.azure_provider.on.credentials_requested, self._on_azure_credentials_requested
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

    @property
    def _peers(self):
        """Retrieve the peer relation."""
        return self.model.get_relation(PEER_RELATION_NAME)

    @property
    def app_peer_data(self) -> Dict:
        """Return the peer relation data."""
        return self.data_peer.fetch_my_relation_data()[self._peers.id]

    def get_missing_parameters(self) -> List[str]:
        """Returns the missing mandatory parameters that are not stored in the peer relation."""
        missing_options = []
        logger.warning(f"peer data is: {self.data_peer.fetch_my_relation_data()}")
        for config_option in AZURE_MANDATORY_OPTIONS:
            if not self.app_peer_data.get(config_option):
                missing_options.append(config_option)
        return missing_options

    def check_and_set_status(
        self,
    ) -> bool:
        """Check for missing parameters and set status of the unit."""
        missing_options = self.get_missing_parameters()
        logger.info(f"Missing options: {missing_options}")
        if missing_options:
            self.unit.status = BlockedStatus(f"Missing parameters: {missing_options}")
            return
        self.unit.status = ActiveStatus()

    def _on_start(self, _: StartEvent) -> None:
        """Handle the charm startup event."""
        missing_options = self.get_missing_parameters()
        logger.info(f"Missing options: {missing_options}")
        if missing_options:
            self.unit.status = ops.model.BlockedStatus(f"Missing parameters: {missing_options}")

    # def decode_secret(self, secret_id: str, field: str) -> Optional[str]:
    #     try:
    #         secret_content = self.model.get_secret(id=secret_id).get_content()
    #         if not secret_content.get(field):
    #             raise ValueError(f"The field '{field}' was not found in the secret.")
    #         return secret_content[field]
    #     except ValueError as ve:
    #         logger.warning(f"Exception in decoding secret: {ve}")
    #     except ops.model.ModelError as me:
    #         logger.warning(f"Exception in decoding secret: {me}")
    #     return None

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:  # noqa: C901
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

                if self.app_peer_data.get(option) is not None:
                    update_config.update({option: ""})

                # skip in case of default value
                continue

            update_config.update({option: str(self.config[option])})

        self.data_peer.update_relation_data(self._peers.id, update_config)
        if len(self.azure_provider.relations) > 0:
            for relation in self.azure_provider.relations:
                self.azure_provider.update_relation_data(relation.id, update_config)

        self.check_and_set_status()

    def _on_sync_azure_credentials(self, event: ops.charm.ActionEvent) -> None:
        """Handle a user synchronizing their Azure credentials to the charm."""
        # only leader can write the new access and secret key into peer relation.
        if not self.unit.is_leader():
            event.fail("The action can be run only on leader unit.")
            return
        # read parameters from the event
        secret_key = event.params["secret-key"]
        # set parameters in the peer relation data
        # self.data_peer.update_relation_data(self._peers.id, {"goku": "choku"})
        self.data_peer.update_relation_data(self._peers.id, {"secret-key": secret_key})
        # update relation data if the relation is present
        if len(self.azure_provider.relations) > 0:
            for relation in self.azure_provider.relations:
                self.azure_provider.set_secret_key(relation.id, secret_key)
        credentials = {"ok": "Credentials successfully updated."}
        self.check_and_set_status()
        event.set_results(credentials)

    def _on_azure_credentials_requested(self, event: CredentialRequestedEvent):
        """Handle the `credential-requested` event for azure storage."""
        if not self.unit.is_leader():
            return

        container_name = self.app_peer_data.get("container", event.container)

        logger.debug(f"Desired container name: {container_name}")
        assert container_name is not None
        self.data_peer.update_relation_data(self._peers.id, {"container": container_name})

        desired_configuration = {}
        # collect all configuration options
        for option in AZURE_OPTIONS:
            if self.app_peer_data.get(option):
                desired_configuration[option] = self.app_peer_data.get(option)

        # update connection parameters in the relation data bug
        self.azure_provider.update_relation_data(event.relation.id, desired_configuration)

    def on_get_credentials_action(self, event: ActionEvent):
        """Handle the action `get-credential`."""
        secret_key = self.app_peer_data.get("secret-key")
        if secret_key is None:
            event.fail("Credentials are not set!")
            return
        credentials = {"ok": "Credentials are configured."}
        event.set_results(credentials)

    def on_get_connection_info_action(self, event: ActionEvent):
        """Handle the action `get connection info`."""
        current_configuration = {}
        for option in AZURE_OPTIONS:
            if self.app_peer_data.get(option):
                if option in KEYS_LIST:
                    current_configuration[option] = "************"  # Hide keys from configuration
                else:
                    current_configuration[option] = self.app_peer_data.get(option)

        # emit event fail if no option is set in the charm
        if len(current_configuration) == 0:
            event.fail("Credentials are not set!")
            return

        event.set_results(current_configuration)


if __name__ == "__main__":
    ops.main.main(ObjectStorageIntegratorCharm)
