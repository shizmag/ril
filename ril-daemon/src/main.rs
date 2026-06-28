mod config;
mod daemon;
mod domain;
mod error;
mod logging;
mod mcp;
mod python_bridge;
mod telegram;

#[cfg(test)]
mod tests;

use clap::{Parser, Subcommand};
use config::Config;
use python_bridge::PythonBridge;

#[derive(Parser)]
#[command(name = "ril-daemon")]
#[command(about = "Read It Later (RIL) Rust Daemon", long_about = None)]
struct Cli {
    #[arg(
        short,
        long,
        help = "Run in mock/test mode without calling real Python"
    )]
    mock: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    #[command(about = "Run the Telegram bot (long polling)")]
    Telegram,

    #[command(about = "Run the MCP server over stdio")]
    Mcp,

    #[command(about = "Run in full daemon mode (runs Telegram bot in background)")]
    Daemon,

    #[command(about = "Perform a health check on the Python bridge connection")]
    Health,

    #[command(about = "Validate config and environment settings")]
    ConfigCheck,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    logging::init_logging();

    let args = Cli::parse();
    let config = Config::load()?;

    let bridge = if args.mock {
        tracing::info!("Running in MOCK mode (no real Python process calls)");
        PythonBridge::new_mock()
    } else {
        PythonBridge::new(config.clone())
    };

    match args.command {
        Commands::Telegram => {
            telegram::run_telegram_bot(bridge, config).await?;
        }
        Commands::Mcp => {
            mcp::run_mcp_server(bridge).await?;
        }
        Commands::Daemon => {
            daemon::run_daemon(bridge, config).await?;
        }
        Commands::Health => {
            tracing::info!("Checking health of Python bridge...");
            match bridge.get_reading_stats().await {
                Ok(stats) => {
                    tracing::info!("Health check PASSED!");
                    tracing::info!("Total articles in database: {}", stats.total_articles);
                    tracing::info!("Read articles: {}", stats.read_articles);
                    tracing::info!("Unread articles: {}", stats.unread_articles);
                }
                Err(e) => {
                    tracing::error!("Health check FAILED: {}", e);
                    std::process::exit(1);
                }
            }
        }
        Commands::ConfigCheck => {
            tracing::info!("Validating configuration environment variables...");
            config_check(&config);
        }
    }

    Ok(())
}

fn config_check(config: &Config) {
    println!("--- Configuration Check ---");
    println!("RIL_DEFAULT_FORMAT:        {}", config.default_format);
    println!(
        "RIL_BRIDGE_TIMEOUT_SECONDS: {}s",
        config.bridge_timeout_seconds
    );

    if let Some(val) = &config.library_dir {
        println!("RIL_LIBRARY_DIR:           {}", val.display());
    } else {
        println!("RIL_LIBRARY_DIR:           [Not Set] (defaults to project/library)");
    }

    if let Some(val) = &config.db_path {
        println!("RIL_DB_PATH:               {}", val.display());
    } else {
        println!("RIL_DB_PATH:               [Not Set] (defaults to library/metadata.db)");
    }

    if let Some(val) = &config.python_cmd {
        println!("RIL_PYTHON_CMD:            {}", val);
    } else {
        println!("RIL_PYTHON_CMD:            [Not Set]");
    }

    if let Some(val) = &config.python_bin {
        println!("RIL_PYTHON_BIN:            {}", val.display());
    } else {
        println!("RIL_PYTHON_BIN:            [Not Set]");
    }

    if let Some(val) = &config.python_workdir {
        println!("RIL_PYTHON_WORKDIR:        {}", val.display());
    } else {
        println!("RIL_PYTHON_WORKDIR:        [Not Set] (defaults to current dir)");
    }

    if let Some(val) = &config.telegram_token {
        let redacted = if val.len() > 10 {
            format!("{}***{}", &val[..5], &val[val.len() - 5..])
        } else {
            "***".to_string()
        };
        println!("TELEGRAM_TOKEN:            {}", redacted);
    } else {
        println!("TELEGRAM_TOKEN:            [Not Configured]");
    }

    println!(
        "ALLOWED_TELEGRAM_USERS:    {:?}",
        config.allowed_telegram_users
    );
    println!("---------------------------");
}
