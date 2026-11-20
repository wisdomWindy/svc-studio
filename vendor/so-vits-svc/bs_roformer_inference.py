"""
BS-Roformer / MelBandRoformer 推理模块
基于 MDX23-v2-2.5-barbara 项目的推理逻辑，适配 So-VITS-SVC 整合包
支持模型类型：
  - BS-RoFormer: model_bs_roformer_ep_368_sdr_12.9628.ckpt
  - MelBandRoFormer: Kim_MelRoformer.ckpt
"""

import os
import sys
import yaml
import numpy as np
import torch
import torch.nn as nn
import soundfile as sf
from pathlib import Path

from audio_decode import load_audio_stereo_at_sr


def load_bs_roformer_config(config_path):
    """加载 BS-Roformer YAML 配置"""
    from ml_collections import ConfigDict
    with open(config_path) as f:
        config = ConfigDict(yaml.load(f, Loader=yaml.FullLoader))
    return config


def build_bs_roformer_model(model_type, config):
    """根据模型类型构建网络"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if model_type == 'bs_roformer':
        from bs_roformer_module.bs_roformer.bs_roformer import BSRoformer
        model = BSRoformer(**dict(config.model))
    elif model_type == 'mel_band_roformer':
        from bs_roformer_module.bs_roformer.mel_band_roformer import MelBandRoformer
        model = MelBandRoformer(**dict(config.model))
    else:
        raise ValueError(f"未知模型类型: {model_type}")
    return model


def detect_model_type(model_name):
    """根据文件名检测模型类型"""
    name_lower = model_name.lower()
    if 'mel_roformer' in name_lower or 'melband' in name_lower:
        return 'mel_band_roformer'
    elif 'bs_roformer' in name_lower:
        return 'bs_roformer'
    # 默认用 bs_roformer
    return 'bs_roformer'


def find_config_for_model(model_name, mdx_data_dir):
    """根据模型文件名查找对应的 YAML 配置"""
    configs_dir = os.path.join(mdx_data_dir, "mdx_c_configs")
    stem = os.path.splitext(model_name)[0]  # e.g. model_bs_roformer_ep_368_sdr_12.9628

    # 1. 精确匹配同名 yaml
    yaml_path = os.path.join(configs_dir, stem + ".yaml")
    if os.path.isfile(yaml_path):
        return yaml_path

    # 2. 查找 configs_dir 下所有 yaml，匹配模型名关键词
    if os.path.isdir(configs_dir):
        for f in os.listdir(configs_dir):
            if not f.endswith(".yaml"):
                continue
            # Kim_MelRoformer.ckpt -> Kim_MelRoformer.yaml
            config_stem = os.path.splitext(f)[0]
            if config_stem.lower() in stem.lower() or stem.lower() in config_stem.lower():
                return os.path.join(configs_dir, f)

    return None


def demix_roformer(model, mix, device, config, overlap=2):
    """
    BS-Roformer 推理核心函数
    mix: numpy array, shape (2, T) 立体声
    返回: dict {stem_name: numpy_array}
    """
    mix = torch.tensor(mix, dtype=torch.float32)
    dim_t = config.inference.dim_t if hasattr(config.inference, 'dim_t') else 801
    N = overlap  # overlap factor, 2 = 50% overlap
    batch_size = 1

    C = config.audio.hop_length * (dim_t - 1)
    fade_size = C // 100
    step = int(C // N)
    border = C - step
    length_init = mix.shape[-1]

    # Reflect pad
    if length_init > 2 * border and (border > 0):
        mix = nn.functional.pad(mix, (border, border), mode='reflect')

    # Window functions for smooth stitching
    window_size = C
    fadein = torch.linspace(0, 1, fade_size)
    fadeout = torch.linspace(1, 0, fade_size)
    window_start = torch.ones(window_size)
    window_middle = torch.ones(window_size)
    window_finish = torch.ones(window_size)
    window_start[-fade_size:] *= fadeout
    window_finish[:fade_size] *= fadein
    window_middle[-fade_size:] *= fadeout
    window_middle[:fade_size] *= fadein

    with torch.cuda.amp.autocast():
        with torch.inference_mode():
            if config.training.target_instrument is not None:
                req_shape = (1,) + tuple(mix.shape)
            else:
                req_shape = (len(config.training.instruments),) + tuple(mix.shape)

            result = torch.zeros(req_shape, dtype=torch.float32)
            counter = torch.zeros(req_shape, dtype=torch.float32)
            i = 0
            batch_data = []
            batch_locations = []

            while i < mix.shape[1]:
                part = mix[:, i:i + C].to(device)
                length = part.shape[-1]
                if length < C:
                    if length > C // 2 + 1:
                        part = nn.functional.pad(input=part, pad=(0, C - length), mode='reflect')
                    else:
                        part = nn.functional.pad(input=part, pad=(0, C - length, 0, 0), mode='constant', value=0)
                batch_data.append(part)
                batch_locations.append((i, length))
                i += step

                if len(batch_data) >= batch_size or (i >= mix.shape[1]):
                    arr = torch.stack(batch_data, dim=0)
                    x = model(arr)

                    window = window_middle
                    if i - step == 0:
                        window = window_start
                    elif i >= mix.shape[1]:
                        window = window_finish

                    for j in range(len(batch_locations)):
                        start, l = batch_locations[j]
                        result[..., start:start + l] += x[j][..., :l].cpu() * window[..., :l]
                        counter[..., start:start + l] += window[..., :l]

                    batch_data = []
                    batch_locations = []

            estimated_sources = result / counter
            estimated_sources = estimated_sources.cpu().numpy()
            np.nan_to_num(estimated_sources, copy=False, nan=0.0)

            if length_init > 2 * border and (border > 0):
                estimated_sources = estimated_sources[..., border:-border]

    if config.training.target_instrument is None:
        return {k: v for k, v in zip(config.training.instruments, estimated_sources)}
    else:
        return {k: v for k, v in zip([config.training.target_instrument], estimated_sources)}


def bs_roformer_separate(audio_path, model_name, gpu_id=0, output_dir=None):
    """
    BS-Roformer 分离入口
    audio_path: 输入音频路径
    model_name: 模型文件名（不含扩展名），如 model_bs_roformer_ep_368_sdr_12.9628
    gpu_id: GPU 编号，-1 为 CPU
    返回: (output_files, message)
    """
    if not audio_path or not os.path.isfile(audio_path):
        return [], "请先上传音频文件"

    gpu_id = int(gpu_id) if gpu_id is not None else 0
    base_dir = os.path.dirname(os.path.abspath(__file__))
    model_dir = os.path.join(base_dir, "uvr5_models")
    mdx_data_dir = os.path.join(base_dir, "mdx_model_data")

    # 查找模型文件（在 uvr5_models/ 及子文件夹中递归搜索）
    model_file = None
    for ext in [".ckpt"]:
        # 先查根目录
        candidate = os.path.join(model_dir, model_name + ext)
        if os.path.isfile(candidate):
            model_file = candidate
            break
        # 再递归查子文件夹
        if os.path.isdir(model_dir):
            for sub_dir in os.listdir(model_dir):
                sub_path = os.path.join(model_dir, sub_dir)
                if os.path.isdir(sub_path):
                    candidate = os.path.join(sub_path, model_name + ext)
                    if os.path.isfile(candidate):
                        model_file = candidate
                        break
        if model_file:
            break

    if not model_file:
        return [], f"找不到模型文件: {model_name}（在 uvr5_models/ 及其子文件夹中）"

    # 设备
    device = torch.device(f"cuda:{gpu_id}" if gpu_id >= 0 and torch.cuda.is_available() else "cpu")

    # 检测模型类型
    model_type = detect_model_type(model_name)
    print(f"[BS-Roformer] 模型类型: {model_type}, 文件: {model_file}")

    try:
        # 查找配置
        config_path = find_config_for_model(model_name, mdx_data_dir)
        if not config_path:
            return [], f"找不到模型配置文件（需在 mdx_model_data/mdx_c_configs/ 下放置同名 .yaml）"

        print(f"[BS-Roformer] 加载配置: {config_path}")
        config = load_bs_roformer_config(config_path)

        # 构建并加载模型
        print(f"[BS-Roformer] 构建模型: {model_type}")
        model = build_bs_roformer_model(model_type, config)
        print(f"[BS-Roformer] 加载权重: {model_file}")
        model.load_state_dict(torch.load(model_file, map_location='cpu'))
        model = model.to(device)
        model.eval()

        # 读取音频（FFmpeg 优先，兼容 MP3/M4A/FLAC/WAV 等）
        target_sr = config.audio.sample_rate if hasattr(config.audio, 'sample_rate') else 44100
        target_sr = int(round(float(target_sr)))
        audio, sr = load_audio_stereo_at_sr(audio_path, target_sr)
        if sr != target_sr:
            print(f"[BS-Roformer] 重采样: {sr} -> {target_sr}")
            from scipy import signal
            audio = signal.resample_poly(audio, target_sr, sr, axis=1)

        print(f"[BS-Roformer] 开始推理... shape={audio.shape}")

        # 推理
        overlap = config.inference.num_overlap if hasattr(config.inference, 'num_overlap') else 4
        sources = demix_roformer(model, audio, device, config, overlap=overlap)

        # 输出目录
        if output_dir is None:
            output_dir = os.path.join(base_dir, "separated", model_name)
        os.makedirs(output_dir, exist_ok=True)

        audio_name = Path(audio_path).stem
        output_files = []

        for stem_name, stem_audio in sources.items():
            # (1, 2, T) 或 (2, T) -> (T, 2)
            if stem_audio.ndim == 3:
                stem_audio = stem_audio[0]
            stem_audio = stem_audio.T  # (2, T) -> (T, 2)

            # 截断到原始长度
            original_len = sf.info(audio_path).frames
            if stem_audio.shape[0] > original_len:
                stem_audio = stem_audio[:original_len]

            output_path = os.path.join(output_dir, f"{audio_name}_{stem_name}.wav")
            sf.write(output_path, stem_audio, target_sr, subtype='PCM_16')
            output_files.append(output_path)
            print(f"[BS-Roformer] 输出: {output_path}")

        # 释放模型
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        if output_files:
            msg = f"分离完成！输出 {len(output_files)} 个文件:\n" + "\n".join(
                os.path.basename(f) for f in output_files
            )
            return output_files, msg
        else:
            return [], "分离完成但未生成输出文件"

    except Exception as e:
        import traceback
        traceback.print_exc()
        return [], f"BS-Roformer 分离出错: {str(e)}"
