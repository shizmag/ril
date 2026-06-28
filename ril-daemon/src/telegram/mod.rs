pub mod callback_data;
pub mod callbacks;
pub mod handlers;
pub mod helpers;
pub mod keyboards;
pub mod state;
pub mod views;

use crate::config::Config;
use crate::domain::SaveFormat;
use crate::python_bridge::PythonBridge;
pub use state::BotState;
use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::utils::command::BotCommands;

#[derive(BotCommands, Clone, Debug)]
#[command(
    rename_rule = "lowercase",
    description = "These commands are supported:"
)]
pub enum Command {
    #[command(description = "display help message")]
    Start,
    #[command(description = "display help message")]
    Help,
    #[command(description = "show hub menu")]
    Hub,
    #[command(description = "show or set save format: /format [markdown|html|epub]")]
    Format(String),
    #[command(description = "show reading statistics")]
    Stats,
    #[command(description = "list recent articles")]
    List,
    #[command(description = "search articles: /search <query>")]
    Search(String),
    #[command(description = "get saved article file: /get <id>")]
    Get(String),
    #[command(description = "mark article as read: /read <id>")]
    Read(String),
    #[command(description = "mark article as unread: /unread <id>")]
    Unread(String),
    #[command(description = "delete an article: /delete <id>")]
    Delete(String),
    #[command(description = "reset library (delete all articles)")]
    Reset,
    #[command(description = "confirm library reset")]
    ConfirmReset,
    #[command(description = "cancel pending actions")]
    Cancel,
}

pub async fn run_telegram_bot(bridge: PythonBridge, config: Config) -> anyhow::Result<()> {
    let token = config
        .telegram_token
        .clone()
        .ok_or_else(|| anyhow::anyhow!("TELEGRAM_TOKEN environment variable is not configured"))?;

    tracing::info!("Initializing Telegram bot event loop...");

    let bot = Bot::new(token);
    let state = Arc::new(BotState::new(bridge, config));

    let handler = dptree::entry()
        .branch(Update::filter_message().endpoint(handlers::handle_message))
        .branch(Update::filter_callback_query().endpoint(callbacks::handle_callback_query));

    Dispatcher::builder(bot, handler)
        .dependencies(dptree::deps![state])
        .enable_ctrlc_handler()
        .build()
        .dispatch()
        .await;

    Ok(())
}

pub fn is_allowed(user_id: UserId, allowed_users: &[i64]) -> bool {
    if allowed_users.is_empty() {
        return true;
    }
    allowed_users.contains(&(user_id.0 as i64))
}

pub fn extract_urls(text: &str) -> Vec<String> {
    let mut urls = vec![];
    for word in text.split_whitespace() {
        if word.starts_with("http://") || word.starts_with("https://") {
            urls.push(word.to_string());
        }
    }
    urls
}

pub fn detect_format_override(text: &str, default: SaveFormat) -> SaveFormat {
    let text_lower = text.to_lowercase();
    if text_lower.contains("html") {
        SaveFormat::Html
    } else if text_lower.contains("epub") {
        SaveFormat::Epub
    } else if text_lower.contains("markdown") || text_lower.contains("md") {
        SaveFormat::Markdown
    } else {
        default
    }
}

pub trait MapTgError<T> {
    fn tg_err(self) -> Result<T, teloxide::RequestError>;
}

impl<T, E: std::fmt::Display> MapTgError<T> for Result<T, E> {
    fn tg_err(self) -> Result<T, teloxide::RequestError> {
        self.map_err(|e| {
            let io_err = std::io::Error::new(std::io::ErrorKind::Other, e.to_string());
            teloxide::RequestError::from(io_err)
        })
    }
}
