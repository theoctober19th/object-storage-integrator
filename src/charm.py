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
)

logger = logging.getLogger(__name__)


class ObjectStorageIntegratorCharm(ops.charm.CharmBase):
    """Charm for s3 integrator service."""

    SECRET_FIELDS = ["secret-key"]

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.azure_provider = AzureStorageProvides(self, AZURE_RELATION_NAME)

        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.azure_provider.on.credentials_requested, self._on_azure_credentials_requested
        )

        # actions
        self.framework.observe(
            self.on.get_azure_credentials_action, self.on_get_credentials_action
        )
        self.framework.observe(
            self.on.get_azure_connection_info_action, self.on_get_connection_info_action
        )


    def get_missing_parameters(self) -> List[str]:
        """Returns the missing mandatory parameters that are not stored in the peer relation."""
        missing_options = []
        for config_option in AZURE_MANDATORY_OPTIONS:
            if not self.config.get(config_option):
                missing_options.append(config_option)
        return missing_options


    def check_and_set_status(
        self, set_active_if_passed=False
    ) -> bool:
        """Check for missing parameters and set status of the unit."""
        missing_options = self.get_missing_parameters()
        logger.info(f"Missing options: {missing_options}")
        if missing_options:
            self.unit.status = BlockedStatus(f"Missing parameters: {missing_options}")
            return
        if set_active_if_passed:
            self.unit.status = ActiveStatus()


    def _on_start(self, _: StartEvent) -> None:
        """Handle the charm startup event."""
        self.check_and_set_status()


    def decode_secret(self, secret_id: str, field: str) -> Optional[str]:
        try:
            secret_content = self.model.get_secret(id=secret_id).get_content()
            if not secret_content.get(field):
                raise ValueError(f"The field '{field}' was not found in the secret.")
            return secret_content[field]
        except ValueError as ve:
            logger.warning(f"Exception in decoding secret: {ve}")
        except ops.model.ModelError as me:
            logger.warning(f"Exception in decoding secret: {me}")
        return None

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

                # skip in case of default value
                continue

            update_config.update({option: str(self.config[option])})

        if len(self.azure_provider.relations) > 0:
            for relation in self.azure_provider.relations:
                self.azure_provider.update_relation_data(relation.id, update_config)

        self.check_and_set_status(set_active_if_passed=True)


    def _on_azure_credentials_requested(self, event: CredentialRequestedEvent):
        """Handle the `credential-requested` event for azure storage."""
        if not self.unit.is_leader():
            return

        container_name = self.config.get("container")
        assert container_name is not None

        desired_configuration = {}
        # collect all configuration options
        for option in AZURE_OPTIONS:
            if self.config.get(option):
                desired_configuration[option] = self.config.get(option)

        # update connection parameters in the relation data bug
        self.azure_provider.update_relation_data(event.relation.id, desired_configuration)


    def on_get_credentials_action(self, event: ActionEvent):
        """Handle the action `get-credential`."""
        secret_key = self.config.get("secret-key")
        if secret_key is None:
            event.fail("Credentials are not set!")
            return
        credentials = {"ok": "Credentials are configured."}
        event.set_results(credentials)


    def on_get_connection_info_action(self, event: ActionEvent):
        """Handle the action `get connection info`."""
        current_configuration = {}
        for option in AZURE_OPTIONS:
            if self.config.get(option):
                if option in KEYS_LIST:
                    current_configuration[option] = "************"  # Hide keys from configuration
                else:
                    current_configuration[option] = self.config.get(option)

        # emit event fail if no option is set in the charm
        if len(current_configuration) == 0:
            event.fail("Credentials are not set!")
            return

        event.set_results(current_configuration)


if __name__ == "__main__":
    ops.main.main(ObjectStorageIntegratorCharm)
