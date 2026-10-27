"""Real CUDA/operator check. This does not run a learned VAD checkpoint."""
import json
import torch
from mmcv.ops.multi_scale_deform_attn import MultiScaleDeformableAttnFunction
from axiom_vad.native import inspect_environment

report = inspect_environment()
if report['status'] != 'available_for_native_check':
    raise RuntimeError(json.dumps(report))
# One level, one sample location at its pixel centre: bilinear output equals input.
value = torch.tensor([[[[2.0, 3.0]]]], device='cuda')
shapes = torch.tensor([[1, 1]], dtype=torch.long, device='cuda')
starts = torch.tensor([0], dtype=torch.long, device='cuda')
locations = torch.full((1, 1, 1, 1, 1, 2), 0.5, device='cuda')
weights = torch.ones((1, 1, 1, 1, 1), device='cuda')
actual = MultiScaleDeformableAttnFunction.apply(value, shapes, starts, locations, weights, 1)
torch.cuda.synchronize()
if not torch.allclose(actual, torch.tensor([[[2.0, 3.0]]], device='cuda')):
    raise RuntimeError('MMCV CUDA operator returned an unexpected value')
report.update(cuda_operator='passed', learned_model_inference=False)
print(json.dumps(report, indent=2))
