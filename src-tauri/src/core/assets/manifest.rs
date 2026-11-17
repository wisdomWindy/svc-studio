//! 资产清单：结构、签名、以及"文件与清单是否一致"的校验。
//!
//! 信任模型：清单 JSON 被 Ed25519 私钥签名，公钥编译期嵌入（`assets/trust/pubkey.hex`）。
//! 开发构建允许无签名清单（显式环境变量），发布构建必须验签通过。

use std::path::Path;

use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};

pub const MANIFEST_SCHEMA: u32 = 1;
const EMBEDDED_PUBKEY: &str = include_str!("../../../../assets/trust/pubkey.hex");
pub const ALLOW_UNSIGNED_ENV: &str = "SVCSTUDIO_ALLOW_UNSIGNED_MANIFEST";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Manifest {
    pub schema: u32,
    pub generated_at: String,
    pub assets: Vec<AssetEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AssetEntry {
    pub id: String,
    pub kind: AssetKind,
    pub key: String,
    pub version: String,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub source_url: Option<String>,
    #[serde(default)]
    pub license_note: Option<String>,
    pub files: Vec<AssetFile>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum AssetKind {
    Encoder,
    F0,
    Vocoder,
    Pretrained,
    Diffusion,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AssetFile {
    pub rel_path: String,
    pub bytes: i64,
    pub sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum SignatureState {
    Verified,
    Unsigned,
    Invalid,
}

pub fn parse(bytes: &[u8]) -> AppResult<Manifest> {
    let manifest: Manifest = serde_json::from_slice(bytes).map_err(|e| {
        Box::new(AppError::new(
            "SVC-2001",
            "资产清单格式不正确",
            e.to_string(),
        ))
    })?;
    if manifest.schema != MANIFEST_SCHEMA {
        return Err(Box::new(AppError::new(
            "SVC-2003",
            "资产清单版本不受支持",
            format!("schema={} 期望 {}", manifest.schema, MANIFEST_SCHEMA),
        )));
    }
    Ok(manifest)
}

/// 校验清单签名。无公钥时返回 Unsigned；有公钥但签名不符返回错误。
pub fn verify_manifest(bytes: &[u8], signature_hex: Option<&str>) -> AppResult<SignatureState> {
    let pubkey_hex = EMBEDDED_PUBKEY.trim();
    if pubkey_hex.is_empty() {
        return Ok(SignatureState::Unsigned);
    }
    let Some(sig_hex) = signature_hex else {
        return Ok(SignatureState::Unsigned);
    };
    let key_bytes = hex::decode(pubkey_hex)
        .map_err(|e| Box::new(AppError::new("SVC-2003", "内置公钥损坏", e.to_string())))?;
    let key_array: [u8; 32] = key_bytes.as_slice().try_into().map_err(|_| {
        Box::new(AppError::new(
            "SVC-2003",
            "内置公钥长度不正确",
            format!("{} bytes", key_bytes.len()),
        ))
    })?;
    let key = VerifyingKey::from_bytes(&key_array)
        .map_err(|e| Box::new(AppError::new("SVC-2003", "内置公钥无法解析", e.to_string())))?;

    let sig_bytes = hex::decode(sig_hex.trim())
        .map_err(|e| Box::new(AppError::new("SVC-2003", "签名文件损坏", e.to_string())))?;
    let sig = Signature::from_slice(&sig_bytes)
        .map_err(|e| Box::new(AppError::new("SVC-2003", "签名格式不正确", e.to_string())))?;
    // 签名存在但不匹配：返回 Invalid，由 enforce 统一拒绝（区别于"没有签名"）。
    if key.verify(bytes, &sig).is_err() {
        return Ok(SignatureState::Invalid);
    }
    Ok(SignatureState::Verified)
}

pub fn allow_unsigned() -> bool {
    std::env::var(ALLOW_UNSIGNED_ENV)
        .map(|v| v == "1")
        .unwrap_or(false)
}

/// 发布构建必须校验通过；开发构建允许无签名清单但会显式标注。
pub fn enforce(state: SignatureState) -> AppResult<()> {
    match state {
        SignatureState::Verified => Ok(()),
        SignatureState::Invalid => Err(Box::new(AppError::new(
            "SVC-2003",
            "资产清单签名无效",
            "清单被篡改或与当前公钥不匹配，拒绝使用",
        ))),
        SignatureState::Unsigned if allow_unsigned() => Ok(()),
        SignatureState::Unsigned => Err(Box::new(AppError::new(
            "SVC-2003",
            "资产清单未签名",
            format!("发布构建要求签名；开发构建可设置 {ALLOW_UNSIGNED_ENV}=1"),
        ))),
    }
}

/// 单文件校验：大小与 sha256 都必须匹配。
pub fn verify_file(path: &Path, expected: &AssetFile) -> AppResult<FileVerdict> {
    if !path.is_file() {
        return Ok(FileVerdict::Missing);
    }
    let size = std::fs::metadata(path)?.len() as i64;
    if size != expected.bytes {
        return Ok(FileVerdict::SizeMismatch {
            actual: size,
            expected: expected.bytes,
        });
    }
    let actual = crate::infra::fs::sha256_file(path)?;
    if !actual.eq_ignore_ascii_case(&expected.sha256) {
        return Ok(FileVerdict::HashMismatch {
            actual,
            expected: expected.sha256.clone(),
        });
    }
    Ok(FileVerdict::Ok)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum FileVerdict {
    Ok,
    Missing,
    SizeMismatch { actual: i64, expected: i64 },
    HashMismatch { actual: String, expected: String },
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    const SEED: [u8; 32] = [7u8; 32];

    fn sample() -> Vec<u8> {
        serde_json::to_vec(&Manifest {
            schema: 1,
            generated_at: "2026-11-16T00:00:00Z".into(),
            assets: vec![AssetEntry {
                id: "contentvec".into(),
                kind: AssetKind::Encoder,
                key: "vec768l12".into(),
                version: "2022-11".into(),
                required: true,
                source_url: None,
                license_note: Some("ContentVec, MIT".into()),
                files: vec![AssetFile {
                    rel_path: "pretrain/checkpoint_best_legacy_500.pt".into(),
                    bytes: 3,
                    sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
                        .into(),
                }],
            }],
        })
        .unwrap()
    }

    #[test]
    fn parses_valid_manifest_and_rejects_schema_drift() {
        let bytes = sample();
        let m = parse(&bytes).expect("parse");
        assert_eq!(m.assets.len(), 1);
        assert_eq!(m.assets[0].kind, AssetKind::Encoder);

        let bad = br#"{"schema":99,"generatedAt":"x","assets":[]}"#;
        let err = parse(bad).unwrap_err();
        assert_eq!(err.code, "SVC-2003");
    }

    #[test]
    fn rejects_unknown_fields() {
        let bad = br#"{"schema":1,"generatedAt":"x","assets":[],"extra":1}"#;
        assert!(parse(bad).is_err());
    }

    #[test]
    fn signature_roundtrip_with_seed_key() {
        let bytes = sample();
        let signing = SigningKey::from_bytes(&SEED);
        let sig = signing.sign(&bytes);
        // 用同一对公私钥验证：这里模拟"内置公钥"的验证路径。
        let verifying = signing.verifying_key();
        verifying.verify(&bytes, &sig).expect("signature ok");

        let mut tampered = bytes.clone();
        tampered[10] ^= 0xff;
        assert!(verifying.verify(&tampered, &sig).is_err(), "篡改必须验不过");
    }

    #[test]
    fn unsigned_manifest_is_not_verified() {
        assert_eq!(
            verify_manifest(b"{}", None).unwrap(),
            SignatureState::Unsigned
        );
    }

    #[test]
    fn verify_file_detects_size_and_hash_changes() {
        let dir = std::env::temp_dir().join(format!("svcasset-{}", uuid::Uuid::now_v7().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("a.bin");
        std::fs::write(&path, b"abc").unwrap();

        let good = AssetFile {
            rel_path: "a.bin".into(),
            bytes: 3,
            sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad".into(),
        };
        assert_eq!(verify_file(&path, &good).unwrap(), FileVerdict::Ok);

        let size_bad = AssetFile {
            bytes: 4,
            ..good.clone()
        };
        assert!(matches!(
            verify_file(&path, &size_bad).unwrap(),
            FileVerdict::SizeMismatch { .. }
        ));

        std::fs::write(&path, b"abd").unwrap();
        assert!(matches!(
            verify_file(&path, &good).unwrap(),
            FileVerdict::HashMismatch { .. }
        ));

        std::fs::remove_file(&path).unwrap();
        assert_eq!(verify_file(&path, &good).unwrap(), FileVerdict::Missing);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
