use crate::domain::SaveFormat;
use crate::telegram::state::{BotState, PendingState};
use crate::telegram::{Command, keyboards, views, MapTgError};
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

    let text = msg.text().unwrap_or("").trim();

    // Check pending states first (e.g. user is entering comments or tags)
    let pending = state.get_pending_state(user.id).await;
    if !matches!(pending, PendingState::None) {
        if text.eq_ignore_ascii_case("/cancel") {
            state.clear_pending_state(user.id).await;
            bot.send_message(msg.chat.id, "Действие отменено.").await?;
            // Return to hub or article if we had one
            if let PendingState::WaitingForComment { article_id } | PendingState::WaitingForTag { article_id } = pending {
                if article_id > 0 {
                    let content = state.bridge.get_article_content(article_id).await.tg_err()?;
                    bot.send_message(msg.chat.id, views::render_article_card(&content.article))
                        .reply_markup(keyboards::article_card_keyboard(article_id, &content.article.status, &content.article.url))
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await?;
                    return Ok(());
                }
            }
            send_hub(bot, msg.chat.id, state).await?;
            return Ok(());
        }

        match pending {
            PendingState::WaitingForComment { article_id } => {
                state.bridge.set_article_comment(article_id, Some(text.to_string())).await.tg_err()?;
                state.clear_pending_state(user.id).await;
                
                let content = state.bridge.get_article_content(article_id).await.tg_err()?;
                let mut text_card = "💬 <b>Комментарий сохранен!</b>\n\n".to_string();
                text_card.push_str(&views::render_article_card(&content.article));
                
                bot.send_message(msg.chat.id, text_card)
                    .reply_markup(keyboards::article_card_keyboard(article_id, &content.article.status, &content.article.url))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            PendingState::WaitingForTag { article_id } => {
                let tags: Vec<String> = text.split(',')
                    .map(|t| t.trim().to_string())
                    .filter(|t| !t.is_empty())
                    .collect();
                state.bridge.add_tags(article_id, tags).await.tg_err()?;
                state.clear_pending_state(user.id).await;
                
                let content = state.bridge.get_article_content(article_id).await.tg_err()?;
                let mut text_card = "🏷 <b>Теги обновлены!</b>\n\n".to_string();
                text_card.push_str(&views::render_article_card(&content.article));
                
                bot.send_message(msg.chat.id, text_card)
                    .reply_markup(keyboards::article_card_keyboard(article_id, &content.article.status, &content.article.url))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            PendingState::WaitingForSearchQuery => {
                state.update_search_session(user.id, |s| {
                    s.query = if text.is_empty() { None } else { Some(text.to_string()) };
                }).await;
                state.clear_pending_state(user.id).await;
                
                let session = state.get_search_session(user.id).await;
                bot.send_message(msg.chat.id, "🔎 <b>Поисковый запрос сохранен.</b>")
                    .reply_markup(keyboards::search_menu_keyboard(&session))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            PendingState::WaitingForFilterDomain => {
                state.update_search_session(user.id, |s| {
                    s.domain = if text.is_empty() { None } else { Some(text.to_string()) };
                }).await;
                state.clear_pending_state(user.id).await;
                
                let session = state.get_search_session(user.id).await;
                bot.send_message(msg.chat.id, "🌐 <b>Фильтр по домену обновлен.</b>")
                    .reply_markup(keyboards::search_menu_keyboard(&session))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            PendingState::None => {}
        }
        return Ok(());
    }

    // Try parsing as command
    if text.starts_with('/') {
        if let Ok(cmd) = Command::parse(text, "") {
            return handle_command(bot, msg, cmd, state).await;
        }
    }

    // Extract URLs if not a command and not in pending state
    let urls = super::extract_urls(text);
    if !urls.is_empty() {
        handle_urls(bot, msg, urls, state).await?;
    } else if text.starts_with('/') {
        let _ = bot
            .send_message(
                msg.chat.id,
                "Неизвестная команда. Введите /help для просмотра списка команд.",
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
        Command::Start | Command::Help | Command::Hub => {
            send_hub(bot, msg.chat.id, state).await?;
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
                    format!("Текущий формат сохранения по умолчанию: <b>{}</b>", current_fmt),
                )
                .reply_markup(keyboards::settings_keyboard(&current_fmt.to_string()))
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
                            format!("Формат по умолчанию успешно изменен на <b>{}</b>", fmt),
                        )
                        .reply_markup(keyboards::settings_keyboard(&fmt.to_string()))
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await?;
                    }
                    Err(_) => {
                        bot.send_message(msg.chat.id, "Недопустимый формат. Поддерживаемые форматы: <b>markdown</b>, <b>html</b>, <b>epub</b>")
                            .parse_mode(teloxide::types::ParseMode::Html)
                            .await?;
                    }
                }
            }
        }
        Command::Stats => {
            let stats = state.bridge.get_extended_stats().await.tg_err()?;
            bot.send_message(msg.chat.id, views::render_stats_overview(&stats))
                .reply_markup(keyboards::stats_menu_keyboard())
                .parse_mode(teloxide::types::ParseMode::Html)
                .await?;
        }
        Command::List => {
            let limit = 8;
            let paginated = state.bridge.search_articles_advanced(None, None, None, None, None, false, false, None, limit, 0).await.tg_err()?;
            let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;
            let next_cb = if total_pages > 1 { Some("list:all:1".to_string()) } else { None };
            
            bot.send_message(msg.chat.id, views::render_articles_list(&paginated.articles, "Все материалы библиотеки", 0, total_pages))
                .reply_markup(keyboards::pagination_keyboard(None, next_cb, "hub"))
                .parse_mode(teloxide::types::ParseMode::Html)
                .await?;
        }
        Command::Search(query) => {
            let query = query.trim();
            if query.is_empty() {
                let session = state.get_search_session(user.id).await;
                bot.send_message(msg.chat.id, "🔎 <b>Поиск материалов:</b>")
                    .reply_markup(keyboards::search_menu_keyboard(&session))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            } else {
                let limit = 6;
                let paginated = state.bridge.search_articles_advanced(Some(query.to_string()), None, None, None, None, false, false, None, limit, 0).await.tg_err()?;
                let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;
                
                // Save query to search session for user
                state.update_search_session(user.id, |s| {
                    s.query = Some(query.to_string());
                }).await;
                
                let next_cb = if total_pages > 1 { Some("sf_run:1".to_string()) } else { None };
                bot.send_message(msg.chat.id, views::render_articles_list(&paginated.articles, &format!("Результаты поиска по \"{}\"", query), 0, total_pages))
                    .reply_markup(keyboards::pagination_keyboard(None, next_cb, "search"))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
        }
        Command::Get(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    bot.send_message(
                        msg.chat.id,
                        "Пожалуйста, укажите числовой ID: /get &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.get_article_content(id).await.tg_err() {
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
                            text.push_str("\n\n[Содержимое урезано из-за лимита Telegram]");
                        }
                        bot.send_message(msg.chat.id, text).await?;
                    } else {
                        bot.send_message(
                            msg.chat.id,
                            "Файл не найден, а текст пуст.",
                        )
                        .await?;
                    }
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Ошибка при получении статьи: {}", e))
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
                        "Пожалуйста, укажите числовой ID: /read &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.mark_article_read(id).await.tg_err() {
                Ok(true) => {
                    bot.send_message(msg.chat.id, format!("Материал {} отмечен как прочитанный.", id))
                        .await?;
                }
                Ok(false) => {
                    bot.send_message(msg.chat.id, format!("Материал {} не найден.", id))
                        .await?;
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Ошибка: {}", e))
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
                        "Пожалуйста, укажите числовой ID: /unread &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.mark_article_unread(id).await.tg_err() {
                Ok(true) => {
                    bot.send_message(msg.chat.id, format!("Материал {} отмечен как непрочитанный.", id))
                        .await?;
                }
                Ok(false) => {
                    bot.send_message(msg.chat.id, format!("Материал {} не найден.", id))
                        .await?;
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Ошибка: {}", e))
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
                        "Пожалуйста, укажите числовой ID: /delete &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    return Ok(());
                }
            };
            match state.bridge.delete_article(id).await.tg_err() {
                Ok(true) => {
                    bot.send_message(msg.chat.id, format!("Материал {} успешно удален.", id))
                        .await?;
                }
                Ok(false) => {
                    bot.send_message(msg.chat.id, format!("Материал {} не найден.", id))
                        .await?;
                }
                Err(e) => {
                    bot.send_message(msg.chat.id, format!("Ошибка: {}", e))
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
                "⚠️ <b>ВНИМАНИЕ:</b> Все файлы, материалы и базы данных будут удалены.\n\n\
                Подтвердите сброс командой /confirmreset или отмените с помощью /cancel."
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
                match state.bridge.reset_library().await.tg_err() {
                    Ok(_) => {
                        bot.send_message(msg.chat.id, "Библиотека успешно очищена.")
                            .await?;
                    }
                    Err(e) => {
                        bot.send_message(msg.chat.id, format!("Ошибка при сбросе: {}", e))
                            .await?;
                    }
                }
            } else {
                bot.send_message(msg.chat.id, "Нет активного запроса на сброс. Введите /reset.")
                    .await?;
            }
        }
        Command::Cancel => {
            bot.send_message(msg.chat.id, "Действие отменено.").await?;
        }
    }

    Ok(())
}

async fn send_hub(bot: Bot, chat_id: ChatId, state: Arc<BotState>) -> ResponseResult<()> {
    let stats = state.bridge.get_reading_stats().await.tg_err()?;
    let progress = if stats.total_articles > 0 {
        (stats.read_articles as f64 / stats.total_articles as f64) * 100.0
    } else {
        0.0
    };
    
    let welcome = format!(
        "📚 <b>Read It Later Bot</b> — ваш удобный хаб для материалов!\n\n\
         Всего материалов: <b>{}</b>\n\
         Прочитано: <b>{}</b> (прогресс {:.1}%)\n\
         Не прочитано: <b>{}</b>\n\n\
         Отправьте мне ссылку, чтобы сохранить её, или воспользуйтесь кнопками меню:",
        stats.total_articles, stats.read_articles, progress, stats.unread_articles
    );
    bot.send_message(chat_id, welcome)
        .reply_markup(keyboards::hub_keyboard())
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
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

    let chat_id = msg.chat.id;
    
    // Send a single compact message for importing
    let status_msg = bot
        .send_message(
            chat_id,
            format!("⏳ <b>Импорт {} ссылок...</b>", urls.len()),
        )
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;

    let bridge = state.bridge.clone();
    let sem = Arc::new(tokio::sync::Semaphore::new(2));

    let mut join_handles = vec![];

    for url in urls {
        let sem = sem.clone();
        let bridge = bridge.clone();
        
        let handle = tokio::spawn(async move {
            let _permit = sem.acquire().await.unwrap();
            bridge.process_url(&url, import_format).await
        });
        join_handles.push(handle);
    }

    let mut imported = vec![];
    let mut errors = vec![];

    for h in join_handles {
        match h.await {
            Ok(Ok(res)) => imported.push(res),
            Ok(Err(e)) => errors.push(e.to_string()),
            Err(e) => errors.push(format!("Join error: {}", e)),
        }
    }

    if !imported.is_empty() {
        // Show card of the last successfully imported article
        let last_res = &imported[imported.len() - 1];
        
        let mut final_text = format!(
            "✅ <b>Материал добавлен!</b>\n\n\
             <b>Название:</b> {}\n\
             <b>Источник:</b> {}\n\
             <b>Слов:</b> {}\n\
             <b>ID:</b> <code>{}</code>\n\n\
             Что сделать дальше?",
            views::escape_html(&last_res.title),
            views::escape_html(&views::format_domain(&last_res.url)),
            last_res.word_count,
            last_res.id
        );
        
        if imported.len() > 1 {
            final_text.insert_str(0, &format!("📥 Успешно импортировано {} материалов.\n\n", imported.len()));
        }
        if !errors.is_empty() {
            final_text.push_str(&format!("\n\n⚠️ Ошибок при импорте: {}", errors.len()));
        }

        let markup = keyboards::article_card_keyboard(last_res.id, "unread", &last_res.url);

        // Edit the status message into the final article card
        let _ = bot.edit_message_text(chat_id, status_msg.id, final_text)
            .reply_markup(markup)
            .parse_mode(teloxide::types::ParseMode::Html)
            .await;
    } else {
        // If all imports failed
        let mut error_text = "❌ <b>Не удалось импортировать материалы</b>\n\n".to_string();
        for (i, err) in errors.iter().enumerate() {
            error_text.push_str(&format!("{}. {}\n", i + 1, views::escape_html(&err.to_string())));
        }
        
        let _ = bot.edit_message_text(chat_id, status_msg.id, error_text)
            .reply_markup(keyboards::back_to_hub_keyboard())
            .parse_mode(teloxide::types::ParseMode::Html)
            .await;
    }

    Ok(())
}
