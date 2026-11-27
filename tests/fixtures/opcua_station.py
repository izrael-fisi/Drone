"""Emit mapped inspection events over a real local OPC UA session for tests."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import sys

from asyncua import Server, ua


async def run(port):
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/fixture/")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    index = await server.register_namespace("urn:proxigo:test:inspection")
    event_type = await server.create_custom_event_type(index, "ResultReadyEventType", properties=[
        *((name, ua.VariantType.String) for name in ("ResultId", "RecipeId", "ComponentId", "PartId", "LotId", "Outcome")),
        ("IsSimulated", ua.VariantType.Boolean), ("IsPartial", ua.VariantType.Boolean),
    ])
    generator = await server.get_event_generator(event_type)
    async with server:
        print("OPC fixture ready " + event_type.nodeid.to_string(), flush=True)
        while line := await asyncio.to_thread(sys.stdin.readline):
            data = json.loads(line)
            if data.get("operation") == "stop":
                return
            event = generator.event
            event.ResultId = data["id"]
            event.RecipeId = data.get("recipe", "bracket-v4")
            event.ComponentId = "retaining-clip"
            event.PartId = data.get("part", "frame-001")
            event.LotId = data.get("lot", "lot-a")
            event.Outcome = data["outcome"]
            event.IsSimulated = True
            event.IsPartial = data.get("partial", False)
            stamp = datetime.fromisoformat(data["at"]) if data.get("at") else datetime.now(timezone.utc)
            await generator.trigger(time_attr=stamp)
            print("OPC fixture emitted " + data["id"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(run(parser.parse_args().port))
