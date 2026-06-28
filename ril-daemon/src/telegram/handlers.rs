use crate::domain::SaveFormat;
use crate::telegram::{BotState, Command};
use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::{InputFile, Message};
use teloxide::utils::command::BotCommands;

pub async fn handle_message(bot: Bot, msg: Message, state: Arc<BotState>) -> ResponseResult<()> {
    let user = match msg.from() {
        Some(u) => u,
        None => return Ok(()),
    };

    if !super::is_allowed(user.id, &state.config.allowed_telegram_users) {
        let _ = bot.send_message(msg.chat.id, "Access denied").await;
        return Ok(());
    }

    let text = msg.text().unwrap_or("");

    // Try parsing as command
    if text.starts_with('/') {
        if let Ok(cmd) = Command::parse(text, "") {
            return handle_command(bot, msg, cmd, state).await;
        }
    }

    // Extract URLs if not a command
    let urls = super::extract_urls(text);
    if !urls.is_empty() {
        handle_urls(bot, msg, urls, state).await?;
    } else if text.starts_with('/') {
        let _ = bot
            .send_message(
                msg.chat.id,
                "Unknown command. Type /help to see supported commands.",
            )
            .await;
    }

    Ok(())
}

pub async fn handle_command(
    bot: Bot,
    msg: Message,
    cmd: Command,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user = match msg.from() {
        Some(u) => u,
        None => return Ok(()),
    };

    // If any other command is run, clear their pending reset status
    if !matches!(cmd, Command::ConfirmReset) {
        let mut pending = state.pending_resets.lock().await;
        pending.remove(&user.id);
    }

    match cmd {
        Command::Start | Command::Help => {
            let help_text = "📚 <b>Read It Later Bot</b>\n\n\
            Commands:\n\
            /start or /help - Show this help message\n\
            /format [markdown|html|epub] - Show or set default save format\n\
            /stats - Show library reading statistics\n\
            /list - List 15 most recent articles\n\
            /search &lt;query&gt; - Search library using SQLite FTS5\n\
            /get &lt;id&gt; - Retrieve article as a document\n\
            /read &lt;id&gt; - Mark article as read\n\
            /unread &lt;id&gt; - Mark article as unread\n\
            /delete &lt;id&gt; - Delete article from library\n\
            /reset - Reset library (deletes everything)\n\n\
            Simply send me one or more URLs to import them into your library!";
            bot.send_message(msg.chat.id, help_text)
                .parse_mode(teloxide::types::ParseMode::Html)
                .await?;
        }
        Command::Format(arg) => {
            let arg = arg.trim();
            if arg.is_empty() {
                let current_fmt = {
                    let map = state.user_formats.lock().await;
                    *map.get(&user.id).unwrap_or(&state.config.default_format)
                };
                bot.send_message(
                    msg.chat.id,
                    format!("Current default save format is: <b>{}</b>", current_fmt),
                )
                .parse_mode(teloxide::types::ParseMode::Html)
                .await?;
            } else {
                match arg.parse::<SaveFormat>() {
                    Ok(fmt) => {
                        {
                            let mut map = state.user_formats.lock().await;
                            map.insert(user.id, fmt);
                        }
                        bot.send_message(
                            msg.chat.id,
                            format!("Default format successfully changed to <b>{}</b>", fmt),
                        )
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await?;
                    }
                    Err(_) => {
                        bot.send_message(msg.chat.id, "Invalid format. Supported formats: <b>markdown</b>, <b>html</b>, <b>epub</b>")
                            .parse_mode(teloxide::types::ParseMode::Html)
                            .await?;
                    }
                }
            }
        }
        Command::Stats => match state.bridge.get_reading_stats().await {
            Ok(stats) => {
                let progress = if stats.total_articles > 0 {
                    (stats.read_articles as f64 / stats.total_articles as f64) * 100.0
                } else {
                    0.0
                };
                let stats_text = format!(
                    "📊 <b>Reading Statistics</b>\n\n\
                        Total articles: <b>{}</b>\n\
                        Unread: <b>{}</b>\n\
                        Read: <b>{}</b> ({:.1}% completed)\n\n\
                        Total words: <b>{}</b>\n\
                        Words read: <b>{}</b>\n\
                        Words unread: <b>{}</b>\n\
                        Average words/article: <b>{:.0}</b>",
                    stats.total_articles,
                    stats.unread_articles,
                    stats.read_articles,
                    progress,
                    stats.total_words,
                    stats.read_words,
                    stats.unread_words,
                    stats.avg_words_per_article
                );
                bot.send_message(msg.chat.id, stats_text)
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            Err(e) => {
                bot.send_message(msg.chat.id, format!("Error fetching stats: {}", e))
                    .await?;
            }
        },
        Command::List => match state.bridge.list_articles(None, Some(15)).await {
            Ok(articles) => {
                if articles.is_empty() {
                    bot.send_message(
                        msg.chat.id,
                        "Your library is empty. Send me some links to start!",
                    )
                    .await?;
                } else {
                    let mut list_text = "📚 <b>Recent Articles:</b>\n\n".to_string();
                    for a in articles {
                        let status_emoji = if a.status == "read" { "✅" } else { "📖" };
                        let title_escaped = a
                            .title
                            .replace('&', "&amp;")
                            .replace('<', "&lt;")
                            .replace('>', "&gt;");
                        list_text.push_str(&format!(
                            "{} <b>[{}]</b> {}\n<i>Words: {} | Added: {}</i>\n\n",
                            status_emoji,
                            a.id,
                            title_escaped,
                            a.word_count,
                            a.added_at.chars().take(10).collect::<String>()
                        ));
                    }
                    bot.send_message(msg.chat.id, list_text)
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await?;
                }
            }
            Err(e) => {
                bot.send_message(msg.chat.id, format!("Error fetching list: {}", e))
                    .await?;
            }
        },
        Command::Search(query) => {
            let query = query.trim();
            if query.is_empty() {
                bot.send_message(
                    msg.chat.id,
                    "Please specify search terms: /search &lt;query&gt;",
                )
                .parse_mode(teloxide::types::ParseMode::Html)
                .await?;
                return Ok(());
            }
            match state.bridge.search_articles(query).await {
                Ok(results) => {
                    if results.is_empty() {
                        bot.send_message(msg.chat.id, format!("No matches found for '{}'", query))
                            .await?;
                    } else {
                        let mut search_text =
                            format!("🔎 <b>Search matches for '{}':</b>\n\n", query);
                        for r in results {
                            let status_emoji = if r.status == "read" { "✅" } else { "📖" };
                            let title_escaped = r
                                .title
                                .replace('&', "&amp;")
                                .replace('<', "&lt;")
                                .replace('>', "&gt;");
                            let snippet_escaped = r
                                .snippet
                                .replace('&', "&amp;")
                                .replace('<', "&lt;")
                                .replace('>', "&gt;");

                            let mut snip = snippet_escaped;
                            while snip.contains("***") {
                                snip = snip.replacen("***", "<b>", 1);
                                snip = snip.replacen("***", "</b>", 1);
                            }

                            search_text.push_str(&format!(
                                "{} <b>[{}]</b> {}\n<i>... {} ...</i>\n\n",
                                status_emoji, r.id, title_escaped, snip
                            ));
                        }
                        bot.send_message(msg.chat.id, search_text)
                            .parse_mode(teloxide::types::ParseMode::Html)
                            .await?;
                    }
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Error during search: {}", e))
                        .await?;
                }
            }
        }
        Command::Get(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    bot.send_message(
                        msg.chat.id,
                        "Please specify a valid article ID: /get &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.get_article_content(id).await {
                Ok(res) => {
                    let file_path = std::path::Path::new(&res.article.file_path);
                    if file_path.exists() {
                        let doc = InputFile::file(file_path);
                        bot.send_document(msg.chat.id, doc)
                            .caption(format!("📄 {}", res.article.title))
                            .await?;
                    } else if !res.content.is_empty() {
                        let mut text = res.content;
                        if text.len() > 4000 {
                            text.truncate(4000);
                            text.push_str("\n\n[Content truncated due to Telegram size limit]");
                        }
                        bot.send_message(msg.chat.id, text).await?;
                    } else {
                        bot.send_message(
                            msg.chat.id,
                            "Saved file not found and contents are empty.",
                        )
                        .await?;
                    }
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Error retrieving article: {}", e))
                        .await?;
                }
            }
        }
        Command::Read(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    bot.send_message(
                        msg.chat.id,
                        "Please specify a valid article ID: /read &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.mark_article_read(id).await {
                Ok(true) => {
                    bot.send_message(msg.chat.id, format!("Article {} marked as read.", id))
                        .await?;
                }
                Ok(false) => {
                    bot.send_message(msg.chat.id, format!("Article {} not found.", id))
                        .await?;
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Error marking article read: {}", e))
                        .await?;
                }
            }
        }
        Command::Unread(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    bot.send_message(
                        msg.chat.id,
                        "Please specify a valid article ID: /unread &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.mark_article_unread(id).await {
                Ok(true) => {
                    bot.send_message(msg.chat.id, format!("Article {} marked as unread.", id))
                        .await?;
                }
                Ok(false) => {
                    bot.send_message(msg.chat.id, format!("Article {} not found.", id))
                        .await?;
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Error marking article unread: {}", e))
                        .await?;
                }
            }
        }
        Command::Delete(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    bot.send_message(
                        msg.chat.id,
                        "Please specify a valid article ID: /delete &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.delete_article(id).await {
                Ok(true) => {
                    bot.send_message(msg.chat.id, format!("Article {} successfully deleted.", id))
                        .await?;
                }
                Ok(false) => {
                    bot.send_message(msg.chat.id, format!("Article {} not found.", id))
                        .await?;
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Error deleting article: {}", e))
                        .await?;
                }
            }
        }
        Command::Reset => {
            {
                let mut pending = state.pending_resets.lock().await;
                pending.insert(user.id);
            }
            bot.send_message(
                msg.chat.id,
                "⚠️ <b>WARNING:</b> This will delete ALL saved articles, files, and database records.\n\n\
                Are you sure? Send /confirmreset to proceed, or /cancel to abort."
            )
            .parse_mode(teloxide::types::ParseMode::Html)
            .await?;
        }
        Command::ConfirmReset => {
            let is_pending = {
                let mut pending = state.pending_resets.lock().await;
                pending.remove(&user.id)
            };
            if is_pending {
                match state.bridge.reset_library().await {
                    Ok(_) => {
                        bot.send_message(msg.chat.id, "Library and database successfully cleared.")
                            .await?;
                    }
                    Err(e) => {
                        bot.send_message(msg.chat.id, format!("Error resetting library: {}", e))
                            .await?;
                    }
                }
            } else {
                bot.send_message(msg.chat.id, "No pending reset action. Send /reset first.")
                    .await?;
            }
        }
        Command::Cancel => {
            bot.send_message(msg.chat.id, "Action cancelled.").await?;
        }
    }

    Ok(())
}

pub async fn handle_urls(
    bot: Bot,
    msg: Message,
    urls: Vec<String>,
    state: Arc<BotState>,
) -> ResponseResult<()> {
    let user = match msg.from() {
        Some(u) => u,
        None => return Ok(()),
    };

    let default_fmt = {
        let map = state.user_formats.lock().await;
        *map.get(&user.id).unwrap_or(&state.config.default_format)
    };
    let text = msg.text().unwrap_or("");
    let import_format = super::detect_format_override(text, default_fmt);

    let bot_clone = bot.clone();
    let chat_id = msg.chat.id;
    let bridge = state.bridge.clone();
    let sem = Arc::new(tokio::sync::Semaphore::new(2));

    let mut join_handles = vec![];
    let _ = bot
        .send_message(
            chat_id,
            format!("Starting import of {} link(s)...", urls.len()),
        )
        .await;

    for url in urls {
        let sem = sem.clone();
        let bridge = bridge.clone();
        let bot = bot_clone.clone();

        let handle = tokio::spawn(async move {
            let _permit = sem.acquire().await.unwrap();
            let _ = bot
                .send_message(chat_id, format!("Processing URL: {} ...", url))
                .await;
            match bridge.process_url(&url, import_format).await {
                Ok(res) => {
                    let title_escaped = res
                        .title
                        .replace('&', "&amp;")
                        .replace('<', "&lt;")
                        .replace('>', "&gt;");
                    let file_path = std::path::Path::new(&res.file_path);
                    if file_path.exists() {
                        let doc = InputFile::file(file_path);
                        let caption = format!(
                            "✅ <b>Successfully Imported!</b>\n\n\
                            <b>Title:</b> {}\n\
                            <b>Words:</b> {}\n\
                            <b>ID:</b> <code>{}</code>",
                            title_escaped, res.word_count, res.id
                        );
                        let _ = bot
                            .send_document(chat_id, doc)
                            .caption(caption)
                            .parse_mode(teloxide::types::ParseMode::Html)
                            .await;
                    } else {
                        let text = format!(
                            "✅ <b>Successfully Imported!</b>\n\n\
                            <b>Title:</b> {}\n\
                            <b>Words:</b> {}\n\
                            <b>Saved path:</b> {}\n\
                            <b>ID:</b> <code>{}</code>\n\n\
                            ⚠️ <i>Note: Document file not found on disk.</i>",
                            title_escaped, res.word_count, res.file_path, res.id
                        );
                        let _ = bot
                            .send_message(chat_id, text)
                            .parse_mode(teloxide::types::ParseMode::Html)
                            .await;
                    }
                }
                Err(e) => {
                    let _ = bot
                        .send_message(
                            chat_id,
                            format!(
                                "❌ <b>Failed to import</b> <code>{}</code>\nError: {}",
                                url, e
                            ),
                        )
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await;
                }
            }
        });
        join_handles.push(handle);
    }

    for h in join_handles {
        let _ = h.await;
    }

    let _ = bot.send_message(chat_id, "Import session finished.").await;
    Ok(())
}
