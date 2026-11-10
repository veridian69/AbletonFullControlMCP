# Film stem delivery

`film_stems_prepare` packages existing WAV captures as sample-exact, common-start
PCM24 deliveries. `film_stems_audit` independently checks headers, sample counts,
sample rate and SHA-256 hashes against the manifest. Neither tool drives Live.

## Timing contract

Each input explicitly supplies `source_start_sample`: the position of that WAV's
first sample relative to picture second zero. Negative values represent preroll.
At 48kHz, a capture beginning one second before picture has origin **-48000**.
Do not infer this value from audible onset: intentional silence is part of music.

The delivery region is `[start_frame, end_frame)`, followed by `tail_frames`.
All stems have the same start and sample count. Absolute frame boundaries are
rounded to the nearest sample (half-up), then subtracted; this avoids rounding
drift at NTSC rates. Use exact rationals such as 24000/1001 or 30000/1001.
No BPM is involved, so variable-tempo scores use the same clock as picture.

## Example MCP call

```json
{
  "name": "film_stems_prepare",
  "arguments": {
    "stems": [
      {"name": "music", "path": "D:/Score/captures/music.wav", "source_start_sample": 0},
      {"name": "texture", "path": "D:/Score/captures/texture.wav", "source_start_sample": 0}
    ],
    "output_dir": "D:/Score/delivery-001",
    "frame_rate": {"numerator": 24, "denominator": 1},
    "start_frame": 0,
    "end_frame": 2400,
    "tail_frames": 48,
    "sample_rate": 48000
  }
}
```

The default is a header-only preview: no output is written. To create the
delivery, explicitly add `"preview": false, "confirm_origins": true` after
checking the input origins. The example produces 102-second stems (4,896,000
samples each), comprising 100 seconds of picture plus two seconds of tail.
Inputs must cover that entire interval; short files are rejected, not padded.
The parent directory must exist, and the delivery directory must not exist.

Then call `film_stems_audit` with the delivery's `output_dir`. Import the WAVs
at the manifest's common start in Resolve. The manifest is the placement
authority; this tool does not insert BWF time-reference metadata.

## Guarantees and boundaries

- Sources are read-only and their hashes are rechecked after copying.
- Existing output directories/files are never overwritten.
- Mono/stereo WAV inputs must match the requested sample rate. No implicit SRC.
- No gain changes, normalisation, fade, dither, silence trimming or padding.
- Non-finite samples or values outside PCM24's representable amplitude are refused.
- A final 10ms peak above -60dBFS raises `tail_not_quiet`; inspect that tail or
  choose a longer export. The tool does not claim a quiet tail is musically complete.
- Output headers, exact frame counts and hashes are read back before success.
- No claim is made about the truth of supplied capture origins, plugin latency,
  the original recording's synchronisation, or cancellation of an external render.
  A sample-exact file length is not proof of correctly captured audio.
- The existing real-time bounce engine remains a capture mechanism. Its wall-clock
  settling overhead must not be interpreted as an exact delivered file duration.

## Tests

```powershell
.venv/Scripts/python.exe -m pytest tests/test_film_delivery.py -q
```

Fixtures are real WAVs with independently positioned impulses. Tests compare
output samples, source hashes, NTSC boundaries, explicit tails, refusal behaviour
and tamper detection; no open set or film media is touched.
