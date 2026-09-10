"""Exercise the actual analysis path on disposable audio, including NumPy 2 tempo."""

import json

import numpy as np
import pytest
import soundfile as sf
from mcp.server.fastmcp import FastMCP

from ableton_mcp.tools.audio_analysis import register


@pytest.mark.asyncio
async def test_audio_analyze_tonal_pulses_returns_json_scalar_tempo(tmp_path):
    sr = 22050
    t = np.arange(sr * 6) / sr
    y = .12 * np.sin(2 * np.pi * 220 * t) + .06 * np.sin(2 * np.pi * 330 * t)
    for onset in np.arange(0, 6, .5):
        i = int(onset * sr)
        n = min(600, len(y) - i)
        y[i:i+n] += .4 * np.exp(-np.arange(n) / 90) * np.sin(
            2 * np.pi * 1300 * np.arange(n) / sr
        )
    path = tmp_path / "tonal-pulses.wav"
    sf.write(path, y, sr)
    mcp = FastMCP("analysis-test")
    register(mcp)
    _, result = await mcp.call_tool("audio_analyze", {"path": str(path)})
    assert isinstance(result["tempo_bpm"], float)
    assert 110 < result["tempo_bpm"] < 130
    assert result["duration_sec"] == pytest.approx(6)
    assert len(result["mfcc_mean_13"]) == 13
    json.dumps(result, allow_nan=False)
