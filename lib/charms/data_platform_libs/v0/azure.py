from ops.framework import Object, EventSource
from ops.charm import CharmEvents, RelationEvent


class ContainerEvent(RelationEvent):

    @property
    def container(self):
        return "container"

class CredentialRequestedEvent(ContainerEvent):
    pass

class AzureStorageCredentialEvents(CharmEvents):
    credentials_requested = EventSource(CredentialRequestedEvent)


class AzureStorageProvier(Object):
    on = AzureStorageCredentialEvents()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)

    
    def _on_relation_changed(self, event):
        if not self.charm.unit.is_leader():
            return
        