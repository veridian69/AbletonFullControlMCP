# Capture calibration: measured relative alignment

This is task 4 of `../FILM-WORKFLOW-PLAN.md`. The implementation creates a
reproducible probe and measures **supplied WAV files**, entirely offline. It
does not call Live, modify score media, change device settings, move locators,
apply compensation, or prove an absolute picture origin. Main owns tool
registration, application calls and real capture validation.

## API and registration

Core module: `ableton_mcp.bounce.film_calibration`.
Tool module: `ableton_mcp.tools.film_calibration`; main calls `register(mcp)`.
Both expose the same three names:

```python
generate_calibration_probe(
    output_path: str, sample_rate: int = 48000, channels: int = 2, seed: int = 0
) -> dict

measure_capture_calibration(
    probe: dict, capture_paths: list[str], configuration: dict
) -> dict

validate_capture_calibration(profile: dict, configuration: dict) -> dict
```

Only generation writes: an exclusive `xb` create of the exact supplied path.
The parent must already exist; existing files/symlinks are never overwritten.
If an I/O failure leaves a partial new file, it is not silently removed or
presented as a successful probe. Choose another absent path after inspection.
Measurement and validation are read-only. Core failures raise `ValueError` or
filesystem exceptions; tool failures return `{"status":"error","error":"..."}`.
No receipt/profile JSON is automatically written.

## Signal and acceptance rules

- Probe: five seconds, PCM16, mono or stereo, at 32000, 44100, 48000, 88200 or
  96000 Hz. Seed is an integer in 0..4294967295. Hash-derived noise landmarks
  are deterministic and distinct per seed, channel and event, Hann-windowed,
  with peak amplitude 0.25. Starts are floor(rate × 0.500/1.370/2.610/3.890);
  length is floor(rate / 12) samples. There is leading/trailing silence.
- Captures: 2–5 distinct files, each at most 32 MiB and 20 seconds, same rate
  and channel count as the reference. Strict RIFF WAV container lengths;
  PCM16/24/32 or float32/64 only. No resampling, lossy coding or channel mapping.
- Every landmark on every channel is searched across the entire capture using
  absolute normalised cross-correlation. Gain and polarity inversion are
  permitted, but silence, noise-only/periodic unrelated signals and missing
  landmarks cannot pass. Minimum correlation is 0.90. Any secondary correlation
  of 0.70 or more outside ±2 samples of the best match refuses as ambiguous.
- Every landmark, channel and capture must yield **exactly the same integer
  offset**. No averaging away clock drift, jitter or channel skew. This is
  integer-sample resolution, not proof of zero fractional-sample drift or
  sub-sample precision. Correlation values are evidence, not probabilities.
- Any non-finite or near-full-scale sample (absolute amplitude >=32767/32768)
  refuses. The recorded range must cover the reference tail at the measured
  offset. Negative offsets may consume leading silence but cannot lose a
  landmark. Deliberate silence in the reference is not latency evidence.
- Distinct files with identical bytes are allowed: genuinely independent
  digital captures may be identical. `identical_capture_content` reports any
  repeated hash. Reusing the same file, including aliases/hardlinks, refuses.
  Using the reference probe itself as any capture also refuses, including
  aliases/hardlinks; validation rejects previously issued profiles with that flaw.
  Hash differences do not prove independence either; the caller must supply
  separate recording runs. `capture_provenance_verified` is always false.
- This strict model may refuse a genuinely usable but filtered, noisy,
  time-varying or channel-remapped path. Refusal is not permission to loosen
  thresholds or manufacture a passing recording.

## Configuration and hash contract for preflight

Supply this exact configuration shape; `settings` is nonempty and should include
**all** timing-relevant settings, including routing/plugin identities and states:

```json
{
  "audio_path": "disposable set / probe track -> master -> resampling capture",
  "device": "exact interface model and device identifier",
  "driver": "exact driver name and version",
  "sample_rate": 48000,
  "buffer_size_samples": 256,
  "settings": {
    "live_version": "record actual version",
    "input_output_channels": "record actual channel routing",
    "delay_compensation": true,
    "reduced_latency_when_monitoring": false,
    "monitoring": "off",
    "driver_error_compensation_ms": 0,
    "plugins": [],
    "warp": false,
    "capture_method": "record actual capture/bounce path and trim/warmup settings"
  }
}
```

These are example values, **not settings to apply**. Record the actual existing
configuration. `buffer_size_samples` must be an integer in 1..65536; the rate
must equal the WAV rate. Settings are caller-declared, never independently
queried, and `configuration_verified` is always false. A changed device,
buffer, monitoring/compensation state, plugin/routing path, warmup or trimming
rule invalidates reuse even if the sample rate has not changed.

Generation returns `film-calibration-probe/v1`:

```text
schema, path (absolute), sha256 (whole WAV bytes), sample_rate, channels,
seed, sample_count, landmarks [{start_sample, sample_count}]
```

Measurement returns `film-calibration/v1` with `status: "measured"`:

```text
probe                         complete generation receipt
captures[]                    path, sha256, sample_count, offset_samples, matches[]
matches[]                     landmark, channel (zero-based), reference_sample,
                              capture_sample, offset_samples, correlation,
                              secondary_correlation, gain, polarity (+1/-1)
configuration                 snapshot of caller's configuration
configuration_sha256          canonical configuration hash
profile_sha256                canonical complete receipt hash, excluding ONLY this key
configuration_verified        false
capture_provenance_verified    false
identical_capture_content     whether any capture file hashes repeat
sample_rate                   native probe/capture rate
offset_samples                signed recorded position minus reference position
correction_samples            -offset_samples; informational, never applied
offset_seconds                offset_samples / sample_rate
repeat_spread_samples         0 (otherwise measurement refuses)
confidence                    minimum_correlation, maximum_secondary_correlation,
                              required_minimum_correlation (0.90),
                              maximum_allowed_secondary_correlation (0.70),
                              landmark_spread_samples (0), resolution_samples (1)
limitations                   human-readable limits
```

Hashes are lowercase SHA-256 hex. Canonical JSON is precisely:

```python
hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")).hexdigest()
# Python's default ensure_ascii=True is part of this contract.
```

Before accepting an existing receipt, preflight must check its schema/status,
profile hash, current file hashes (probe and **every** capture), and the current
declared configuration hash. Missing files or changed configuration refuse;
missing current settings/provenance remain unknown, not a pass. Hashes detect
changes, not malicious fabrication or truthful capture provenance. Do not
present `measured` as independently verified hardware calibration.

`validate_capture_calibration` also regenerates/verifies the deterministic probe
and repeats the DSP from the bound files. It returns
`film-calibration-validation/v1`, `status: "valid"`, `profile_sha256`,
`configuration_sha256`, signed `offset_samples`, `correction_samples`,
`configuration_verified: false`, and `evidence_remeasured: true`.
There is no TTL-based automatic reuse: validate with the actual current settings
before every use. Retain original files unchanged. Different numerical-library
versions may produce a different floating evidence hash; remeasure rather than
silently accepting that difference.

## Disposable-set real capture procedure (main executes)

1. Preserve the production set and any unsaved state. Do not open a different
   set over unsaved work. Main/user must safely arrange a separate disposable
   session; if that cannot be done safely, stop before touching Live.
2. Read and record the existing device, driver, rate, buffer and relevant Live
   settings. Do not change global audio settings. Identify the exact intended
   delivery capture path. A master/resampling calibration does not certify a
   different per-track path or external loopback.
3. Generate a probe at that existing supported rate into an absent WAV path.
   Keep its JSON receipt. Import it at a known zero reference in the disposable
   arrangement, unwarped, without fades, clip gain changes or time stretching.
   Disable speakers/monitor loudness safely if needed; the probe is noise.
4. Route through precisely the path being calibrated. For a bounce-specific
   calibration use the same warmup/settle/trimming/recording procedure as the
   future bounce. Do not add effects to obtain a pass or edit production tracks.
   If plugins/compensation are part of that path, record their full state.
5. Record at least two separate runs into separate WAVs, from the same defined
   capture origin. Include the entire probe and at least one second of tail;
   each capture must stay under 20 seconds. No post-capture trimming, auto-align,
   normalisation, fades, channel conversion or resampling. If a capture begins
   earlier than the reference, record that known origin difference: the measured
   offset includes it and **cannot itself distinguish origin from latency**.
6. Stop the disposable transport and verify its state. Call measurement with
   the original probe receipt, capture paths and the actual configuration.
   A refusal is evidence of an unresolved path; inspect the reason and capture
   again, never invent/average an offset. Main alone controls these app calls.
7. Retain receipt and original WAVs. Run validation against freshly described
   current settings immediately before using the profile. Any later application
   of `correction_samples` is a separate, explicit integration decision and must
   account for known capture origins and any compensation already applied.
   This module never shifts media or modifies a delivery manifest.

Offline invocation after main has captured the files (existing directory and
absent probe path required; preserve `probe` from the earlier generation call):

```python
import json
from pathlib import Path
from ableton_mcp.bounce.film_calibration import (
    generate_calibration_probe, measure_capture_calibration, validate_capture_calibration,
)

# Before recording:
probe = generate_calibration_probe(r"D:\calibration\probe.wav", 48000, 2, 73)
print(json.dumps(probe, indent=2))

# After two real recordings (configuration.json must describe the actual path):
configuration = json.loads(Path(r"D:\calibration\configuration.json").read_text("utf-8"))
profile = measure_capture_calibration(probe, [
    r"D:\calibration\capture-1.wav", r"D:\calibration\capture-2.wav"
], configuration)
print(json.dumps(profile, indent=2))
print(json.dumps(validate_capture_calibration(profile, configuration), indent=2))
```

## Implementation and verification

Owned files: this document, `bounce/film_calibration.py`,
`tools/film_calibration.py` and `tests/test_film_calibration.py`.
Implementation sequence: red synthetic DSP fixtures → deterministic probe and
measurement → red MCP call-through → wrappers → red integrity regressions →
strict container/profile checks. No Live mocks manufacture calibration evidence.

```powershell
Set-Location D:\AI\Dad-MCPs\AbletonFullControlMCP
.\.venv\Scripts\python.exe -B -m pytest tests/test_film_calibration.py -q -p no:cacheprovider
```

Synthetic success proves the algorithm against those fixtures only. It does not
claim a real device/application capture has passed. Main records that evidence
separately after executing the disposable procedure.
