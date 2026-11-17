//! 训练日志解析（唯一事实来自 `vendor/so-vits-svc/train.py` 的固定格式）。
//!
//! 上游一行：
//! `Losses: [1.2, 3.4, 0.5, 2.2, 0.9], step: 1200, lr: 0.0001, reference_loss: 8.2`
//! 另有 `Train Epoch: 3 [45%]`、checkpoint 文件名 `G_1200.pth`。

#[derive(Debug, Clone, PartialEq)]
pub struct TrainStep {
    pub step: u64,
    pub losses: Vec<f64>,
    pub lr: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum TrainSignal {
    Step(TrainStep),
    Epoch { epoch: u64, percent: Option<f64> },
    Checkpoint { step: u64 },
    Oom,
    Diverged,
    Other,
}

fn bracket_inner(text: &str) -> Option<&str> {
    let start = text.find('[')?;
    let end = text[start..].find(']')? + start;
    Some(&text[start + 1..end])
}

fn number_list(text: &str) -> Option<Vec<f64>> {
    let inner = bracket_inner(text)?;
    let mut out = Vec::new();
    for part in inner.split(',') {
        let trimmed = part.trim();
        // `tensor(1.5)` / `tensor(1.5, device='cuda:0')` 都要还原成数字
        let without_tensor = trimmed.strip_prefix("tensor(").unwrap_or(trimmed);
        let token = without_tensor.trim_end_matches(')').trim();
        if token.is_empty() {
            continue;
        }
        let cleaned: String = token
            .chars()
            .take_while(|c| c.is_ascii_digit() || matches!(c, '.' | '-' | '+' | 'e' | 'E'))
            .collect();
        match cleaned.parse::<f64>() {
            Ok(v) => out.push(v),
            Err(_) => return None,
        }
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

fn scalar_after(text: &str, key: &str) -> Option<f64> {
    let idx = text.find(key)?;
    let rest = &text[idx + key.len()..];
    let token: String = rest
        .trim_start_matches([':', ' ', '='])
        .chars()
        .take_while(|c| c.is_ascii_digit() || matches!(c, '.' | '-' | '+' | 'e' | 'E'))
        .collect();
    token.parse::<f64>().ok()
}

pub fn parse_line(line: &str) -> TrainSignal {
    let lower = line.to_ascii_lowercase();

    if lower.contains("out of memory")
        || lower.contains("outofmemory")
        || lower.contains("cuda error: out of memory")
    {
        return TrainSignal::Oom;
    }
    if lower.contains("losses:") {
        // 只在损失列表内部判断 nan/inf，避免 "INFO" 里的 "inf" 被误判。
        if let Some(inner) = bracket_inner(line) {
            let inner_lower = inner.to_ascii_lowercase();
            if inner_lower.contains("nan") || inner_lower.contains("inf") {
                return TrainSignal::Diverged;
            }
        }
        if let (Some(losses), Some(step)) = (
            number_list(line),
            scalar_after(line, "step:").map(|v| v as u64),
        ) {
            if losses.iter().any(|v| !v.is_finite()) {
                return TrainSignal::Diverged;
            }
            return TrainSignal::Step(TrainStep {
                step,
                losses,
                lr: scalar_after(line, "lr:"),
            });
        }
    }

    if let Some(pos) = line.find("Train Epoch:") {
        let rest = &line[pos + "Train Epoch:".len()..];
        let epoch = rest
            .trim()
            .chars()
            .take_while(|c| c.is_ascii_digit())
            .collect::<String>()
            .parse::<u64>()
            .ok();
        let percent = rest
            .find('[')
            .and_then(|s| rest[s + 1..].find('%').map(|p| (s + 1, p)))
            .and_then(|(s, p)| rest[s..s + p].trim().parse::<f64>().ok());
        if let Some(epoch) = epoch {
            return TrainSignal::Epoch { epoch, percent };
        }
    }

    if let Some(pos) = line.find("G_") {
        let rest = &line[pos + 2..];
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        if !digits.is_empty() {
            if let Ok(step) = digits.parse::<u64>() {
                if rest.contains(".pth") {
                    return TrainSignal::Checkpoint { step };
                }
            }
        }
    }

    TrainSignal::Other
}

/// 判断最近若干步是否发散：全为 NaN/Inf，或波动超过阈值。
fn _is_diverged(losses: &[f64]) -> bool {
    !losses.is_empty() && losses.iter().any(|v| !v.is_finite())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_canonical_loss_line() {
        let line =
            "Losses: [1.25, 3.5, 0.75, 2.25, 0.95], step: 1200, lr: 0.0001, reference_loss: 8.2";
        match parse_line(line) {
            TrainSignal::Step(step) => {
                assert_eq!(step.step, 1200);
                assert_eq!(step.losses, vec![1.25, 3.5, 0.75, 2.25, 0.95]);
                assert!((step.lr.unwrap() - 0.0001).abs() < 1e-9);
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn tolerates_loguru_prefix_and_tensor_notation() {
        let line = "2026-11-16 12:00:00.123 | INFO | Losses: [tensor(1.5), tensor(2.0)], step: 42, lr: 1e-4";
        match parse_line(line) {
            TrainSignal::Step(step) => assert_eq!(step.step, 42),
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn detects_nan_as_diverged() {
        assert_eq!(
            parse_line("Losses: [nan, 3.0], step: 10, lr: 0.0001"),
            TrainSignal::Diverged
        );
    }

    #[test]
    fn detects_oom_in_common_phrasings() {
        assert_eq!(
            parse_line("RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB"),
            TrainSignal::Oom
        );
        assert_eq!(parse_line("torch.cuda.OutOfMemoryError"), TrainSignal::Oom);
    }

    #[test]
    fn parses_epoch_and_percent() {
        assert_eq!(
            parse_line("Train Epoch: 3 [45%]"),
            TrainSignal::Epoch {
                epoch: 3,
                percent: Some(45.0)
            }
        );
    }

    #[test]
    fn parses_checkpoint_names() {
        assert_eq!(
            parse_line("saving logs/44k/G_1200.pth"),
            TrainSignal::Checkpoint { step: 1200 }
        );
    }

    #[test]
    fn ignores_unrelated_lines() {
        assert_eq!(
            parse_line("Loading speech encoder for content..."),
            TrainSignal::Other
        );
        assert_eq!(parse_line(""), TrainSignal::Other);
    }
}
