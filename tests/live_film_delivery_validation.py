"""Real MCP transport, synthetic WAV delivery; never connects to the Live set."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import soundfile as sf
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run():
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable, args=["-m", "ableton_mcp"], cwd=str(root))
    with tempfile.TemporaryDirectory(prefix="film-mcp-delivery-") as scratch:
        folder = Path(scratch)
        stems = []
        for name, origin, impulse in (("music", 0, 3000), ("texture", -1000, 4000)):
            data = np.zeros((20000, 2)); data[impulse] = [.25, -.125]
            path = folder / (name + ".wav")
            sf.write(path, data, 48000, subtype="PCM_24")
            stems.append({"name": name, "path": str(path), "source_start_sample": origin})
        output = folder / "delivery"
        args = {"stems": stems, "output_dir": str(output),
                "frame_rate": {"numerator": 24, "denominator": 1},
                "start_frame": 1, "end_frame": 4, "tail_frames": 1}
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                async def call(name, arguments):
                    r = await session.call_tool(name, arguments)
                    assert not r.isError, r
                    return r.structuredContent or json.loads(next(c.text for c in r.content if c.type == "text"))
                preview = await call("film_stems_prepare", args)
                assert preview["status"] == "preview" and not output.exists()
                result = await call("film_stems_prepare", {**args, "preview": False, "confirm_origins": True})
                assert result["status"] == "prepared", result
                audit = await call("film_stems_audit", {"output_dir": str(output)})
                assert audit["status"] == "passed", audit
                for name in ("music", "texture"):
                    audio, rate = sf.read(output / (name + ".wav"))
                    assert rate == 48000 and audio.shape == (8000, 2)
                    assert np.flatnonzero(audio[:, 0]).tolist() == [1000]
                print(json.dumps({"status": "passed", "transport": "MCP stdio",
                                  "stems": 2, "samples_each": 8000,
                                  "common_impulse_sample": 1000, "tail_samples": 2000,
                                  "live_set_accessed": False, "source_media": "synthetic fixtures only"}))


if __name__ == "__main__":
    asyncio.run(run())
