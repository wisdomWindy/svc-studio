# 内核说明：so-vits-svc 4.8.1（社区整合版）

本目录是从 `SO-VITS-SVC4.8.1` 整合版中**只取代码**得到的精简内核，
用于 SVC Studio 的预处理、训练与推理。权重、模型、UVR5 模型库、workenv 均未收录，
由应用在运行时提供（`pretrain/` 内置权重 + 用户数据目录）。

- 上游来源：社区整合包 SO-VITS-SVC 4.8.1（基于 so-vits-svc 4.1-Structure 分支的社区维护版）
- 许可证：AGPL-3.0（见 `LICENSE`）
- 收录范围：根目录预处理/训练脚本 + `inference/ modules/ vdecoder/ vencoder/ diffusion/ cluster/ configs_template/`
- 未收录：`webUI.py` 依赖的 gradio、`uvr5_*`、`mdx_*`、`bs_roformer_*`、`workenv/`、`models/`、`models_backup/`、`pre_trained_model/`

## 本仓库对内核做的最小改动

1. `utils.py`
   - 强制 `matplotlib.use("Agg")`：训练在无 GUI 的后台进程里跑；
   - `plot_spectrogram_to_numpy` 兼容 matplotlib ≥ 3.10（`tostring_rgb` 已移除，
     改为 `buffer_rgba` → `print_to_buffer` → 旧 API 三级兜底）。
2. `train.py`
   - 单卡（`n_gpus == 1`）不再调用 `dist.init_process_group`：Windows 上 gloo 会因网卡探测失败
     报 `unsupported gloo device`；
   - 单卡用轻量 `_NoDDP` 包装替代 `DDP`，保留 `.module` 语义；
   - DataLoader 的 `n_workers` 由 worker 在启动前强制置 0（父进程 spawn 多进程会崩）。

改动仅限上述兼容性修复，不涉及模型结构与算法。

## 推理入口

```python
from inference.infer_tool import Svc
svc = Svc(model_path, config_path, device, cluster_path, nsf_enhance,
          diffusion_model_path, diffusion_config_path, shallow_diffusion,
          only_diffusion, spk_mix_enable, feature_retrieval)
audio = svc.slice_inference(**kwargs)
```
