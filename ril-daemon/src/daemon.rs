use crate::config::Config;
use crate::python_bridge::PythonBridge;
use crate::telegram::run_telegram_bot;

pub async fn run_daemon(bridge: PythonBridge, config: Config) -> anyhow::Result<()> {
    tracing::info!("Starting Read It Later Daemon...");

    if config.telegram_token.is_some() {
        tracing::info!("Starting Telegram Bot task in daemon mode...");
        let bot_bridge = bridge.clone();
        let bot_config = config.clone();
        tokio::spawn(async move {
            if let Err(e) = run_telegram_bot(bot_bridge, bot_config).await {
                tracing::error!("Telegram Bot task exited with error: {}", e);
            }
        });
    } else {
        tracing::warn!("TELEGRAM_TOKEN is not configured; Telegram Bot will not start.");
    }

    tracing::info!("Daemon successfully started. Press Ctrl+C to terminate.");
    tokio::signal::ctrl_c().await?;
    tracing::info!("Shutdown signal received. Stopping daemon...");

    Ok(())
}
