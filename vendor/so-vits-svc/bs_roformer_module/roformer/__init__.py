"""UVR 兼容的 BS-RoFormer / Mel-Band RoFormer 网络实现（vendored）。

来源：python-audio-separator 的 separator/uvr_lib_v5/roformer（MIT），
其结构与 UVR 发布的 model_bs_roformer_ep_368_sdr_12.9628.ckpt 等权重一一对应。
本地只做了一处改动：把跨包 import 改为包内相对 import，去掉对 audio_separator 的依赖。
"""

from .bs_roformer import BSRoformer  # noqa: F401
from .mel_band_roformer import MelBandRoformer  # noqa: F401
