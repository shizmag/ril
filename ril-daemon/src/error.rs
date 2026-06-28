use thiserror::Error;

#[derive(Debug, Error)]
pub enum DaemonError {
    #[error("Bridge execution failed: {0}")]
    BridgeExec(String),

    #[error("Bridge timed out after {0} seconds")]
    BridgeTimeout(u64),

    #[error("Bridge returned error [{code}]: {message}\nDetails: {details}")]
    BridgePython {
        code: String,
        message: String,
        details: String,
    },

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("Configuration error: {0}")]
    Config(String),

    #[error("Other error: {0}")]
    Other(String),
}

pub type Result<T> = std::result::Result<T, DaemonError>;
