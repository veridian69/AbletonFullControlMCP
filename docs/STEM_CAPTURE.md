# Realtime stem capture

`bounce_tracks` in resampling mode creates one capture track per source and,
optionally, one master capture. All captures record during one playback pass.

Source routes are selected from the destination track's advertised input routing
names. An exact, unique source name is preferred; a numbered name is used only
when Live actually advertises it. Duplicate names without a unique advertised
numbered route are rejected. Rename ambiguous tracks before capture.

Each stem uses the `Post Mixer` input channel. Master capture uses `Resampling`
without a per-track tap channel. After every capture track has been configured
and armed, the implementation reads back every route, stem channel and arm.
An incorrect or unavailable setting aborts before arrangement recording begins.
Direct OSC arm setters are used; exclusive-arm preferences are not changed.
If earlier captures become disarmed, the error identifies those tracks and
suggests checking exclusive arm or an interfering controller.

With default cleanup enabled, tracks created before a routing failure,
verification failure or cancellation are removed in reverse index order.
`cleanup_temp_tracks=False` deliberately retains capture tracks for inspection.
Successful setup reports `routing_confirmed` and `arm_confirmed` per stem and
master; these flags do not prove that the resulting audio is non-silent.

Regression tests use strict OSC fakes for advertised names, final arm state,
channel rejection, ambiguous names and interrupted setup. This patch has not
been deployed or exercised against a running Live session. A disposable Live
set is still required to validate the complete recording and file-harvest path.
