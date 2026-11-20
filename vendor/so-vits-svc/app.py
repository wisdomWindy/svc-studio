import ast
import base64
import datetime
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
import warnings
import zipfile

# 屏蔽所有warning，防止Gradio queue模式将warning转发为前端error
warnings.filterwarnings("ignore")
from itertools import chain
from pathlib import Path

# 确保 cwd 是脚本所在目录，防止 os.system 启动后 cwd 漂移
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import gradio as gr
import librosa
import numpy as np
import soundfile as sf
from audio_decode import ffmpeg_binary, load_audio_stereo_at_sr
import torch
import yaml

from compress_model import removeOptimizer
from fap.utils.file import AUDIO_EXTENSIONS, list_files
from fap.utils.slice_audio_v2 import slice_audio_file_v2
from fap.cli.length import length
from inference.infer_tool_webui import Svc
from onnx_export import main as onnx_export

from tts_voices import SUPPORTED_LANGUAGES
from utils import mix_model

os.environ["PATH"] += os.pathsep + os.path.join(os.getcwd(), "ffmpeg", "bin")

logging.getLogger('numba').setLevel(logging.WARNING)
logging.getLogger('markdown_it').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('matplotlib').setLevel(logging.WARNING)

INTEGRITY_LOG_PATH = "integrity.log"
OFFICIAL_BACKUP_DIR = "_official_backup"
OFFICIAL_APP_BACKUP = os.path.join(OFFICIAL_BACKUP_DIR, "app.py")

INTEGRITY_ANCHORS = {
    "guide_btn_label": "user_guide_btn = gr.Button(\"使用指南（点击跳转）\", variant=\"primary\", size=\"sm\", elem_id=\"user_guide_btn\")",
    "guide_btn_bind": "user_guide_btn.click(open_user_guide, inputs=[], outputs=None)",
    "guide_url_check": "https://acnqmq094zu5.feishu.cn/wiki/",
}
INTEGRITY_EXPECTED = {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in INTEGRITY_ANCHORS.items()}

def _append_integrity_log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(INTEGRITY_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

def _ensure_official_backup():
    """首次运行自动备份官方 app.py，用于一键恢复。"""
    try:
        if not os.path.exists(OFFICIAL_BACKUP_DIR):
            os.makedirs(OFFICIAL_BACKUP_DIR, exist_ok=True)
        if not os.path.exists(OFFICIAL_APP_BACKUP):
            shutil.copy2(os.path.abspath(__file__), OFFICIAL_APP_BACKUP)
    except Exception as e:
        _append_integrity_log(f"创建官方备份失败: {e}")

def check_integrity():
    """校验关键片段是否被篡改。"""
    try:
        with open(os.path.abspath(__file__), "r", encoding="utf-8") as f:
            code_text = f.read().replace("\r\n", "\n")
        for key, anchor in INTEGRITY_ANCHORS.items():
            if anchor not in code_text:
                return False, f"缺失关键片段: {key}"
            current_hash = hashlib.sha256(anchor.encode("utf-8")).hexdigest()
            if current_hash != INTEGRITY_EXPECTED[key]:
                return False, f"关键片段校验失败: {key}"
        return True, "ok"
    except Exception as e:
        return False, f"完整性校验异常: {e}"

def restore_official_files():
    """恢复官方 app.py 备份，不做破坏性删除。"""
    try:
        if not os.path.exists(OFFICIAL_APP_BACKUP):
            return "未找到官方备份文件，请重新覆盖官方整合包后再试。"
        shutil.copy2(OFFICIAL_APP_BACKUP, os.path.abspath(__file__))
        _append_integrity_log("已执行恢复官方文件操作")
        return "已恢复官方文件（app.py），请重启 WebUI 生效。"
    except Exception as e:
        _append_integrity_log(f"恢复官方文件失败: {e}")
        return f"恢复失败: {e}"

_ensure_official_backup()
INTEGRITY_OK, INTEGRITY_MSG = check_integrity()
if not INTEGRITY_OK:
    _append_integrity_log(f"完整性校验失败: {INTEGRITY_MSG}")

# Some directories
workdir = "logs/44k"
second_dir = "models"
diff_second_dir = "models/diffusion"
diff_workdir = "logs/44k/diffusion"
config_dir = "configs/"
dataset_dir = "dataset/44k"
raw_path = "dataset_raw"
raw_wavs_path = "raw"
# 无模型时的兜底（正常推理已加载 model，以 model.target_sample 为准）
_VC_INFER_TARGET_SR_FALLBACK = 44100


def _normalize_input_wav_for_vc(mono_f32, sr: int, target_sr: int):
    """
    单声道 float32；与当前加载模型的 target_sample 对齐（通常等于 config 的 data.sampling_rate）。
    仅当 sr != target_sr 时重采样，避免多绕一圈、并与切片/推理采样率一致。
    """
    if sr == target_sr:
        return np.asarray(mono_f32, dtype=np.float32), target_sr
    y = librosa.resample(
        np.asarray(mono_f32, dtype=np.float32),
        orig_sr=sr,
        target_sr=target_sr,
    )
    return y.astype(np.float32, copy=False), target_sr


models_backup_path = 'models_backup'
root_dir = "checkpoints"
default_settings_file = "settings.yaml"
current_mode = ""
# Some global variables
debug = True
precheck_ok = False
model = None
sovits_params = {}
diff_params = {}
# Some dicts for mapping
MODEL_TYPE = {
    "vec768l12": 768,
    "vec256l9": 256,
    "hubertsoft": 256,
    "whisper-ppg": 1024,
    "cnhubertlarge": 1024,
    "dphubert": 768,
    "wavlmbase+": 768,
    "whisper-ppg-large": 1280
}
ENCODER_PRETRAIN = {
    "vec256l9": "pretrain/checkpoint_best_legacy_500.pt",
    "vec768l12": "pretrain/checkpoint_best_legacy_500.pt",
    "hubertsoft": "pretrain/hubert-soft-0d54a1f4.pt",
    "whisper-ppg": "pretrain/medium.pt",
    "cnhubertlarge": "pretrain/chinese-hubert-large-fairseq-ckpt.pt",
    "dphubert": "pretrain/DPHuBERT-sp0.75.pth",
    "wavlmbase+": "pretrain/WavLM-Base+.pt",
    "whisper-ppg-large": "pretrain/large-v2.pt"
}


class Config:
    def __init__(self, path, type):
        self.path = path
        self.type = type
    
    def read(self):
        if self.type == "json":
            with open(self.path, 'r') as f:
                return json.load(f)
        if self.type == "yaml":
            with open(self.path, 'r') as f:
                return yaml.safe_load(f)
    
    def save(self, content):
        if self.type == "json":
            with open(self.path, 'w') as f:
                json.dump(content, f, indent=4)
        if self.type == "yaml":
            with open(self.path, 'w') as f:
                yaml.safe_dump(content, f, default_flow_style=False, sort_keys=False)


class ReleasePacker:
    def __init__(self, speaker, model):
        self.speaker = speaker
        self.model = model
        self.output_path = os.path.join("release_packs", f"{speaker}_release.zip")
        self.file_list = []

    def remove_temp(self, path):
        for filename in os.listdir(path):
            file_path = os.path.join(path, filename)
            if os.path.isfile(file_path) and not filename.endswith(".zip"):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path, ignore_errors=True)

    def add_file(self, file_paths):
        self.file_list.extend(file_paths)
    
    def spk_to_dict(self):
        spk_string = self.speaker.replace('，', ',')
        spk_string = spk_string.replace(' ', '')
        _spk = spk_string.split(',')
        return {_spk: index for index, _spk in enumerate(_spk)}

    def generate_config(self, diff_model, config_origin):
        _config_origin = Config(os.path.join(config_read_dir, config_origin), "json")
        _template = Config("release_packs/config_template.json", "json")
        _d_template = Config("release_packs/diffusion_template.yaml", "yaml")
        orig_config = _config_origin.read()
        config_template = _template.read()
        diff_config_template = _d_template.read()
        spk_dict = self.spk_to_dict()
        _net = torch.load(os.path.join(ckpt_read_dir, self.model), map_location='cpu')
        emb_dim, model_dim = _net['model'].get('emb_g.weight', torch.empty(0, 0)).size()
        vol_emb = _net['model'].get('emb_vol.weight')
        if vol_emb is not None:
            config_template["train"]["vol_aug"] = config_template["model"]["vol_embedding"] = True
        #Keep the spk_dict length same as emb_dim
        if emb_dim > len(spk_dict):
            for i in range(emb_dim - len(spk_dict)):
                spk_dict[f"spk{i}"] = len(spk_dict)
        if emb_dim < len(spk_dict):
            for i in range(len(spk_dict) - emb_dim):
                spk_dict.popitem()
        self.speaker = ','.join(spk_dict.keys())
        config_template['model']['ssl_dim'] = config_template["model"]["filter_channels"] = config_template["model"]["gin_channels"] = model_dim
        config_template['model']['n_speakers'] = diff_config_template['model']['n_spk'] = emb_dim
        config_template['spk'] = diff_config_template['spk'] = spk_dict
        encoder = [k for k, v in MODEL_TYPE.items() if v == model_dim]
        if orig_config['model']['speech_encoder'] in encoder:
            config_template['model']['speech_encoder'] = orig_config['model']['speech_encoder']
        else:
            raise Exception("Config is not compatible with the model")
        
        if diff_model != "no_diff":
            _diff = torch.load(os.path.join(diff_read_dir, diff_model), map_location='cpu')
            _, diff_dim = _diff["model"].get("unit_embed.weight", torch.empty(0, 0)).size()
            if diff_dim == 256:
                diff_config_template['data']['encoder'] = 'hubertsoft'
                diff_config_template['data']['encoder_out_channels'] = 256
            elif diff_dim == 768:
                diff_config_template['data']['encoder'] = 'vec768l12'
                diff_config_template['data']['encoder_out_channels'] = 768
            elif diff_dim == 1024:
                diff_config_template['data']['encoder'] = 'whisper-ppg'
                diff_config_template['data']['encoder_out_channels'] = 1024

        with open("release_packs/install.txt", 'w') as f:
            f.write(str(self.file_list) + '#' + str(self.speaker))

        _template.save(config_template)
        _d_template.save(diff_config_template)

    def unpack(self, zip_file):
        with zipfile.ZipFile(zip_file, 'r') as zipf:
            zipf.extractall("release_packs")

    def formatted_install(self, install_txt):
        with open(install_txt, 'r') as f:
            content = f.read()
        file_list, speaker = content.split('#')
        self.speaker = speaker
        file_list = ast.literal_eval(file_list)
        self.file_list = file_list
        for _, target_path in self.file_list:
            if target_path != "install.txt" and target_path != "":
                shutil.move(os.path.join("release_packs", target_path), target_path)
        self.remove_temp("release_packs")
        return self.speaker

    def pack(self):
        with zipfile.ZipFile(self.output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path, target_path in self.file_list:
                if os.path.isfile(file_path):
                    zipf.write(file_path, arcname=target_path)


def debug_change():
    global debug
    debug = debug_button.value

def get_default_settings():
    global sovits_params, diff_params, second_dir_enable
    config_file = Config(default_settings_file, "yaml")
    default_settings = config_file.read()
    sovits_params = default_settings['sovits_params']
    diff_params = default_settings['diff_params']
    webui_settings = default_settings['webui_settings']
    second_dir_enable = webui_settings['second_dir']
    return sovits_params, diff_params, second_dir_enable

def webui_change(model_dir_mode_str):
    global ckpt_read_dir, config_read_dir, diff_read_dir, second_dir_enable
    read_second_dir = model_dir_mode_str == "独立目录（./models）"
    config_file = Config(default_settings_file, "yaml")
    default_settings = config_file.read()
    second_dir_enable = default_settings['webui_settings']['second_dir'] = read_second_dir
    config_file.save(default_settings)
    ckpt_read_dir = second_dir if second_dir_enable else workdir
    config_read_dir = second_dir if second_dir_enable else config_dir
    diff_read_dir = diff_second_dir if second_dir_enable else diff_workdir
    ckpt_list, config_list, cluster_list, diff_list, diff_config_list = load_options()
    return (
        choice_ckpt.update(choices=ckpt_list, value="no_model"),
        cluster_choice.update(choices=cluster_list, value="no_clu"),
        diff_choice.update(choices=diff_list, value="no_diff"),
        diff_config_choice.update(choices=diff_config_list, value="no_diff_config"),
    )

def get_current_mode():
    global current_mode
    current_mode = "当前模式：独立目录模式，将从'./models/'读取模型文件" if second_dir_enable else "当前模式：工作目录模式，将从'./logs/44k'读取模型文件" 
    return current_mode

def _integrity_block_message():
    return "完整性校验失败，请使用官方版本"

def open_user_guide():
    """打开使用指南（分段编码 + 域名校验，避免误改）。"""
    b64_parts = [
        "aHR0cHM6Ly9hY25xbXEwOTR6dTUu",
        "ZmVpc2h1LmNuL3dpa2kvR2tGWXdCY2RHaWZrVnprZU1GRmNxbVJJbmRiP2Zyb209ZnJvbV9jb3B5bGluaw==",
    ]
    try:
        url = base64.b64decode("".join(b64_parts)).decode("utf-8")
    except Exception:
        return
    if not url.startswith("https://acnqmq094zu5.feishu.cn/wiki/"):
        return
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url])
    except Exception as e:
        print(f"打开使用指南失败：{e}")

def save_default_settings(log_interval,eval_interval,keep_ckpts,batch_size,learning_rate,amp_dtype,all_in_mem,num_workers,cache_all_data,cache_device,diff_amp_dtype,diff_batch_size,diff_lr,diff_interval_log,diff_interval_val,diff_force_save,diff_k_step_max):
    config_file = Config(default_settings_file, "yaml")
    default_settings = config_file.read()
    default_settings['sovits_params']['log_interval'] = int(log_interval)
    default_settings['sovits_params']['eval_interval'] = int(eval_interval)
    default_settings['sovits_params']['keep_ckpts'] = int(keep_ckpts)
    default_settings['sovits_params']['batch_size'] = int(batch_size)
    default_settings['sovits_params']['learning_rate'] = float(learning_rate)
    default_settings['sovits_params']['amp_dtype'] = str(amp_dtype)
    default_settings['sovits_params']['all_in_mem'] = all_in_mem
    default_settings['diff_params']['num_workers'] = int(num_workers)
    default_settings['diff_params']['cache_all_data'] = cache_all_data
    default_settings['diff_params']['cache_device'] = str(cache_device)
    default_settings['diff_params']['amp_dtype'] = str(diff_amp_dtype)
    default_settings['diff_params']['diff_batch_size'] = int(diff_batch_size)
    default_settings['diff_params']['diff_lr'] = float(diff_lr)
    default_settings['diff_params']['diff_interval_log'] = int(diff_interval_log)
    default_settings['diff_params']['diff_interval_val'] = int(diff_interval_val)
    default_settings['diff_params']['diff_force_save'] = int(diff_force_save)
    default_settings['diff_params']['diff_k_step_max'] = diff_k_step_max
    config_file.save(default_settings)
    return "成功保存默认配置"

def get_model_info(choice_ckpt):
    if not choice_ckpt or choice_ckpt == "no_model":
        return "请先选择模型"
    pthfile = os.path.join(ckpt_read_dir, choice_ckpt)
    net = torch.load(pthfile, map_location=torch.device('cpu')) #cpu load to avoid using gpu memory
    spk_emb = net["model"].get("emb_g.weight")
    if spk_emb is None:
        return "所选模型缺少emb_g.weight，你可能选择了一个底模"
    _layer = spk_emb.size(1)
    encoder = [k for k, v in MODEL_TYPE.items() if v == _layer] #通过维度对应编码器
    encoder.sort()
    if encoder == ["hubertsoft", "vec256l9"]:
        encoder = ["vec256l9 / hubertsoft"]
    if encoder == ["cnhubertlarge", "whisper-ppg"]:
        encoder = ["whisper-ppg / cnhubertlarge"]
    if encoder == ["dphubert", "vec768l12", "wavlmbase+"]:
        encoder = ["vec768l12 / dphubert / wavlmbase+"]
    return encoder[0]
    
def load_json_encoder(config_choice, choice_ckpt):
    if config_choice == "no_config":
        return "未启用自动加载，请手动选择配置文件"
    if choice_ckpt == "no_model":
        return "请先选择模型"
    config_file = Config(os.path.join(config_read_dir, config_choice), "json")
    config = config_file.read()
    try:
        #比对配置文件中的模型维度与该encoder的实际维度是否对应，防止古神语
        config_encoder = config["model"].get("speech_encoder", "no_encoder")
        config_dim = config["model"]["ssl_dim"]
        #旧版配置文件自动匹配
        if config_encoder == "no_encoder":
            config_encoder = config["model"]["speech_encoder"] = "vec256l9" if config_dim == 256 else "vec768l12"
            config_file.save(config)
        correct_dim = MODEL_TYPE.get(config_encoder, "unknown")
        if config_dim != correct_dim:
            return "配置文件中的编码器与模型维度不匹配"
        return config_encoder
    except Exception as e:
        return f"出错了: {e}"
        
def auto_load(choice_ckpt):
    global second_dir_enable
    model_output_msg = get_model_info(choice_ckpt)
    json_output_msg = config_choice = ""
    choice_ckpt_name, _ = os.path.splitext(choice_ckpt)
    if second_dir_enable:
        all_config = [json for json in os.listdir(second_dir) if json.endswith(".json")]
        for config in all_config:
            config_fname, _ = os.path.splitext(config)
            if config_fname == choice_ckpt_name:
                config_choice = config
                json_output_msg = load_json_encoder(config, choice_ckpt)
        if json_output_msg != "":
            return model_output_msg, config_choice, json_output_msg
        else:
            return model_output_msg, "no_config", ""
    else:
        return model_output_msg, "no_config", ""
    
def auto_load_diff(diff_model):
    global second_dir_enable
    if second_dir_enable is False:
        return "no_diff_config"
    all_diff_config = [yaml for yaml in os.listdir(second_dir) if yaml.endswith(".yaml")]
    for config in all_diff_config:
        config_fname, _ = os.path.splitext(config)
        diff_fname, _ = os.path.splitext(diff_model)
        if config_fname == diff_fname:
            return config
    return "no_diff_config"
        
def load_model_func(ckpt_name,cluster_name,config_name,enhance,diff_model_name,diff_config_name,only_diffusion,use_spk_mix,using_device,method,speedup,cl_num,vocoder_name):
    global model
    if not INTEGRITY_OK:
        return _integrity_block_message(), gr.Dropdown.update(choices=[], value=""), 0, None
    config_path = os.path.join(config_read_dir, config_name) if not only_diffusion else "configs/config.json"
    diff_config_path = os.path.join(config_read_dir, diff_config_name) if diff_config_name != "no_diff_config" else "configs/diffusion.yaml"
    ckpt_path = os.path.join(ckpt_read_dir, ckpt_name)
    cluster_path = os.path.join(ckpt_read_dir, cluster_name)
    diff_model_path = os.path.join(diff_read_dir, diff_model_name)
    k_step_max = 1000

    if not only_diffusion:
        # 处理 no_config 场景：优先同名 json，其次默认 configs/config.json
        if not config_name or config_name == "no_config":
            ckpt_base, _ = os.path.splitext(ckpt_name)
            guessed_config = os.path.join(config_read_dir, f"{ckpt_base}.json")
            fallback_config = os.path.join(config_dir, "config.json")
            if os.path.exists(guessed_config):
                config_path = guessed_config
            elif os.path.exists(fallback_config):
                config_path = fallback_config
            else:
                return "未找到可用配置文件：请在模型同目录放置同名 .json，或提供 configs/config.json", gr.Dropdown.update(choices=[], value=""), 0, None
        elif not os.path.exists(config_path):
            fallback_config = os.path.join(config_dir, "config.json")
            if os.path.exists(fallback_config):
                config_path = fallback_config
            else:
                return f"配置文件不存在：{config_path}", gr.Dropdown.update(choices=[], value=""), 0, None

    if not only_diffusion:
        config = Config(config_path, "json").read()
    if diff_model_name != "no_diff":
        _diff = Config(diff_config_path, "yaml")
        _content = _diff.read()
        diff_spk = _content.get('spk', {})
        diff_spk_choice = spk_choice = next(iter(diff_spk), "未检测到音色")
        if not only_diffusion:
            if _content['data'].get('encoder_out_channels') != config["model"].get('ssl_dim'):
                return "扩散模型维度与主模型不匹配，请确保两个模型使用的是同一个编码器", gr.Dropdown.update(choices=[], value=""), 0, None
        _content["infer"]["speedup"] = int(speedup)
        _content["infer"]["method"] = str(method)
        _content["vocoder"]["ckpt"] = f"pretrain/{vocoder_name}/model"
        k_step_max = _content["model"].get('k_step_max', 0) if _content["model"].get('k_step_max', 0) != 0 else 1000
        _diff.save(_content)
    if not only_diffusion:
        net = torch.load(ckpt_path, map_location=torch.device('cpu'))
    #读取模型各维度并比对，还有小可爱无视提示硬要加载底模的就返回个未初始张量
        emb_dim, model_dim = net["model"].get("emb_g.weight", torch.empty(0, 0)).size() 
        if emb_dim > config["model"]["n_speakers"]:
            return "模型说话人数量与emb维度不匹配", gr.Dropdown.update(choices=[], value=""), 0, None
        if model_dim != config["model"]["ssl_dim"]: 
            return "配置文件与模型不匹配", gr.Dropdown.update(choices=[], value=""), 0, None
        encoder = config["model"]["speech_encoder"]
        spk_dict = config.get('spk', {})
        spk_choice = next(iter(spk_dict), "未检测到音色")
    else:
        spk_dict = diff_spk
        spk_choice = diff_spk_choice
    fr = cluster_name.endswith(".pkl") #如果是pkl后缀就启用特征检索
    shallow_diffusion = diff_model_name != "no_diff" #加载了扩散模型就启用浅扩散
    device = cuda[using_device] if "CUDA" in using_device else using_device
    model = Svc(ckpt_path,
                    config_path,
                    device=device if device != "Auto" else None,
                    cluster_model_path=cluster_path,
                    nsf_hifigan_enhance=enhance,
                    diffusion_model_path=diff_model_path,
                    diffusion_config_path=diff_config_path,
                    shallow_diffusion=shallow_diffusion,
                    only_diffusion=only_diffusion,
                    spk_mix_enable=use_spk_mix,
                    feature_retrieval=fr)
    spk_list = list(spk_dict.keys())
    if enhance:
        from modules.enhancer import Enhancer
        model.enhancer = Enhancer('nsf-hifigan', f'pretrain/{vocoder_name}/model', device=model.dev)
    if not only_diffusion:
        clip = 25 if encoder == "whisper-ppg" or encoder == "whisper-ppg-large" else cl_num #Whisper必须强制切片25秒
        device_name = torch.cuda.get_device_properties(model.dev).name if "cuda" in str(model.dev) else str(model.dev)
        sovits_msg = f"模型被成功加载到了{device_name}上\n"
    else: 
        clip = cl_num
        sovits_msg = "启用全扩散推理，未加载So-VITS模型\n"
    index_or_kmeans = "特征索引" if fr else "聚类模型"
    clu_load = "未加载" if cluster_name == "no_clu" else cluster_name
    diff_load = "未加载" if diff_model_name == "no_diff" else f"{diff_model_name} | 采样器: {method} | 加速倍数：{int(speedup)} | 最大浅扩散步数：{k_step_max} | 声码器： {vocoder_name}"
    output_msg = f"{sovits_msg}{index_or_kmeans}：{clu_load}\n扩散模型：{diff_load}"
    return (
        output_msg, 
        gr.Dropdown.update(choices=spk_list, value=spk_choice), 
        clip, 
        gr.Slider.update(value=100 if k_step_max>100 else k_step_max, minimum=speedup, maximum=k_step_max)
    )

def model_empty_cache():
    global model
    if model is None:
        return sid.update(choices = [],value=""),"没有模型需要卸载!"
    else:
        model.unload_model()
        model = None
        torch.cuda.empty_cache()
        return sid.update(choices = [],value=""),"模型卸载完毕!"

def get_file_options(directory, extension):
    return [file for file in os.listdir(directory) if file.endswith(extension)]

def load_options():
    ckpt_list = [file for file in get_file_options(ckpt_read_dir, ".pth") if not file.startswith("D_") or file == "G_0.pth"]
    config_list = get_file_options(config_read_dir, ".json")
    cluster_list = ["no_clu"] + get_file_options(ckpt_read_dir, ".pt") + get_file_options(ckpt_read_dir, ".pkl") # 聚类和特征检索模型
    diff_list = ["no_diff"] + get_file_options(diff_read_dir, ".pt")
    diff_config_list = ["no_diff_config"] + get_file_options(config_read_dir, ".yaml")
    return ckpt_list, config_list, cluster_list, diff_list, diff_config_list

def refresh_options():
    global ckpt_read_dir, config_read_dir, diff_read_dir
    ckpt_read_dir = second_dir if second_dir_enable else workdir
    config_read_dir = second_dir if second_dir_enable else config_dir
    diff_read_dir = diff_second_dir if second_dir_enable else diff_workdir
    ckpt_list, config_list, cluster_list, diff_list, diff_config_list = load_options()
    return (
        choice_ckpt.update(choices=ckpt_list),
        cluster_choice.update(choices=cluster_list),
        diff_choice.update(choices=diff_list),
        diff_config_choice.update(choices=diff_config_list),
    )

def source_change(use_microphone):
    if use_microphone:
        return vc_input3.update(source="microphone")
    else:
        return vc_input3.update(source="upload")

def vc_infer(output_format, sid, audio_path, truncated_basename, vc_transform, auto_f0, cluster_ratio, slice_db, noise_scale, pad_seconds, cl_num, lg_num, lgr_num, f0_predictor, enhancer_adaptive_key, cr_threshold, k_step, use_spk_mix, second_encoding, loudness_envelope_adjustment, infer_speedup=10, progress_callback=None):
    global model
    # 动态设置扩散加速倍数，无需重新加载模型
    if model is not None and hasattr(model, 'diffusion_args') and model.diffusion_args is not None:
        model.diffusion_args.infer.speedup = int(infer_speedup)
    _audio = model.slice_inference(
        audio_path,
        sid,
        vc_transform,
        slice_db,
        cluster_ratio,
        auto_f0,
        noise_scale,
        pad_seconds,
        cl_num,
        lg_num,
        lgr_num,
        f0_predictor,
        enhancer_adaptive_key,
        cr_threshold,
        k_step,
        use_spk_mix,
        second_encoding,
        loudness_envelope_adjustment,
        progress_callback=progress_callback
    )  
    model.clear_empty()
    if not os.path.exists("results"):
        os.makedirs("results")
    key = "auto" if auto_f0 else f"{int(vc_transform)}key"
    output_file_name = f'{truncated_basename}_{sid}_{key}.{output_format}'
    output_file_path = os.path.join("results", output_file_name)
    if os.path.exists(output_file_path):
        count = 1
        while os.path.exists(output_file_path):
            output_file_name = f'{truncated_basename}_{sid}_{key}_{str(count)}.{output_format}'
            output_file_path = os.path.join("results", output_file_name)
            count += 1
    sf.write(output_file_path, _audio, model.target_sample, format=output_format)
    return output_file_path, _audio.astype(np.float32), model.target_sample

# 全局进度状态，供 vc_progress_reader 函数读取
_vc_progress_data = {"current": 0, "total": 0, "seg_msg": "", "done": False, "error": None, "result_path": ""}

def _render_vc_progress_html(current=0, total=0, seg_msg="", elapsed=0, done=False, error=None, result_path="", title="推理"):
    """构建推理状态 HTML，统一用于实时输出与结束提示。"""
    card_style = (
        "font-family:monospace;padding:12px 14px;border:1px solid #e5e7eb;"
        "border-radius:10px;background:#fafafa;min-height:108px;display:flex;"
        "flex-direction:column;justify-content:flex-start;box-sizing:border-box;"
    )

    if error:
        return (
            f'<div style="{card_style}">'
            f'<div style="color:#dc2626;line-height:1.5">❌ {title}出错: {error}</div>'
            f'</div>'
        )

    safe_elapsed = round(float(elapsed or 0), 1)
    safe_current = max(0, float(current or 0))
    safe_total = max(0, float(total or 0))

    if done and result_path:
        abs_path = os.path.abspath(result_path).replace("\\", "/")
        done_total = int(safe_total) if safe_total > 0 else "?"
        return (
            f'<div style="{card_style}">'
            f'<div style="color:#16a34a;line-height:1.5">✅ {title}完成！共处理 {done_total} 个分段，耗时 {safe_elapsed}s</div>'
            f'<div style="margin-top:6px">文件: <a href="/file={abs_path}" target="_blank" style="color:#2196F3">{abs_path}</a></div>'
            f'<div style="margin-top:10px"><audio controls src="/file={abs_path}" style="width:100%"></audio></div>'
            f'</div>'
        )

    if safe_total > 0:
        pct = int(min(100, max(0, (safe_current / safe_total) * 100)))
        return (
            f'<div style="{card_style}">'
            f'<div style="line-height:1.5;height:24px;display:flex;align-items:center;">🔄 {title}中... {pct}% ({safe_current:.1f}/{safe_total:.0f})</div>'
            f'<div style="margin-top:8px;width:100%;height:10px;background:#e5e7eb;border-radius:999px;overflow:hidden;">'
            f'<div style="height:100%;width:{pct}%;background:#3b82f6;transition:width .2s ease;"></div>'
            f'</div>'
            f'<div style="margin-top:8px;line-height:1.4;color:#4b5563;height:38px;overflow:hidden;">{seg_msg or "正在处理分段..."}<br>⏱ 已耗时 {safe_elapsed}s</div>'
            f'</div>'
        )

    return (
        f'<div style="{card_style}">'
        f'<div style="line-height:1.5;height:24px;display:flex;align-items:center;">⏳ {title}中...</div>'
        f'<div style="margin-top:8px;width:100%;height:10px;background:#e5e7eb;border-radius:999px;overflow:hidden;">'
        f'<div style="height:100%;width:8%;background:#93c5fd;"></div>'
        f'</div>'
        f'<div style="margin-top:8px;line-height:1.4;color:#4b5563;height:38px;overflow:hidden;">{seg_msg or "正在初始化"}<br>⏱ 已耗时 {safe_elapsed}s</div>'
        f'</div>'
    )

def vc_progress_reader():
    """读取当前推理进度并返回HTML，供 gr.HTML(every=1) 调用"""
    import json as _json
    progress_file = os.path.join("raw", "vc_progress.json")
    try:
        with open(progress_file, 'r') as f:
            d = _json.load(f)
        if d.get("error"):
            return f'<p style="color:red;font-family:monospace">❌ 推理出错: {d["error"]}</p>'
        if d.get("done") and d.get("result_path"):
            path = d["result_path"]
            elapsed = d.get("elapsed", 0)
            return f'''<div style="font-family:monospace;padding:5px 0">
<span style="color:#4CAF50">✅ 推理完成！共处理 {d.get("total", "?")} 个分段，耗时 {elapsed}s</span><br>
文件: <a href="/file={path}" target="_blank" style="color:#2196F3">{path}</a>
</div>
<div style="margin:8px 0"><audio controls src="/file={path}" style="width:100%;max-width:600px"></audio></div>'''
        cur = d.get("current", 0)
        total = d.get("total", 0)
        seg_msg = d.get("seg_msg", "")
        elapsed = d.get("elapsed", 0)
        if total > 0:
            pct = int(cur / total * 100)
            filled = pct // 5
            bar = "█" * filled + "░" * (20 - filled)
            return f'<p style="font-family:monospace;margin:0">🔄 推理进度: [{bar}] {pct}% ({cur}/{total}) {seg_msg} ⏱ {elapsed}s</p>'
        elif cur > 0:
            return f'<p style="font-family:monospace;margin:0">⏳ 正在推理中... 已处理 {cur} 个分段 ⏱ {elapsed}s</p>'
        else:
            return '<span></span>'
    except:
        return '<span></span>'

def vc_fn(output_format=None, sid=None, input_audio=None, vc_transform=None, auto_f0=None, cluster_ratio=None, slice_db=None, noise_scale=None, pad_seconds=None, cl_num=None, lg_num=None, lgr_num=None, f0_predictor=None, enhancer_adaptive_key=None, cr_threshold=None, k_step=None, use_spk_mix=None, second_encoding=None, loudness_envelope_adjustment=None, infer_speedup=None):
    global model, _vc_progress_data
    try:
        if not INTEGRITY_OK:
            yield f'<p style="color:red">❌ {_integrity_block_message()}</p>'
            return
        if input_audio is None:
            yield '<p style="color:red">你还没有上传音频</p>'
            return
        if model is None:
            yield '<p style="color:red">你还没有加载模型</p>'
            return
        if getattr(model, 'cluster_model', None) is None and model.feature_retrieval is False:
            if cluster_ratio != 0:
                yield '<p style="color:red">你还未加载聚类或特征检索模型，无法启用聚类/特征检索混合比例</p>'
                return
        audio, sampling_rate = sf.read(input_audio)
        if np.issubdtype(audio.dtype, np.integer):
            audio = (audio / np.iinfo(audio.dtype).max).astype(np.float32)
        if len(audio.shape) > 1:
            audio = librosa.to_mono(audio.transpose(1, 0))
        _tgt_sr = int(getattr(model, "target_sample", _VC_INFER_TARGET_SR_FALLBACK))
        audio, sampling_rate = _normalize_input_wav_for_vc(audio, int(sampling_rate), _tgt_sr)
        truncated_basename = Path(input_audio).stem[:-6] if Path(input_audio).stem[-6:] == "-0-100" else Path(input_audio).stem
        processed_audio = os.path.join("raw", f"{truncated_basename}.wav")
        if not os.path.exists("raw"):
            os.makedirs("raw")
        sf.write(processed_audio, audio, sampling_rate, format="wav")

        import time as _time
        _vc_progress_data["current"] = 0
        _vc_progress_data["total"] = 0
        _vc_progress_data["seg_msg"] = "正在推理..."
        _vc_progress_data["done"] = False
        _vc_progress_data["error"] = None
        _vc_progress_data["start_time"] = _time.time()
        _vc_progress_data["result_path"] = ""

        def _progress_callback(current, total, seg_msg=""):
            elapsed = _time.time() - _vc_progress_data.get("start_time", _time.time())
            _vc_progress_data["current"] = current
            _vc_progress_data["total"] = total
            _vc_progress_data["seg_msg"] = seg_msg
            _vc_progress_data["elapsed"] = round(elapsed, 1)

        infer_result = {"output_file_path": None, "error": None}
        import threading as _threading

        def _run_infer():
            try:
                output_file_path, _, _ = vc_infer(
                    output_format, sid, processed_audio, truncated_basename,
                    vc_transform, auto_f0, cluster_ratio, slice_db, noise_scale,
                    pad_seconds, cl_num, lg_num, lgr_num, f0_predictor,
                    enhancer_adaptive_key, cr_threshold, k_step,
                    use_spk_mix, second_encoding, loudness_envelope_adjustment,
                    infer_speedup=int(str(infer_speedup).replace("×", "")),
                    progress_callback=_progress_callback
                )
                infer_result["output_file_path"] = output_file_path
            except Exception as _e:
                infer_result["error"] = _e

        infer_thread = _threading.Thread(target=_run_infer, daemon=True)
        infer_thread.start()

        yield _render_vc_progress_html(seg_msg="任务已提交，开始推理...", title="音频转换")

        while infer_thread.is_alive():
            elapsed = _time.time() - _vc_progress_data.get("start_time", _time.time())
            _vc_progress_data["elapsed"] = round(elapsed, 1)
            yield _render_vc_progress_html(
                current=_vc_progress_data.get("current", 0),
                total=_vc_progress_data.get("total", 0),
                seg_msg=_vc_progress_data.get("seg_msg", ""),
                elapsed=_vc_progress_data.get("elapsed", 0),
                title="音频转换"
            )
            _time.sleep(0.5)

        infer_thread.join()
        if infer_result["error"] is not None:
            raise infer_result["error"]

        output_file_path = infer_result["output_file_path"]
        _vc_progress_data["done"] = True
        _vc_progress_data["result_path"] = output_file_path
        yield _render_vc_progress_html(
            current=_vc_progress_data.get("current", 0),
            total=_vc_progress_data.get("total", 0),
            seg_msg="",
            elapsed=_vc_progress_data.get("elapsed", 0),
            done=True,
            result_path=output_file_path,
            title="音频转换"
        )
    except torch.cuda.OutOfMemoryError as e:
        _vc_progress_data["error"] = str(e)
        yield f'<p style="color:red">❌ {e}<br>显存不足，请参考文档解决</p>'
    except Exception as e:
        traceback.print_exc()
        _vc_progress_data["error"] = f"{type(e).__name__}: {e}"
        yield f'<p style="color:red">❌ {type(e).__name__}: {e}</p>'

def vc_batch_fn(output_format=None, sid=None, input_audio_files=None, vc_transform=None, auto_f0=None, cluster_ratio=None, slice_db=None, noise_scale=None, pad_seconds=None, cl_num=None, lg_num=None, lgr_num=None, f0_predictor=None, enhancer_adaptive_key=None, cr_threshold=None, k_step=None, use_spk_mix=None, second_encoding=None, loudness_envelope_adjustment=None, infer_speedup=None):
    global model, _vc_progress_data
    try:
        if not INTEGRITY_OK:
            yield f'<p style="color:red">❌ {_integrity_block_message()}</p>'
            return
        if input_audio_files is None or len(input_audio_files) == 0:
            yield '<p style="color:red">你还没有上传音频</p>'
            return
        if model is None:
            yield '<p style="color:red">你还没有加载模型</p>'
            return
        if getattr(model, 'cluster_model', None) is None and model.feature_retrieval is False:
            if cluster_ratio != 0:
                yield '<p style="color:red">你还未加载聚类或特征检索模型，无法启用聚类/特征检索混合比例</p>'
                return
        infer_speedup_int = int(str(infer_speedup).replace("×", ""))
        _output = []
        n_files = len(input_audio_files)
        import time as _time
        _vc_progress_data["current"] = 0
        _vc_progress_data["total"] = n_files
        _vc_progress_data["seg_msg"] = "正在初始化..."
        _vc_progress_data["done"] = False
        _vc_progress_data["error"] = None
        _vc_progress_data["start_time"] = _time.time()
        _vc_progress_data["result_path"] = ""
        yield _render_vc_progress_html(
            current=0, total=n_files, seg_msg="任务已提交，开始批量转换...", elapsed=0, title="批量转换"
        )

        for idx, file_obj in enumerate(input_audio_files):
            _vc_progress_data["current"] = idx
            _vc_progress_data["total"] = n_files
            _vc_progress_data["seg_msg"] = f"处理文件 {idx+1}/{n_files}..."
            elapsed = _time.time() - _vc_progress_data.get("start_time", _time.time())
            _vc_progress_data["elapsed"] = round(elapsed, 1)
            yield _render_vc_progress_html(
                current=_vc_progress_data["current"],
                total=_vc_progress_data["total"],
                seg_msg=_vc_progress_data["seg_msg"],
                elapsed=_vc_progress_data["elapsed"],
                title="批量转换"
            )
            print(f"Start processing: {file_obj.name}")
            input_audio_path = file_obj.name
            audio, sampling_rate = sf.read(input_audio_path)
            if np.issubdtype(audio.dtype, np.integer):
                audio = (audio / np.iinfo(audio.dtype).max).astype(np.float32)
            if len(audio.shape) > 1:
                audio = librosa.to_mono(audio.transpose(1, 0))
            _tgt_sr = int(getattr(model, "target_sample", _VC_INFER_TARGET_SR_FALLBACK))
            audio, sampling_rate = _normalize_input_wav_for_vc(audio, int(sampling_rate), _tgt_sr)
            truncated_basename = Path(input_audio_path).stem[:-6] if Path(input_audio_path).stem[-6:] == "-0-100" else Path(input_audio_path).stem
            processed_audio = os.path.join("raw", f"{truncated_basename}.wav")
            if not os.path.exists("raw"):
                os.makedirs("raw")
            sf.write(processed_audio, audio, sampling_rate, format="wav")

            def _file_progress(seg_idx, total_segs, seg_msg=""):
                if total_segs > 0:
                    _vc_progress_data["current"] = idx + seg_idx / total_segs
                _vc_progress_data["seg_msg"] = f"文件 {idx+1}/{n_files} {seg_msg}"

            output_file_path, _, _ = vc_infer(
                output_format, sid, processed_audio, truncated_basename,
                vc_transform, auto_f0, cluster_ratio, slice_db, noise_scale,
                pad_seconds, cl_num, lg_num, lgr_num, f0_predictor,
                enhancer_adaptive_key, cr_threshold, k_step,
                use_spk_mix, second_encoding, loudness_envelope_adjustment,
                infer_speedup=infer_speedup_int, progress_callback=_file_progress
            )
            _output.append(os.path.abspath(output_file_path).replace("\\", "/"))
            _vc_progress_data["current"] = idx + 1
            elapsed = _time.time() - _vc_progress_data.get("start_time", _time.time())
            _vc_progress_data["elapsed"] = round(elapsed, 1)
            yield _render_vc_progress_html(
                current=_vc_progress_data["current"],
                total=_vc_progress_data["total"],
                seg_msg=f"已完成文件 {idx+1}/{n_files}",
                elapsed=_vc_progress_data["elapsed"],
                title="批量转换"
            )

        _vc_progress_data["done"] = True
        _vc_progress_data["result_path"] = _output[-1] if _output else ""
        links = '<br>'.join([f'<a href="/file={p}" target="_blank">{p}</a>' for p in _output])
        yield (
            f'{_render_vc_progress_html(current=n_files, total=n_files, seg_msg="", elapsed=_vc_progress_data.get("elapsed", 0), title="批量转换")}'
            f'<div style="margin-top:10px;color:#4CAF50;font-family:monospace">✅ 批量推理完成！共 {len(_output)} 个文件，已保存到 results 文件夹</div>'
            f'<div style="margin-top:8px">{links}</div>'
        )
    except Exception as e:
        traceback.print_exc()
        _vc_progress_data["error"] = str(e)
        yield f'<p style="color:red">❌ {e}</p>'

def tts_fn(_text, _gender, _lang, _rate, _volume, output_format, sid, vc_transform, auto_f0,cluster_ratio, slice_db, noise_scale,pad_seconds,cl_num,lg_num,lgr_num,f0_predictor,enhancer_adaptive_key,cr_threshold, k_step,use_spk_mix,second_encoding,loudness_envelope_adjustment):
    global model
    try:
        if model is None:
            return '<p style="color:red">你还没有加载模型</p>'
        if getattr(model, 'cluster_model', None) is None and model.feature_retrieval is False:
            if cluster_ratio != 0:
                return '<p style="color:red">你还未加载聚类或特征检索模型，无法启用聚类/特征检索混合比例</p>'
        _rate = f"+{int(_rate*100)}%" if _rate >= 0 else f"{int(_rate*100)}%"
        _volume = f"+{int(_volume*100)}%" if _volume >= 0 else f"{int(_volume*100)}%"
        if _lang == "Auto":
            _gender = "Male" if _gender == "男" else "Female"
            subprocess.run([r".\workenv\Python\python.exe", "tts.py", _text, _lang, _rate, _volume, _gender])
        else:
            subprocess.run([r".\workenv\Python\python.exe", "tts.py", _text, _lang, _rate, _volume])
        target_sr = 44100
        y, sr = librosa.load("tts.wav")
        resampled_y = librosa.resample(y, orig_sr=sr, target_sr=target_sr)
        sf.write("tts.wav", resampled_y, target_sr, subtype = "PCM_16")
        input_audio = "tts.wav"
        output_file_path, output_audio, output_sr = vc_infer(output_format, sid, input_audio, "tts", vc_transform, auto_f0, cluster_ratio, slice_db, noise_scale, pad_seconds, cl_num, lg_num, lgr_num, f0_predictor, enhancer_adaptive_key, cr_threshold, k_step, use_spk_mix, second_encoding, loudness_envelope_adjustment)
        #os.remove("tts.wav")
        abs_path = os.path.abspath(output_file_path).replace("\\", "/")
        return f'<span style="color:#4CAF50">✅ 推理完成！</span> 文件: <a href="/file={abs_path}" target="_blank" style="color:#2196F3">{abs_path}</a><div style="margin-top:10px"><audio controls src="/file={abs_path}" style="width:100%"></audio></div>'
    except Exception as e:
        if debug:
            traceback.print_exc()
        return f'<p style="color:red">❌ {e}</p>'

def open_dataset_folder():
    open_folder(raw_path)

def open_folder(path):
    abs_path = os.path.abspath(path)
    os.makedirs(abs_path, exist_ok=True)
    if os.name == "nt":
        os.startfile(abs_path)
    else:
        subprocess.Popen(["xdg-open", abs_path])

def load_raw_dirs():
    global precheck_ok
    precheck_ok = False
    allowed_pattern = re.compile(r'^[a-zA-Z0-9_@#$%^&()_+\-=\s\.]*$')
    illegal_files = illegal_dataset = []
    for root, dirs, files in os.walk(raw_path):
        for dir in dirs:
            if not allowed_pattern.match(dir):
                illegal_dataset.append(dir)
        if illegal_dataset:
            return f"数据集文件夹名只能包含数字、字母、下划线，以下文件夹不符合要求，请改名后再试：\n{illegal_dataset}"
        if root != raw_path:  # 只处理子文件夹内的文件
            for file in files:
                if not allowed_pattern.match(file) and file not in illegal_files:
                    illegal_files.append(file)
                if not file.lower().endswith('.wav') and file not in illegal_files:
                    illegal_files.append(file)
    if illegal_files:
        return f"数据集文件名只能包含数字、字母、下划线，且必须是.wav格式，以下文件不符合要求，请改名后再试：\n{illegal_files}"
    spk_dirs = [entry.name for entry in os.scandir(raw_path) if entry.is_dir()]
    if spk_dirs:
        precheck_ok = True
        return spk_dirs
    else:
        return "未找到数据集，请检查dataset_raw文件夹"
    
def dataset_preprocess(encoder, f0_predictor, use_diff, vol_aug, skip_loudnorm, num_processes, tiny_enable):
    if precheck_ok:
        diff_arg = "--use_diff" if use_diff else ""
        vol_aug_arg = "--vol_aug" if vol_aug else ""
        skip_loudnorm_arg = "--skip_loudnorm" if skip_loudnorm else ""
        tiny_arg = "--tiny" if tiny_enable else ""
        # gr.Number 可能传入 1.0 这类浮点字符串，脚本参数要求 int
        try:
            num_processes_int = max(1, int(float(num_processes)))
        except (TypeError, ValueError):
            num_processes_int = 1
        preprocess_commands = [
            r".\workenv\Python\python.exe resample.py %s" % (skip_loudnorm_arg),
            r".\workenv\Python\python.exe preprocess_flist_config.py --speech_encoder %s %s %s" % (encoder, vol_aug_arg, tiny_arg),
            r".\workenv\Python\python.exe preprocess_hubert_f0.py --num_processes %s --f0_predictor %s %s" % (num_processes_int, f0_predictor, diff_arg)
            ]
        accumulated_output = ""
        #清空dataset
        dataset = os.listdir(dataset_dir)
        if len(dataset) != 0:
            for dir in dataset:
                dataset_spk_dir = os.path.join(dataset_dir, str(dir))
                if os.path.isdir(dataset_spk_dir):
                    shutil.rmtree(dataset_spk_dir)
                    accumulated_output += f"Deleting previous dataset: {dir}\n"
        for command in preprocess_commands:
            try:
                result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, text=True)
                accumulated_output += f"Command: {command}, Using Encoder: {encoder}, Using f0 Predictor: {f0_predictor}\n"
                yield accumulated_output, None
                progress_line = None
                for line in result.stdout:
                    if r"it/s" in line or r"s/it" in line: #防止进度条刷屏
                        progress_line = line
                    else:
                        accumulated_output += line
                    if progress_line is None:
                        yield accumulated_output, None
                    else:
                        yield accumulated_output + progress_line, None
                result.communicate()
            except subprocess.CalledProcessError as e:
                result = e.output
                accumulated_output += f"Error: {result}\n"
                yield accumulated_output, None
            if progress_line is not None:
                accumulated_output += progress_line
            accumulated_output += '-' * 50 + '\n'
            yield accumulated_output, None
            config_path = "configs/config.json"
        with open(config_path, 'r') as f:
            config = json.load(f)
        spk_name = config.get('spk', None)
        yield accumulated_output, gr.Textbox.update(value=spk_name)
    else:
        yield "数据集识别未通过，请先识别数据集并确保没有报错信息", None

def regenerate_config(encoder, vol_aug, tiny_enable):
    if precheck_ok is False:
        return "数据集识别未通过，请检查识别结果的报错信息"
    vol_aug_arg = "--vol_aug" if vol_aug else ""
    tiny_arg = "--tiny" if tiny_enable else ""
    cmd = r".\workenv\Python\python.exe preprocess_flist_config.py --speech_encoder %s %s %s" % (encoder, vol_aug_arg, tiny_arg)
    output = ""
    try:
        result = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, text=True)
        for line in result.stdout:
            output += line
        output += "Regenerate config file successfully."
    except subprocess.CalledProcessError as e:
        result = e.output
        output += f"Error: {result}\n"
    return output

def clear_output():
    return gr.Textbox.update(value="Cleared!>_<")

def get_available_encoder():
    current_pretrain = os.listdir("pretrain")
    current_pretrain = [("pretrain/" + model) for model in current_pretrain]
    encoder_list = []
    for encoder, path in ENCODER_PRETRAIN.items():
        if path in current_pretrain:
            encoder_list.append(encoder)
    return encoder_list

def config_fn(log_interval, eval_interval, keep_ckpts, batch_size, lr, amp_dtype, all_in_mem, diff_num_workers, diff_cache_all_data, diff_batch_size, diff_lr, diff_interval_log, diff_interval_val, diff_cache_device, diff_amp_dtype, diff_force_save, diff_k_step_max):
    if amp_dtype == "fp16" or amp_dtype == "bf16":
        fp16_run = True
    else:
        fp16_run = False
        amp_dtype = "fp16"
    config_origin = Config("configs/config.json", "json")
    diff_config = Config("configs/diffusion.yaml", "yaml")
    config_data = config_origin.read()
    config_data['train']['log_interval'] = int(log_interval)
    config_data['train']['eval_interval'] = int(eval_interval)
    config_data['train']['keep_ckpts'] = int(keep_ckpts)
    config_data['train']['batch_size'] = int(batch_size)
    config_data['train']['learning_rate'] = float(lr)
    config_data['train']['fp16_run'] = fp16_run
    config_data['train']['half_type'] = str(amp_dtype)
    config_data['train']['all_in_mem'] = all_in_mem
    config_origin.save(config_data)
    diff_config_data = diff_config.read()
    diff_config_data['train']['num_workers'] = int(diff_num_workers)
    diff_config_data['train']['cache_all_data'] = diff_cache_all_data
    diff_config_data['train']['batch_size'] = int(diff_batch_size)
    diff_config_data['train']['lr'] = float(diff_lr)
    diff_config_data['train']['interval_log'] = int(diff_interval_log)
    diff_config_data['train']['interval_val'] = int(diff_interval_val)
    diff_config_data['train']['cache_device'] = str(diff_cache_device)
    diff_config_data['train']['amp_dtype'] = str(diff_amp_dtype)
    diff_config_data['train']['interval_force_save'] = int(diff_force_save)
    diff_config_data['model']['k_step_max'] = 100 if diff_k_step_max else 0
    diff_config.save(diff_config_data)
    return "配置文件写入完成"

def check_dataset(dataset_path):
    if not os.listdir(dataset_path):
        return "数据集不存在，请检查dataset文件夹"
    no_npy_pt_files = True
    for root, dirs, files in os.walk(dataset_path):
        for file in files:
            if file.endswith('.npy') or file.endswith('.pt'):
                no_npy_pt_files = False
                break
    if no_npy_pt_files:
        return "数据集中未检测到f0和hubert文件，可能是预处理未完成"
    return None

def training(gpu_selection, encoder, tiny_enable):
    if tiny_enable:
        encoder = "vec768l12_tiny"
    config_file = Config("configs/config.json", "json")
    config_data = config_file.read()
    vol_emb = config_data["model"]["vol_embedding"]
    dataset_warn = check_dataset(dataset_dir)
    if dataset_warn is not None:
        return dataset_warn
    PRETRAIN = { 
        "vec256l9": ("D_0.pth", "G_0.pth", "pre_trained_model"),
        "vec768l12": ("D_0.pth", "G_0.pth", "pre_trained_model/768l12/vol_emb" if vol_emb else "pre_trained_model/768l12"),
        "vec768l12_tiny": ("D_0.pth", "G_0.pth", "pre_trained_model/tiny/vec768l12_vol_emb"),
        "hubertsoft": ("D_0.pth", "G_0.pth", "pre_trained_model/hubertsoft"),
        "whisper-ppg": ("D_0.pth", "G_0.pth", "pre_trained_model/whisper-ppg"),
        "cnhubertlarge": ("D_0.pth", "G_0.pth", "pre_trained_model/cnhubertlarge"),
        "dphubert": ("D_0.pth", "G_0.pth", "pre_trained_model/dphubert"),
        "wavlmbase+": ("D_0.pth", "G_0.pth", "pre_trained_model/wavlmbase+"),
        "whisper-ppg-large": ("D_0.pth", "G_0.pth", "pre_trained_model/whisper-ppg-large")
    }
    if encoder not in PRETRAIN:
        return "未知编码器"
    d_0_file, g_0_file, encoder_model_path = PRETRAIN[encoder]
    d_0_path = os.path.join(encoder_model_path, d_0_file)
    g_0_path = os.path.join(encoder_model_path, g_0_file)
    timestamp = datetime.datetime.now().strftime('%Y_%m_%d_%H_%M')
    new_backup_folder = os.path.join(models_backup_path, str(timestamp))
    output_msg = ""
    if os.listdir(workdir) != ['diffusion']:
        os.makedirs(new_backup_folder, exist_ok=True)
        for file in os.listdir(workdir):
            if file != "diffusion":
                shutil.move(os.path.join(workdir, file), os.path.join(new_backup_folder, file))
    if os.path.isfile(g_0_path) and os.path.isfile(d_0_path):
        shutil.copy(d_0_path, os.path.join(workdir, "D_0.pth"))
        shutil.copy(g_0_path, os.path.join(workdir, "G_0.pth"))
        output_msg += f"成功装载预训练模型，编码器：{encoder}\n"
    else:
        output_msg += f"{encoder}的预训练模型不存在，未装载预训练模型\n"

    cmd = r"set CUDA_VISIBLE_DEVICES=%s && .\workenv\Python\python.exe train.py -c configs/config.json -m 44k" % (gpu_selection)
    subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cmd])
    output_msg += "已经在新的终端窗口开始训练，请监看终端窗口的训练日志。在终端中按Ctrl+C可暂停训练。"
    return output_msg

def continue_training(gpu_selection, encoder):
    dataset_warn = check_dataset(dataset_dir)
    if dataset_warn is not None:
        return dataset_warn
    if encoder == "":
        return "请先选择预处理对应的编码器"
    all_files = os.listdir(workdir)
    model_files = [f for f in all_files if f.startswith('G_') and f.endswith('.pth')]
    if len(model_files) == 0:
        return "你还没有已开始的训练"
    cmd = r"set CUDA_VISIBLE_DEVICES=%s && .\workenv\Python\python.exe train.py -c configs/config.json -m 44k" % (gpu_selection)
    subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cmd])
    return "已经在新的终端窗口开始训练，请监看终端窗口的训练日志。在终端中按Ctrl+C可暂停训练。"

def kmeans_training(kmeans_gpu):
    if not os.listdir(dataset_dir):
        return "数据集不存在，请检查dataset文件夹"
    cmd = r".\workenv\Python\python.exe cluster/train_cluster.py --gpu" if kmeans_gpu else r".\workenv\Python\python.exe cluster/train_cluster.py"
    subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cmd])
    return "已经在新的终端窗口开始训练，训练聚类模型不会输出日志，CPU训练一般需要5-10分钟左右"

def index_training():
    if not os.listdir(dataset_dir):
        return "数据集不存在，请检查dataset文件夹"
    cmd = r".\workenv\Python\python.exe train_index.py -c configs/config.json"
    subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cmd])
    return "已经在新的终端窗口开始训练"

def diff_training(encoder, k_step_max):
    if not os.listdir(dataset_dir):
        return "数据集不存在，请检查dataset文件夹"
    timestamp = datetime.datetime.now().strftime('%Y_%m_%d_%H_%M')
    new_backup_folder = os.path.join(models_backup_path, "diffusion", str(timestamp))
    if len(os.listdir(diff_workdir)) != 0:
        os.makedirs(new_backup_folder, exist_ok=True)
        for file in os.listdir(diff_workdir):
            shutil.move(os.path.join(diff_workdir, file), os.path.join(new_backup_folder, file))
    DIFF_PRETRAIN = {
        "768-kstepmax100": "pre_trained_model/diffusion/768l12/max100/model_0.pt",
        "vec768l12": "pre_trained_model/diffusion/768l12/model_0.pt",
        "hubertsoft": "pre_trained_model/diffusion/hubertsoft/model_0.pt",
        "whisper-ppg": "pre_trained_model/diffusion/whisper-ppg/model_0.pt"
    }
    if encoder not in DIFF_PRETRAIN:
        return "你所选的编码器暂时不支持训练扩散模型"
    if k_step_max:
        encoder = "768-kstepmax100"
    diff_pretrained_model = DIFF_PRETRAIN[encoder]
    shutil.copy(diff_pretrained_model, os.path.join(diff_workdir, "model_0.pt"))
    subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", r".\workenv\Python\python.exe train_diff.py -c configs/diffusion.yaml"])
    output_message = "已经在新的终端窗口开始训练，请监看终端窗口的训练日志。在终端中按Ctrl+C可暂停训练。"
    if encoder == "768-kstepmax100":
        output_message += "\n正在进行100步深度的浅扩散训练，已加载底模"
    else:
        output_message += f"\n正在进行完整深度的扩散训练，编码器{encoder}"
    return output_message

def diff_continue_training(encoder):
    if not os.listdir(dataset_dir):
        return "数据集不存在，请检查dataset文件夹"
    if encoder == "":
        return "请先选择预处理对应的编码器"
    all_files = os.listdir(diff_workdir)
    model_files = [f for f in all_files if f.endswith('.pt')]
    if len(model_files) == 0:
        return "你还没有已开始的训练"
    subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", r".\workenv\Python\python.exe train_diff.py -c configs/diffusion.yaml"])
    return "已经在新的终端窗口开始训练，请监看终端窗口的训练日志。在终端中按Ctrl+C可暂停训练。"

def open_training_results():
    open_folder(workdir)

def _extract_step_from_name(file_name, pattern):
    match = re.match(pattern, file_name)
    return int(match.group(1)) if match else -1

def _is_ascii_alnum_name(name):
    return bool(re.fullmatch(r"[A-Za-z0-9]+", name))

def _is_chinese_name(name):
    return bool(re.fullmatch(r"[\u4e00-\u9fff]+", name))

def _contains_chinese(name):
    return bool(re.search(r"[\u4e00-\u9fff]", name))

def _to_pinyin_name(name):
    try:
        from pypinyin import lazy_pinyin
    except Exception:
        return None
    chars = []
    for ch in name:
        if re.match(r"[\u4e00-\u9fff]", ch):
            chars.extend(lazy_pinyin(ch))
        elif re.match(r"[A-Za-z0-9]", ch):
            chars.append(ch.lower())
        else:
            # 仅允许中英数命名，其他字符直接视为非法
            return None
    py = "".join(chars).lower()
    return py if py else None

def archive_training_models(archive_name):
    """
    将 logs/44k 中最新轮次的 So-VITS / Diffusion 模型归档到 models 目录，方便在独立目录模式中直接调用。
    默认保留 logs/44k 中缓存与数据中间件，避免影响继续训练。
    """
    os.makedirs(second_dir, exist_ok=True)
    os.makedirs(diff_second_dir, exist_ok=True)

    moved_items = []
    skipped_items = []

    archive_name = (archive_name or "").strip()
    if not archive_name:
        return "请先输入归档名称，再点击确认归档。"

    if _is_ascii_alnum_name(archive_name):
        model_base_name = archive_name
        diff_base_name = archive_name
    elif _contains_chinese(archive_name):
        model_base_name = archive_name
        diff_base_name = _to_pinyin_name(archive_name)
        if not diff_base_name:
            return "命名仅支持中文、英文、数字组合。请去掉空格和特殊符号后重试。"
    else:
        return "归档名称仅支持中文、英文、数字（中文会自动为扩散模型转拼音）。"

    # 1) 选主模型：G_*.pth 中训练轮次最大的 1 个
    g_models = [f for f in os.listdir(workdir) if re.match(r"^G_(\d+)\.pth$", f)]
    g_models = sorted(g_models, key=lambda x: _extract_step_from_name(x, r"^G_(\d+)\.pth$"), reverse=True)
    selected_g_model = g_models[0] if g_models else None

    # 2) 选扩散模型：排除 model_0.pt，仅取训练轮次 > 0 的最大 1 个
    diff_models = [
        f for f in os.listdir(diff_workdir)
        if re.match(r"^model_(\d+)\.pt$", f) and _extract_step_from_name(f, r"^model_(\d+)\.pt$") > 0
    ]
    diff_models = sorted(diff_models, key=lambda x: _extract_step_from_name(x, r"^model_(\d+)\.pt$"), reverse=True)
    selected_diff_model = diff_models[0] if diff_models else None

    if not selected_g_model and not selected_diff_model:
        return "未检测到可归档文件：主模型缺失，且扩散模型为空或仅有 model_0.pt。"

    model_target = os.path.join(second_dir, f"{model_base_name}.pth")
    model_config_target = os.path.join(second_dir, f"{model_base_name}.json")
    diff_target = os.path.join(diff_second_dir, f"{diff_base_name}.pt")
    diff_config_target = os.path.join(diff_second_dir, f"{diff_base_name}.yaml")

    planned_targets = []
    if selected_g_model:
        planned_targets.extend([model_target, model_config_target])
    if selected_diff_model:
        planned_targets.extend([diff_target, diff_config_target])

    conflict_paths = [p for p in planned_targets if os.path.exists(p)]
    if conflict_paths:
        return "归档名称重复，无法归档。请更换名称后重试。"

    # 3) 归档模型文件（重命名）
    if selected_g_model:
        g_src = os.path.join(workdir, selected_g_model)
        shutil.move(g_src, model_target)
        moved_items.append(model_target)

    if selected_diff_model:
        diff_src = os.path.join(diff_workdir, selected_diff_model)
        shutil.move(diff_src, diff_target)
        moved_items.append(diff_target)

    # 4) 归档配置文件（按命名规则重命名）
    if selected_g_model:
        config_src = os.path.join(config_dir, "config.json")
        if os.path.isfile(config_src):
            shutil.copy2(config_src, model_config_target)
            moved_items.append(model_config_target)
        else:
            skipped_items.append("config.json（不存在）")

    if selected_diff_model:
        diff_config_src = os.path.join(config_dir, "diffusion.yaml")
        if os.path.isfile(diff_config_src):
            shutil.copy2(diff_config_src, diff_config_target)
            moved_items.append(diff_config_target)
        else:
            skipped_items.append("diffusion.yaml（不存在）")

    summary = [f"归档完成：已处理 {len(moved_items)} 个文件。"]
    if selected_g_model:
        summary.append(f"So-VITS 归档：{selected_g_model} -> {os.path.basename(model_target)}")
        summary.append(f"So-VITS 配置归档：{os.path.basename(model_config_target)}")
    else:
        summary.append("So-VITS：未检测到可归档主模型，已跳过。")
    if selected_diff_model:
        summary.append(f"扩散归档：{selected_diff_model} -> {os.path.basename(diff_target)}")
        summary.append(f"扩散配置归档：{os.path.basename(diff_config_target)}")
    else:
        summary.append("扩散：未检测到可归档扩散模型（已排除 model_0.pt），已跳过。")
    summary.append("已默认保留 logs/44k 内缓存文件，避免影响后续继续训练。")
    if skipped_items:
        summary.append("以下文件跳过：" + "；".join(skipped_items))
    return "\n".join(summary)

def clean_training_files():
    """
    清理 logs/44k 训练产物（包含 diffusion 子目录），用于归档后释放空间。
    注意：清理后将无法从当前工作目录继续训练。
    """
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(diff_workdir, exist_ok=True)

    removed_count = 0
    failed_items = []

    # 清理主训练目录（保留 diffusion 目录本身）
    for name in os.listdir(workdir):
        if name == "diffusion":
            continue
        target = os.path.join(workdir, name)
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            removed_count += 1
        except Exception as e:
            failed_items.append(f"{name}（{e}）")

    # 清理扩散目录
    for name in os.listdir(diff_workdir):
        target = os.path.join(diff_workdir, name)
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            removed_count += 1
        except Exception as e:
            failed_items.append(f"diffusion/{name}（{e}）")

    message = f"清理完成：共删除 {removed_count} 项训练文件。"
    if failed_items:
        message += "\n以下项目删除失败：" + "；".join(failed_items)
    message += "\n注意：归档/清理后无法继续训练。"
    return message

def upload_mix_append_file(files,sfiles):
    try:
        if(sfiles is None):
            file_paths = [file.name for file in files]
        else:
            file_paths = [file.name for file in chain(files,sfiles)]
        p = {file:100 for file in file_paths}
        return file_paths,mix_model_output1.update(value=json.dumps(p,indent=2))
    except Exception as e:
        if debug:
            traceback.print_exc()
        raise gr.Error(e)

def mix_submit_click(js,mode):
    try:
        assert js.lstrip()!=""
        modes = {"凸组合":0, "线性组合":1}
        mode = modes[mode]
        data = json.loads(js)
        data = list(data.items())
        model_path,mix_rate = zip(*data)
        path = mix_model(model_path,mix_rate,mode)
        return f"成功，文件被保存在了{path}"
    except Exception as e:
        if debug:
            traceback.print_exc()
        raise gr.Error(e)

def updata_mix_info(files):
    try:
        if files is None:
            return mix_model_output1.update(value="")
        p = {file.name:100 for file in files}
        return mix_model_output1.update(value=json.dumps(p,indent=2))
    except Exception as e:
        if debug:
            traceback.print_exc()
        raise gr.Error(e)

def pth_identify():
    if not os.path.exists(root_dir):
        return f"未找到{root_dir}文件夹，请先创建一个{root_dir}文件夹并按第一步流程操作"
    model_dirs = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
    if not model_dirs:
        return f"未在{root_dir}文件夹中找到模型文件夹，请确保每个模型和配置文件都被放置在单独的文件夹中"
    valid_model_dirs = []
    for path in model_dirs:
        pth_files = glob.glob(f"{root_dir}/{path}/*.pth")
        json_files = glob.glob(f"{root_dir}/{path}/*.json")
        if len(pth_files) != 1 or len(json_files) != 1:
            return f"错误: 在{root_dir}/{path}中找到了{len(pth_files)}个.pth文件和{len(json_files)}个.json文件。应当确保每个文件夹内有且只有一个.pth文件和.json文件"
        valid_model_dirs.append(path)
        
    return f"成功识别了{len(valid_model_dirs)}个模型：{valid_model_dirs}"

def onnx_export_func():
    model_dirs = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
    output_msg = ""
    try:
        for path in model_dirs:
            pth_files = glob.glob(f"{root_dir}/{path}/*.pth")
            json_files = glob.glob(f"{root_dir}/{path}/*.json")
            model_file = Path(pth_files[0]).name
            json_file = Path(json_files[0]).name
            try:
                onnx_export(path, json_file, model_file)
                output_msg += f"成功转换{path}\n"
            except Exception as e:
                output_msg += f"转换{path}时出现错误: {e}\n"
        return output_msg
    except Exception as e:
        if debug:
            traceback.print_exc()
        raise gr.Error(e)

def load_raw_audio(audio_path):
    audio_path = audio_path.replace("\"", "")
    if not os.path.isdir(audio_path):
        return "请输入正确的目录", None
    audio_files = list_files(audio_path, extensions=AUDIO_EXTENSIONS, recursive=False)
    if not audio_files:
        return "未在目录中找到音频文件", None
    
    return f"成功加载{len(audio_files)}条音频", str(os.path.join(audio_path, "output"))

def auto_slice(input_dir, output_dir, max_sec, min_sec, min_silence_duration, max_silence_kept):
    input_dir = str(input_dir or "").replace('"', "").strip()
    output_dir = str(output_dir or "").replace('"', "").strip()
    if output_dir == "":
        return "请先选择输出的文件夹"
    if os.path.normpath(output_dir) == os.path.normpath(input_dir):
        return "输出目录不能和输入目录相同"
    if not input_dir or not os.path.isdir(input_dir):
        return "请输入正确的原始音频文件夹路径"
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        audio_files = list_files(input_dir, extensions=AUDIO_EXTENSIONS, recursive=False)
        if not audio_files:
            return "输入目录中未找到支持的音频文件"

        for file in audio_files:
            slice_audio_file_v2(
                input_file=file,
                output_dir=output_dir,
                min_duration=min_sec,
                max_duration=max_sec,
                min_silence_duration=min_silence_duration,
                top_db=-40,
                hop_length=10,
                max_silence_kept=max_silence_kept,
            )

        len_files, total_duration, avg_duration, min_duration, max_duration, short_files = length(
            output_dir, short_threshold=min_sec
        )

        if short_files:
            for i in short_files:
                os.remove(str(Path(output_dir) / i[3]))
            len_files, total_duration, avg_duration, min_duration, max_duration, _ = length(output_dir)

        original_duration = 0.0
        for file in audio_files:
            original_duration += float(librosa.get_duration(filename=str(file)))

        msg = (
            f"成功将音频切分为{len_files}条片段，其中最长{max_duration:.2f}秒，最短{min_duration:.2f}秒，"
            f"切片后的音频总时长{total_duration/3600:.2f}小时，平均每条音频时长{avg_duration:.2f}秒"
        )
        if original_duration > 0:
            msg += f"\n为原始音频时长的{total_duration / original_duration * 100:.2f}%"
        return msg
    except Exception as e:
        logging.exception("auto_slice")
        return f"切片过程出错：{e}"


def model_compression(_model, is_fp16):
    if _model == "":
        return "请先选择要压缩的模型"
    else:
        model_path = os.path.join(ckpt_read_dir, _model)
        filename, extension = os.path.splitext(_model)
        output_model_name = f"{filename}_compressed{extension}"
        output_path = os.path.join(ckpt_read_dir, output_model_name)
        removeOptimizer("configs/config.json", model_path, is_fp16, output_path)
        return f"模型已成功被保存在了{output_path}"
    
def pack_autoload(model_to_pack):
    _, config_name, _ = auto_load(model_to_pack)
    if config_name == "no_config":
        return "未找到对应的配置文件，请手动选择", None
    else:
        _config = Config(os.path.join(config_read_dir, config_name), "json")
        _content = _config.read()
        spk_dict = _content["spk"]
        spk_list = ",".join(spk_dict.keys())
        return config_name, spk_list
    
def release_packing(model_to_pack, model_config, speaker, diff_to_pack, cluster_to_pack):
    model_path = diff_path = cluster_path = ""
    basename = os.path.splitext(model_to_pack)[0]
    diff_basename = os.path.splitext(diff_to_pack)[0]
    if model_to_pack == "" or model_config == "" or speaker == "":
        return "存在必选项为空，请检查后重试"
    released_pack = ReleasePacker(speaker, model_to_pack)
    released_pack.remove_temp("release_packs")
    model_path = os.path.join(ckpt_read_dir, model_to_pack)
    config_path = os.path.join(config_read_dir, model_config)
    if os.stat(model_path).st_size > 300000000:
        removeOptimizer(config_path, model_path, False, os.path.join("release_packs", model_to_pack))
        model_path = os.path.join("release_packs", model_to_pack)
    if diff_to_pack != "no_diff":
        diff_path = os.path.join(diff_read_dir, diff_to_pack)
    if cluster_to_pack != "no_cluster":
        cluster_path = os.path.join(ckpt_read_dir, cluster_to_pack)
    shutil.copyfile("configs_template/config_template.json", "release_packs/config_template.json")
    shutil.copyfile("configs_template/diffusion_template.yaml", "release_packs/diffusion_template.yaml")
    files_to_pack = [
        (model_path, f"models/{model_to_pack}"),
        (diff_path, f"models/diffusion/{diff_to_pack}") if diff_to_pack != "no_diff" else ("", ""),
        (cluster_path, f"models/{cluster_to_pack}") if cluster_to_pack != "no_cluster" else ("", ""),
        (f"release_packs/{basename}.json", f"models/{basename}.json"),
        (f"release_packs/{diff_basename}.yaml", f"models/{diff_basename}.yaml") if diff_to_pack != "no_diff" else ("", ""),
        ("release_packs/install.txt", "install.txt")
    ]
    released_pack.add_file(files_to_pack)
    released_pack.generate_config(diff_to_pack, model_config)
    os.rename("release_packs/config_template.json", f"release_packs/{basename}.json")
    os.rename("release_packs/diffusion_template.yaml", f"release_packs/{diff_basename}.yaml")
    released_pack.pack()
    to_remove = [file for file in os.listdir("release_packs") if not file.endswith(".zip")]
    for file in to_remove:
        os.remove(os.path.join("release_packs", file))
    return "打包成功, 请在release_packs目录下查看"

def release_install(model_zip_path):
    model_zip = ReleasePacker("", "")
    model_zip.unpack(model_zip_path)
    for file in os.listdir("release_packs"):
        if file.endswith(".txt"):
            install_txt = os.path.join("release_packs", file)
            break
    else:
        model_zip.remove_temp("release_packs")
        return "非格式化安装包，无法安装"
    _spk = model_zip.formatted_install(install_txt)
    model_zip.remove_temp("release_packs")
    return f"安装成功，可用说话人{_spk}，请启用独立目录模式加载模型"

def mdx_separate(audio_path, model_name, gpu_id):
    """使用 MDX-Net 模型分离音频，支持 ONNX 和 ckpt（MDX23C）两种格式"""
    if not audio_path or not os.path.isfile(audio_path):
        return [], "请先上传音频文件"

    gpu_id = int(gpu_id) if gpu_id is not None else 0
    base_dir = os.path.dirname(os.path.abspath(__file__))
    mdx_data_dir = os.path.join(base_dir, "mdx_model_data")

    # 查找模型文件（支持 .onnx 和 .ckpt，在 uvr5_models/ 及子文件夹中递归搜索）
    model_file = _find_model_file(model_name, base_dir)
    if model_file is None:
        return [], f"模型 {model_name} 未找到（未找到 .ckpt 或 .onnx 文件）"

    # 输出目录
    audio_name = Path(audio_path).stem
    output_dir = os.path.join(base_dir, "separated", "MDX")
    os.makedirs(output_dir, exist_ok=True)

    try:
        # 设备
        if gpu_id >= 0 and torch.cuda.is_available():
            device = f"cuda:{gpu_id}"
        else:
            device = "cpu"

        # 加载音频（FFmpeg 优先，通用格式）
        mix, sr = load_audio_stereo_at_sr(audio_path, 44100)
        print(f"[MDX] audio loaded shape: {mix.shape}, sr={sr}")

        if model_file.endswith(".ckpt"):
            # ====== MDX23C / MDXC 推理（PyTorch） ======
            print(f"[MDX-C] 加载 ckpt 模型: {model_file} (device={device})")

            # 动态导入 mdx_inference 模块
            import importlib.util
            mdx_mod_path = os.path.join(base_dir, "mdx_inference.py")
            spec = importlib.util.spec_from_file_location("mdx_inference", mdx_mod_path)
            mdx_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mdx_mod)

            # 确定配置文件
            # 文件名含 8K → model_2_stem_full_band_8k.yaml
            config_yaml = "model_2_stem_full_band_8k.yaml"
            config_path = os.path.join(mdx_data_dir, "mdx_c_configs", config_yaml)

            # 也尝试通过 model_data.json 的 hash 匹配
            params = mdx_mod.get_mdx_params(model_file, mdx_data_dir)
            if params and "config_yaml" in params:
                config_yaml = params["config_yaml"]
                config_path = os.path.join(mdx_data_dir, "mdx_c_configs", config_yaml)

            if not os.path.isfile(config_path):
                # 尝试在 mdx_model_data 根目录找
                config_path = os.path.join(mdx_data_dir, config_yaml)

            if not os.path.isfile(config_path):
                return [], f"找不到 MDXC 配置文件: {config_yaml}（需在 mdx_model_data/mdx_c_configs/ 下）"

            config = mdx_mod.load_mdx_c_config(config_path)
            model = mdx_mod.TFC_TDF_net(config, device)
            model.load_state_dict(torch.load(model_file, map_location="cpu"))
            model.to(device).eval()

            # 推理
            overlap = config.inference.num_overlap if hasattr(config.inference, 'num_overlap') else 8
            result = mdx_mod.mdx_c_separate(model, config, mix, device, overlap=overlap, batch_size=1)

            output_files = []
            if isinstance(result, dict):
                # 多 stem 输出
                for stem_name, stem_audio in result.items():
                    stem_audio = np.asarray(stem_audio).squeeze()
                    # 确保形状为 (T, C) 供 soundfile 写入
                    if stem_audio.ndim == 3:
                        # [S, 2, T] 或 [2, S, T] → 取第一个 stem 并转置
                        stem_audio = stem_audio[0]
                    if stem_audio.ndim == 2 and stem_audio.shape[0] <= stem_audio.shape[1]:
                        stem_audio = stem_audio.T
                    out_path = os.path.join(output_dir, f"{audio_name}_{stem_name.lower()}.wav")
                    sf.write(out_path, stem_audio, 44100)
                    output_files.append(out_path)
            else:
                # 单 stem 输出
                stem_audio = np.asarray(result).squeeze()
                compensate = params.get("compensate", 1.0) if params else 1.0
                stem_audio = stem_audio * compensate
                if stem_audio.ndim == 3:
                    stem_audio = stem_audio[0]
                if stem_audio.ndim == 2 and stem_audio.shape[0] <= stem_audio.shape[1]:
                    stem_audio = stem_audio.T
                stem_name = params.get("primary_stem", "Vocals") if params else "Vocals"
                out_path = os.path.join(output_dir, f"{audio_name}_{stem_name.lower()}.wav")
                sf.write(out_path, stem_audio, 44100)
                output_files.append(out_path)

            msg = f"MDX-C (MDX23C) 分离完成！输出 {len(output_files)} 个文件:\n" + "\n".join(
                os.path.basename(f) for f in output_files
            )
            return output_files, msg

        else:
            # ====== 旧版 MDX-Net ONNX 推理 ======
            import onnxruntime as ort

            print(f"[MDX] 加载 ONNX 模型: {model_file}")

            # 动态导入 mdx_inference 模块
            import importlib.util
            mdx_mod_path = os.path.join(base_dir, "mdx_inference.py")
            spec = importlib.util.spec_from_file_location("mdx_inference", mdx_mod_path)
            mdx_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mdx_mod)

            if gpu_id >= 0 and ort.get_device() == "GPU":
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            else:
                providers = ["CPUExecutionProvider"]

            session = ort.InferenceSession(model_file, providers=providers)

            # 获取模型参数
            model_params = mdx_mod.get_mdx_params(model_file, mdx_data_dir) or {}
            compensate = model_params.get("compensate", 1.0)
            primary_stem = model_params.get("primary_stem", "Vocals")

            # 如果没有 model_data 参数，使用默认值
            if not model_params:
                model_params = {
                    "mdx_n_fft_scale_set": 6144,
                    "mdx_dim_f_set": 2048,
                    "mdx_dim_t_set": 8,
                    "compensate": 1.035,
                    "primary_stem": "Vocals",
                }

            # 推理
            tar_waves = mdx_mod.mdx_onnx_separate(session, mix, model_params, device)

            output_files = []
            # tar_waves shape: [1, 2, T] → primary stem
            primary_audio = tar_waves[0] * compensate  # [2, T]
            out_path = os.path.join(output_dir, f"{audio_name}_{primary_stem.lower()}.wav")
            sf.write(out_path, primary_audio, 44100)
            output_files.append(out_path)

            # secondary = 原始 - primary
            secondary_audio = mix - primary_audio
            secondary_name = "instrumental" if primary_stem.lower() == "vocals" else "other"
            out_path2 = os.path.join(output_dir, f"{audio_name}_{secondary_name}.wav")
            sf.write(out_path2, secondary_audio, 44100)
            output_files.append(out_path2)

            msg = f"MDX-Net 分离完成！输出 {len(output_files)} 个文件:\n" + "\n".join(
                os.path.basename(f) for f in output_files
            )
            return output_files, msg

    except Exception as e:
        if debug:
            traceback.print_exc()
        return [], f"MDX-Net 分离出错: {str(e)}"

def vr_separate(audio_path, model_name, gpu_id, output_dir=None):
    """使用 VR Architecture 模型分离音频（基于 RVC 的 uvr5_pack，PyTorch 推理）"""
    import sys
    import importlib

    if not audio_path or not os.path.isfile(audio_path):
        return [], "请先上传音频文件"

    gpu_id = int(gpu_id) if gpu_id is not None else 0
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # 查找 .pth 模型文件（在 uvr5_models/ 及子文件夹中递归搜索）
    model_path = _find_model_file(model_name, base_dir)
    if model_path is None:
        return [], f"模型 {model_name} 未找到"

    # 输出目录
    audio_name = Path(audio_path).stem
    if output_dir is None:
        output_dir = os.path.join(base_dir, "separated", "VR")
    os.makedirs(output_dir, exist_ok=True)

    try:
        # 设备
        if gpu_id >= 0 and torch.cuda.is_available():
            device = f"cuda:{gpu_id}"
        else:
            device = "cpu"

        print(f"[VR] 加载模型: {model_name} (device={device})")

        # 将 uvr5_pack 目录加入 import path
        uvr5_pack_dir = os.path.join(base_dir, "uvr5_pack")
        if uvr5_pack_dir not in sys.path:
            sys.path.insert(0, uvr5_pack_dir)

        # 导入 RVC 的 uvr5_pack 库
        from lib_v5 import nets_61968KB as Nets
        from lib_v5 import spec_utils
        from lib_v5.model_param_init import ModelParameters
        from lib_v5.nets_new import CascadedNet
        # 用 importlib 直接加载 uvr5_pack/utils.py，避免和 50SVC 根目录的 utils.py 冲突
        import importlib.util as _ilu
        _u_spec = _ilu.spec_from_file_location("uvr5_utils", os.path.join(uvr5_pack_dir, "utils.py"))
        _u_mod = _ilu.module_from_spec(_u_spec)
        _u_spec.loader.exec_module(_u_mod)
        inference = _u_mod.inference

        # 判断模型类型并选择配置
        is_decho = "De-Echo" in model_name or "DeEcho" in model_name
        is_denoise = "DeNoise" in model_name or "De-noise" in model_name

        if is_decho:
            # De-Echo 模型使用 4band_v3 配置 + CascadedNet
            param_file = os.path.join(uvr5_pack_dir, "lib_v5", "modelparams", "4band_v3.json")
            nout = 64 if "DeReverb" in model_name else 48
            mp = ModelParameters(param_file)
            model = CascadedNet(mp.param["bins"] * 2, nout)
            cpk = torch.load(model_path, map_location="cpu")
            model.load_state_dict(cpk)
            model.eval()
            model.to(device)
        else:
            # 标准模型（5_HP / 6_HP / 9_HP 等）使用 4band_v2 配置 + CascadedASPPNet
            param_file = os.path.join(uvr5_pack_dir, "lib_v5", "modelparams", "4band_v2.json")
            mp = ModelParameters(param_file)
            model = Nets.CascadedASPPNet(mp.param["bins"] * 2)
            cpk = torch.load(model_path, map_location="cpu")
            model.load_state_dict(cpk)
            model.eval()
            model.to(device)

        # 加载音频（FFmpeg 优先，与 MDX 管线一致，兼容 MP3/M4A/FLAC/WAV 等）
        _sr_tgt = int(round(float(mp.param["band"][len(mp.param["band"])]["sr"])))
        X_wave, _ = load_audio_stereo_at_sr(audio_path, _sr_tgt)
        if X_wave.ndim == 1:
            X_wave = np.asfortranarray([X_wave, X_wave])

        # 多频带 STFT
        bands_n = len(mp.param["band"])
        X_spec_s = {}
        input_high_end = None
        input_high_end_h = 0

        for d in range(bands_n, 0, -1):
            bp = mp.param["band"][d]
            if d == bands_n:
                wav_d = X_wave
            else:
                wav_d = librosa.core.resample(
                    wav_next, mp.param["band"][d + 1]["sr"], bp["sr"],
                    res_type=bp["res_type"]
                )
            wav_next = wav_d
            X_spec_s[d] = spec_utils.wave_to_spectrogram_mt(
                wav_d, bp["hl"], bp["n_fft"],
                mp.param["mid_side"], mp.param["mid_side_b2"], mp.param["reverse"]
            )
            if d == bands_n and mp.param.get("high_end_process", "mirroring") != "none":
                input_high_end_h = (bp["n_fft"] // 2 - bp["crop_stop"]) + (
                    mp.param["pre_filter_stop"] - mp.param["pre_filter_start"]
                )
                input_high_end = X_spec_s[d][:, bp["n_fft"] // 2 - input_high_end_h: bp["n_fft"] // 2, :]

        X_spec_m = spec_utils.combine_spectrograms(X_spec_s, mp)

        # 推理
        aggressiveness = {"value": 0.2, "split_bin": mp.param["band"][1]["crop_stop"]}
        data = {"postprocess": False, "tta": False, "window_size": 512, "agg": 20, "high_end_process": "mirroring"}

        with torch.no_grad():
            pred, X_mag, X_phase = inference(X_spec_m, device, model, aggressiveness, data)

        y_spec_m = pred * X_phase
        v_spec_m = X_spec_m - y_spec_m

        # De-Echo 模型的 primary/secondary 是反的
        if is_decho:
            vocal_spec = y_spec_m
            inst_spec = v_spec_m
        else:
            vocal_spec = v_spec_m
            inst_spec = y_spec_m

        # Karaoke / 去和声类模型：谱域 pred 与残差对应的主唱、和声，与「vocal=残差、instrumental=pred」的常见命名相反。
        # 不交换则多步串联里 _extract_vocal 会得到和声、harmony_file 会得到主唱，最终「人声」「和声」文件内容颠倒。
        if "karaoke" in model_name.lower():
            vocal_spec, inst_spec = inst_spec, vocal_spec

        # ISTFT + 合并频带
        if input_high_end is not None and data["high_end_process"].startswith("mirroring"):
            input_high_end_ = spec_utils.mirroring(data["high_end_process"], inst_spec, input_high_end, mp)
            wav_inst = spec_utils.cmb_spectrogram_to_wave(inst_spec, mp, input_high_end_h, input_high_end_)
            input_high_end_v = spec_utils.mirroring(data["high_end_process"], vocal_spec, input_high_end, mp)
            wav_vocal = spec_utils.cmb_spectrogram_to_wave(vocal_spec, mp, input_high_end_h, input_high_end_v)
        else:
            wav_inst = spec_utils.cmb_spectrogram_to_wave(inst_spec, mp)
            wav_vocal = spec_utils.cmb_spectrogram_to_wave(vocal_spec, mp)

        # 保存结果（文件名必须和实际内容一致，不能反转）
        output_files = []
        vocal_path = os.path.join(output_dir, f"{audio_name}_vocal.wav")
        inst_path = os.path.join(output_dir, f"{audio_name}_instrumental.wav")
        sf.write(vocal_path, np.array(wav_vocal), mp.param["sr"])
        sf.write(inst_path, np.array(wav_inst), mp.param["sr"])
        output_files.extend([vocal_path, inst_path])

        msg = f"VR 分离完成！输出 {len(output_files)} 个文件:\n" + "\n".join(
            os.path.basename(f) for f in output_files
        )
        return output_files, msg

    except Exception as e:
        if debug:
            traceback.print_exc()
        import io as _io
        buf = _io.StringIO()
        traceback.print_exc(file=buf)
        detail = buf.getvalue().strip().split('\n')[-1]  # 最后一行（具体错误）
        return [], f"VR 分离出错: {str(e)} | {detail}"

def _find_model_file(model_name, base_dir=None):
    """在 uvr5_models/ 及其子文件夹中递归查找模型文件，返回完整路径"""
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    model_dir = os.path.join(base_dir, "uvr5_models")
    # 支持的模型扩展名
    for ext in [".ckpt", ".pth", ".onnx"]:
        # 先查根目录
        candidate = os.path.join(model_dir, model_name + ext)
        if os.path.isfile(candidate):
            return candidate
        # 再递归查子文件夹
        if os.path.isdir(model_dir):
            for sub_dir in os.listdir(model_dir):
                sub_path = os.path.join(model_dir, sub_dir)
                if os.path.isdir(sub_path):
                    candidate = os.path.join(sub_path, model_name + ext)
                    if os.path.isfile(candidate):
                        return candidate
    return None


def _detect_engine(model_name, model_dir=None):
    """根据模型文件扩展名和名称自动检测引擎类型"""
    model_file = _find_model_file(model_name, model_dir)
    if model_file is None:
        return None
    ext = os.path.splitext(model_file)[1].lower()
    if ext == ".ckpt":
        # ckpt 可能是 MDX-Net 也可能是 BS-RoFormer
        name_lower = model_name.lower()
        if "bs_roformer" in name_lower or "mel_roformer" in name_lower or "melband" in name_lower:
            return "BS-RoFormer"
        else:
            return "MDX-Net"
    elif ext == ".pth":
        return "VR Architecture"
    elif ext == ".onnx":
        return "MDX-Net"
    return None


_MODEL_GROUPS = ["伴奏分离", "去混响", "去和声", "降噪"]


def _get_models_in_group(group_name):
    """扫描指定分组文件夹中的模型名称列表（不含扩展名）"""
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uvr5_models", group_name)
    if not os.path.isdir(base_dir):
        return []
    models = []
    for f in os.listdir(base_dir):
        if f.endswith((".ckpt", ".pth", ".onnx")):
            models.append(os.path.splitext(f)[0])
    return sorted(models)


def _get_uvr_default_models():
    config_file = Config(default_settings_file, "yaml")
    default_settings = config_file.read() or {}
    webui_settings = default_settings.get("webui_settings", {})
    uvr_defaults = webui_settings.get("uvr_default_models", {})
    return uvr_defaults if isinstance(uvr_defaults, dict) else {}


def _get_uvr_default_choice(group_name, fallback="无"):
    models = _get_models_in_group(group_name)
    all_choices = ["无"] + models
    uvr_defaults = _get_uvr_default_models()
    stored = uvr_defaults.get(group_name)
    if stored in all_choices:
        return stored
    if fallback in all_choices:
        return fallback
    return models[0] if models else "无"


def get_uvr_models(group_name):
    """根据分组名返回对应的模型列表（含"无"选项）"""
    models = _get_models_in_group(group_name)
    all_choices = ["无"] + models
    default_val = _get_uvr_default_choice(group_name)
    return gr.Dropdown.update(choices=all_choices, value=default_val)


def set_uvr_default_model(group_name, selected_model):
    if not selected_model:
        return f"[{group_name}] 未选择模型，无法设为默认。"
    valid_choices = ["无"] + _get_models_in_group(group_name)
    if selected_model not in valid_choices:
        return f"[{group_name}] 当前选择的模型无效，请重新选择。"
    config_file = Config(default_settings_file, "yaml")
    default_settings = config_file.read() or {}
    if "webui_settings" not in default_settings or not isinstance(default_settings["webui_settings"], dict):
        default_settings["webui_settings"] = {}
    if "uvr_default_models" not in default_settings["webui_settings"] or not isinstance(default_settings["webui_settings"]["uvr_default_models"], dict):
        default_settings["webui_settings"]["uvr_default_models"] = {}
    default_settings["webui_settings"]["uvr_default_models"][group_name] = selected_model
    config_file.save(default_settings)
    return f"[{group_name}] 默认模型已设置为：{selected_model}"


def _update_file_from_paths(path_str):
    """将隐藏 Textbox 中的文件路径字符串转为 gr.File 可用的列表"""
    if not path_str or path_str.strip() == "":
        return None
    # 路径以换行分隔，去掉空行
    paths = [p.strip() for p in path_str.strip().split("\n") if p.strip()]
    return paths if paths else None


_last_result_dir = None  # 最后一次分离的输出文件夹路径

def open_separated_root_dir():
    """打开音频分离结果根目录（separated），无结果时也可点击。"""
    root_dir = os.path.join(os.path.abspath("."), "separated")
    open_folder(root_dir)


def uvr_separate(audio_path, model1, model2, model3, model4, gpu_id):
    """
    多步骤串联分离：上一步的人声自动作为下一步的输入，选"无"跳过该步。
    自动根据模型文件扩展名和名称检测引擎类型（_detect_engine）。
    Generator 模式，实时推送日志到 UI。
    最终只保留关键文件：伴奏、人声、和声（可选）、降噪人声（可选）。
    """
    global _last_result_dir
    log = _LogCollector()
    log.log("=" * 50)

    # 收集有效步骤
    steps = []
    step_labels = ["伴奏分离", "去混响", "去和声", "降噪"]
    for i, mdl in enumerate([model1, model2, model3, model4]):
        if mdl and mdl != "无":
            engine = _detect_engine(mdl)
            if engine is None:
                log.log(f"警告: 无法检测模型 {mdl} 的引擎类型，跳过")
            else:
                steps.append((engine, mdl, step_labels[i]))

    if not steps:
        log.log("未选择任何模型，请至少选择一个步骤")
        yield log.get_log()
        return

    if not audio_path or not os.path.isfile(audio_path):
        log.log("请先上传音频文件")
        yield log.get_log()
        return

    # 创建歌曲文件夹（始终带时间戳后缀）
    base_dir = os.path.dirname(os.path.abspath(__file__))
    song_name = Path(audio_path).stem
    # Gradio 的 Audio 组件上传时会自动加 "-crop_min-crop_max" 后缀（如 "-0-100"），需截掉
    import re
    song_name = re.sub(r'-\d+-\d+$', '', song_name)
    from datetime import datetime
    ts = datetime.now().strftime("%H%M")
    song_dir = os.path.join(base_dir, "separated", f"{song_name}_{ts}")
    os.makedirs(song_dir, exist_ok=True)
    _last_result_dir = song_dir
    log.log(f"输出目录: {song_dir}")

    # 临时目录（存放每步的中间结果，最终清理）
    temp_dir = os.path.join(song_dir, "_temp")
    os.makedirs(temp_dir, exist_ok=True)

    total_steps = len(steps)
    log.log(f"多步骤分离模式：共 {total_steps} 步")
    for idx, (eng, mdl, label) in enumerate(steps, 1):
        log.log(f"  Step {idx}: [{label}] {mdl} ({eng})")
    log.log("=" * 50)
    yield log.get_log()

    gpu_id = int(gpu_id) if gpu_id is not None else 0
    current_audio = audio_path

    # 记录关键数据
    instrumental_file = None     # Step 1 的伴奏
    final_vocal_file = None       # 最终人声（最后一步输出）
    harmony_file = None           # 去和声步骤直接输出的和声文件（VR 模型等）
    harmony_input_file = None     # 去和声步骤的输入文件路径（差值法 fallback）
    harmony_output_file = None    # 去和声步骤的输出文件路径（差值法 fallback）

    for step_num, (engine, model_name, label) in enumerate(steps, 1):
        log.log("")
        log.log(f"[Step {step_num}/{total_steps}] [{label}] {model_name}")
        if step_num > 1:
            log.log(f"[Step {step_num}/{total_steps}] 输入: 上一步的人声")
        yield log.get_log()

        step_files = []

        try:
            if engine == "MDX-Net":
                for msg in _mdx_separate(current_audio, model_name, gpu_id, log, output_dir=temp_dir):
                    if isinstance(msg, tuple):
                        _, files = msg
                        if files:
                            step_files = [f.strip() for f in files.split("\n") if f.strip()]
                        yield msg[0]
                    else:
                        yield msg
            elif engine == "VR Architecture":
                for msg in _vr_separate(current_audio, model_name, gpu_id, log, output_dir=temp_dir):
                    if isinstance(msg, tuple):
                        _, files = msg
                        if files:
                            step_files = [f.strip() for f in files.split("\n") if f.strip()]
                        yield msg[0]
                    else:
                        yield msg
            elif engine == "BS-RoFormer":
                for msg in _bs_roformer_separate(current_audio, model_name, gpu_id, log, output_dir=temp_dir):
                    if isinstance(msg, tuple):
                        _, files = msg
                        if files:
                            step_files = [f.strip() for f in files.split("\n") if f.strip()]
                        yield msg[0]
                    else:
                        yield msg
            else:
                log.log(f"未知引擎: {engine}，跳过")
                continue
        except Exception as e:
            import traceback
            traceback.print_exc()
            log.log(f"[Step {step_num}/{total_steps}] 失败: {str(e)}")
            yield log.get_log()
            break

        if not step_files:
            log.log(f"[Step {step_num}/{total_steps}] 本步无输出，跳过")
            # current_audio 保持不变（沿用上一步的人声），不重置为原始音频
            continue

        vocal_file = _extract_vocal(step_files)
        inst_file = _extract_instrumental(step_files)

        # 去和声步骤：VR 模型直接输出和声文件；其他模型用差值法
        if label == "去和声":
            if inst_file:
                # VR 等模型直接输出了和声（instrumental 部分）
                harmony_file = inst_file
            else:
                # BS-RoFormer 等模型只输出人声，需要差值法
                harmony_input_file = current_audio
                harmony_output_file = vocal_file

        # Step 1（伴奏分离）：保存伴奏
        if label == "伴奏分离" and inst_file:
            instrumental_file = inst_file

        # 记录最终人声
        final_vocal_file = vocal_file

        # 串联：当前步的人声作为下一步输入
        if step_num < total_steps:
            current_audio = vocal_file if vocal_file else audio_path
            log.log(f"[Step {step_num}/{total_steps}] 人声: {os.path.basename(current_audio)}")

    # ==================== 整理最终文件 ====================
    log.log("")
    log.log("=" * 50)
    log.log("正在整理最终文件...")

    final_files = []

    if instrumental_file:
        dest = os.path.join(song_dir, f"{song_name}_伴奏.wav")
        shutil.move(instrumental_file, dest)
        final_files.append(dest)
        log.log(f"  ✓ 伴奏: {song_name}_伴奏.wav")

    # 和声：优先用去和声步骤直接输出的文件，否则用差值法
    # 必须在 move final_vocal 之前处理
    if harmony_file:
        dest = os.path.join(song_dir, f"{song_name}_和声.wav")
        shutil.move(harmony_file, dest)
        final_files.append(dest)
        log.log(f"  ✓ 和声: {song_name}_和声.wav")
    elif harmony_input_file and harmony_output_file:
        harmony_path = os.path.join(song_dir, f"{song_name}_和声.wav")
        try:
            v_in, sr_in = sf.read(harmony_input_file, dtype='float32')
            v_out, sr_out = sf.read(harmony_output_file, dtype='float32')
            min_len = min(len(v_in), len(v_out))
            harmony = v_in[:min_len] - v_out[:min_len]
            sf.write(harmony_path, harmony, sr_in)
            final_files.append(harmony_path)
            log.log(f"  ✓ 和声(差值): {song_name}_和声.wav")
        except Exception as e:
            log.log(f"  ✗ 和声提取失败: {e}")

    if final_vocal_file:
        dest = os.path.join(song_dir, f"{song_name}_人声.wav")
        shutil.move(final_vocal_file, dest)
        final_files.append(dest)
        log.log(f"  ✓ 人声: {song_name}_人声.wav")

    # 清理临时目录
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except:
        pass

    log.log(f"")
    log.log(f"全部完成！共 {len(final_files)} 个文件 → {song_dir}")
    for f in final_files:
        log.log(f"  {os.path.basename(f)}")
    yield log.get_log()


def _extract_vocal(file_list):
    """从分离输出的文件列表中提取人声文件路径（优先选含 vocal(s)/人声 的，否则选第一个非 instrumental 的）"""
    if not file_list:
        return None
    # 优先匹配 vocals / vocal（但不能同时含 instrumental）
    for f in file_list:
        basename = os.path.basename(f).lower()
        if ("vocals" in basename or "vocal" in basename or "人声" in basename) and "instrumental" not in basename:
            return f
    # 排除 instrumental
    for f in file_list:
        basename = os.path.basename(f).lower()
        if "instrumental" not in basename and "伴奏" not in basename:
            return f
    # 兜底返回第一个
    return file_list[0]


def _extract_instrumental(file_list):
    """从分离输出的文件列表中提取伴奏文件路径"""
    if not file_list:
        return None
    # 优先匹配 instrumental（不能同时含 vocal）
    for f in file_list:
        basename = os.path.basename(f).lower()
        if ("instrumental" in basename or "伴奏" in basename) and "vocal" not in basename:
            return f
    # 第二优先：排除含 vocal 的文件
    for f in file_list:
        basename = os.path.basename(f).lower()
        if "vocal" not in basename and "人声" not in basename:
            return f
    return None


class _LogCollector:
    """收集日志：同时 print 到黑框 + 存储到字符串，最终可获取完整日志"""
    def __init__(self):
        self._lines = []

    def log(self, msg):
        print(msg, flush=True)
        self._lines.append(msg)

    def progress(self, msg):
        """更新进度行（覆盖上一条 progress 行，不追加新行）"""
        if self._lines and self._lines[-1].startswith("["):
            # 上一行是进度行，覆盖它
            self._lines[-1] = msg
        else:
            self._lines.append(msg)
        print(f"\r{msg}", end="", flush=True)

    def get_log(self):
        return "\n".join(self._lines)


def _mdx_separate(audio_path, model_name, gpu_id, log, output_dir=None):
    """MDX-Net 分离（generator 版，实时日志）"""
    if not audio_path or not os.path.isfile(audio_path):
        log.log("请先上传音频文件")
        yield log.get_log(), None
        return

    gpu_id = int(gpu_id) if gpu_id is not None else 0
    base_dir = os.path.dirname(os.path.abspath(__file__))
    mdx_data_dir = os.path.join(base_dir, "mdx_model_data")

    # 查找模型文件（在 uvr5_models/ 及子文件夹中递归搜索）
    model_file = _find_model_file(model_name, base_dir)
    if model_file is None:
        log.log(f"模型 {model_name} 未找到")
        yield log.get_log(), None
        return

    audio_name = Path(audio_path).stem
    import time as _time
    ts_suffix = _time.strftime("_%H%M%S")
    if output_dir is None:
        output_dir = os.path.join(base_dir, "separated", "MDX")
    os.makedirs(output_dir, exist_ok=True)

    if gpu_id >= 0 and torch.cuda.is_available():
        device = f"cuda:{gpu_id}"
    else:
        device = "cpu"

    log.log(f"[UVR5] 引擎: MDX-Net, 模型: {model_name}")
    log.log(f"[MDX] 输入文件: {audio_path}")

    # getsize 在网络盘 / OneDrive「仅联机」文件上可能无限阻塞，导致界面永远停在第一次 yield 之后
    import threading as _threading

    _sz_ref, _sz_err = [None], [None]

    def _getsize_worker():
        try:
            _sz_ref[0] = os.path.getsize(audio_path)
        except Exception as e:
            _sz_err[0] = e

    _gst = _threading.Thread(target=_getsize_worker, daemon=True)
    _gst.start()
    _gst.join(timeout=5.0)
    if _gst.is_alive():
        log.log("[MDX] 获取文件大小超时（常见于网络路径、OneDrive 未本地下载）；将继续解码")
    elif _sz_err[0] is not None:
        log.log(f"[MDX] 无法读取文件大小: {_sz_err[0]}")
    else:
        log.log(f"[MDX] 约 {_sz_ref[0] / (1024 * 1024):.2f} MB")

    _ffp = ffmpeg_binary()
    log.log(f"[MDX] FFmpeg: {_ffp or '未找到（将尝试 soundfile/librosa，部分格式易卡住）'}")

    log.log("[MDX] 正在加载音频（解码可能较久，日志会每秒刷新进度）…")
    yield log.get_log(), None

    _load_pack, _load_err, _load_done = [None], [None], [False]

    def _load_worker():
        try:
            _load_pack[0] = load_audio_stereo_at_sr(audio_path, 44100, log=None)
        except Exception as e:
            _load_err[0] = e
        finally:
            _load_done[0] = True

    _threading.Thread(target=_load_worker, daemon=True).start()
    _hb0 = _time.time()
    while not _load_done[0]:
        _elapsed = int(_time.time() - _hb0)
        log.progress(f"[MDX] 解码进行中… 已等待 {_elapsed}s（若一直不涨请查 FFmpeg、杀毒软件或换本地 WAV）")
        yield log.get_log(), None
        _time.sleep(1)

    if _load_err[0] is not None:
        log.log(f"[MDX] 音频加载失败: {_load_err[0]}")
        yield log.get_log(), None
        return

    mix, sr = _load_pack[0]

    duration_sec = mix.shape[1] / 44100
    log.log(f"[MDX] 音频已加载: {duration_sec:.1f} 秒")
    yield log.get_log(), None

    if model_file.endswith(".ckpt"):
        # MDX23C 推理
        import importlib.util
        mdx_mod_path = os.path.join(base_dir, "mdx_inference.py")
        spec = importlib.util.spec_from_file_location("mdx_inference", mdx_mod_path)
        mdx_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mdx_mod)

        config_yaml = "model_2_stem_full_band_8k.yaml"
        config_path = os.path.join(mdx_data_dir, "mdx_c_configs", config_yaml)
        params = mdx_mod.get_mdx_params(model_file, mdx_data_dir)
        if params and "config_yaml" in params:
            config_yaml = params["config_yaml"]
            config_path = os.path.join(mdx_data_dir, "mdx_c_configs", config_yaml)
        if not os.path.isfile(config_path):
            config_path = os.path.join(mdx_data_dir, config_yaml)
        if not os.path.isfile(config_path):
            log.log(f"找不到配置文件: {config_yaml}")
            yield log.get_log(), None
            return

        log.log(f"[MDX-C] 正在加载模型 {model_name}（{device}）...")
        yield log.get_log(), None

        config = mdx_mod.load_mdx_c_config(config_path)
        model = mdx_mod.TFC_TDF_net(config, device)
        model.load_state_dict(torch.load(model_file, map_location="cpu"))
        model.to(device).eval()
        log.log(f"[MDX-C] 模型加载完成")
        yield log.get_log(), None

        overlap = config.inference.num_overlap if hasattr(config.inference, 'num_overlap') else 8
        chunk_size = config.audio.hop_length * (config.inference.dim_t - 1)
        log.log(f"[MDX-C] 开始推理: overlap={overlap}, chunk_size={chunk_size}")
        yield log.get_log(), None

        # 用线程运行推理，generator 通过 progress_callback 保持连接活跃
        import threading
        _separate_result = [None]
        _separate_error = [None]
        _separate_progress = [0, 0]  # [done, total]
        _separate_done = [False]

        def _progress_cb(done, total):
            _separate_progress[0] = done
            _separate_progress[1] = total

        def _run_inference():
            try:
                _separate_result[0] = mdx_mod.mdx_c_separate(
                    model, config, mix, device, overlap=overlap, batch_size=1,
                    progress_callback=_progress_cb
                )
            except Exception as e:
                _separate_error[0] = e
            finally:
                _separate_done[0] = True

        t = threading.Thread(target=_run_inference, daemon=True)
        t.start()

        # generator 保持 yield 来维持 WebSocket 连接活跃
        import time as _time
        while not _separate_done[0]:
            done, total = _separate_progress
            if total > 0:
                pct = done * 100 // total
                log.progress(f"[MDX-C] 推理进度: {done}/{total} ({pct}%)")
            else:
                log.progress(f"[MDX-C] 推理中...")
            yield log.get_log(), None
            _time.sleep(2)

        t.join(timeout=5)

        if _separate_error[0] is not None:
            log.log(f"[MDX-C] 推理失败: {_separate_error[0]}")
            yield log.get_log(), None
            return

        result = _separate_result[0]
        log.log(f"[MDX-C] 推理完成")

        output_files = []
        has_instrumental = False
        if isinstance(result, dict):
            # 先保存所有 stem，同时记录原始 shape（未转置前）用于差值计算
            saved_stems = {}
            for stem_name, stem_audio in result.items():
                stem_audio = np.asarray(stem_audio).squeeze()
                if stem_audio.ndim == 3:
                    stem_audio = stem_audio[0]
                # 判断是否需要转置：确保 (T, 2) 格式用于保存
                if stem_audio.ndim == 2 and stem_audio.shape[0] < stem_audio.shape[1]:
                    stem_audio_save = stem_audio.T
                else:
                    stem_audio_save = stem_audio
                out_path = os.path.join(output_dir, f"{audio_name}{ts_suffix}_{stem_name.lower()}.wav")
                sf.write(out_path, stem_audio_save, 44100)
                output_files.append(out_path)
                log.log(f"[MDX-C] 输出: {os.path.basename(out_path)}")
                if "instrumental" in stem_name.lower():
                    has_instrumental = True
                # 保存原始（可能未转置的）stem 用于差值计算
                saved_stems[stem_name] = stem_audio
            # 如果没有伴奏 stem，手动做差值
            if not has_instrumental:
                for stem_name, stem_audio in saved_stems.items():
                    # 确保和 mix 同维度 (2, T) → 转为 (T, 2) 后计算差值再转回
                    stem_t = stem_audio.T if stem_audio.shape[0] < stem_audio.shape[1] else stem_audio
                    mix_t = mix.T  # (2, T) → (T, 2)
                    min_len = min(stem_t.shape[0], mix_t.shape[0])
                    inst_t = mix_t[:min_len] - stem_t[:min_len]
                    out_path = os.path.join(output_dir, f"{audio_name}{ts_suffix}_instrumental.wav")
                    sf.write(out_path, inst_t, 44100)
                    output_files.append(out_path)
                    log.log(f"[MDX-C] 输出(差值): {os.path.basename(out_path)}")
                    break  # 只需算一次
        else:
            stem_audio = np.asarray(result).squeeze()
            compensate = params.get("compensate", 1.0) if params else 1.0
            stem_audio = stem_audio * compensate
            if stem_audio.ndim == 3:
                stem_audio = stem_audio[0]
            if stem_audio.ndim == 2 and stem_audio.shape[0] < stem_audio.shape[1]:
                stem_audio_save = stem_audio.T
            else:
                stem_audio_save = stem_audio
            stem_name = params.get("primary_stem", "Vocals") if params else "Vocals"
            out_path = os.path.join(output_dir, f"{audio_name}{ts_suffix}_{stem_name.lower()}.wav")
            sf.write(out_path, stem_audio_save, 44100)
            output_files.append(out_path)
            log.log(f"[MDX-C] 输出: {os.path.basename(out_path)}")
            # 手动做差值算伴奏
            stem_t = stem_audio.T if stem_audio.shape[0] < stem_audio.shape[1] else stem_audio
            mix_t = mix.T
            min_len = min(stem_t.shape[0], mix_t.shape[0])
            inst_t = mix_t[:min_len] - stem_t[:min_len]
            inst_name = "instrumental" if stem_name.lower() == "vocals" else "other"
            out_path2 = os.path.join(output_dir, f"{audio_name}{ts_suffix}_{inst_name}.wav")
            sf.write(out_path2, inst_t, 44100)
            output_files.append(out_path2)
            log.log(f"[MDX-C] 输出(差值): {os.path.basename(out_path2)}")

        # 释放模型
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        log.log(f"[UVR5] 分离完成！共输出 {len(output_files)} 个文件，文件在 separated/MDX/ 目录")
        # 文件路径转为换行分隔字符串，传给隐藏 Textbox，由 .then() 回调转给 gr.File
        yield log.get_log(), "\n".join(output_files)

    else:
        # ONNX 推理
        import onnxruntime as ort

        mdx_mod_path = os.path.join(base_dir, "mdx_inference.py")
        spec = importlib.util.spec_from_file_location("mdx_inference", mdx_mod_path)
        mdx_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mdx_mod)

        log.log(f"[MDX-ONNX] 正在加载模型 {model_name}...")
        yield log.get_log(), None

        if gpu_id >= 0 and ort.get_device() == "GPU":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]

        session = ort.InferenceSession(model_file, providers=providers)
        model_params = mdx_mod.get_mdx_params(model_file, mdx_data_dir) or {}
        compensate = model_params.get("compensate", 1.0)
        primary_stem = model_params.get("primary_stem", "Vocals")

        if not model_params:
            model_params = {
                "mdx_n_fft_scale_set": 6144, "mdx_dim_f_set": 2048, "mdx_dim_t_set": 8,
                "compensate": 1.035, "primary_stem": "Vocals",
            }

        log.log("[MDX-ONNX] 开始推理...")
        yield log.get_log(), None

        tar_waves = mdx_mod.mdx_onnx_separate(session, mix, model_params, device)

        output_files = []
        primary_audio = tar_waves[0] * compensate
        out_path = os.path.join(output_dir, f"{audio_name}{ts_suffix}_{primary_stem.lower()}.wav")
        sf.write(out_path, primary_audio, 44100)
        output_files.append(out_path)

        secondary_audio = mix - primary_audio
        secondary_name = "instrumental" if primary_stem.lower() == "vocals" else "other"
        out_path2 = os.path.join(output_dir, f"{audio_name}{ts_suffix}_{secondary_name}.wav")
        sf.write(out_path2, secondary_audio, 44100)
        output_files.append(out_path2)

        log.log(f"[UVR5] 分离完成！共输出 {len(output_files)} 个文件，文件在 separated/MDX/ 目录")
        yield log.get_log(), "\n".join(output_files)


def _vr_separate(audio_path, model_name, gpu_id, log, output_dir=None):
    """VR Architecture 分离（generator 版，日志实时更新到 UI）"""
    if not audio_path or not os.path.isfile(audio_path):
        log.log("请先上传音频文件")
        yield log.get_log(), None
        return

    import threading as _vr_th
    import time as _vr_time

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if output_dir is None:
        output_dir = os.path.join(base_dir, "separated", "VR")
    os.makedirs(output_dir, exist_ok=True)

    log.log(f"[UVR5] 引擎: VR Architecture, 模型: {model_name}")
    log.log(f"[VR] 输入: {audio_path}")
    log.log(
        f"[VR] 正在运行分离（加载权重 / 解码 / 多频带 STFT / GPU 推理；"
        f"长音频可能需数分钟，日志每秒刷新）…"
    )
    yield log.get_log(), None

    _vr_result, _vr_error, _vr_done = [None], [None], [False]

    def _run_vr():
        try:
            _vr_result[0] = vr_separate(audio_path, model_name, gpu_id, output_dir)
        except Exception as e:
            _vr_error[0] = e
        finally:
            _vr_done[0] = True

    _vr_th.Thread(target=_run_vr, daemon=True).start()
    _vr_t0 = _vr_time.time()
    while not _vr_done[0]:
        _elapsed = int(_vr_time.time() - _vr_t0)
        log.progress(
            f"[VR] 处理中… 已等待 {_elapsed}s（STFT 与推理阶段 CPU/GPU 会满载，属正常）"
        )
        yield log.get_log(), None
        _vr_time.sleep(1)

    if _vr_error[0] is not None:
        log.log(f"[VR] 异常中断: {_vr_error[0]}")
        yield log.get_log(), None
        return

    result = _vr_result[0]
    if isinstance(result, tuple) and len(result) == 2:
        output_files, msg = result
        if not output_files and msg:
            log.log(f"[VR] 错误: {msg}")
    else:
        log.log(f"[VR] 返回格式异常: {type(result)}")
        yield log.get_log(), None
        return

    if output_files:
        for f in output_files:
            log.log(f"[VR] 输出: {os.path.basename(f)}")
    log.log(f"[UVR5] 分离完成！共输出 {len(output_files)} 个文件，文件在 separated/VR/ 目录")
    yield log.get_log(), "\n".join(output_files) if output_files else None


def _bs_roformer_separate(audio_path, model_name, gpu_id, log, output_dir=None):
    """BS-RoFormer 分离（generator 版，日志实时更新到 UI，带心跳保持连接）"""
    if not audio_path or not os.path.isfile(audio_path):
        log.log("请先上传音频文件")
        yield log.get_log(), None
        return

    import importlib.util
    import threading
    import time as _time
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if output_dir is None:
        output_dir = os.path.join(base_dir, "separated", "BSRoformer")
    os.makedirs(output_dir, exist_ok=True)
    bs_path = os.path.join(base_dir, "bs_roformer_inference.py")
    spec = importlib.util.spec_from_file_location("bs_roformer_inference", bs_path)
    bs_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs_mod)

    log.log(f"[UVR5] 引擎: BS-RoFormer, 模型: {model_name}")
    log.log(f"[BS-RoFormer] 正在加载模型 {model_name}...")
    yield log.get_log(), None

    # 用线程运行推理，generator 通过 yield 保持 WebSocket 连接活跃
    _bs_result = [None]
    _bs_error = [None]
    _bs_done = [False]

    def _run_bs():
        try:
            _bs_result[0] = bs_mod.bs_roformer_separate(audio_path, model_name, gpu_id, output_dir=output_dir)
        except Exception as e:
            _bs_error[0] = e
        finally:
            _bs_done[0] = True

    t = threading.Thread(target=_run_bs, daemon=True)
    t.start()

    while not _bs_done[0]:
        log.progress(f"[BS-RoFormer] 推理中...")
        yield log.get_log(), None
        _time.sleep(2)

    t.join(timeout=5)

    if _bs_error[0] is not None:
        log.log(f"[BS-RoFormer] 推理失败: {_bs_error[0]}")
        yield log.get_log(), None
        return

    result = _bs_result[0]
    output_files, _ = result

    if output_files:
        for f in output_files:
            log.log(f"[BS-RoFormer] 输出: {os.path.basename(f)}")
    log.log(f"[UVR5] 分离完成！共输出 {len(output_files)} 个文件")
    yield log.get_log(), "\n".join(output_files)





#read default params
sovits_params, diff_params, second_dir_enable = get_default_settings()
ckpt_read_dir = second_dir if second_dir_enable else workdir
config_read_dir = second_dir if second_dir_enable else config_dir
diff_read_dir = diff_second_dir if second_dir_enable else diff_workdir
current_mode = get_current_mode()

# create dirs if they don't exist
dirs_to_check = [
    workdir,
    second_dir,
    diff_workdir,
    diff_second_dir,
    dataset_dir,
]
for dir in dirs_to_check:
    if not os.path.exists(dir):
        os.makedirs(dir)

# read ckpt list
ckpt_list, config_list, cluster_list, diff_list, diff_config_list = load_options()

# read available encoder list
encoder_list = get_available_encoder()

#read GPU info
ngpu=torch.cuda.device_count()
gpu_infos=[]
if(torch.cuda.is_available() is False or ngpu==0):
    if_gpu_ok=False
else:
    if_gpu_ok = False
    for i in range(ngpu):
        gpu_name=torch.cuda.get_device_name(i)
        if("MX"in gpu_name):
            continue
        if("RTX" in gpu_name.upper() or "10"in gpu_name or "16"in gpu_name or "20"in gpu_name or "30"in gpu_name or "40"in gpu_name or "A50"in gpu_name.upper() or "70"in gpu_name or "80"in gpu_name or "90"in gpu_name or "M4"in gpu_name or"P4"in gpu_name or "T4"in gpu_name or "TITAN"in gpu_name.upper()):#A10#A100#V100#A40#P40#M40#K80
            if_gpu_ok=True#至少有一张能用的N卡
            gpu_infos.append("%s\t%s"%(i,gpu_name))
gpu_info="\n".join(gpu_infos)if if_gpu_ok is True and len(gpu_infos)>0 else "很遗憾您这没有能用的显卡来支持您训练"
gpus="-".join([i[0]for i in gpu_infos])

#read cuda info for inference
cuda = {}
min_vram = 0
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        current_vram = torch.cuda.get_device_properties(i).total_memory
        min_vram = current_vram if current_vram > min_vram else min_vram
        device_name = torch.cuda.get_device_properties(i).name
        cuda[f"CUDA:{i} {device_name}"] = f"cuda:{i}"
total_vram = round(min_vram * 9.31322575e-10) if min_vram != 0 else 0
auto_batch = total_vram - 2 if total_vram <= 12 and total_vram > 0 else total_vram
print(f"Current vram: {total_vram} GiB, recommended batch size: {auto_batch}")

#Check BF16 support
amp_options = ["fp32", "fp16"]
if if_gpu_ok:
    if torch.cuda.is_bf16_supported():
        amp_options = ["fp32", "fp16", "bf16"] 

#Get F0 Options
f0_options = ["crepe","pm","dio","harvest","rmvpe","fcpe"]

#Get Vocoder Options
vocoder_options = []
for dir in os.listdir("pretrain"):
    if os.path.isdir(os.path.join("pretrain", dir)):
        if os.path.isfile(os.path.join("pretrain", dir, "model")) and os.path.isfile(os.path.join("pretrain", dir, "config.json")):
            vocoder_options.append(dir)


from gradio.themes import Base
from gradio.themes.utils import colors

# 自定义主题：橙色强调色 + 加深输入框边框和文字，提高可读性
custom_theme = Base(primary_hue=colors.orange, secondary_hue=colors.orange).set(
    input_border_color="#444444",
    input_border_color_focus="#ea580c",
    input_border_color_hover="#555555",
    body_text_color="#222222",
    input_text_weight=500,
)

app = gr.Blocks(theme=custom_theme, css="""
/* 标题颜色强制保持紫色 */
.gradio-container .app-main-title,
.gradio-container .app-main-title * {
    color: #7C3AED !important;
    -webkit-text-fill-color: #7C3AED !important;
}

.gradio-container .app-main-subtitle,
.gradio-container .app-main-subtitle * {
    color: #000000 !important;
    -webkit-text-fill-color: #000000 !important;
    font-size: 1.02em !important;
    font-weight: 600 !important;
}

/* 全局强调色改为橙色，覆盖 Gradio 默认蓝色 */
.gradio-container {
    --color-accent: #f59e0b !important;
    --color-accent-soft: #fed7aa !important;
    --color-accent-50: #fff7ed !important;
    --color-accent-100: #ffedd5 !important;
    --color-accent-200: #fed7aa !important;
    --color-accent-300: #fdba74 !important;
    --color-accent-400: #fb923c !important;
    --color-accent-500: #f59e0b !important;
    --color-accent-600: #ea580c !important;
    --color-accent-700: #c2410c !important;
}

/* 所有原生 button：黑色描边（下拉/滑条/文本框等不是 button；Tab 切换条见下方单独去掉） */
.gradio-container button {
    box-sizing: border-box !important;
    border: 1px solid #000000 !important;
}

/* 按钮：去除悬停描边；排除 Accordion 折叠头（.label-wrap），避免按下位移导致要点很多次 */
.gradio-container button:not([role="tab"]):not(.label-wrap) {
    transition: transform .08s ease, box-shadow .12s ease !important;
}

.gradio-container button.primary:not([role="tab"]) {
    background: linear-gradient(135deg, #f59e0b, #ea580c) !important;
    border: 1px solid #000000 !important;
    color: #ffffff !important;
}

.gradio-container button.primary:not([role="tab"]):hover,
.gradio-container button.primary:not([role="tab"]):focus {
    background: linear-gradient(135deg, #f59e0b, #ea580c) !important;
    border: 1px solid #000000 !important;
    color: #ffffff !important;
    box-shadow: none !important;
}

/* 训练页按钮个性化样式（仅：归档模型 / 清理训练文件） */
.gradio-container #archive_models_btn,
.gradio-container #archive_models_btn button {
    color: #dc2626 !important; /* 红色字体 */
    font-weight: 700 !important;
}

.gradio-container #clean_training_files_btn,
.gradio-container #clean_training_files_btn button {
    background: #dc2626 !important; /* 红色背景 */
    border: 1px solid #000000 !important;
}

/* 按钮点击反馈（不含 Accordion 折叠头） */
.gradio-container button:not([role="tab"]):not(.label-wrap):active {
    transform: translateY(1px) scale(0.985) !important;
    box-shadow: 0 2px 8px rgba(234, 88, 12, 0.3) !important;
}

/* 勾选项颜色统一为橙色系 */
.gradio-container input[type="checkbox"],
.gradio-container input[type="radio"] {
    accent-color: #f59e0b !important;
}

/* Gradio 单选/勾选组件的选中态（常见为 aria-checked） */
.gradio-container [role="radio"][aria-checked="true"],
.gradio-container [role="checkbox"][aria-checked="true"],
.gradio-container .radio-item.selected,
.gradio-container .checkbox-item.selected {
    border-color: #f59e0b !important;
    box-shadow: inset 0 0 0 1px #f59e0b !important;
}

/* 单选圆点本体（你截图里的蓝色圆圈） */
.gradio-container [role="radio"][aria-checked="true"]::before,
.gradio-container .radio-item.selected::before {
    background: linear-gradient(135deg, #f59e0b, #ea580c) !important;
    border-color: #f59e0b !important;
}

/* Tab 切换条视为「选项」：不要黑框；勿再用 .tabs button 全选，否则会去掉面板内所有按钮边框 */
.gradio-container .tabs > div:first-child button,
.gradio-container [role="tab"] {
    border: none !important;
    box-shadow: none !important;
    transform: none !important;
}

/* Accordion 折叠头：禁用按下缩放/动画，避免命中区域漂移或连点失败 */
.gradio-container .accordion > button.label-wrap {
    pointer-events: auto !important;
    cursor: pointer !important;
    transform: none !important;
    transition: none !important;
    touch-action: manipulation;
}
.gradio-container .accordion > button.label-wrap:active {
    transform: none !important;
    box-shadow: none !important;
}

/* 推理步骤卡片：同一步骤内容统一框选 */
.step-card {
    border: 2px solid #fdba74 !important;
    border-radius: 12px !important;
    padding: 12px !important;
    margin: 10px 0 !important;
    background: rgba(255, 255, 255, 0.7) !important;
}

/* 音频分离 Step1-Step4 模型区橙色边框 */
#uvr_steps_card {
    border: 2px solid #f59e0b !important;
    border-radius: 10px !important;
    padding: 10px !important;
    margin: 6px 0 8px 0 !important;
}

/* 音频分离：设为默认按钮缩小并拉开间距 */
#uvr_steps_card .uvr-default-btn button {
    min-height: 28px !important;
    height: 28px !important;
    padding: 0 8px !important;
    font-size: 12px !important;
    line-height: 1 !important;
    width: 92% !important;
    margin: 0 auto !important;
}

#uvr_steps_card .uvr-default-btn {
    margin-top: 0 !important;
    align-self: center !important;
}

.step-title {
    margin: 0 0 8px 0 !important;
    font-weight: 700 !important;
    color: #c2410c !important;
}

/* 标题统一加黑（编码器与f0预测器说明 / 更多选项 / 填写训练设置和超参数 等） */
.gradio-container .prose h1,
.gradio-container .prose h2,
.gradio-container .prose h3,
.gradio-container .prose h4,
.gradio-container .label-wrap .label,
.gradio-container .accordion .label-wrap .label,
.gradio-container .accordion button span {
    color: #000000 !important;
    -webkit-text-fill-color: #000000 !important;
}

/* 训练参数区：浅黑色边框 */
#train_params_card {
    border: 1px solid rgba(0, 0, 0, 0.34) !important;
    border-radius: 8px !important;
    padding: 10px !important;
}

/* 显卡识别栏：黑色边框 + 圆角 */
#gpu_info_box textarea,
#gpu_info_box input {
    border: 1px solid #000000 !important;
    border-radius: 8px !important;
}

/* 同类型输入框统一：仅可编辑的文本框/数字框/多行框 */
.gradio-container textarea:not([readonly]):not([disabled]),
.gradio-container input[type="text"]:not([readonly]):not([disabled]),
.gradio-container input[type="number"]:not([readonly]):not([disabled]) {
    border: 1px solid #000000 !important;
    border-radius: 8px !important;
}

/* 输出信息框排除：不使用黑色边框（兜底） */
.gradio-container .output-text textarea,
.gradio-container .output-text input,
.gradio-container .output-html textarea,
.gradio-container .output-html input,
.gradio-container .output-markdown textarea,
.gradio-container .output-markdown input {
    border: 1px solid #d9e1eb !important;
    border-radius: 10px !important;
}

/* 训练预处理区：浅黑色边框（较明显） */
#train_preprocess_card {
    border: 1px solid rgba(0, 0, 0, 0.32) !important;
    border-radius: 8px !important;
    padding: 10px !important;
}

/* 推理页两块区域：仅加一层外框 */
#infer_step1_card,
#infer_step3_card {
    border: 2px solid #fdba74 !important;
    border-radius: 12px !important;
}

/* 模型选择栏：黑色边框 + 圆角 */
#model_select_box .wrap {
    border: 1px solid #000000 !important;
    border-radius: 8px !important;
}

#model_select_box .gr-input,
#model_select_box select,
#model_select_box input {
    border: none !important;
    box-shadow: none !important;
}

/* 模型安装按钮：允许两行文本并居中 */
#install_model_btn button {
    min-height: 54px !important;
    padding: 8px 12px !important;
    font-size: 14px !important;
    font-weight: 700 !important;
    text-align: center !important;
}

#install_model_btn button span {
    white-space: nowrap !important;
    display: block !important;
    text-align: center !important;
}

/* 扩散模型（可选）/推理倍速：同款单层黑框 */
#diff_model_box .wrap,
#infer_speedup_box .wrap {
    border: 1px solid #000000 !important;
    border-radius: 8px !important;
}

#diff_model_box .gr-input,
#diff_model_box select,
#diff_model_box input,
#infer_speedup_box .gr-input,
#infer_speedup_box select,
#infer_speedup_box input {
    border: none !important;
    box-shadow: none !important;
}


/* 上传/拖放区域：给最外层组件加描边，包住整个拖拽框 */
#vc_single_upload,
#vc_batch_upload,
#uvr_input_upload {
    border: 2px solid #fdba74 !important; /* 兜底 */
    border-radius: 12px !important;
}

/* 清除内部子区域边框，避免只框到中间提示区 */
#vc_single_upload [data-testid="file-upload"],
#vc_single_upload .file-drop-area,
#vc_single_upload .upload-box,
#vc_single_upload .wrap,
#vc_batch_upload [data-testid="file-upload"],
#vc_batch_upload .file-drop-area,
#vc_batch_upload .upload-box,
#vc_batch_upload .wrap,
#uvr_input_upload [data-testid="file-upload"],
#uvr_input_upload .file-drop-area,
#uvr_input_upload .upload-box,
#uvr_input_upload .wrap {
    border: none !important;
    border-image: none !important;
    box-shadow: none !important;
}

#uvr_log_box textarea {
    height: 200px !important;
    max-height: 200px !important;
    min-height: 200px !important;
    overflow-y: auto !important;
    resize: none !important;
}

/* 作者说明右侧“使用指南”按钮 */
#user_guide_btn,
.gradio-container #user_guide_btn {
    width: 100% !important;
}
#guide_under_subtitle {
    margin-top: 16px !important;
    margin-bottom: 2px !important;
}
.gradio-container #user_guide_btn button,
#user_guide_btn button,
button#user_guide_btn {
    width: 100% !important;
    min-width: 100% !important;
    max-width: 100% !important;
    height: 42px !important;
    min-height: 42px !important;
    max-height: 42px !important;
    border-radius: 10px !important;
    padding: 0 12px !important;
    background-image: linear-gradient(135deg, #2563eb 0%, #ffffff 100%) !important;
    background-color: #2563eb !important;
    border: 1px solid #000000 !important;
    color: #0f172a !important;
}
.gradio-container #user_guide_btn button:hover,
.gradio-container #user_guide_btn button:focus,
#user_guide_btn button:hover,
#user_guide_btn button:focus,
button#user_guide_btn:hover,
button#user_guide_btn:focus {
    width: 100% !important;
    min-width: 100% !important;
    max-width: 100% !important;
    height: 42px !important;
    min-height: 42px !important;
    max-height: 42px !important;
    background: linear-gradient(135deg, #2563eb, #ffffff) !important;
    border: 1px solid #000000 !important;
    color: #0f172a !important;
    box-shadow: none !important;
}

""")

with app:
    if not INTEGRITY_OK:
        gr.Markdown(
            "### ⚠ 完整性校验失败，请使用官方版本\n"
            f"- 详情：`{INTEGRITY_MSG}`\n"
            "- 当前已禁用推理相关功能。\n"
            "- 如需恢复，可点击下方“恢复官方文件”按钮后重启。",
        )
        restore_official_btn = gr.Button("恢复官方文件", variant="stop")
        restore_official_msg = gr.Markdown("")

    gr.Markdown(
        """
<div style="text-align:center; margin: 6px 0 8px 0;">
  <div class="app-main-title" style="font-size:2.8em; font-weight:800; margin:0 0 8px 0; letter-spacing:2px; line-height:1.2;">So-VITS-SVC 4.8</div>
  <p class="app-main-subtitle" style="font-size:1.02em; font-weight:600; margin:0; color:#000000; -webkit-text-fill-color:#000000;">仅供个人娱乐和非商业用途，禁止用于血腥、暴力、性相关、政治相关内容</p>
</div>
        """
    )
    with gr.Row(elem_id="guide_under_subtitle"):
        with gr.Column(scale=2):
            gr.Markdown("")
        with gr.Column(scale=1, min_width=120):
            user_guide_btn = gr.Button("使用指南（点击跳转）", variant="primary", size="sm", elem_id="user_guide_btn")
        with gr.Column(scale=2):
            gr.Markdown("")
    # 给顶层 tab 内容区域添加边框
    gr.HTML(value=(
        '<script>'
        '(function(){'
        'var tabBorderColors = ["#6366F1", "#059669", "#D97706", "#DC2626", "#8B5CF6"];'
        'function applyTabBorders() {'
        '  try {'
        '    var host = document.querySelector("gradio-app");'
        '    if (!host || !host.shadowRoot) return;'
        '    var tabs = host.shadowRoot.querySelectorAll(".tabs .tabitem");'
        '    if (!tabs.length) return;'
        '    for (var i = 0; i < tabs.length; i++) {'
        '      var inner = tabs[i].querySelector(":scope > div");'
        '      if (inner && !inner.dataset.tabBordered) {'
        '        inner.dataset.tabBordered = "1";'
        '        inner.style.border = "2px solid " + (tabBorderColors[i % tabBorderColors.length]);'
        '        inner.style.borderRadius = "12px";'
        '        inner.style.padding = "16px";'
        '        inner.style.marginTop = "8px";'
        '      }'
        '    }'
        '  } catch (e) {}'
        '}'
        'applyTabBorders();'
        'var _tbAttempts = 0;'
        'var _tbIv = setInterval(function() {'
        '  applyTabBorders();'
        '  if (++_tbAttempts >= 16) clearInterval(_tbIv);'
        '}, 450);'
        '})();'
        '</script>'
    ))
    with gr.Tabs():
        with gr.TabItem("推理") as inference_tab:
            with gr.Group(elem_classes=["step-card"], elem_id="infer_step1_card"):
                gr.Markdown("步骤 1：加载模型", elem_classes=["step-title"])
                with gr.Row():
                    with gr.Column(scale=4):
                        model_dir_mode = gr.Radio(
                            choices=["工作目录（./logs/44k）", "独立目录（./models）"],
                            value="独立目录（./models）" if second_dir_enable else "工作目录（./logs/44k）",
                            label="模型目录"
                        )
                    with gr.Column(scale=1):
                        install_model_dir_btn = gr.Button("模型安装", variant="primary", elem_id="install_model_btn")
                        gr.Markdown("<div style='text-align:center;'>独立目录（./models）</div>")
                config_choice = gr.State(value="no_config")
                config_info = gr.State(value="")
                with gr.Accordion("模型配置与加载", open=True):
                    with gr.Row():
                        choice_ckpt = gr.Dropdown(label="模型", choices=ckpt_list, value="no_model", scale=3, elem_id="model_select_box", filterable=True)
                        model_branch = gr.Textbox(label="编码器", placeholder="选择模型后显示", interactive=False, scale=1)
                        using_device = gr.Dropdown(label="推理设备", choices=["Auto",*cuda.keys(),"cpu"], value="Auto", scale=1)
                    with gr.Row():
                        diff_choice = gr.Dropdown(label="扩散模型（可选）", choices=diff_list, value="no_diff", scale=3, elem_id="diff_model_box")
                        diff_config_choice = gr.Dropdown(label="扩散配置", choices=diff_config_list, value="no_diff_config", scale=1)
                        refresh = gr.Button("刷新模型", size="sm")
                    with gr.Accordion("更多选项", open=False):
                        with gr.Row():
                            cluster_choice = gr.Dropdown(label="聚类模型（可选）", choices=cluster_list, value="no_clu", scale=1)
                            vocoder_choice = gr.Dropdown(label="声码器", choices=vocoder_options, value="nsf_hifigan_finetuned", scale=1)
                            diffusion_method = gr.Dropdown(label="采样器", choices=["dpm-solver++","dpm-solver","pndm","ddim","unipc"], value="dpm-solver++", scale=1)
                            diffusion_speedup = gr.Number(label="扩散加速", value=10, scale=1)
                        with gr.Row():
                            enhance = gr.Checkbox(label="NSF-HIFIGAN增强", value=False)
                            only_diffusion = gr.Checkbox(label="全扩散推理（不推荐）", value=False)
                    with gr.Row():
                        loadckpt = gr.Button("加载模型", variant="primary", interactive=INTEGRITY_OK)
                        unload = gr.Button("卸载模型", variant="stop")
                        model_message = gr.Textbox(label="状态", interactive=False, scale=3)
                        sid = gr.Dropdown(label="说话人", value="speaker0")
            
            inference_tab.select(
                refresh_options,
                [],
                [choice_ckpt, cluster_choice, diff_choice, diff_config_choice],
                queue=False,
            )
            choice_ckpt.change(auto_load, [choice_ckpt], [model_branch, config_choice, config_info])
            diff_choice.change(auto_load_diff, [diff_choice], [diff_config_choice])
            model_dir_mode.change(
                webui_change,
                [model_dir_mode],
                [choice_ckpt, cluster_choice, diff_choice, diff_config_choice],
                queue=False,
            )
            refresh.click(
                refresh_options,
                [],
                [choice_ckpt, cluster_choice, diff_choice, diff_config_choice],
                queue=False,
            )

            gr.Markdown("步骤 2：上传音频", elem_classes=["step-title"])
            gr.Markdown(value="""
                请稍等片刻，模型加载大约需要10秒。后续操作不需要重新加载模型
                """)
            with gr.Tabs():
                with gr.TabItem("单个音频上传"):
                    vc_input3 = gr.Audio(label="单个音频上传", type="filepath", source="upload", elem_id="vc_single_upload")
                    use_microphone = gr.Checkbox(label="使用麦克风输入")
                with gr.TabItem("批量音频上传"):
                    vc_batch_files = gr.Files(label="批量音频上传", file_types=["audio"], file_count="multiple", elem_id="vc_batch_upload")
                with gr.TabItem("推理说明"):
                    gr.Markdown("""
                        **推理倍速**：按钮行中的「推理倍速」下拉框可实时调整扩散加速倍数，数值越大速度越快，无需重新加载模型。<br>
                        建议：10 = 默认质量，20 = 平衡，30+ = 追求速度
                    """)

            with gr.Group(elem_classes=["step-card"], elem_id="infer_step3_card"):
                gr.Markdown("步骤 3：调整参数并开始转换", elem_classes=["step-title"])
                with gr.Row():
                    vc_transform = gr.Number(label="变调（半音，+12升高八度）", value=0, scale=1)
                    f0_predictor = gr.Radio(label="f0预测器（如遇哑音可更换）", choices=f0_options, value="rmvpe", scale=2)
                    infer_speedup_input = gr.Dropdown(label="推理倍速", choices=["×10", "×20", "×30", "×40", "×50"], value="×10", show_label=True, scale=1, elem_id="infer_speedup_box")
                with gr.Row():
                    k_step = gr.Slider(label="浅扩散步数（需配合扩散模型）", value=100, minimum=1, maximum=1000, scale=3)
                    cl_num = gr.Number(label="自动切片(秒/0默认)", value=0, scale=1)
                with gr.Accordion("更多参数", open=False):
                    with gr.Row():
                        auto_f0 = gr.Checkbox(label="自动f0预测（配合聚类效果更好，会导致变调失效，歌声勿选会跑调）", value=False, scale=2)
                        cluster_ratio = gr.Number(label="聚类混合比例(0-1，提升音色但咬字下降)", value=0, scale=1)
                        output_format = gr.Radio(label="输出格式", choices=["wav", "flac", "mp3"], value="wav", scale=1)
                    with gr.Row():
                        cr_threshold = gr.Number(label="F0过滤阈值(仅crepe有效，0-1)", value=0.05, scale=1)
                        enhancer_adaptive_key = gr.Number(label="增强器音域适应(半音)", value=0, scale=1)
                        slice_db = gr.Number(label="切片阈值", value=-50, scale=1)
                        noise_scale = gr.Number(label="noise_scale（建议不动）", value=0.4, scale=1)
                    with gr.Row():
                        pad_seconds = gr.Number(label="推理pad秒数(消除首尾异响)", value=0.5, scale=1)
                        lg_num = gr.Number(label="切片交叉淡入长度(秒)", value=1, scale=1)
                        lgr_num = gr.Number(label="切片头尾保留比例(0-1)", value=0.75, scale=1)
                        second_encoding = gr.Checkbox(label="二次编码（浅扩散前二次编码，玄学选项）", value=False, scale=1)
                    with gr.Row():
                        loudness_envelope_adjustment = gr.Number(label="响度包络融合比例", value=1, scale=1)
                        use_spk_mix = gr.Checkbox(label="动态声线融合（未完成勿开）", value=False, interactive=False, scale=1)
                with gr.Row():
                    vc_submit = gr.Button("音频转换", variant="primary", interactive=INTEGRITY_OK)
                    vc_batch_submit = gr.Button("批量转换", variant="primary", interactive=INTEGRITY_OK)
                    open_results_btn = gr.Button("转换结果", variant="primary")
                vc_output1 = gr.HTML(label="Output", value="")
        
        loadckpt.click(load_model_func,[choice_ckpt,cluster_choice,config_choice,enhance,diff_choice,diff_config_choice,only_diffusion,use_spk_mix,using_device,diffusion_method,diffusion_speedup,cl_num,vocoder_choice],[model_message, sid, cl_num, k_step])
        unload.click(model_empty_cache, [], [sid, model_message])
        if not INTEGRITY_OK:
            restore_official_btn.click(restore_official_files, inputs=[], outputs=[restore_official_msg])
        install_model_dir_btn.click(lambda: open_folder("models"), inputs=None, outputs=None)
        use_microphone.change(source_change, [use_microphone], [vc_input3])
        open_results_btn.click(lambda: open_folder("results"), inputs=None, outputs=None)
        vc_submit.click(vc_fn, [output_format, sid, vc_input3, vc_transform,auto_f0,cluster_ratio, slice_db, noise_scale,pad_seconds,cl_num,lg_num,lgr_num,f0_predictor,enhancer_adaptive_key,cr_threshold,k_step,use_spk_mix,second_encoding,loudness_envelope_adjustment,infer_speedup_input], [vc_output1])
        vc_batch_submit.click(vc_batch_fn, [output_format, sid, vc_batch_files, vc_transform,auto_f0,cluster_ratio, slice_db, noise_scale,pad_seconds,cl_num,lg_num,lgr_num,f0_predictor,enhancer_adaptive_key,cr_threshold,k_step,use_spk_mix,second_encoding,loudness_envelope_adjustment,infer_speedup_input], [vc_output1])
        #interrupt_button.click(fn=None, inputs=None, outputs=None, cancels=[vc_event])

        with gr.TabItem("训练"):
            with gr.Group():
                gr.Markdown(value="""点击放置→ 放置切片音频【文件夹】然后点击识别""")
                with gr.Row():
                    raw_dirs_list = gr.Textbox(label="已识别的数据集", interactive=False, scale=2)
                    open_dataset_btn = gr.Button("放置训练数据", scale=1)
                    get_raw_dirs = gr.Button("识别数据集", variant="primary", scale=1)

            with gr.Group():
                with gr.Group(elem_id="train_preprocess_card"):
                    gr.Markdown(value="""确认数据集正确识别后请选择编码器和f0预测器，**训练扩散模型请选Vec768l12/hubertsoft/whisper-ppg，并确保So-VITS和扩散模型使用同一编码器**""")
                    with gr.Accordion("编码器与f0预测器说明", open=False):
                        with gr.Row():
                            gr.Markdown(value="""### 编码器说明\n**vec256l9**：ContentVec(256L9)，旧版 v1，不推荐。\n**vec768l12**：ContentVec 第 12 层 Transformer 输出，更还原训练集音色。\n**hubertsoft**：So-VITS 3.0 编码器，咬字准确，但可能出现多说话人泄露。\n**whisper-ppg**：OpenAI 出品，咬字最准，但显存占用更高、耗时更长。""")
                            gr.Markdown(value="""### f0 预测器说明\n**crepe**：抗噪最强，速度较慢。\n**pm**：速度快，抗噪较弱。\n**dio**：旧版默认方案，不推荐。\n**harvest**：有一定抗噪能力，显存友好，速度较慢。\n**rmvpe**：精度高，可视为 crepe 的上位替代。\n**fcpe**：SVC 自研，速度最快，精度不输 crepe。""")
                    with gr.Row():
                        use_diff = gr.Checkbox(label="浅扩散模型（预处理时生成扩散特征文件）", value=True, scale=1)
                        branch_selection = gr.Dropdown(label="编码器", choices=encoder_list, value="vec768l12", interactive=True, scale=1)
                        f0_predictor_selection = gr.Dropdown(label="f0预测器", choices=f0_options, value="rmvpe", interactive=True, scale=1)
                    with gr.Accordion("更多预处理选项", open=False):
                        with gr.Row():
                            vol_aug = gr.Checkbox(label="响度嵌入/音量增强（仅支持vec768l12）", value=False, scale=2)
                            tiny_enable = gr.Checkbox(label="TINY训练（实时专用，需开响度嵌入）", value=False, scale=2)
                        with gr.Row():
                            skip_loudnorm = gr.Checkbox(label="跳过响度匹配（已做过响度匹配时勾选）", value=False, scale=2)
                            num_processes = gr.Number(label="CPU线程数（12G显存建议设为2）", value=1, scale=1)
                    with gr.Row():
                        raw_preprocess = gr.Button("数据预处理", variant="primary")
                        regenerate_config_btn = gr.Button("重新生成配置文件", variant="secondary")
                preprocess_output = gr.Textbox(label="预处理输出信息，完成后请检查是否有报错", max_lines=999)
                clear_preprocess_output = gr.Button("清空输出信息")

            with gr.Group():
                with gr.Group(elem_id="train_params_card"):
                    gr.Markdown(value="""填写训练设置和超参数""")
                    with gr.Row():
                        gr.Textbox(label="当前使用显卡信息", value=gpu_info, elem_id="gpu_info_box")
                        gpu_selection = gr.Textbox(label="多卡用户请指定希望训练使用的显卡ID（0,1,2...）", value=gpus, interactive=True)
                    with gr.Row():
                        batch_size = gr.Textbox(label="批量大小，每步取多少条数据进行训练，大batch有助于训练但显著增加显存占用。6G显存建议设定为4", value=auto_batch)
                        keep_ckpts = gr.Textbox(label="仅保留最新的X个模型，超出该数字的旧模型会被删除。设置为0则永不删除", value=sovits_params['keep_ckpts'])
                    with gr.Accordion("语音模型参数", open=False):
                        with gr.Row():
                            log_interval = gr.Textbox(label="每隔多少步(steps)生成一次评估日志", value=sovits_params['log_interval'])
                            eval_interval = gr.Textbox(label="每隔多少步(steps)验证并保存一次模型", value=sovits_params['eval_interval'])
                        with gr.Row():
                            lr = gr.Textbox(label="学习率，一般不用动，批量大小较大时可以适当增大学习率，但强烈不建议超过0.0002，有炸炉风险", value=sovits_params['learning_rate'])
                            amp_dtype = gr.Radio(label="训练数据类型，fp16可能会有更快的训练速度和更低的显存占用，但容易炸炉", choices=amp_options, value=sovits_params['amp_dtype'])
                            all_in_mem = gr.Checkbox(label="是否加载所有数据集到内存中，硬盘IO过于低下、同时内存容量远大于数据集体积时可以启用，能显著加快训练速度", value=sovits_params['all_in_mem'])
                    with gr.Row():
                        gr.Markdown("请先确认说话人列表无误，再点击“写入配置文件”，随后即可开始训练。")
                        speakers = gr.Textbox(label="说话人列表")
                with gr.Accordion(label = "扩散模型参数", open=False):
                    with gr.Row():
                        diff_num_workers = gr.Number(label="num_workers, 如果你的电脑配置较高，可以将这里设置为0加快训练速度", value=diff_params['num_workers'])
                        diff_k_step_max = gr.Checkbox(label="只训练100步深度的浅扩散模型。能加快训练速度并提高模型质量，代价是无法执行超过100步的浅扩散推理", value=diff_params['diff_k_step_max'])
                        diff_cache_all_data = gr.Checkbox(label="是否缓存数据，启用后可以加快训练速度，关闭后可以节省显存或内存，但会减慢训练速度", value=diff_params['cache_all_data'])
                        diff_cache_device = gr.Radio(label="若启用缓存数据，使用显存(cuda)还是内存(cpu)缓存，如果显卡显存充足，选择cuda以加快训练速度", choices=["cuda","cpu"], value=diff_params['cache_device'])
                        diff_amp_dtype = gr.Radio(label="训练数据类型，fp16可能会有更快的训练速度，前提是你的显卡支持", choices=["fp32","fp16"], value=diff_params['amp_dtype'])
                    with gr.Row():
                        diff_batch_size = gr.Number(label="批量大小(batch_size)，根据显卡显存设置，小显存适当降低该项，6G显存可以设定为48，但该数值不要超过数据集总数量的1/4", value=diff_params['diff_batch_size'])
                        diff_lr = gr.Number(label="学习率（一般不需要动）", value=diff_params['diff_lr'])
                        diff_interval_log = gr.Number(label="每隔多少步(steps)生成一次评估日志", value=diff_params['diff_interval_log'])
                        diff_interval_val = gr.Number(label="每隔多少步(steps)验证并保存一次模型，如果你的批量大小较大，可以适当减少这里的数字，但不建议设置为1000以下", value=diff_params['diff_interval_val'])
                        diff_force_save = gr.Number(label="每隔多少步强制保留模型，只有该步数的倍数保存的模型会被保留，其余会被删除。设置为与验证步数相同的值则每个模型都会被保留", value=diff_params['diff_force_save'])
                with gr.Row():
                    save_params = gr.Button("将当前设置保存为默认设置", variant="primary")
                    write_config = gr.Button("写入配置文件", variant="primary")
                write_config_output = gr.Textbox(label="输出信息")

            with gr.Group():
                gr.Markdown(value="""**从头训练**：自动备份旧进度到 models_backup，并装载预训练模型。
                    **继续训练**：从上次保存的模型继续，无需重新预处理。
                    """)
                with gr.Row():
                    with gr.Column(scale=2):
                        start_training = gr.Button("从头开始训练", variant="primary")
                    with gr.Column(scale=1):
                        continue_training_btn = gr.Button("继续训练", variant="primary")
                with gr.Row(equal_height=True):
                    with gr.Column(scale=2):
                        training_output = gr.Textbox(label="训练输出信息", lines=2, max_lines=4)
                    with gr.Column(scale=1):
                        gr.HTML("<div style='height:30px;'></div>")
                        open_training_results_btn = gr.Button("打开训练结果（./logs/44k）", variant="secondary", elem_id="open_training_results_btn")
                gr.HTML("<div style='height:10px;'></div>")
                with gr.Row():
                    with gr.Column(scale=2):
                        diff_training_btn = gr.Button("从头训练扩散模型", variant="primary")
                    with gr.Column(scale=1):
                        diff_continue_training_btn = gr.Button("继续训练", variant="primary")
                with gr.Row(equal_height=True):
                    with gr.Column(scale=2):
                        diff_training_output = gr.Textbox(label="训练输出信息", lines=2, max_lines=4)
                    with gr.Column(scale=1):
                        gr.HTML("<div style='height:30px;'></div>")
                        archive_model_name = gr.Textbox(label="归档命名（英文/数字；中文会给扩散自动转拼音）", placeholder="例如：model01 或 安语", lines=1, max_lines=1)
                        archive_models_btn = gr.Button("确认归档", variant="secondary", elem_id="archive_models_btn")
                        clean_training_files_btn = gr.Button("清理训练文件", variant="stop", elem_id="clean_training_files_btn")
                        gr.Markdown("（归档/清理后 无法继续训练）")
                with gr.Accordion(label = "聚类、特征检索训练", open=False):
                    with gr.Row():
                        with gr.Column():
                            kmeans_button=gr.Button("训练聚类模型", variant="primary")
                            kmeans_gpu = gr.Checkbox(label="使用GPU训练", value=True)
                            kmeans_output=gr.Textbox(label="训练输出信息")
                        with gr.Column():
                            index_button=gr.Button("训练特征检索模型", variant="primary")
                            index_output=gr.Textbox(label="训练输出信息")

        with gr.TabItem("音频分离"):
            gr.Markdown(value="多步骤串联分离：上一步的人声自动作为下一步的输入，选 **\"无\"** 则跳过。新增模型 → 点击\"添加模型\"打开对应分组文件夹。")
            # 左右分栏：左侧模型选择，右侧上传+操作
            with gr.Row():
                # ====== 左栏：模型选择 ======
                with gr.Column(scale=1):
                    with gr.Group(elem_id="uvr_steps_card"):
                        with gr.Row():
                            uvr_model1 = gr.Dropdown(
                                label="Step 1 — 伴奏分离",
                                choices=get_uvr_models("伴奏分离")["choices"],
                                value=_get_uvr_default_choice("伴奏分离", "MDX23C-8KFFT-InstVoc_HQ"),
                                interactive=True,
                                scale=4,
                            )
                        set_uvr_default_btn1 = gr.Button("设为默认", size="sm", scale=1, elem_classes=["uvr-default-btn"])
                        with gr.Row():
                            uvr_model2 = gr.Dropdown(
                                label="Step 2 — 去混响",
                                choices=get_uvr_models("去混响")["choices"],
                                value=_get_uvr_default_choice("去混响", "UVR-DeEcho-DeReverb"),
                                interactive=True,
                                scale=4,
                            )
                        set_uvr_default_btn2 = gr.Button("设为默认", size="sm", scale=1, elem_classes=["uvr-default-btn"])
                        with gr.Row():
                            uvr_model3 = gr.Dropdown(
                                label="Step 3 — 去和声",
                                choices=get_uvr_models("去和声")["choices"],
                                value=_get_uvr_default_choice("去和声", "6_HP-Karaoke-UVR"),
                                interactive=True,
                                scale=4,
                            )
                        set_uvr_default_btn3 = gr.Button("设为默认", size="sm", scale=1, elem_classes=["uvr-default-btn"])
                        with gr.Row():
                            uvr_model4 = gr.Dropdown(
                                label="Step 4 — 降噪",
                                choices=get_uvr_models("降噪")["choices"],
                                value=_get_uvr_default_choice("降噪", "无"),
                                interactive=True,
                                scale=4,
                            )
                        set_uvr_default_btn4 = gr.Button("设为默认", size="sm", scale=1, elem_classes=["uvr-default-btn"])
                    add_model_btn = gr.Button("添加模型")
                # ====== 右栏：上传 + 操作 ======
                with gr.Column(scale=2):
                    input_audio = gr.Audio(label="上传音频文件", type="filepath", source="upload", elem_id="uvr_input_upload")
                    uvr_gpu = gr.Number(label="GPU 编号（-1 为 CPU）", value=0)
                    with gr.Row():
                        uvr_submit = gr.Button("开始分离", variant="primary")
                        uvr_result_btn = gr.Button("分离结果")
            # 底部：运行日志
            uvr_output_msg = gr.Textbox(label="运行日志", interactive=False, lines=8, max_lines=30, elem_id="uvr_log_box")
            gr.HTML(value="""<script>
setInterval(function(){
    var el = document.querySelector('#uvr_log_box textarea');
    if (el) el.scrollTop = el.scrollHeight;
}, 300);
</script>""")

        with gr.TabItem("音频切片"):
            gr.Markdown(value="""
                自动将音频切片为 2~15 秒片段，适合制作训练数据集。整合自 [Fish Audio Preprocessor](https://github.com/fishaudio/audio-preprocess)
                """)
            with gr.Row():
                raw_audio_path = gr.Textbox(label="原始音频文件夹", placeholder="包含所有待切片音频的文件夹，示例: D:\\干声\\speakers")
                load_raw_audio_btn = gr.Button("加载原始音频", variant = "primary")
            load_raw_audio_output = gr.Textbox(label = "输出信息")
            slicer_output_dir = gr.Textbox(label = "输出目录", placeholder = "选择输出目录（不要和输入音频是同一个文件夹）")
            with gr.Row():
                max_sec = gr.Number(label = "切片的最长秒数", value = 15)
                min_sec = gr.Number(label = "切片的最短秒数", value = 2)
            with gr.Accordion(label="高级设置", open=False):
                min_silence_dur = gr.Number(label = "视为静音的最短长度（秒）", value = 0.3)
                max_silence_kept = gr.Number(label = "切片音频周围保持的最大静音长度（秒）", value = 1.0)
            slicer_btn = gr.Button("开始切片", variant = "primary")
            slicer_output_msg = gr.Textbox(label = "输出信息")

        with gr.TabItem("小工具"):
            gr.Markdown(value="""
                        ### 小工具
                        提供了一些实用工具，可以自行探索
                        """)
            with gr.Tabs():
                with gr.TabItem("静态声线融合"):
                    gr.Markdown(value="""
                        <font size=2> 介绍:该功能可以将多个声音模型合成为一个声音模型(多个模型参数的凸组合或线性组合)，从而制造出现实中不存在的声线 
                                          注意：
                                          1.该功能仅支持单说话人的模型
                                          2.如果强行使用多说话人模型，需要保证多个模型的说话人数量相同，这样可以混合同一个SpaekerID下的声音
                                          3.保证所有待混合模型的config.json中的model字段是相同的
                                          4.输出的混合模型可以使用待合成模型的任意一个config.json，但聚类模型将不能使用
                                          5.批量上传模型的时候最好把模型放到一个文件夹选中后一起上传
                                          6.混合比例调整建议大小在0-100之间，也可以调为其他数字，但在线性组合模式下会出现未知的效果
                                          7.混合完毕后，文件将会保存在项目根目录中，文件名为output.pth
                                          8.凸组合模式会将混合比例执行Softmax使混合比例相加为1，而线性组合模式不会
                        </font>
                        """)
                    mix_model_path = gr.Files(label="选择需要混合模型文件")
                    mix_model_upload_button = gr.UploadButton("选择/追加需要混合模型文件", file_count="multiple")
                    mix_model_output1 = gr.Textbox(
                                            label="混合比例调整，单位/%",
                                            interactive = True
                                         )
                    mix_mode = gr.Radio(choices=["凸组合", "线性组合"], label="融合模式",value="凸组合",interactive = True)
                    mix_submit = gr.Button("声线融合启动", variant="primary")
                    mix_model_output2 = gr.Textbox(
                                            label="Output Message"
                                         )
                with gr.TabItem("onnx转换"):
                    gr.Markdown(value="""
                        提供了将.pth模型（批量）转换为.onnx模型的功能
                        源项目本身自带转换的功能，但不支持批量，操作也不够简单，这个工具可以支持在WebUI中以可视化的操作方式批量转换.onnx模型
                        有人可能会问，转.onnx模型有什么作用呢？相信我，如果你问出了这个问题，说明这个工具你应该用不上

                        ### Step 1: 
                        在整合包根目录下新建一个"checkpoints"文件夹，将pth模型和对应的json配置文件按目录分别放置到checkpoints文件夹下
                        看起来应该像这样：
                        checkpoints
                        ├───xxxx
                        │   ├───xxxx.pth
                        │   └───xxxx.json
                        ├───xxxx
                        │   ├───xxxx.pth
                        │   └───xxxx.json
                        └───……
                        """)
                    pth_dir_msg = gr.Textbox(label="识别待转换模型", placeholder="请将模型和配置文件按上述说明放置在正确位置")
                    pth_dir_identify_btn = gr.Button("识别", variant="primary")
                    gr.Markdown(value="""
                        ### Step 2:
                        识别正确后点击下方开始转换，转换一个模型可能需要一分钟甚至更久
                        """)
                    pth2onnx_btn = gr.Button("开始转换", variant="primary")
                    pth2onnx_msg = gr.Textbox(label="输出信息")
                    mix_model_path.change(updata_mix_info,[mix_model_path],[mix_model_output1])
                    mix_model_upload_button.upload(upload_mix_append_file, [mix_model_upload_button,mix_model_path], [mix_model_path,mix_model_output1])
                    mix_submit.click(mix_submit_click, [mix_model_output1,mix_mode], [mix_model_output2])
                    pth_dir_identify_btn.click(pth_identify, [], [pth_dir_msg])
                    pth2onnx_btn.click(onnx_export_func, [], [pth2onnx_msg])

                with gr.TabItem("模型压缩工具"):
                    gr.Markdown(value="""
                        该工具可以实现对模型的体积压缩，在**不影响模型推理功能**的情况下，将原本约600M的So-VITS模型压缩至约200M, 大大减少了硬盘的压力。
                        **注意：压缩后的模型将无法继续训练，请在确认封炉后再压缩。**
                        将模型文件放置在logs/44k下，然后选择需要压缩的模型
                    """)
                    model_to_compress = gr.Dropdown(label="模型选择", choices=ckpt_list, value="no_model")
                    fp16_compress = gr.Checkbox(label="使用 fp16 压缩", value=False)
                    compress_model_btn = gr.Button("压缩模型", variant="primary")
                    compress_model_output = gr.Textbox(label="输出信息", value="")

                    compress_model_btn.click(model_compression, [model_to_compress, fp16_compress], [compress_model_output])

                with gr.TabItem("模型发布打包/安装"):
                    gr.Markdown(value="""
                        如果你想将你的模型分享给他人，请使用该工具对模型进行打包。
                        该工具可以自动生成正确的配置文件，确保你在打包过程中不出现任何遗漏和错误，接收到使用该工具打包的模型后，也可以用该工具进行自动安装。
                    """)
                    with gr.Tabs():
                        with gr.TabItem("安装"):
                            with gr.Row():
                                model_to_install = gr.Textbox(label = "模型压缩包路径", placeholder="示例：D:\Downloads\model_packing.zip") 
                                install_model_btn = gr.Button("安装", variant="primary")
                            install_output = gr.Textbox(label="输出信息", value="")
                        with gr.TabItem("打包"):
                            with gr.Row():
                                model_to_pack = gr.Dropdown(label="选择要打包的模型", choices=ckpt_list, value="")
                                model_config = gr.Dropdown(label="选择要打包的模型配置文件", choices=config_list, value="", interactive=True)
                                speaker_name = gr.Textbox(label="模型说话人名称", placeholder="该模型的说话人名称，仅限数字字母下划线，如模型中有多说话人，请用逗号分割，例如：spk1,spk2,spk3", value = "")
                            with gr.Row():
                                diff_to_pack = gr.Dropdown(label="（可选）选择要打包的扩散模型", choices=diff_list, value="no_diff")
                                cluster_to_pack = gr.Dropdown(label="（可选）选择要打包的聚类或特征检索模型", choices=cluster_list, value="no_cluster")
                            packing_btn = gr.Button("开始打包", variant="primary")
                            packing_output_msg = gr.Textbox(label = "输出信息")
                   
                    model_to_pack.change(pack_autoload, [model_to_pack], [model_config, speaker_name])
                    packing_btn.click(release_packing, [model_to_pack, model_config, speaker_name, diff_to_pack, cluster_to_pack], [packing_output_msg])
                    install_model_btn.click(release_install, [model_to_install], [install_output])



        # 音频切片事件绑定
        load_raw_audio_btn.click(load_raw_audio, [raw_audio_path], [load_raw_audio_output, slicer_output_dir])
        slicer_btn.click(
            auto_slice,
            [raw_audio_path, slicer_output_dir, max_sec, min_sec, min_silence_dur, max_silence_kept],
            [slicer_output_msg],
            queue=False,
        )
        # 音频分离事件绑定
        add_model_btn.click(lambda: open_folder("uvr5_models"), inputs=None, outputs=None)
        set_uvr_default_btn1.click(lambda m: set_uvr_default_model("伴奏分离", m), inputs=[uvr_model1], outputs=[uvr_output_msg])
        set_uvr_default_btn2.click(lambda m: set_uvr_default_model("去混响", m), inputs=[uvr_model2], outputs=[uvr_output_msg])
        set_uvr_default_btn3.click(lambda m: set_uvr_default_model("去和声", m), inputs=[uvr_model3], outputs=[uvr_output_msg])
        set_uvr_default_btn4.click(lambda m: set_uvr_default_model("降噪", m), inputs=[uvr_model4], outputs=[uvr_output_msg])
        uvr_submit.click(uvr_separate, [input_audio, uvr_model1, uvr_model2, uvr_model3, uvr_model4, uvr_gpu], [uvr_output_msg])
        uvr_result_btn.click(
            open_separated_root_dir,
            inputs=None, outputs=None
        )
        # 训练事件绑定
        get_raw_dirs.click(load_raw_dirs,[],[raw_dirs_list])
        open_dataset_btn.click(lambda: open_folder(raw_path), inputs=None, outputs=None)
        raw_preprocess.click(dataset_preprocess,[branch_selection, f0_predictor_selection, use_diff, vol_aug, skip_loudnorm, num_processes, tiny_enable],[preprocess_output, speakers])
        regenerate_config_btn.click(regenerate_config,[branch_selection, vol_aug, tiny_enable],[preprocess_output])
        clear_preprocess_output.click(clear_output,[],[preprocess_output])
        save_params.click(save_default_settings, [log_interval,eval_interval,keep_ckpts,batch_size,lr,amp_dtype,all_in_mem,diff_num_workers,diff_cache_all_data,diff_cache_device,diff_amp_dtype,diff_batch_size,diff_lr,diff_interval_log,diff_interval_val,diff_force_save,diff_k_step_max], [write_config_output])
        write_config.click(config_fn,[log_interval, eval_interval, keep_ckpts, batch_size, lr, amp_dtype, all_in_mem, diff_num_workers, diff_cache_all_data, diff_batch_size, diff_lr, diff_interval_log, diff_interval_val, diff_cache_device, diff_amp_dtype, diff_force_save, diff_k_step_max],[write_config_output])
        start_training.click(training,[gpu_selection, branch_selection, tiny_enable],[training_output])
        diff_training_btn.click(diff_training,[branch_selection, diff_k_step_max],[diff_training_output])
        continue_training_btn.click(continue_training,[gpu_selection, branch_selection],[training_output])
        open_training_results_btn.click(open_training_results, inputs=None, outputs=None)
        diff_continue_training_btn.click(diff_continue_training,[branch_selection],[diff_training_output])
        archive_models_btn.click(archive_training_models, inputs=[archive_model_name], outputs=[diff_training_output])
        clean_training_files_btn.click(clean_training_files, inputs=None, outputs=[diff_training_output])
        kmeans_button.click(kmeans_training,[kmeans_gpu],[kmeans_output])
        index_button.click(index_training, [], [index_output])

    with gr.Row():
        with gr.Column(scale=1):
            gr.HTML(value="""
<div style="width:100%; text-align:left; padding-left:18px; margin-top:4px; margin-bottom:10px; line-height:1.6;">
  <div style="
    display:inline-block;
    font-weight:700;
    background: linear-gradient(135deg, #f59e0b, #ea580c);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    color: transparent;
  ">整合包作者：@安语</div>
  <div style="color:#d32f2f; font-weight:600;">基于bilibili@羽毛布団 4.1整合包升级优化</div>
</div>
""")
        with gr.Column(scale=1):
            gr.Markdown("")
        with gr.Column(scale=1):
            gr.Markdown("")
    gr.HTML("""
<script>
(() => {
  function styleGuideBtn() {
    const app = document.querySelector("gradio-app");
    const root = (app && app.shadowRoot) ? app.shadowRoot : document;
    let target = root.querySelector("#user_guide_btn button")
      || root.querySelector("button#user_guide_btn")
      || root.querySelector("#user_guide_btn");
    if (!target) return;
    if (target.tagName !== "BUTTON") target = target.querySelector("button") || target;
    target.style.setProperty("width", "100%", "important");
    target.style.setProperty("min-width", "100%", "important");
    target.style.setProperty("max-width", "100%", "important");
    target.style.setProperty("height", "42px", "important");
    target.style.setProperty("min-height", "42px", "important");
    target.style.setProperty("max-height", "42px", "important");
    target.style.setProperty("border-radius", "10px", "important");
    target.style.setProperty("padding", "0 12px", "important");
    target.style.setProperty("background-image", "linear-gradient(135deg, #2563eb 0%, #ffffff 100%)", "important");
    target.style.setProperty("background-color", "#2563eb", "important");
    target.style.setProperty("border", "1px solid #3b82f6", "important");
    target.style.setProperty("color", "#0f172a", "important");
  }
  styleGuideBtn();
  setInterval(styleGuideBtn, 1200);
})();
</script>
""")
    user_guide_btn.click(open_user_guide, inputs=[], outputs=None)

    with gr.Tabs():
        gr.HTML("<div style='height:26px;'></div>")
        with gr.Row(variant="panel"):
            with gr.Column():
                gr.Markdown(value="""
                    <font size=2> WebUI设置</font>
                    """)
                with gr.Row():
                    debug_button = gr.Checkbox(label="Debug模式，反馈BUG需要打开，打开后控制台可以显示具体错误提示", value=debug)
        debug_button.change(debug_change,[],[])

        # 启动时清理 raw 目录里的历史临时文件，避免目录无限增长
        import json as _json_init
        _raw_dir = "raw"
        _init_progress_file = os.path.join(_raw_dir, "vc_progress.json")
        try:
            os.makedirs(_raw_dir, exist_ok=True)
            for _name in os.listdir(_raw_dir):
                _path = os.path.join(_raw_dir, _name)
                if os.path.isfile(_path):
                    os.remove(_path)
                elif os.path.isdir(_path):
                    shutil.rmtree(_path, ignore_errors=True)
            if os.path.exists(_init_progress_file):
                pass
            with open(_init_progress_file, 'w') as _f:
                _json_init.dump({"current": 0, "total": 0, "seg_msg": "", "done": False, "error": None, "result_path": "", "elapsed": 0}, _f)
        except:
            pass

        app.queue()

        app.launch(server_name="127.0.0.1",inbrowser=True,quiet=True,allowed_paths=["raw","results"])