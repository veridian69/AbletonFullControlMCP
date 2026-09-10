"""Verify only the two named film MCP registrations, without live writes."""
import argparse
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(config_path):
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    async with asyncio.timeout(90), AsyncExitStack() as stack:
        clients = {}
        for name in ("davinci-resolve-film", "ableton-film"):
            entry = config["mcp_servers"][name]
            params = StdioServerParameters(command=entry["command"], args=entry["args"],
                cwd=entry["cwd"], env=dict(os.environ, **entry.get("env", {})))
            streams = await stack.enter_async_context(stdio_client(params))
            client = await stack.enter_async_context(ClientSession(*streams))
            await client.initialize()
            clients[name] = client
        async def call(client, name, arguments):
            result = await client.call_tool(name, arguments)
            assert not result.isError, result
            return result.structuredContent or json.loads(next(c.text for c in result.content if c.type == "text"))
        resolve, ableton = clients.values()
        names = {t.name for t in (await ableton.list_tools()).tools}
        required = {"film_cues_plan", "film_cues_import", "film_cues_export_set", "film_stems_prepare", "film_stems_audit"}
        assert required <= names, required - names
        before = await call(resolve, "timeline_markers", {"action": "get_all"})
        sheet = await call(resolve, "timeline_markers", {"action": "export_cue_sheet"})
        args = {"cue_sheet": sheet, "tempo_bpm": 120, "cue_namespace": "jim-ch5-preview"}
        plan = await call(ableton, "film_cues_plan", args)
        assert plan["status"] == "ok", plan
        preview = await call(ableton, "film_cues_import", {**args, "preview": True})
        assert preview["status"] in ("preview", "conflict"), preview
        assert preview["added"] == [] and preview["transport_before"] == preview["transport_after"]
        after = await call(resolve, "timeline_markers", {"action": "get_all"})
        assert before["markers"] == after["markers"]
        print(json.dumps({"status": "passed", "configured_servers": list(clients),
            "new_tools": sorted(required), "cue_count": len(sheet["cues"]), "live_writes": 0,
            "tempo_bpm_test_assumption": 120, "current_host_reloaded": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    asyncio.run(run(parser.parse_args().config))
