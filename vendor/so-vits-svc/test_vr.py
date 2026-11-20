import sys, os, traceback
os.environ['PYTHONHOME'] = r'D:\50-so-vits-svc\50SVC\workenv'
os.environ['PYTHONPATH'] = r'D:\50-so-vits-svc\50SVC\workenv\Lib\site-packages'
sys.path.insert(0, r'D:\50-so-vits-svc\50SVC\uvr5_pack')

import torch, numpy as np, librosa
from lib_v5.nets_new import CascadedNet
from lib_v5 import spec_utils
from lib_v5.model_param_init import ModelParameters
from utils import inference

model_path = r'D:\50-so-vits-svc\50SVC\uvr5_models\去混响\UVR-DeEcho-DeReverb.pth'
mp_file = r'D:\50-so-vits-svc\50SVC\uvr5_pack\lib_v5\modelparams\4band_v3.json'

# 找一个测试 wav 文件
test_dir = r'D:\50-so-vits-svc\50SVC\separated'
audio_path = None
for root, dirs, files in os.walk(test_dir):
    for f in files:
        if f.endswith('.wav') and 'vocal' in f.lower():
            audio_path = os.path.join(root, f)
            break
    if audio_path:
        break

if not audio_path:
    print("找不到测试音频文件")
    sys.exit(1)

print(f"测试文件: {audio_path}")

try:
    mp = ModelParameters(mp_file)
    print(f"ModelParameters ok, bands: {len(mp.param['band'])}")

    bins = mp.param['bins'] * 2
    m = CascadedNet(bins, 64)
    cpk = torch.load(model_path, map_location='cpu')
    m.load_state_dict(cpk)
    m.eval()
    m.to('cuda:0')
    print("模型加载成功")

    sr = mp.param["band"][len(mp.param["band"])]["sr"]
    print(f"采样率: {sr}")

    X_wave, _ = librosa.core.load(audio_path, sr, False, dtype=np.float32, res_type=mp.param["band"][len(mp.param["band"])]["res_type"])
    print(f"音频加载: shape={X_wave.shape}")
    if X_wave.ndim == 1:
        X_wave = np.asfortranarray([X_wave, X_wave])

    bands_n = len(mp.param["band"])
    X_spec_s = {}
    wav_next = None
    for d in range(bands_n, 0, -1):
        bp = mp.param["band"][d]
        if d == bands_n:
            wav_d = X_wave
        else:
            wav_d = librosa.core.resample(wav_next, mp.param["band"][d+1]["sr"], bp["sr"], res_type=bp["res_type"])
        wav_next = wav_d
        X_spec_s[d] = spec_utils.wave_to_spectrogram_mt(wav_d, bp["hl"], bp["n_fft"], mp.param["mid_side"], mp.param["mid_side_b2"], mp.param["reverse"])
        print(f"  band {d}: {X_spec_s[d].shape}")

    X_spec_m = spec_utils.combine_spectrograms(X_spec_s, mp)
    print(f"频谱合并: {X_spec_m.shape}")

    aggressiveness = {"value": 0.2, "split_bin": mp.param["band"][1]["crop_stop"]}
    data = {"postprocess": False, "tta": False, "window_size": 512, "agg": 20, "high_end_process": "mirroring"}

    with torch.no_grad():
        pred, X_mag, X_phase = inference(X_spec_m, 'cuda:0', m, aggressiveness, data)
    print(f"推理完成: pred shape={pred.shape}")
    print("VR DeEcho 测试通过！")

except Exception as e:
    traceback.print_exc()
    print(f"错误: {e}")
