"""Export real Resolve cues into a NEW saved-set copy; never opens Live."""
import argparse
import asyncio
from contextlib import AsyncExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import tomllib
import xml.etree.ElementTree as ET

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(args):
    original = args.source.read_bytes()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    async with asyncio.timeout(90), AsyncExitStack() as stack:
        clients = []
        for name in ("davinci-resolve-film", "ableton-film"):
            entry = config["mcp_servers"][name]
            streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=entry["command"], args=entry["args"], cwd=entry["cwd"],
                env=dict(os.environ, **entry.get("env", {})))))
            client = await stack.enter_async_context(ClientSession(*streams))
            await client.initialize()
            clients.append(client)
        async def call(client, name, arguments):
            result = await client.call_tool(name, arguments)
            assert not result.isError, result
            return result.structuredContent or json.loads(next(c.text for c in result.content if c.type == "text"))
        sheet = await call(clients[0], "timeline_markers", {"action": "export_cue_sheet"})
        result = await call(clients[1], "film_cues_export_set", {
            "source_set_path": str(args.source), "output_set_path": str(args.output),
            "cue_sheet": sheet, "tempo_bpm": args.tempo_bpm,
            "confirm_tempo": True, "cue_namespace": "jim-ch5"})
        assert result["status"] == "ok", result
        assert args.source.read_bytes() == original
        output = args.output.read_bytes()
        assert hashlib.sha256(output).hexdigest() == result["output_sha256"]
        source_xml, output_xml = (ET.fromstring(gzip.decompress(data)) for data in (original, output))
        source_locators = source_xml.find("LiveSet/Locators/Locators")
        output_locators = output_xml.find("LiveSet/Locators/Locators")
        added = list(output_locators)[len(source_locators):]
        assert len(added) == len(sheet["cues"]) == result["locators_added"]
        for locator, cue in zip(added, result["plan"]["cues"]):
            assert float(locator.find("Time").get("Value")) == cue["start_beats"]
            assert locator.find("Name").get("Value") == cue["locator_name"]
            output_locators.remove(locator)
        assert ET.tostring(source_xml) == ET.tostring(output_xml)
        print(json.dumps({key: value for key, value in result.items() if key != "plan"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tempo-bpm", type=float, required=True,
                        help="Must already be verified against the complete saved-set tempo")
    asyncio.run(run(parser.parse_args()))
