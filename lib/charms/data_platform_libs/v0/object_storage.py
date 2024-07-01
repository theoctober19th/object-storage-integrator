#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from collections import namedtuple
from typing import Dict, List, Optional

from charms.data_platform_libs.v0.data_interfaces import (
    EventHandlers,
    ProviderData,
    RequirerData,
    RequirerEventHandlers,
)
from ops import Model
from ops.charm import (
    CharmBase,
    CharmEvents,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationEvent,
    RelationJoinedEvent,
)
from ops.framework import EventSource, ObjectEvents
from ops.model import Relation

# The unique Charmhub library identifier, never change it
LIBID = "fca396f6254246c9bfa5650000000000"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


logger = logging.getLogger(__name__)

Diff = namedtuple("Diff", "added changed deleted")
Diff.__doc__ = """
A tuple for storing the diff between two data mappings.

added - keys that were added
changed - keys that still exist but have new values
deleted - key that were deleted"""


AZURE_STORAGE_REQUIRED_OPTIONS = ["container", "storage-account", "secret-key"]


class ObjectStorageEvent(RelationEvent):
    pass


class ContainerEvent(RelationEvent):
    """Base class for Azure storage events."""

    @property
    def container(self) -> Optional[str]:
        """Returns the container name."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("container", "")


class CredentialRequestedEvent(ContainerEvent):
    pass


class CredentialsChangedEvent(ContainerEvent):
    pass


class CredentialsGoneEvent(RelationEvent):
    pass


class AzureStorageProviderEvents(CharmEvents):
    credentials_requested = EventSource(CredentialRequestedEvent)


class AzureStorageRequirerEvents(ObjectEvents):
    """Event descriptor for events raised by the AzureStorageProvider."""

    credentials_changed = EventSource(CredentialsChangedEvent)
    credentials_gone = EventSource(CredentialsGoneEvent)


class AzureStorageRequirerData(RequirerData):
    SECRET_FIELDS = ["secret-key"]
    # ADDITIONAL_SECRET_FIELDS = ["secret-key"]

    def __init__(self, model, relation_name: str, container: Optional[str] = None):
        super().__init__(
            model,
            relation_name,
            # additional_secret_fields=self.ADDITIONAL_SECRET_FIELDS
        )
        self.container = container


class AzureStorageRequirerEventHandlers(RequirerEventHandlers):
    on = AzureStorageRequirerEvents()  # pyright: ignore[reportAssignmentType]

    def __init__(
        self, charm: CharmBase, relation_data: AzureStorageRequirerData, unique_key: str = ""
    ):
        super().__init__(charm, relation_data, unique_key)

        self.relation_name = relation_data.relation_name
        self.charm = charm
        self.local_app = self.charm.model.app
        self.local_unit = self.charm.unit

        self.framework.observe(
            self.charm.on[self.relation_name].relation_joined, self._on_relation_joined_event
        )
        self.framework.observe(
            self.charm.on[self.relation_name].relation_changed, self._on_relation_changed_event
        )

        self.framework.observe(
            self.charm.on[self.relation_name].relation_broken,
            self._on_relation_broken_event,
        )

    def _on_relation_joined_event(self, event: RelationJoinedEvent) -> None:
        """Event emitted when the azure storage relation is joined."""
        logger.info("Azure storage relation joined...")
        if self.container is None:
            self.container = f"relation-{event.relation.id}"
        event_data = {"container": self.container}
        self.relation_data.update_relation_data(event.relation.id, event_data)

    def get_azure_connection_info(self) -> Dict[str, str]:
        """Return the azure storage credentials as a dictionary."""
        for relation in self.relations:
            if relation and relation.app:
                return self.relation_data.fetch_relation_data([relation.id])[relation.id]
        return {}

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Notify the charm about the presence of Azure credentials."""
        logger.info("Azure storage relation changed...")

        ##############################################################################
        diff = self._diff(event)
        if any(newval for newval in diff.added if self.relation_data._is_secret_field(newval)):
            self.relation_data._register_secrets_to_relation(event.relation, diff.added)
        ##############################################################################

        # check if the mandatory options are in the relation data
        contains_required_options = True
        # get current credentials data
        credentials = self.get_azure_connection_info()
        logger.info(credentials)
        # records missing options
        missing_options = []
        for configuration_option in AZURE_STORAGE_REQUIRED_OPTIONS:
            if configuration_option not in credentials:
                contains_required_options = False
                missing_options.append(configuration_option)

        # emit credential change event only if all mandatory fields are present
        if contains_required_options:
            getattr(self.on, "credentials_changed").emit(
                event.relation, app=event.app, unit=event.unit
            )
        else:
            logger.warning(
                f"Some mandatory fields: {missing_options} are not present, do not emit credential change event!"
            )

    def _on_relation_broken_event(self, event: RelationBrokenEvent) -> None:
        logger.info("Azure Storage relation broken...")
        getattr(self.on, "credentials_gone").emit(event.relation, app=event.app, unit=event.unit)

    @property
    def relations(self) -> List[Relation]:
        """The list of Relation instances associated with this relation_name."""
        return list(self.charm.model.relations[self.relation_name])


class AzureStorageRequires(AzureStorageRequirerData, AzureStorageRequirerEventHandlers):

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        container: Optional[str] = None,
    ):
        AzureStorageRequirerData.__init__(self, charm.model, relation_name, container)
        AzureStorageRequirerEventHandlers.__init__(self, charm, self)


class AzureStorageProviderData(ProviderData):

    def __init__(self, model: Model, relation_name: str) -> None:
        super().__init__(model, relation_name)

    def set_container(self, relation_id: int, container: str) -> None:
        self.update_relation_data(relation_id, {"container": container})

    def set_secret_key(self, relation_id, secret_key: str) -> None:
        self.update_relation_data(relation_id, {"secret-key": secret_key})


class AzureStorageProviderEventHandlers(EventHandlers):
    on = AzureStorageProviderEvents()

    def __init__(
        self, charm: CharmBase, relation_data: AzureStorageProviderData, unique_key: str = ""
    ):
        super().__init__(charm, relation_data, unique_key)
        self.relation_data = relation_data

    def _on_relation_changed_event(self, event: RelationChangedEvent):
        if not self.charm.unit.is_leader():
            return
        diff = self._diff(event)
        if "container" in diff.added:
            self.on.credentials_requested.emit(event.relation, app=event.app, unit=event.unit)


class AzureStorageProvides(AzureStorageProviderData, AzureStorageProviderEventHandlers):

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        AzureStorageProviderData.__init__(self, charm.model, relation_name)
        AzureStorageProviderEventHandlers.__init__(self, charm, self)
