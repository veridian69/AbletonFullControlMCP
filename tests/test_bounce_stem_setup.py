"""Stem setup contracts against a strict OSC boundary double."""
import asyncio
import pytest
from ableton_mcp.bounce import resampling
from tests.test_bounce_resampling import FakeOSC, FakeBridge, _patch


class StemOSC(FakeOSC):
    def __init__(self, *, indexed=False, exclusive=False, broken_channel=False):
        super().__init__(track_names=["PAD", "ARP"])
        self.routes = ["1-PAD", "2-ARP"] if indexed else ["PAD", "ARP"]
        self.exclusive = exclusive
        self.broken_channel = broken_channel
        self.channels = {}
        self.record_arms = []
        self.record_routes = []

    async def request(self, addr, *args):
        if addr == "/live/track/get/available_input_routing_types":
            return (args[0], "Ext. In", "Resampling", *self.routes)
        if addr == "/live/track/get/input_routing_channel":
            return (args[0], self.channels.get(args[0], "Pre FX"))
        return await super().request(addr, *args)

    def send(self, addr, *args):
        if addr == "/live/track/set/input_routing_type" and args[1] not in [*self.routes, "Resampling"]:
            return  # Live rejects unavailable display names
        if addr == "/live/track/set/input_routing_channel" and not self.broken_channel:
            self.channels[args[0]] = args[1]
        if addr == "/live/track/set/arm" and args[1] and self.exclusive:
            self.arm = {}
        if addr == "/live/song/set/record_mode" and args[0]:
            self.record_arms.append(dict(self.arm))
            self.record_routes.append(dict(self.input_routing))
        super().send(addr, *args)


@pytest.mark.asyncio
@pytest.mark.parametrize("indexed", [False, True])
async def test_stems_choose_advertised_routes_and_confirm_all_captures(monkeypatch, tmp_path, indexed):
    osc = StemOSC(indexed=indexed)
    _patch(monkeypatch, osc, FakeBridge(file_path=None))
    result = await resampling.bounce_tracks_via_resampling(
        [0, 1], tmp_path, 0, settle_sec=0, include_master=True,
        clip_finalize_timeout_sec=0,
    )
    assert osc.record_arms == [{2: 1, 3: 1, 4: 1}]
    expected = {2: "1-PAD", 3: "2-ARP", 4: "Resampling"} if indexed else {2: "PAD", 3: "ARP", 4: "Resampling"}
    assert osc.record_routes == [expected]
    assert all(s["routing_confirmed"] and s["arm_confirmed"] for s in result["stems"])
    assert osc.track_names == ["PAD", "ARP"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exclusive", "channel", "unavailable", "exception"])
async def test_invalid_stem_setup_never_records_and_cleans_created_tracks(monkeypatch, tmp_path, failure):
    osc = StemOSC(exclusive=failure == "exclusive", broken_channel=failure == "channel")
    if failure == "unavailable":
        osc.routes = ["PAD"]
    _patch(monkeypatch, osc, FakeBridge(file_path=None))
    if failure == "exception":
        async def broken(*args, **kwargs):
            raise RuntimeError("routing unavailable")
        monkeypatch.setattr(resampling, "_set_input_routing", broken)
    with pytest.raises((resampling.BounceError, RuntimeError)):
        await resampling.bounce_tracks_via_resampling(
            [0, 1], tmp_path, 0, settle_sec=0, include_master=True,
            clip_finalize_timeout_sec=0,
        )
    assert osc.record_arms == []
    assert osc.track_names == ["PAD", "ARP"]



@pytest.mark.asyncio
async def test_duplicate_source_display_names_are_rejected(monkeypatch, tmp_path):
    osc = StemOSC()
    osc.track_names = ["PAD", "PAD"]
    osc.routes = ["PAD", "PAD"]
    _patch(monkeypatch, osc, FakeBridge(file_path=None))
    with pytest.raises(resampling.BounceError, match="uniquely route"):
        await resampling.bounce_tracks_via_resampling([1], tmp_path, 0)
    assert osc.record_arms == []
    assert osc.track_names == ["PAD", "PAD"]


@pytest.mark.asyncio
async def test_cancelled_stem_setup_cleans_created_track(monkeypatch, tmp_path):
    osc = StemOSC()
    _patch(monkeypatch, osc, FakeBridge(file_path=None))
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(resampling, "_set_input_routing", cancel)
    with pytest.raises(asyncio.CancelledError):
        await resampling.bounce_tracks_via_resampling([0, 1], tmp_path, 0)
    assert osc.record_arms == []
    assert osc.track_names == ["PAD", "ARP"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", ["input_routing_type", "arm"])
async def test_master_invalid_setup_never_records_or_copies_existing_clip(monkeypatch, tmp_path, rejected):
    class MasterOSC(StemOSC):
        def send(self, address, *args):
            if address == f"/live/track/set/{rejected}":
                return
            super().send(address, *args)

    osc = MasterOSC()
    source = tmp_path / "previous.wav"
    source.write_bytes(b"previous recording")
    bridge = FakeBridge(file_path=str(source))
    _patch(monkeypatch, osc, bridge)
    destination = tmp_path / "master.wav"
    with pytest.raises(resampling.BounceError, match="Capture setup not confirmed"):
        await resampling.bounce_song_via_resampling(destination, 0, settle_sec=0)
    assert osc.record_arms == []
    assert bridge.calls == []
    assert not destination.exists()
    assert osc.track_names == ["PAD", "ARP"]


@pytest.mark.asyncio
async def test_master_resampling_does_not_set_track_tap_channel(monkeypatch, tmp_path):
    osc = StemOSC()
    _patch(monkeypatch, osc, FakeBridge(file_path=None))
    await resampling.bounce_song_via_resampling(
        tmp_path / "master.wav", 0, settle_sec=0, clip_finalize_timeout_sec=0,
    )
    assert not any(address == "/live/track/set/input_routing_channel" for address, _ in osc.sent)
    assert osc.record_arms == [{2: 1}]
