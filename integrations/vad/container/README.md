# Optional native VAD research container

This directory is excluded from the default platform package. It does not contain
nuScenes data or learned weights and does not grant their usage rights.
The base image digest is fixed. On this host the image tagged PyTorch1.9.1 actually
contained Python3.7/PyTorch1.10. Therefore the Dockerfile creates a separate
Python3.8.20 environment, installs the exact PyTorch1.9.1+cu111 wheel, and checks
the runtime versions rather than trusting the tag. Obsolete NVIDIA apt feeds
are removed only inside the build image; the host driver is untouched.
The image follows the pinned VAD installation profile; actual CUDA operator and
checkpoint inference acceptance are separate. The detector/runtime dependencies
use older versions intentionally. Do not install them into the CARLA venv.

```bash
docker build -t axiom-vad-research:profile1 integrations/vad/container
```

On the verified remote Linux host the prepared workspace is
`/home/axiom-user/workspace/axiom-vad-research`. With the core contract and
adapter source in that directory:

```bash
docker run --rm --network=none --gpus all \
  --mount type=bind,src=/home/axiom-user/workspace/axiom-vad-research,dst=/work,readonly \
  -e PYTHONPATH=/work/backend/src:/work/integrations/vad/src \
  axiom-vad-research:profile1 python /work/container/cuda_smoke.py
```

`cuda_smoke.py` checks a real MMCV deformable-attention CUDA operator against a
known numeric result. Its one-pixel input is an operator diagnostic, not a driving
prediction, road image or learned model acceptance. Failures must remain failures.
Record the base image digest, built image ID, full `pip freeze`, dependency source
commit and smoke report before attempting native inference. Checkpoint/data roots
should be read-only mounts; place outputs in a separate explicitly writable mount.
No network or host Docker socket is needed during inference.

The build compiles MMDetection3D for the verified RTX2070SUPER's compute capability
7.5. Select and accept another architecture separately for other equipment.

The image trusts only the selected `/work/VAD` Git mount path. To check the pinned
source and Tiny configuration imports, run the same GPU container command with
`python -B /work/container/source_smoke.py`. This also does not run a checkpoint.


The official Tiny checkpoint is staged only in the remote research workspace.
`checkpoint_smoke.py --confirm-trusted-native-code` checks the explicitly trusted
checkpoint against the pinned configuration with `strict=True` on CPU, without
input images, a forward pass or predictions. The script pins the hash of the
inspected official download; this is not a publisher signature or a commercial
rights grant. Never load an arbitrary replacement pickle as trusted input.
