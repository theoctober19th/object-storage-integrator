#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Application charm that connects to database charms.

This charm is meant to be used only for testing
the database requires-provides relation.
"""

import logging

from charms.data_platform_libs.v0.object_storage import (
    AzureStorageRequirer,
    CredentialsChangedEvent,
    CredentialsGoneEvent,
)
from ops.charm import CharmBase, RelationJoinedEvent
from ops.main import main
from ops.model import ActiveStatus, WaitingStatus

logger = logging.getLogger(__name__)

PEER = "application-peers"

FIRST_RELATION = "first-azure-credentials"
SECOND_RELATION = "second-azure-credentials"
CONTAINER_NAME = "test-bucket"


class ApplicationCharm(CharmBase):
    """Application charm that relates to S3 integrator."""

    def __init__(self, *args):
        super().__init__(*args)

        # Default charm events.
        self.framework.observe(self.on.start, self._on_start)

        # Events related to the requested database
        # (these events are defined in the database requires charm library).

        self.first_azure_client = AzureStorageRequirer(self, FIRST_RELATION)
        self.second_azure_client = AzureStorageRequirer(
            self, SECOND_RELATION, container_name=CONTAINER_NAME
        )

        # add relation
        self.framework.observe(
            self.first_azure_client.on.credentials_changed, self._on_first_credential_changed
        )
        self.framework.observe(
            self.second_azure_client.on.credentials_changed, self._on_second_credential_changed
        )

        self.framework.observe(
            self.on[FIRST_RELATION].relation_joined, self._on_first_relation_joined
        )
        self.framework.observe(
            self.on[SECOND_RELATION].relation_joined, self._on_second_relation_joined
        )

        self.framework.observe(
            self.first_azure_client.on.credentials_gone, self._on_first_credential_gone
        )
        self.framework.observe(
            self.second_azure_client.on.credentials_gone, self._on_second_credential_gone
        )

    def _on_start(self, _) -> None:
        """Only sets an waiting status."""
        self.unit.status = WaitingStatus("Waiting for relation")

    def _on_first_relation_joined(self, _: RelationJoinedEvent):
        """On Azure credential relation joined."""
        self.unit.status = ActiveStatus()

    def _on_second_relation_joined(self, _: RelationJoinedEvent):
        """On s3 credential relation joined."""
        self.unit.status = ActiveStatus()

    def _on_first_credential_changed(self, e: CredentialsChangedEvent):
        credentials = self.first_azure_client.get_azure_connection_info()
        logger.info(f"First Azure credential info: {credentials}")

    def _on_second_credential_changed(self, e: CredentialsChangedEvent):
        credentials = self.second_azure_client.get_azure_connection_info()
        logger.info(f"Second Azure credential info: {credentials}")

    def _on_first_credential_gone(self, _: CredentialsGoneEvent):
        logger.info("appcharm: first Azure relation credentials GONE!")
        self.unit.status = WaitingStatus("Waiting for relation")

    def _on_second_credential_gone(self, _: CredentialsGoneEvent):
        logger.info("appcharm: second Azure relation credentials GONE!")
        self.unit.status = WaitingStatus("Waiting for relation")

    @property
    def _peers(self):
        """Retrieve the peer relation (`ops.model.Relation`)."""
        return self.model.get_relation(PEER)


if __name__ == "__main__":
    main(ApplicationCharm)
