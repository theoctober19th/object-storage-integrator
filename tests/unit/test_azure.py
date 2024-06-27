# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from asyncio.log import logger
from unittest import mock

from ops.model import BlockedStatus
from ops.testing import Harness

from charm import ObjectStorageIntegratorCharm
from constants import PEER_RELATION_NAME


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(ObjectStorageIntegratorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.peer_relation_id = self.harness.add_relation(PEER_RELATION_NAME, PEER_RELATION_NAME)
        self.charm = self.harness.charm

    def test_on_start(self):
        """Checks that the charm started in blockled status for missing parameters."""
        self.harness.set_leader(True)
        self.charm.on.config_changed.emit()
        self.charm.on.start.emit()
        # check that the charm is in blocked status
        logger.info(f"Status: {self.harness.model.unit.status}")
        self.assertTrue(isinstance(self.harness.model.unit.status, BlockedStatus))

    def test_on_config_changed(self):
        """Checks that configuration parameters are correctly stored in the databag."""
        # ensure that the peer relation databag is empty
        peer_relation_databag = self.harness.get_relation_data(
            self.peer_relation_id, self.harness.charm.app
        )
        self.assertEqual(peer_relation_databag, {})
        # trigger the leader_elected and config_changed events
        self.harness.set_leader(True)
        self.harness.update_config({"storage-account": "storage-account"})
        self.harness.update_config({"path": "/some/path"})

        # ensure that the peer relation has 'cluster_name' set to the config value
        peer_relation_databag = self.harness.get_relation_data(
            self.peer_relation_id, self.harness.charm.app
        )

        self.assertEqual(peer_relation_databag["storage-account"], "storage-account")
        self.assertEqual(peer_relation_databag["path"], "/some/path")

        peer_relation_databag = self.harness.get_relation_data(
            self.peer_relation_id, self.harness.charm.app
        )

        self.harness.update_config({"path": ""})
        self.assertIsNot("region", peer_relation_databag)

    def test_set_secret_key(self):
        """Tests that secret key is set."""
        self.harness.set_leader(True)
        action_event = mock.Mock()
        action_event.params = {"secret-key": "test-secret-key"}
        self.harness.charm._on_sync_azure_credentials(action_event)

        secret_key = self.harness.charm.app_peer_data["secret-key"]
        # verify app data is updated and results are reported to user
        self.assertEqual("test-secret-key", secret_key)

        action_event.set_results.assert_called_once_with(
            {"ok": "Credentials successfully updated."}
        )

    def test_get_azure_credentials(self):
        """Tests that secret key are retrieved correctly."""
        self.harness.set_leader(True)
        event = mock.Mock()
        self.harness.charm.on_get_credentials_action(event)
        event.fail.assert_called()

        self.harness.charm.app_peer_data["secret-key"] = "test-secret-key"

        self.harness.charm.on_get_credentials_action(event)
        event.set_results.assert_called_with({"ok": "Credentials are configured."})

    def test_get_connection_info(self):
        """Tests that Azure connection parameters are retrieved correctly."""
        self.harness.set_leader(True)
        event = mock.Mock()
        self.harness.charm.app_peer_data["secret-key"] = "test-secret-key"
        self.harness.charm.on_get_connection_info_action(event)
        event.set_results.assert_called_with({"secret-key": "************"})
        # update some configuration parameters
        self.harness.update_config({"storage-account": "stoacc"})
        self.harness.update_config({"path": "foo/bar"})
        # test that new parameter are present in the event results.
        self.harness.charm.on_get_connection_info_action(event)
        event.set_results.assert_called_with(
            {
                "secret-key": "************",
                "storage-account": "stoacc",
                "path": "foo/bar",
            }
        )
