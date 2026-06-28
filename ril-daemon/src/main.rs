#[tokio::main]
async fn main() -> anyhow::Result<()> {
    ril_daemon::run_cli().await
}
