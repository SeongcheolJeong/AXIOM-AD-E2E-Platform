# Optional research runtime; no CARLA or platform-server dependencies.
ARG BASE_IMAGE=pytorch/pytorch:1.9.1-cuda11.1-cudnn8-devel@sha256:fd8fcd6e1196d8965657b04e7dfb666046063904b767c1fd75df8039fe0ada17
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive FORCE_CUDA=1 TORCH_CUDA_ARCH_LIST=7.5 MAX_JOBS=2
RUN rm -f /etc/apt/sources.list.d/cuda.list /etc/apt/sources.list.d/nvidia-ml.list && apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates build-essential libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-unknown-linux-gnu.tar.gz | tar -xz -C /opt && /opt/uv-x86_64-unknown-linux-gnu/uv python install 3.8.20 && /opt/uv-x86_64-unknown-linux-gnu/uv venv --python 3.8.20 --seed /opt/vad
ENV PATH=/opt/vad/bin:$PATH
RUN python -m pip install --no-cache-dir 'pip==23.3.2' 'setuptools==59.5.0' 'wheel==0.41.3'
RUN python -m pip install --no-cache-dir torch==1.9.1+cu111 torchvision==0.10.1+cu111 -f https://download.pytorch.org/whl/torch_stable.html
RUN python -c "import sys,torch; assert sys.version_info[:2] == (3,8); assert torch.__version__.split('+')[0] == '1.9.1'"
COPY constraints.txt /opt/vad-constraints.txt
ENV PIP_CONSTRAINT=/opt/vad-constraints.txt
RUN python -m pip install --no-cache-dir numpy numba llvmlite scipy Pillow matplotlib scikit-image scikit-learn networkx trimesh plyfile tensorboard lyft-dataset-sdk nuscenes-devkit timm einops yapf opencv-python
RUN python -m pip install --no-cache-dir mmcv-full==1.4.0 -f https://download.openmmlab.com/mmcv/dist/cu111/torch1.9.0/index.html
RUN python -m pip install --no-cache-dir mmdet==2.14.0 mmsegmentation==0.14.1
RUN git clone --depth 1 --branch v0.17.1 https://github.com/open-mmlab/mmdetection3d.git /opt/mmdetection3d && cd /opt/mmdetection3d && python -m pip install --no-cache-dir --no-deps --no-build-isolation .
RUN git config --global --add safe.directory /work/VAD
WORKDIR /work
