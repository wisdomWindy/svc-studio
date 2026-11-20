"""
VR Architecture 模型的 PyTorch 推理模块
从 UVR5 模型权重 state_dict 反推的网络架构
用于 So-VITS-SVC 4.8 WebUI 的音频分离功能
"""

import os
import json
import torch
import torch.nn as nn
import numpy as np


class Conv2DBNActiv(nn.Module):
    def __init__(self, nin, nout, ksize=3, stride=1, pad=1, dilation=1, activ=True):
        super().__init__()
        if activ:
            self.conv = nn.Sequential(
                nn.Conv2d(nin, nout, ksize, stride, pad, dilation),
                nn.BatchNorm2d(nout),
                nn.LeakyReLU(0.1, inplace=True)
            )
        else:
            self.conv = nn.Sequential(
                nn.Conv2d(nin, nout, ksize, stride, pad, dilation),
            )

    def forward(self, x):
        return self.conv(x)


class ResBlock(nn.Module):
    def __init__(self, nin, nout):
        super().__init__()
        self.conv1 = Conv2DBNActiv(nin, nout, 3, 1, 1)
        self.conv2 = Conv2DBNActiv(nout, nout, 3, 1, 1)

    def forward(self, x):
        return x + self.conv2(self.conv1(x))


class BandModel(nn.Module):
    """单个频带的 CNN 分离网络"""
    def __init__(self, nin, nout=64, base_channels=32):
        super().__init__()
        c = base_channels
        # Encoder: 4 stages
        self.enc1 = nn.Sequential(Conv2DBNActiv(nin, c), Conv2DBNActiv(c, c))
        self.enc2 = nn.Sequential(Conv2DBNActiv(c, c*2), Conv2DBNActiv(c*2, c*2))
        self.enc3 = nn.Sequential(Conv2DBNActiv(c*2, c*4), Conv2DBNActiv(c*4, c*4))
        self.enc4 = nn.Sequential(Conv2DBNActiv(c*4, c*8), Conv2DBNActiv(c*8, c*8))

        # ASPP
        self.aspp = nn.ModuleDict({
            "conv1": nn.Sequential(nn.Identity(), Conv2DBNActiv(c*8, c*8, 1, 1, 0)),
            "conv2": nn.Sequential(nn.Identity(), Conv2DBNActiv(c*8, c*8, 1, 1, 0)),
            "conv3": nn.Sequential(nn.AvgPool2d(6, 1, 0, 1, count_include_pad=False), Conv2DBNActiv(1, c*8, 1, 1, 0)),
            "conv4": nn.Sequential(nn.AvgPool2d(12, 1, 0, 1, count_include_pad=False), Conv2DBNActiv(1, c*8, 1, 1, 0)),
            "conv5": nn.Sequential(nn.AvgPool2d(18, 1, 0, 1, count_include_pad=False), Conv2DBNActiv(1, c*8, 1, 1, 0)),
            "bottleneck": nn.Sequential(Conv2DBNActiv(c*8 * 5, c*8 * 2, 1, 1, 0)),
        })

        # Decoder: 4 stages (with skip connections)
        self.dec4 = nn.Sequential(Conv2DBNActiv(c*8*3, c*8, 3, 1, 1))
        self.dec3 = nn.Sequential(Conv2DBNActiv(c*8 + c*4, c*4, 3, 1, 1))
        self.dec2 = nn.Sequential(Conv2DBNActiv(c*4 + c*2, c*2, 3, 1, 1))
        self.dec1 = nn.Sequential(Conv2DBNActiv(c*2 + c, c, 3, 1, 1))

    def forward(self, x):
        # Encoder
        e1 = self.enc1(x)
        e2 = self.enc2(e1)
        e3 = self.enc3(e2)
        e4 = self.enc4(e3)

        # ASPP
        a1 = self.aspp["conv1"](e4)
        a2 = self.aspp["conv2"](e4)
        a3 = self.aspp["conv3"](e4)
        a4 = self.aspp["conv4"](e4)
        a5 = self.aspp["conv5"](e4)
        bottleneck = self.aspp["bottleneck"](torch.cat([a1, a2, a3, a4, a5], dim=1))

        # Decoder with skip connections
        d4 = self.dec4(torch.cat([bottleneck, e4], dim=1))
        d3 = self.dec3(torch.cat([d4, e3], dim=1))
        d2 = self.dec2(torch.cat([d3, e2], dim=1))
        d1 = self.dec1(torch.cat([d2, e1], dim=1))

        return d1


class VRModel(nn.Module):
    """完整的 VR Architecture 多频带模型"""

    # 模型配置映射: 模型文件名 -> 参数名
    MODEL_PARAM_MAP = {
        "5_HP-Karaoke-UVR.pth": "3band_44100_msb2",
        "6_HP-Karaoke-UVR.pth": "3band_44100_msb2",
        "9_HP2-UVR.pth": "3band_44100_msb2",
        "UVR-De-Echo-Aggressive.pth": "4band_v3",
        "UVR-De-Echo-Normal.pth": "4band_v3",
        "UVR-DeEcho-DeReverb.pth": "4band_v3",
        "UVR-DeNoise.pth": "4band_v3",
    }

    def __init__(self):
        super().__init__()
        # Stage 1: low/high band (2-channel input)
        self.stg1_low_band_net = BandModel(2, base_channels=32)
        self.stg1_high_band_net = BandModel(2, base_channels=32)
        # Stage 2: bridge + full band (reduced input)
        self.stg2_bridge = Conv2DBNActiv(34, 16, 1, 1, 0)
        self.stg2_full_band_net = BandModel(16, base_channels=32)
        # Stage 3: bridge + full band (reduced input)
        self.stg3_bridge = Conv2DBNActiv(66, 32, 1, 1, 0)
        self.stg3_full_band_net = BandModel(32, base_channels=64)
        # Outputs
        self.out = nn.Conv2d(64, 2, 1)
        self.aux1_out = nn.Conv2d(32, 2, 1)
        self.aux2_out = nn.Conv2d(32, 2, 1)

    def forward(self, x):
        # Stage 1
        s1_low = self.stg1_low_band_net(x)
        s1_high = self.stg1_high_band_net(x)

        # Stage 2: bridge from stage 1 outputs
        s2_input = self.stg2_bridge(torch.cat([s1_low, s1_high], dim=1))
        s2 = self.stg2_full_band_net(s2_input)

        # Stage 3: bridge from stage 2
        s3_input = self.stg3_bridge(torch.cat([s2, x], dim=1))
        s3 = self.stg3_full_band_net(s3_input)

        # Final output
        mask = self.out(s3)
        return mask


def load_vr_model(model_path, param_dir, device="cpu"):
    """加载 VR 模型并返回推理函数"""

    model_name = os.path.basename(model_path)
    param_name = VRModel.MODEL_PARAM_MAP.get(model_name)

    if not param_name:
        raise ValueError(f"未知模型: {model_name}")

    # 加载模型参数配置
    param_path = os.path.join(param_dir, f"{param_name}.json")
    with open(param_path, "r") as f:
        param_config = json.load(f)

    # 初始化并加载模型
    model = VRModel()
    checkpoint = torch.load(model_path, map_location="cpu")
    if isinstance(checkpoint, dict) and "model" in checkpoint:
        model.load_state_dict(checkpoint["model"])
    else:
        model.load_state_dict(checkpoint)

    model.to(device)
    model.eval()

    return model, param_config


def stft(audio, n_fft, hop_length, device="cpu"):
    """STFT"""
    window = torch.hann_window(n_fft, device=device)
    spec = torch.stft(audio, n_fft=n_fft, hop_length=hop_length,
                      win_length=n_fft, window=window, return_complex=True,
                      center=True)
    return spec


def istft(spec, hop_length, length, n_fft, device="cpu"):
    """ISTFT"""
    window = torch.hann_window(n_fft, device=device)
    audio = torch.istft(spec, n_fft=n_fft, hop_length=hop_length,
                        win_length=n_fft, window=window, length=length, center=True)
    return audio


def resample(audio_np, orig_sr, target_sr):
    """使用 scipy 重采样"""
    import scipy.signal
    if orig_sr == target_sr:
        return audio_np
    channels = audio_np.shape[0]
    target_len = int(audio_np.shape[1] * target_sr / orig_sr)
    result = np.zeros((channels, target_len), dtype=np.float32)
    for c in range(channels):
        result[c] = scipy.signal.resample(audio_np[c], target_len).astype(np.float32)
    return result


def _apply_filter(spec, band_info):
    """对频谱应用频带滤波"""
    mag = torch.abs(spec)
    n_bins = mag.shape[2]

    lpf_start = band_info.get("lpf_start", -1)
    lpf_stop = band_info.get("lpf_stop", -1)
    hpf_start = band_info.get("hpf_start", -1)
    hpf_stop = band_info.get("hpf_stop", -1)

    if hpf_start < 0 and lpf_start < 0:
        return spec

    mask = torch.ones_like(mag)

    if hpf_start >= 0 and hpf_stop >= 0:
        if hpf_stop < n_bins:
            for i in range(max(0, hpf_stop), min(hpf_start, n_bins)):
                mask[:, :, i, :] = (i - hpf_stop) / max(1, (hpf_start - hpf_stop))
            if hpf_stop > 0:
                mask[:, :, :max(0, hpf_stop), :] = 0

    if lpf_start >= 0 and lpf_stop >= 0:
        if lpf_start < n_bins:
            for i in range(max(0, lpf_start), min(lpf_stop, n_bins)):
                mask[:, :, i, :] = (lpf_stop - i) / max(1, (lpf_stop - lpf_start))
            if lpf_stop < n_bins:
                mask[:, :, min(lpf_stop, n_bins):, :] = 0

    return spec * mask


@torch.no_grad()
def vr_separate_audio(model, param_config, mix_np, device="cpu"):
    """
    使用 VR 模型分离音频

    Args:
        model: VRModel 实例
        param_config: 模型参数配置 (dict)
        mix_np: 输入音频 numpy array [channels, samples]
        device: 推理设备

    Returns:
        (primary_np, secondary_np): 分离后的两个音频 numpy array
    """
    # 确保立体声
    if mix_np.ndim == 1:
        mix_np = np.stack([mix_np, mix_np], axis=0)

    target_sr = param_config.get("sr", 44100)
    original_length = mix_np.shape[1]
    unstable_bins = param_config.get("unstable_bins", 0)
    pre_filter_start = param_config.get("pre_filter_start", -1)
    pre_filter_stop = param_config.get("pre_filter_stop", -1)

    mix_t = torch.from_numpy(mix_np).float().to(device)

    # 多频带处理
    bands = param_config.get("band", {})
    sorted_band_ids = sorted(bands.keys(), key=lambda x: int(x))

    # Stage 1: 对每个频带分别处理
    stg1_outputs = []
    for band_id in sorted_band_ids:
        band_info = bands[band_id]
        n_fft = band_info["n_fft"]
        hop_length = band_info.get("hl", n_fft // 4)
        sr_band = band_info["sr"]
        crop_start = band_info.get("crop_start", 0)
        crop_stop = band_info.get("crop_stop", n_fft // 2 + 1)

        # Resample to band SR
        mix_band_np = resample(mix_t.cpu().numpy(), target_sr, sr_band)
        mix_band = torch.from_numpy(mix_band_np).float().to(device)

        # STFT
        spec = stft(mix_band, n_fft, hop_length, device)

        # Apply band filter
        spec = _apply_filter(spec, band_info)

        # To magnitude
        mag = torch.abs(spec)

        # Crop
        mag_cropped = mag[:, :, crop_start:crop_stop, :]

        # Zero unstable bins
        if unstable_bins > 0 and mag_cropped.shape[2] > unstable_bins:
            mag_cropped[:, :, :unstable_bins, :] = 0

        # Predict mask
        mask = model(mag_cropped)
        mask = torch.clamp(mask, 0, 1)

        # Take first stem
        pred_mask = mask[:, 0:1, :, :]

        # Apply mask to original spec (within crop range)
        result_spec = mag.clone()
        result_spec[:, :, crop_start:crop_stop, :] *= pred_mask

        # Apply pre-filter to output
        if pre_filter_start >= 0 and pre_filter_stop >= 0:
            if pre_filter_start < result_spec.shape[2]:
                atten = torch.ones_like(result_spec)
                atten[:, :, pre_filter_start:pre_filter_stop, :] = 0
                result_spec = result_spec * atten

        # ISTFT
        result_audio = istft(result_spec, hop_length, mix_band.shape[1], n_fft, device)

        # Resample back to target SR
        result_audio_np = result_audio.cpu().numpy()
        result_audio_np = resample(result_audio_np, sr_band, target_sr)

        stg1_outputs.append(result_audio_np)

    # Stage 2 & 3: 全频带处理（简化版，直接在原始采样率上做 STFT）
    # 对于实际使用，stage 2/3 的输入需要 stage 1 的中间特征
    # 但由于完整的多阶段推理需要精确的特征图拼接，这里用简化方案：
    # 使用最后一个频带的结果作为主要输出
    # （真正的 UVR5 使用累加重叠来合并各频带结果）

    # 合并频带结果 - 简单加权平均
    if len(stg1_outputs) == 1:
        primary = stg1_outputs[0]
    else:
        # 加权平均：每个频带贡献的频率范围越大，权重越高
        primary = np.zeros_like(mix_np, dtype=np.float64)
        weight_sum = 0.0
        for i, band_id in enumerate(sorted_band_ids):
            band_info = bands[band_id]
            n_fft = band_info["n_fft"]
            crop_start = band_info.get("crop_start", 0)
            crop_stop = band_info.get("crop_stop", n_fft // 2 + 1)
            weight = (crop_stop - crop_start) / n_fft
            min_len = min(stg1_outputs[i].shape[1], primary.shape[1])
            primary[:, :min_len] += stg1_outputs[i][:, :min_len] * weight
            weight_sum += weight
        primary = (primary / max(weight_sum, 1e-8)).astype(np.float32)

    # Secondary = original - primary
    secondary = mix_np - primary

    # Trim to original length
    primary = primary[:, :original_length]
    secondary = secondary[:, :original_length]

    return primary, secondary
