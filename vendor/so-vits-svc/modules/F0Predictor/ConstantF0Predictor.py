import numpy as np

from modules.F0Predictor.F0Predictor import F0Predictor


class ConstantF0Predictor(F0Predictor):
    """不使用 F0 预测器（none）。

    返回恒定音高（f0 全 0）与清音标记（uv 全 1），让上游跳过音高提取，
    仍然走编码器 → 生成器的音色转换路径。适合念白/无旋律素材；
    歌声用它必然失去旋律，只保留音色。
    """

    def __init__(self, hop_length=512, f0_min=50, f0_max=1100, sampling_rate=44100, **kwargs):
        self.hop_length = hop_length
        self.f0_min = f0_min
        self.f0_max = f0_max
        self.sampling_rate = sampling_rate
        self.name = "none"

    def _frames(self, wav, p_len):
        if p_len is None:
            p_len = len(wav) // self.hop_length
        return max(1, int(p_len))

    def compute_f0(self, wav, p_len=None):
        return np.zeros(self._frames(wav, p_len), dtype=np.float32)

    def compute_f0_uv(self, wav, p_len=None):
        frames = self._frames(wav, p_len)
        f0 = np.zeros(frames, dtype=np.float32)
        uv = np.ones(frames, dtype=np.float32)
        return f0, uv
