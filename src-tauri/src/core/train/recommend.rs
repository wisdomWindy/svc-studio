//! 显存 → 参数的推荐表（E1）。所有值都是"起点"，OOM 时由自愈逻辑降档。

use serde::Serialize;

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Recommend {
    pub vram_mb: u64,
    pub batch_size: u32,
    pub diffusion_batch: u32,
    pub diffusion_duration: f64,
    pub num_processes: u32,
    pub reason: String,
    pub trainable: bool,
}

/// 低于 6GB 直接判不可训练：上游 `train.py` 本身也不接受 CPU/小显存。
pub const MIN_TRAIN_VRAM_MB: u64 = 6 * 1024;

pub fn for_vram(vram_mb: u64) -> Recommend {
    let (batch, diff_batch, duration, procs) = if vram_mb < MIN_TRAIN_VRAM_MB {
        (1, 4, 1.5, 1)
    } else if vram_mb < 8 * 1024 {
        (2, 8, 2.0, 1)
    } else if vram_mb < 12 * 1024 {
        (3, 12, 2.5, 2)
    } else if vram_mb < 24 * 1024 {
        (6, 24, 3.0, 2)
    } else {
        (10, 48, 4.0, 4)
    };

    let reason = if vram_mb < MIN_TRAIN_VRAM_MB {
        format!(
            "显存 {:.1}GB 低于训练下限 6GB，只能推理",
            vram_mb as f64 / 1024.0
        )
    } else {
        format!(
            "按 {:.0}GB 显存估算：主模型 batch={batch}、扩散 batch={diff_batch}/duration={duration}s；实际以 OOM 自愈为准",
            vram_mb as f64 / 1024.0
        )
    };

    Recommend {
        vram_mb,
        batch_size: batch,
        diffusion_batch: diff_batch,
        diffusion_duration: duration,
        num_processes: procs,
        reason,
        trainable: vram_mb >= MIN_TRAIN_VRAM_MB,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tiers_are_monotonic() {
        let six = for_vram(6144);
        let eight = for_vram(8192);
        let twelve = for_vram(12288);
        let twenty_four = for_vram(24576);
        assert!(six.batch_size <= eight.batch_size);
        assert!(eight.batch_size <= twelve.batch_size);
        assert!(twelve.batch_size <= twenty_four.batch_size);
    }

    #[test]
    fn six_gb_matches_prd_table() {
        let r = for_vram(6144);
        assert_eq!(r.batch_size, 2);
        assert_eq!(r.diffusion_batch, 8);
        assert_eq!(r.diffusion_duration, 2.0);
        assert_eq!(r.num_processes, 1);
        assert!(r.trainable);
    }

    #[test]
    fn below_floor_is_not_trainable() {
        let r = for_vram(4096);
        assert!(!r.trainable);
        assert!(r.reason.contains("6GB"));
    }

    #[test]
    fn reason_mentions_oom_fallback() {
        assert!(for_vram(12288).reason.contains("OOM"));
    }
}
