# Optional native VAD research connector

See [driving policies](../../docs/driving-policies.md) for the exact source, input/output contract,
native setup and the accepted three-frame recorded-data research run.
The model was executed with real mini/CAN data; queued product execution, policy A/B
and closed-loop safety evaluation remain uncompleted.

The adapter imports Torch/MMCV only for an explicit native probe/run. It is not registered as an
image-detection runtime or packed into the default local pilot. The core exposes receipt checks only.
Do not promote authored contract/browser fixtures to native VAD execution evidence.
