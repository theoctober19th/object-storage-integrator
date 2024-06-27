import logging
from collections import namedtuple
import json
from typing import Dict, List, Optional, Union

import ops
from ops.framework import Object, EventSource, ObjectEvents
from ops.charm import CharmEvents, RelationEvent, RelationChangedEvent, RelationJoinedEvent, RelationBrokenEvent
from ops.model import Unit, Application, Relation


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


AZURE_STORAGE_REQUIRED_OPTIONS = [
    "container", "storage-account", "secret-key"
]


class AzureStorageEvent(RelationEvent):
    """Base class for Azure storage events."""

    @property
    def container(self) -> Optional[str]:
        """Returns the container name."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("container")


class ContainerEvent(RelationEvent):

    @property
    def container(self):
        return "my-custom-container"

class CredentialRequestedEvent(ContainerEvent):
    pass


class CredentialsChangedEvent(AzureStorageEvent):
    pass


class CredentialsGoneEvent(RelationEvent):
    pass


class AzureStorageCredentialEvents(CharmEvents):
    credentials_requested = EventSource(CredentialRequestedEvent)


class AzureStorageCredentialsRequiresEvents(ObjectEvents):
    """Event descriptor for events raised by the AzureStorageProvider."""

    credentials_changed = EventSource(CredentialsChangedEvent)
    credentials_gone = EventSource(CredentialsGoneEvent)



def diff(event: RelationChangedEvent, bucket: Union[Unit, Application]) -> Diff:
    """Retrieves the diff of the data in the relation changed databag.

    Args:
        event: relation changed event.
        bucket: bucket of the databag (app or unit)

    Returns:
        a Diff instance containing the added, deleted and changed
            keys from the event relation databag.
    """
    logger.info(event.relation.data)
    # Retrieve the old data from the data key in the application relation databag.
    old_data = json.loads(event.relation.data[bucket].get("data", "{}"))
    # Retrieve the new data from the event relation databag.
    new_data = (
        {key: value for key, value in event.relation.data[event.app].items() if key != "data"}
        if event.app
        else {}
    )

    # These are the keys that were added to the databag and triggered this event.
    added = new_data.keys() - old_data.keys()
    # These are the keys that were removed from the databag and triggered this event.
    deleted = old_data.keys() - new_data.keys()
    # These are the keys that already existed in the databag,
    # but had their values changed.
    changed = {key for key in old_data.keys() & new_data.keys() if old_data[key] != new_data[key]}

    # TODO: evaluate the possibility of losing the diff if some error
    # happens in the charm before the diff is completely checked (DPE-412).
    # Convert the new_data to a serializable format and save it for a next diff check.
    event.relation.data[bucket].update({"data": json.dumps(new_data)})

    # Return the diff with all possible changes.
    return Diff(added, changed, deleted)



class AzureStorageProvider(Object):
    on = AzureStorageCredentialEvents()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.local_app = self.charm.model.app
        self.local_unit = self.charm.unit
        self.relation_name = relation_name
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)



    def _diff(self, event: RelationChangedEvent) -> Diff:
        """Retrieves the diff of the data in the relation changed databag.

        Args:
            event: relation changed event.

        Returns:
            a Diff instance containing the added, deleted and changed
                keys from the event relation databag.
        """
        return diff(event, self.local_app)
    
    def _on_relation_changed(self, event):
        logger.info("osi: on relation changed...")
        if not self.charm.unit.is_leader():
            return
        diff = self._diff(event)
        if "az-container-name" in diff.added:
            self.on.credentials_requested.emit(
                event.relation, app=event.app, unit=event.unit
            )
            logger.info("osi: emitted event credential-requested...")

    @property
    def relations(self) -> List[Relation]:
        """The list of Relation instances associated with this relation_name."""
        return list(self.charm.model.relations[self.relation_name])

    def set_secret_key(self, relation_id: int, secret_key: str) -> None:
        """Sets the secret key value in application databag.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation_id: the identifier for a particular relation.
            secret_key: the value of the secret key.
        """
        self.update_connection_info(relation_id, {"secret-key": secret_key})


    def update_connection_info(self, relation_id: int, connection_data: dict) -> None:
        """Updates the credential data as set of key-value pairs in the relation.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation_id: the identifier for a particular relation.
            connection_data: dict containing the key-value pairs
                that should be updated.
        """
        logger.info("relation data updated...")
        # check and write changes only if you are the leader
        if not self.local_unit.is_leader():
            return

        relation = self.charm.model.get_relation(self.relation_name, relation_id)

        if not relation:
            return

        # update the databag, if connection data did not change with respect to before
        # the relation changed event is not triggered
        updated_connection_data = {}
        for configuration_option, configuration_value in connection_data.items():
            updated_connection_data[configuration_option] = configuration_value

        relation.data[self.local_app].update(updated_connection_data)
        logger.debug(f"Updated azure credentials: {updated_connection_data}")

class AzureStorageRequirer(Object):
    on = AzureStorageCredentialsRequiresEvents()  # pyright: ignore[reportAssignmentType]

    def __init__(
        self, charm: ops.charm.CharmBase, relation_name: str, container_name: Optional[str] = None
    ):
        """Manager of the Azure client relations."""
        super().__init__(charm, relation_name)

        self.relation_name = relation_name
        self.charm = charm
        self.local_app = self.charm.model.app
        self.local_unit = self.charm.unit
        self.container = container_name

        self.framework.observe(
            self.charm.on[self.relation_name].relation_changed, self._on_relation_changed
        )

        self.framework.observe(
            self.charm.on[self.relation_name].relation_joined, self._on_relation_joined
        )

        self.framework.observe(
            self.charm.on[self.relation_name].relation_broken,
            self._on_relation_broken,
        )

    def _load_relation_data(self, raw_relation_data: dict) -> dict:
        """Loads relation data from the relation data bag.

        Args:
            raw_relation_data: Relation data from the databag
        Returns:
            dict: Relation data in dict format.
        """
        connection_data = {}
        for key in raw_relation_data:
            try:
                connection_data[key] = json.loads(raw_relation_data[key])
            except (json.decoder.JSONDecodeError, TypeError):
                connection_data[key] = raw_relation_data[key]
        return connection_data


    @property
    def relations(self) -> List[Relation]:
        """The list of Relation instances associated with this relation_name."""
        return list(self.charm.model.relations[self.relation_name])


    def _generate_container_name(self, event: RelationJoinedEvent):
        """Returns the container name generated from relation id."""
        return f"relation-{event.relation.id}"
    
    def update_connection_info(self, relation_id: int, connection_data: dict) -> None:
        """Updates the credential data as set of key-value pairs in the relation.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation_id: the identifier for a particular relation.
            connection_data: dict containing the key-value pairs
                that should be updated.
        """
        logger.info("relation data updated...")
        # check and write changes only if you are the leader
        if not self.local_unit.is_leader():
            return

        relation = self.charm.model.get_relation(self.relation_name, relation_id)

        if not relation:
            return

        # update the databag, if connection data did not change with respect to before
        # the relation changed event is not triggered
        updated_connection_data = {}
        for configuration_option, configuration_value in connection_data.items():
            updated_connection_data[configuration_option] = configuration_value

        relation.data[self.local_app].update(updated_connection_data)
        logger.debug(f"Updated azure credentials: {updated_connection_data}")


    def get_azure_connection_info(self) -> Dict[str, str]:
        """Return the azure storage credentials as a dictionary."""
        for relation in self.relations:
            if relation and relation.app:
                return self._load_relation_data(relation.data[relation.app])

        return {}
    
    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Notify the charm about the presence of S3 credentials."""
        logger.info("Azure storage relation changed...")
        logger.info(event)

        # check if the mandatory options are in the relation data
        contains_required_options = True
        # get current credentials data
        credentials = self.get_azure_connection_info()
        # records missing options
        missing_options = []
        for configuration_option in AZURE_STORAGE_REQUIRED_OPTIONS:
            if configuration_option not in credentials:
                contains_required_options = False
                missing_options.append(configuration_option)
       
        # emit credential change event only if all mandatory fields are present
        if contains_required_options:
            logger.warning("All mandatory fields are present...")
        else:
            logger.warning(
                f"Some mandatory fields: {missing_options} are not present, do not emit credential change event!"
            )
        getattr(self.on, "credentials_changed").emit(
            event.relation, app=event.app, unit=event.unit
        )


    def _on_relation_joined(self, event: RelationJoinedEvent) -> None:
        logger.info("gogo: Azure storage relation joined...")
        logger.info(event)
        if self.container is None:
            self.container = self._generate_container_name(event)
        self.update_connection_info(event.relation.id, {"container": self.container})

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        logger.info("Azure Storage relation broken...")
        logger.info(event)
        getattr(self.on, "credentials_gone").emit(event.relation, app=event.app, unit=event.unit)
