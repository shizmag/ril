use crate::domain::SaveFormat;
use crate::error::Result;
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Config {
    pub library_dir: Option<PathBuf>,
    pub db_path: Option<PathBuf>,
    pub telegram_token: Option<String>,
    pub allowed_telegram_users: Vec<i64>,
    pub default_format: SaveFormat,
    pub python_cmd: Option<String>,
    pub python_bin: Option<PathBuf>,
    pub python_workdir: Option<PathBuf>,
    pub bridge_timeout_seconds: u64,
}

impl Config {
    pub fn load() -> Result<Self> {
        // Load environment variables from .env file if present
        let _ = dotenvy::dotenv();
        Self::load_from_env_only()
    }

    pub fn load_from_env_only() -> Result<Self> {
        let library_dir = std::env::var("RIL_LIBRARY_DIR").ok().map(PathBuf::from);
        let db_path = std::env::var("RIL_DB_PATH").ok().map(PathBuf::from);
        let telegram_token = std::env::var("TELEGRAM_TOKEN").ok();

        let allowed_telegram_users = std::env::var("ALLOWED_TELEGRAM_USERS")
            .ok()
            .map(|val| {
                val.split(',')
                    .filter_map(|s| s.trim().parse::<i64>().ok())
                    .collect::<Vec<i64>>()
            })
            .unwrap_or_default();

        let default_format = std::env::var("RIL_DEFAULT_FORMAT")
            .ok()
            .and_then(|val| val.parse::<SaveFormat>().ok())
            .unwrap_or(SaveFormat::Markdown);

        let python_cmd = std::env::var("RIL_PYTHON_CMD").ok();
        let python_bin = std::env::var("RIL_PYTHON_BIN").ok().map(PathBuf::from);
        let python_workdir = std::env::var("RIL_PYTHON_WORKDIR").ok().map(PathBuf::from);

        let bridge_timeout_seconds = std::env::var("RIL_BRIDGE_TIMEOUT_SECONDS")
            .ok()
            .and_then(|val| val.parse::<u64>().ok())
            .unwrap_or(600);

        Ok(Config {
            library_dir,
            db_path,
            telegram_token,
            allowed_telegram_users,
            default_format,
            python_cmd,
            python_bin,
            python_workdir,
            bridge_timeout_seconds,
        })
    }
}
