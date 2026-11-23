"""Durable cloud delivery through a private SQS queue assigned to one camera."""

import json
from urllib.parse import urlsplit


class SqsOutbox:
    def __init__(self, queue_url, staging, pending_limit):
        from boto3 import client
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError

        endpoint = urlsplit(queue_url)
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("Use a private HTTPS SQS queue URL for the cloud worker.")
        self.queue_url, self.staging, self.pending_limit = queue_url, staging, pending_limit
        self.client = client("sqs", config=Config(connect_timeout=2, read_timeout=4, retries={"total_max_attempts": 1}))
        self.errors = (BotoCoreError, ClientError)

    def request(self, operation, **kwargs):
        try:
            return getattr(self.client, operation)(QueueUrl=self.queue_url, **kwargs)
        except self.errors:
            raise OSError("The cloud detection queue is unavailable.") from None

    def put(self, payload):
        encoded = json.dumps(payload, allow_nan=False)
        try:
            self.request("send_message", MessageBody=encoded)
        except OSError:
            return self.staging.put(payload)
        return True

    def batch(self):
        for identifier, encoded in self.staging.batch():
            self.request("send_message", MessageBody=encoded)
            self.staging.complete(identifier)
        reply = self.request("receive_message", MaxNumberOfMessages=10, VisibilityTimeout=60, WaitTimeSeconds=2)
        return [(message["ReceiptHandle"], message["Body"]) for message in reply.get("Messages", [])]

    def count(self):
        fields = ("ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible")
        reply = self.request("get_queue_attributes", AttributeNames=list(fields))["Attributes"]
        return min(self.pending_limit, self.staging.count() + sum(int(reply.get(field, 0)) for field in fields))

    def complete(self, identifier):
        self.request("delete_message", ReceiptHandle=identifier)
