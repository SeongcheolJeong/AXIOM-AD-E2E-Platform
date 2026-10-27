"""Bounded image appearance descriptor. No learned model or semantic inference.

The weighted RGB code value is an explicit numeric proxy, not scene radiance or
lux. Identical pixels and near visual appearance never establish identical events.
"""
import hashlib
import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, __version__ as pillow_version

from .service import digest

VERSION = 'rgb-layout-histogram/1'
RULES = {
    'dark_image': {'metric': 'mean_code', 'operator': '<=', 'threshold': .20},
    'bright_image': {'metric': 'mean_code', 'operator': '>=', 'threshold': .80},
    'low_contrast': {'metric': 'code_std', 'operator': '<=', 'threshold': .08},
    'high_contrast': {'metric': 'code_std', 'operator': '>=', 'threshold': .30},
    'highlight_rich': {'metric': 'highlight_fraction', 'operator': '>=', 'threshold': .05},
}


def recipe():
    return {'id': VERSION, 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'libraries': {'numpy': np.__version__, 'Pillow': pillow_version},
        'descriptor': '8x8 BOX-resized RGB codes + 16-bin/channel RGB histogram; image aspect ratio is not preserved in the layout descriptor',
        'distance': '0.65 * layout_RMS/255 + 0.35 * mean_channel_histogram_TV',
        'rules': RULES, 'code_proxy': '(.2126 R + .7152 G + .0722 B)/255, without linearization',
        'highlight_rule': 'fraction with all RGB channels >= 250; not proof of sensor saturation',
        'scope': 'Visual appearance baseline only; no language embedding, weather, object, location or time-of-day inference.'}


def analyze(png: bytes):
    with Image.open(io.BytesIO(png)) as source:
        if source.mode != 'RGB':
            raise ValueError('Discovery requires the captured RGB image, without implicit color conversion')
        array = np.asarray(source, dtype=np.uint8)
        layout = list(source.resize((8, 8), Image.Resampling.BOX).tobytes())
        width, height = source.size
    if array.ndim != 3 or array.shape[2] != 3 or not 1 <= width*height <= 2_000_000:
        raise ValueError('Image exceeds the RGB/pixel contract')
    proxy = (array[..., 0].astype(np.float64)*.2126 + array[..., 1]*.7152 + array[..., 2]*.0722)/255.
    metrics = {'mean_code': round(float(proxy.mean()), 9), 'code_std': round(float(proxy.std()), 9),
        'highlight_fraction': round(float(np.all(array >= 250, axis=2).mean()), 9)}
    histograms = [np.bincount((array[..., c]//16).ravel(), minlength=16).tolist() for c in range(3)]
    pixels = array.tobytes()
    proposals = [{'tag': tag, 'source': 'pixel_rule', 'rule_version': VERSION,
        'metric': rule['metric'], 'value': metrics[rule['metric']], 'operator': rule['operator'],
        'threshold': rule['threshold'], 'confidence': None}
        for tag, rule in RULES.items()
        if (metrics[rule['metric']] <= rule['threshold'] if rule['operator'] == '<=' else metrics[rule['metric']] >= rule['threshold'])]
    value = {'pixel_identity': digest({'mode': 'RGB', 'width': width, 'height': height,
              'rgb_sha256': hashlib.sha256(pixels).hexdigest()}),
        'pixels': width*height, 'metrics': metrics, 'layout_rgb': layout,
        'histograms': histograms, 'proposals': proposals}
    validate(value)
    return value


def validate(value):
    if (len(value['layout_rgb']) != 192 or any(type(n) is not int or not 0 <= n <= 255 for n in value['layout_rgb'])
            or type(value['pixels']) is not int or not 1 <= value['pixels'] <= 2_000_000
            or len(value['histograms']) != 3
            or any(len(h) != 16 or any(type(n) is not int or n < 0 for n in h) or sum(h) != value['pixels'] for h in value['histograms'])
            or set(value['metrics']) != {'mean_code', 'code_std', 'highlight_fraction'}
            or any(not isinstance(v, (int,float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in value['metrics'].values())):
        raise ValueError('Invalid visual descriptor')


def distance(first, second):
    validate(first); validate(second)
    layout = math.sqrt(sum((a-b)**2 for a,b in zip(first['layout_rgb'],second['layout_rgb']))/192)/255
    histogram = sum(sum(abs(a/first['pixels']-b/second['pixels']) for a,b in zip(ha,hb))/2
                    for ha,hb in zip(first['histograms'],second['histograms']))/3
    return round(.65*layout + .35*histogram, 9)
