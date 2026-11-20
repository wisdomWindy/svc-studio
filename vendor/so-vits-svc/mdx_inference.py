"""
MDX-Net 推理模块
支持：
1. MDX23C (MDXC) — .ckpt 格式，使用 TFC_TDF_net 架构
2. 旧版 MDX-Net — .onnx 格式，使用 onnxruntime

参考: https://github.com/Anjok07/ultimatevocalremovergui
"""

import os
import json
import hashlib
import numpy as np
import torch
import torch.nn as nn
from functools import partial


# ============================================================
# TFC_TDF_v3 网络定义（MDX23C / MDXC 模型）
# ============================================================

class STFT:
    def __init__(self, n_fft, hop_length, dim_f, device):
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.window = torch.hann_window(window_length=self.n_fft, periodic=True)
        self.dim_f = dim_f
        self.device = device

    def __call__(self, x):
        x_is_mps = not x.device.type in ["cuda", "cpu"]
        if x_is_mps:
            x = x.cpu()

        window = self.window.to(x.device)
        batch_dims = x.shape[:-2]
        c, t = x.shape[-2:]
        x = x.reshape([-1, t])
        x = torch.stft(x, n_fft=self.n_fft, hop_length=self.hop_length, window=window, center=True, return_complex=False)
        x = x.permute([0, 3, 1, 2])
        x = x.reshape([*batch_dims, c, 2, -1, x.shape[-1]]).reshape([*batch_dims, c * 2, -1, x.shape[-1]])

        if x_is_mps:
            x = x.to(self.device)

        return x[..., :self.dim_f, :]

    def inverse(self, x):
        x_is_mps = not x.device.type in ["cuda", "cpu"]
        if x_is_mps:
            x = x.cpu()

        window = self.window.to(x.device)
        batch_dims = x.shape[:-3]
        c, f, t = x.shape[-3:]
        n = self.n_fft // 2 + 1
        f_pad = torch.zeros([*batch_dims, c, n - f, t]).to(x.device)
        x = torch.cat([x, f_pad], -2)
        x = x.reshape([*batch_dims, c // 2, 2, n, t]).reshape([-1, 2, n, t])
        x = x.permute([0, 2, 3, 1])
        x = x[..., 0] + x[..., 1] * 1.j
        x = torch.istft(x, n_fft=self.n_fft, hop_length=self.hop_length, window=window, center=True)
        x = x.reshape([*batch_dims, 2, -1])

        if x_is_mps:
            x = x.to(self.device)

        return x


def get_norm(norm_type):
    def norm(c, norm_type):
        if norm_type == 'BatchNorm':
            return nn.BatchNorm2d(c)
        elif norm_type == 'InstanceNorm':
            return nn.InstanceNorm2d(c, affine=True)
        elif 'GroupNorm' in norm_type:
            g = int(norm_type.replace('GroupNorm', ''))
            return nn.GroupNorm(num_groups=g, num_channels=c)
        else:
            return nn.Identity()
    return partial(norm, norm_type=norm_type)


def get_act(act_type):
    if act_type == 'gelu':
        return nn.GELU()
    elif act_type == 'relu':
        return nn.ReLU()
    elif act_type[:3] == 'elu':
        alpha = float(act_type.replace('elu', ''))
        return nn.ELU(alpha)
    else:
        raise Exception(f"Unknown activation: {act_type}")


class Upscale(nn.Module):
    def __init__(self, in_c, out_c, scale, norm, act):
        super().__init__()
        self.conv = nn.Sequential(
            norm(in_c),
            act,
            nn.ConvTranspose2d(in_channels=in_c, out_channels=out_c, kernel_size=scale, stride=scale, bias=False)
        )

    def forward(self, x):
        return self.conv(x)


class Downscale(nn.Module):
    def __init__(self, in_c, out_c, scale, norm, act):
        super().__init__()
        self.conv = nn.Sequential(
            norm(in_c),
            act,
            nn.Conv2d(in_channels=in_c, out_channels=out_c, kernel_size=scale, stride=scale, bias=False)
        )

    def forward(self, x):
        return self.conv(x)


class TFC_TDF(nn.Module):
    def __init__(self, in_c, c, l, f, bn, norm, act):
        super().__init__()
        self.blocks = nn.ModuleList()
        for i in range(l):
            block = nn.Module()
            block.tfc1 = nn.Sequential(
                norm(in_c),
                act,
                nn.Conv2d(in_c, c, 3, 1, 1, bias=False),
            )
            block.tdf = nn.Sequential(
                norm(c),
                act,
                nn.Linear(f, f // bn, bias=False),
                norm(c),
                act,
                nn.Linear(f // bn, f, bias=False),
            )
            block.tfc2 = nn.Sequential(
                norm(c),
                act,
                nn.Conv2d(c, c, 3, 1, 1, bias=False),
            )
            block.shortcut = nn.Conv2d(in_c, c, 1, 1, 0, bias=False)
            self.blocks.append(block)
            in_c = c

    def forward(self, x):
        for block in self.blocks:
            s = block.shortcut(x)
            x = block.tfc1(x)
            x = x + block.tdf(x)
            x = block.tfc2(x)
            x = x + s
        return x


class TFC_TDF_net(nn.Module):
    """MDX23C / MDXC 模型的网络架构"""
    def __init__(self, config, device):
        super().__init__()
        self.config = config
        self.device = device

        norm = get_norm(norm_type=config.model.norm)
        act = get_act(act_type=config.model.act)

        self.num_target_instruments = 1 if config.training.target_instrument else len(config.training.instruments)
        self.num_subbands = config.model.num_subbands

        dim_c = self.num_subbands * config.audio.num_channels * 2
        n = config.model.num_scales
        scale = config.model.scale
        l = config.model.num_blocks_per_scale
        c = config.model.num_channels
        g = config.model.growth
        bn = config.model.bottleneck_factor
        f = config.audio.dim_f // self.num_subbands

        self.first_conv = nn.Conv2d(dim_c, c, 1, 1, 0, bias=False)

        self.encoder_blocks = nn.ModuleList()
        for i in range(n):
            block = nn.Module()
            block.tfc_tdf = TFC_TDF(c, c, l, f, bn, norm, act)
            block.downscale = Downscale(c, c + g, scale, norm, act)
            f = f // scale[1]
            c += g
            self.encoder_blocks.append(block)

        self.bottleneck_block = TFC_TDF(c, c, l, f, bn, norm, act)

        self.decoder_blocks = nn.ModuleList()
        for i in range(n):
            block = nn.Module()
            block.upscale = Upscale(c, c - g, scale, norm, act)
            f = f * scale[1]
            c -= g
            block.tfc_tdf = TFC_TDF(2 * c, c, l, f, bn, norm, act)
            self.decoder_blocks.append(block)

        self.final_conv = nn.Sequential(
            nn.Conv2d(c + dim_c, c, 1, 1, 0, bias=False),
            act,
            nn.Conv2d(c, self.num_target_instruments * dim_c, 1, 1, 0, bias=False)
        )

        self.stft = STFT(config.audio.n_fft, config.audio.hop_length, config.audio.dim_f, self.device)

    def cac2cws(self, x):
        k = self.num_subbands
        b, c, f, t = x.shape
        x = x.reshape(b, c, k, f // k, t)
        x = x.reshape(b, c * k, f // k, t)
        return x

    def cws2cac(self, x):
        k = self.num_subbands
        b, c, f, t = x.shape
        x = x.reshape(b, c // k, k, f, t)
        x = x.reshape(b, c // k, f * k, t)
        return x

    def forward(self, x):
        x = self.stft(x)
        mix = x = self.cac2cws(x)
        first_conv_out = x = self.first_conv(x)
        x = x.transpose(-1, -2)

        encoder_outputs = []
        for block in self.encoder_blocks:
            x = block.tfc_tdf(x)
            encoder_outputs.append(x)
            x = block.downscale(x)

        x = self.bottleneck_block(x)

        for block in self.decoder_blocks:
            x = block.upscale(x)
            x = torch.cat([x, encoder_outputs.pop()], 1)
            x = block.tfc_tdf(x)

        x = x.transpose(-1, -2)
        x = x * first_conv_out  # reduce artifacts
        x = self.final_conv(torch.cat([mix, x], 1))
        x = self.cws2cac(x)

        if self.num_target_instruments > 1:
            b, c, f, t = x.shape
            x = x.reshape(b, self.num_target_instruments, -1, f, t)

        x = self.stft.inverse(x)
        return x


# ============================================================
# 旧版 MDX-Net 网络定义（ConvTDFNet，用于加载 .ckpt 格式的旧 MDX 模型）
# ============================================================

class OldTFC_TDF(nn.Module):
    """旧版 TFC_TDF，用于 ConvTDFNet"""
    def __init__(self, c, l, f, k, bn, dense=False, bias=True, norm=nn.BatchNorm2d):
        super().__init__()
        self.use_tdf = bn is not None
        if dense:
            self.tfc = DenseTFC(c, l, k, norm)
        else:
            self.tfc = OldTFC(c, l, k, norm)
        if self.use_tdf:
            if bn == 0:
                self.tdf = nn.Sequential(
                    nn.Linear(f, f, bias=bias),
                    norm(c),
                    nn.LeakyReLU()
                )
            else:
                self.tdf = nn.Sequential(
                    nn.Linear(f, f // bn, bias=bias),
                    norm(c),
                    nn.LeakyReLU(),
                    nn.Linear(f // bn, f, bias=bias),
                    norm(c),
                    nn.LeakyReLU()
                )

    def forward(self, x):
        x = self.tfc(x)
        return x + self.tdf(x) if self.use_tdf else x


class OldTFC(nn.Module):
    def __init__(self, c, l, k, norm):
        super().__init__()
        self.H = nn.ModuleList()
        for i in range(l):
            self.H.append(nn.Sequential(
                nn.Conv2d(in_channels=c, out_channels=c, kernel_size=k, stride=1, padding=k // 2),
                norm(c),
                nn.LeakyReLU(),
            ))

    def forward(self, x):
        for h in self.H:
            x = h(x)
        return x


class DenseTFC(nn.Module):
    def __init__(self, c, l, k, norm):
        super().__init__()
        self.conv = nn.ModuleList()
        for i in range(l):
            self.conv.append(nn.Sequential(
                nn.Conv2d(in_channels=c, out_channels=c, kernel_size=k, stride=1, padding=k // 2),
                norm(c),
                nn.LeakyReLU(),
            ))

    def forward(self, x):
        for layer in self.conv[:-1]:
            x = torch.cat([layer(x), x], 1)
        return self.conv[-1](x)


class ConvTDFNet(nn.Module):
    """旧版 MDX-Net 模型（ConvTDFNet）"""
    def __init__(self, dim_c, dim_f, dim_t, n_fft, hop_length, num_blocks, l, g, k, bn, bias, optimizer='rmsprop'):
        super().__init__()
        self.dim_c = dim_c
        self.dim_f = dim_f
        self.dim_t = dim_t
        self.n_fft = n_fft
        self.n_bins = n_fft // 2 + 1
        self.hop_length = hop_length
        self.num_blocks = num_blocks
        self.l = l
        self.g = g
        self.k = k
        self.bn = bn
        self.bias = bias

        if optimizer == 'rmsprop':
            norm = nn.BatchNorm2d
        else:
            norm = lambda input: nn.GroupNorm(2, input)

        self.n = num_blocks // 2
        scale = (2, 2)

        self.first_conv = nn.Sequential(
            nn.Conv2d(in_channels=dim_c, out_channels=g, kernel_size=(1, 1)),
            norm(g),
            nn.ReLU(),
        )

        f = dim_f
        c = g
        self.encoding_blocks = nn.ModuleList()
        self.ds = nn.ModuleList()
        for i in range(self.n):
            self.encoding_blocks.append(OldTFC_TDF(c, l, f, k, bn, bias=bias, norm=norm))
            self.ds.append(nn.Sequential(
                nn.Conv2d(in_channels=c, out_channels=c + g, kernel_size=scale, stride=scale),
                norm(c + g),
                nn.ReLU()
            ))
            f = f // 2
            c += g

        self.bottleneck_block = OldTFC_TDF(c, l, f, k, bn, bias=bias, norm=norm)

        self.decoding_blocks = nn.ModuleList()
        self.us = nn.ModuleList()
        for i in range(self.n):
            self.us.append(nn.Sequential(
                nn.ConvTranspose2d(in_channels=c, out_channels=c - g, kernel_size=scale, stride=scale),
                norm(c - g),
                nn.ReLU()
            ))
            f = f * 2
            c -= g
            self.decoding_blocks.append(OldTFC_TDF(c, l, f, k, bn, bias=bias, norm=norm))

        self.final_conv = nn.Sequential(
            nn.Conv2d(in_channels=c, out_channels=dim_c, kernel_size=(1, 1)),
        )

    def forward(self, x):
        x = self.first_conv(x)
        x = x.transpose(-1, -2)

        ds_outputs = []
        for i in range(self.n):
            x = self.encoding_blocks[i](x)
            ds_outputs.append(x)
            x = self.ds[i](x)

        x = self.bottleneck_block(x)

        for i in range(self.n):
            x = self.us[i](x)
            x *= ds_outputs[-i - 1]
            x = self.decoding_blocks[i](x)

        x = x.transpose(-1, -2)
        x = self.final_conv(x)
        return x


# ============================================================
# 配置加载
# ============================================================

class MDXCConfig:
    """MDXC 配置对象（从 YAML 模拟）"""
    class Audio:
        pass
    class Model:
        pass
    class Training:
        pass
    class Inference:
        pass

    def __init__(self, yaml_data):
        self.audio = self.Audio()
        self.model = self.Model()
        self.training = self.Training()
        self.inference = self.Inference()

        for k, v in yaml_data.get('audio', {}).items():
            setattr(self.audio, k, v)
        for k, v in yaml_data.get('model', {}).items():
            # scale 需要转为 tuple
            if k == 'scale' and isinstance(v, list):
                setattr(self.model, k, tuple(v))
            else:
                setattr(self.model, k, v)
        for k, v in yaml_data.get('training', {}).items():
            setattr(self.training, k, v)
        for k, v in yaml_data.get('inference', {}).items():
            setattr(self.inference, k, v)


def load_mdx_c_config(config_path):
    """加载 MDXC 的 YAML 配置文件"""
    import yaml
    with open(config_path, 'r') as f:
        yaml_data = yaml.safe_load(f)
    return MDXCConfig(yaml_data)


# ============================================================
# 模型数据映射（model_data.json → 模型参数）
# ============================================================

def _get_model_hash(filepath):
    """计算模型文件的 MD5 hash"""
    with open(filepath, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def _load_model_data(data_dir):
    """加载 model_data.json"""
    json_path = os.path.join(data_dir, "model_data.json")
    if os.path.isfile(json_path):
        with open(json_path, 'r') as f:
            return json.load(f)
    return {}


def get_mdx_params(model_path, model_data_dir):
    """获取 MDX 模型的参数"""
    model_data = _load_model_data(model_data_dir)
    model_hash = _get_model_hash(model_path)

    if model_hash in model_data:
        return model_data[model_hash]
    return None


# ============================================================
# MDXC (MDX23C) 推理
# ============================================================

def mdx_c_separate(model, config, mix_np, device="cpu", overlap=8, batch_size=1, progress_callback=None):
    """
    MDXC 模型推理

    Args:
        model: TFC_TDF_net 实例
        config: MDXCConfig 实例
        mix_np: numpy 数组, shape [2, T] 或 [T,]（立体声或单声道）
        device: torch device
        overlap: 重叠倍数
        batch_size: 批量大小
        progress_callback: 可选回调函数 f(chunk_idx, total_chunks) 用于报告进度

    Returns:
        如果是 2 stem: dict {"Vocals": np, "Instrumental": np}
        如果是 1 stem: np [2, T]
    """
    # 确保 shape 为 [2, T]（立体声，通道在前）
    if mix_np.ndim == 1:
        mix_np = np.stack([mix_np, mix_np], axis=0)
    elif mix_np.ndim == 2 and mix_np.shape[0] > 2:
        mix_np = mix_np.T
        if mix_np.shape[0] > 2:
            mix_np = mix_np[:2]

    mix = torch.tensor(np.ascontiguousarray(mix_np), dtype=torch.float32)

    try:
        S = model.num_target_instruments
    except Exception:
        S = model.module.num_target_instruments

    mdx_segment_size = config.inference.dim_t
    chunk_size = config.audio.hop_length * (mdx_segment_size - 1)

    hop_size = chunk_size // overlap
    mix_shape = mix.shape[1]
    pad_size = hop_size - (mix_shape - chunk_size) % hop_size

    # 首尾加零保证完整切分
    mix = torch.cat([
        torch.zeros(2, chunk_size - hop_size),
        mix,
        torch.zeros(2, pad_size + chunk_size - hop_size)
    ], 1)

    # 滑动窗口切块
    chunks = mix.unfold(1, chunk_size, hop_size).transpose(0, 1)
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    total_batches = len(batches)

    if S > 1:
        X = torch.zeros(S, *mix.shape, device=device)
    else:
        X = torch.zeros(*mix.shape, device=device)
    C = torch.zeros(1, mix.shape[1], device=device)

    with torch.no_grad():
        for batch_idx, batch in enumerate(batches):
            x = model(batch.to(device))
            for i, w in enumerate(x):
                global_idx = batch_idx * batch_size + i
                start = global_idx * hop_size
                end = start + w.shape[-1]

                actual_end = min(end, X.shape[-1])
                crop_len = actual_end - start

                if crop_len <= 0:
                    continue

                if S > 1:
                    X[:, :, start:actual_end] += w[:, :, :crop_len]
                else:
                    X[:, start:actual_end] += w[:, :crop_len]

                C[0, start:actual_end] += 1

            if progress_callback:
                progress_callback(batch_idx + 1, total_batches)

    # 计数器避免除零，重叠区域求平均
    C = C.clamp(min=1)
    if S > 1:
        X = X / C.unsqueeze(0).unsqueeze(0)
    else:
        X = X / C

    # 去掉首尾 padding
    offset = chunk_size - hop_size
    end_offset = pad_size + chunk_size - hop_size
    estimated_sources = X[..., offset:-end_offset] if end_offset > 0 else X[..., offset:]
    del X
    del C

    if S > 1:
        est_np = estimated_sources.cpu().detach().numpy()
        sources = {k: v for k, v in zip(config.training.instruments, est_np)}
        del estimated_sources
        return sources
    else:
        est = estimated_sources.cpu().detach().numpy()
        del estimated_sources
        return est


# ============================================================
# 旧版 MDX ONNX 推理
# ============================================================

def mdx_onnx_separate(session, mix_np, model_params, device="cpu"):
    """
    旧版 MDX-Net ONNX 推理

    Args:
        session: onnxruntime InferenceSession
        mix_np: numpy 数组 [2, T]
        model_params: dict（从 model_data.json 获取）

    Returns:
        np [1, 2, T] 分离后的 stem
    """
    import onnxruntime as ort

    n_fft = model_params.get("mdx_n_fft_scale_set", 6144)
    dim_f = model_params.get("mdx_dim_f_set", 2048)
    dim_t = model_params.get("mdx_dim_t_set", 8)
    hop = n_fft // (dim_t * 2)
    overlap = 0.25

    # 转为 PyTorch STFT
    stft = STFT(n_fft, hop, dim_f, device)

    # 配置
    trim = n_fft // 2
    chunk_size = hop * (dim_t - 1)
    gen_size = chunk_size - 2 * trim

    # padding
    pad = gen_size + trim - ((mix_np.shape[-1]) % gen_size)
    mixture = np.concatenate((
        np.zeros((2, trim), dtype='float32'),
        mix_np,
        np.zeros((2, pad), dtype='float32')
    ), 1)

    step = chunk_size - n_fft
    result = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)
    divider = np.zeros((1, 2, mixture.shape[-1]), dtype=np.float32)

    ort_in_name = session.get_inputs()[0].name

    for i in range(0, mixture.shape[-1], step):
        start = i
        end = min(i + chunk_size, mixture.shape[-1])
        chunk_size_actual = end - start

        window = np.hanning(chunk_size_actual)
        window = np.tile(window[None, None, :], (1, 2, 1))

        mix_part = mixture[:, start:end]
        if end != i + chunk_size:
            pad_size = (i + chunk_size) - end
            mix_part = np.concatenate((mix_part, np.zeros((2, pad_size), dtype='float32')), axis=-1)

        mix_tensor = torch.tensor([mix_part], dtype=torch.float32)

        with torch.no_grad():
            spek = stft(mix_tensor)
            spek_np = spek.cpu().numpy()
            # 压制极低频
            spek_np[:, :, :3, :] *= 0
            pred = session.run(None, {ort_in_name: spek_np})[0]
            tar_waves = stft.inverse(torch.tensor(pred, dtype=torch.float32)).cpu().detach().numpy()

        tar_waves[..., :chunk_size_actual] *= window
        divider[..., start:end] += window
        result[..., start:end] += tar_waves[..., :end - start]

    tar_waves = result / np.maximum(divider, 1)
    tar_waves = tar_waves[:, :, trim:-trim]
    tar_waves = tar_waves[:, :, :mix_np.shape[-1]]

    return tar_waves
