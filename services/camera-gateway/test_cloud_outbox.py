import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from ecs_worker import prepare
from sqs_outbox import SqsOutbox


class CloudWorkerTests(unittest.TestCase):
    def test_secret_becomes_task_scoped_config(self):
        env = {"PROXIGO_CCTV_SECRET": json.dumps({"gatewayToken": "a" * 64,
                                                 "source": "rtsp://192.0.2.12/yard",
                                                 "gateway": "https://analytics.site.example"}),
               "PROXIGO_ORGANIZATION_ID": "org-a", "PROXIGO_SITE_ID": "site-a",
               "PROXIGO_CAMERA_ID": "yard", "PROXIGO_CCTV_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/yard"}
        with TemporaryDirectory() as directory:
            config = json.loads(prepare(env, Path(directory) / "private").read_text())
            self.assertNotIn("PROXIGO_CCTV_SECRET", env)
            self.assertEqual(config["sensorId"], "yard")
            self.assertEqual(config["siteId"], "site-a")
            self.assertEqual(config["placement"], "cloud")
            self.assertEqual(Path(config["tokenFile"]).read_text(), "a" * 64)
            self.assertNotIn("gatewayToken", config)

    def queued(self, staging, client):
        box = SqsOutbox.__new__(SqsOutbox)
        box.queue_url = "https://sqs.us-east-1.amazonaws.com/123456789012/example"
        box.staging, box.client, box.errors, box.pending_limit = staging, client, (OSError,), 10000
        return box

    def test_acknowledged_message_can_be_read_after_worker_replacement(self):
        client = Mock()
        original = self.queued(Mock(), client)
        event = {"id": "stable-event", "sensorId": "yard"}
        self.assertTrue(original.put(event))
        original.staging.put.assert_not_called()
        body = client.send_message.call_args.kwargs["MessageBody"]
        client.receive_message.return_value = {"Messages": [{"ReceiptHandle": "receipt", "Body": body}]}
        replacement = self.queued(Mock(**{"batch.return_value": []}), client)
        [(receipt, encoded)] = replacement.batch()
        self.assertEqual(json.loads(encoded), event)
        client.delete_message.assert_not_called()
        replacement.complete(receipt)
        client.delete_message.assert_called_once_with(QueueUrl=original.queue_url, ReceiptHandle="receipt")

    def test_local_staging_is_retained_until_queue_accepts_it(self):
        staging = Mock(**{"put.return_value": True, "batch.return_value": [(5, '{"id":"stable-event"}')]})
        client = Mock()
        client.send_message.side_effect = OSError()
        box = self.queued(staging, client)
        self.assertTrue(box.put({"id": "stable-event"}))
        with self.assertRaises(OSError):
            box.batch()
        staging.complete.assert_not_called()
        client.receive_message.return_value = {}
        client.send_message.side_effect = None
        self.assertEqual(box.batch(), [])
        staging.complete.assert_called_once_with(5)
