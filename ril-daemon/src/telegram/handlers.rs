use crate::domain::SaveFormat;
use crate::telegram::state::{BotState, PendingState};
use crate::telegram::{keyboards, views, Command, MapTgError};
use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::{InputFile, Message, UserId};
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

    // Delete incoming user message immediately to prevent clutter
    let _ = bot.delete_message(msg.chat.id, msg.id).await;

    let text = msg.text().unwrap_or("").trim();

    // Check pending states first (e.g. user is entering comments or tags)
    let pending = state.get_pending_state(user.id).await;
    if !matches!(pending, PendingState::None) {
        if text.eq_ignore_ascii_case("/cancel") {
            state.clear_pending_state(user.id).await;
            bot.send_message(msg.chat.id, "Действие отменено.").await?;
            // Return to hub or article if we had one
            if let PendingState::WaitingForComment { article_id }
            | PendingState::WaitingForTag { article_id } = pending
            {
                if article_id > 0 {
                    let content = state
                        .bridge
                        .get_article_content(article_id)
                        .await
                        .tg_err()?;
                    bot.send_message(msg.chat.id, views::render_article_card(&content.article))
                        .reply_markup(keyboards::article_card_keyboard(
                            article_id,
                            &content.article.status,
                            &content.article.url,
                        ))
                        .parse_mode(teloxide::types::ParseMode::Html)
                        .await?;
                    return Ok(());
                }
            }
            send_hub(bot, msg.chat.id, state, user.id).await?;
            return Ok(());
        }

        match pending {
            PendingState::WaitingForComment { article_id } => {
                state
                    .bridge
                    .set_article_comment(article_id, Some(text.to_string()))
                    .await
                    .tg_err()?;
                state.clear_pending_state(user.id).await;

                let content = state
                    .bridge
                    .get_article_content(article_id)
                    .await
                    .tg_err()?;
                let mut text_card = "💬 <b>Комментарий сохранен!</b>\n\n".to_string();
                text_card.push_str(&views::render_article_card(&content.article));

                bot.send_message(msg.chat.id, text_card)
                    .reply_markup(keyboards::article_card_keyboard(
                        article_id,
                        &content.article.status,
                        &content.article.url,
                    ))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            PendingState::WaitingForTag { article_id } => {
                let tags = super::helpers::normalize_tags(text);
                state.bridge.add_tags(article_id, tags).await.tg_err()?;
                state.clear_pending_state(user.id).await;

                let content = state
                    .bridge
                    .get_article_content(article_id)
                    .await
                    .tg_err()?;
                let mut text_card = "🏷 <b>Теги обновлены!</b>\n\n".to_string();
                text_card.push_str(&views::render_article_card(&content.article));

                bot.send_message(msg.chat.id, text_card)
                    .reply_markup(keyboards::article_card_keyboard(
                        article_id,
                        &content.article.status,
                        &content.article.url,
                    ))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            PendingState::WaitingForSearchQuery => {
                state
                    .update_search_session(user.id, |s| {
                        s.query = if text.is_empty() {
                            None
                        } else {
                            Some(text.to_string())
                        };
                    })
                    .await;
                state.clear_pending_state(user.id).await;

                let session = state.get_search_session(user.id).await;
                bot.send_message(msg.chat.id, "🔎 <b>Поисковый запрос сохранен.</b>")
                    .reply_markup(keyboards::search_menu_keyboard(&session))
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
            }
            PendingState::WaitingForFilterDomain => {
                state
                    .update_search_session(user.id, |s| {
                        s.domain = if text.is_empty() {
                            None
                        } else {
                            Some(text.to_string())
                        };
                    })
                    .await;
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
            send_hub(bot, msg.chat.id, state, user.id).await?;
        }
        Command::Format(arg) => {
            let arg = arg.trim();
            if arg.is_empty() {
                let current_fmt = {
                    let map = state.user_formats.lock().await;
                    *map.get(&user.id).unwrap_or(&state.config.default_format)
                };
                send_menu_message(
                    bot,
                    msg.chat.id,
                    user.id,
                    format!(
                        "Текущий формат сохранения по умолчанию: <b>{}</b>",
                        current_fmt
                    ),
                    Some(keyboards::settings_keyboard(&current_fmt.to_string())),
                    state,
                )
                .await?;
            } else {
                match arg.parse::<SaveFormat>() {
                    Ok(fmt) => {
                        {
                            let mut map = state.user_formats.lock().await;
                            map.insert(user.id, fmt);
                        }
                        send_menu_message(
                            bot,
                            msg.chat.id,
                            user.id,
                            format!("Формат по умолчанию успешно изменен на <b>{}</b>", fmt),
                            Some(keyboards::settings_keyboard(&fmt.to_string())),
                            state,
                        )
                        .await?;
                    }
                    Err(_) => {
                        send_menu_message(
                            bot,
                            msg.chat.id,
                            user.id,
                            "Недопустимый формат. Поддерживаемые форматы: <b>markdown</b>, <b>html</b>, <b>epub</b>".to_string(),
                            None,
                            state,
                        )
                        .await?;
                    }
                }
            }
        }
        Command::Stats => {
            let stats = state.bridge.get_extended_stats().await.tg_err()?;
            send_menu_message(
                bot,
                msg.chat.id,
                user.id,
                views::render_stats_overview(&stats),
                Some(keyboards::stats_menu_keyboard()),
                state,
            )
            .await?;
        }
        Command::List => {
            let limit = 8;
            let paginated = state
                .bridge
                .search_articles_advanced(
                    None, None, None, None, None, false, false, None, limit, 0,
                )
                .await
                .tg_err()?;
            let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;
            let next_cb = if total_pages > 1 {
                Some("list:all:1".to_string())
            } else {
                None
            };

            send_menu_message(
                bot,
                msg.chat.id,
                user.id,
                views::render_articles_list(
                    &paginated.articles,
                    "Все материалы библиотеки",
                    0,
                    total_pages,
                ),
                Some(keyboards::articles_list_keyboard(&paginated.articles, None, next_cb, "hub")),
                state,
            )
            .await?;
        }
        Command::Search(query) => {
            let query = query.trim();
            if query.is_empty() {
                let session = state.get_search_session(user.id).await;
                send_menu_message(
                    bot,
                    msg.chat.id,
                    user.id,
                    "🔎 <b>Поиск материалов:</b>".to_string(),
                    Some(keyboards::search_menu_keyboard(&session)),
                    state,
                )
                .await?;
            } else {
                let limit = 6;
                let paginated = state
                    .bridge
                    .search_articles_advanced(
                        Some(query.to_string()),
                        None,
                        None,
                        None,
                        None,
                        false,
                        false,
                        None,
                        limit,
                        0,
                    )
                    .await
                    .tg_err()?;
                let total_pages = (paginated.total_count as f64 / limit as f64).ceil() as i64;

                // Save query to search session for user
                state
                    .update_search_session(user.id, |s| {
                        s.query = Some(query.to_string());
                    })
                    .await;

                let next_cb = if total_pages > 1 {
                    Some("sf_run:1".to_string())
                } else {
                    None
                };
                send_menu_message(
                    bot,
                    msg.chat.id,
                    user.id,
                    views::render_articles_list(
                        &paginated.articles,
                        &format!("Результаты поиска по \"{}\"", query),
                        0,
                        total_pages,
                    ),
                    Some(keyboards::articles_list_keyboard(&paginated.articles, None, next_cb, "search")),
                    state,
                )
                .await?;
            }
        }
        Command::Get(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    let sent = bot.send_message(
                        msg.chat.id,
                        "Пожалуйста, укажите числовой ID: /get &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                    return Ok(());
                }
            };
            match state.bridge.get_article_content(id).await.tg_err() {
                Ok(res) => {
                    let file_path = std::path::Path::new(&res.article.file_path);
                    if file_path.exists() {
                        let doc = InputFile::file(file_path);
                        let read_time = (res.article.word_count as f64 / 200.0).ceil() as i64;
                        let rating_stars = match res.article.rating {
                            Some(r) => "⭐".repeat(r as usize),
                            None => "нет оценки".to_string(),
                        };
                        let status_emoji = if res.article.status == "read" { "✅" } else { "📖" };
                        let status_text = if res.article.status == "read" { "Прочитано" } else { "Не прочитано" };
                        
                        let caption = format!(
                            "📄 <b>{}</b>\n\n\
                             <b>Слов:</b> {} (~{} мин. чтения)\n\
                             <b>Статус:</b> {} {}\n\
                             <b>Оценка:</b> {}\n\
                             <b>ID:</b> <code>{}</code>",
                            views::escape_html(&res.article.title),
                            res.article.word_count,
                            read_time,
                            status_emoji,
                            status_text,
                            rating_stars,
                            res.article.id
                        );
                        let markup = keyboards::document_keyboard(id, &res.article.status);
                        bot.send_document(msg.chat.id, doc)
                            .caption(caption)
                            .reply_markup(markup)
                            .parse_mode(teloxide::types::ParseMode::Html)
                            .await?;
                    } else if !res.content.is_empty() {
                        let mut text = res.content;
                        if text.len() > 4000 {
                            text.truncate(4000);
                            text.push_str("\n\n[Содержимое урезано из-за лимита Telegram]");
                        }
                        bot.send_message(msg.chat.id, text).await?;
                    } else {
                        let sent = bot.send_message(msg.chat.id, "Файл не найден, а текст пуст.")
                            .await?;
                        spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                    }
                }
                Err(e) => {
                    let sent = bot.send_message(msg.chat.id, format!("Ошибка при получении статьи: {}", e))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
            }
        }
        Command::Read(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    let sent = bot.send_message(
                        msg.chat.id,
                        "Пожалуйста, укажите числовой ID: /read &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                    return Ok(());
                }
            };
            match state.bridge.mark_article_read(id).await.tg_err() {
                Ok(true) => {
                    let sent = bot.send_message(
                        msg.chat.id,
                        format!("Материал {} отмечен как прочитанный.", id),
                    )
                    .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
                Ok(false) => {
                    let sent = bot.send_message(msg.chat.id, format!("Материал {} не найден.", id))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
                Err(e) => {
                    let sent = bot.send_message(msg.chat.id, format!("Ошибка: {}", e))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
            }
        }
        Command::Unread(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    let sent = bot.send_message(
                        msg.chat.id,
                        "Пожалуйста, укажите числовой ID: /unread &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                    return Ok(());
                }
            };
            match state.bridge.mark_article_unread(id).await.tg_err() {
                Ok(true) => {
                    let sent = bot.send_message(
                        msg.chat.id,
                        format!("Материал {} отмечен как непрочитанный.", id),
                    )
                    .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
                Ok(false) => {
                    let sent = bot.send_message(msg.chat.id, format!("Материал {} не найден.", id))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
                Err(e) => {
                    let sent = bot.send_message(msg.chat.id, format!("Ошибка: {}", e))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
            }
        }
        Command::Delete(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    let sent = bot.send_message(
                        msg.chat.id,
                        "Пожалуйста, укажите числовой ID: /delete &lt;id&gt;",
                    )
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                    return Ok(());
                }
            };
            match state.bridge.delete_article(id).await.tg_err() {
                Ok(true) => {
                    let sent = bot.send_message(msg.chat.id, format!("Материал {} успешно удален.", id))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
                Ok(false) => {
                    let sent = bot.send_message(msg.chat.id, format!("Материал {} не найден.", id))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
                Err(e) => {
                    let sent = bot.send_message(msg.chat.id, format!("Ошибка: {}", e))
                        .await?;
                    spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                }
            }
        }
        Command::Reset => {
            {
                let mut pending = state.pending_resets.lock().await;
                pending.insert(user.id);
            }
            let sent = bot.send_message(
                msg.chat.id,
                "⚠️ <b>ВНИМАНИЕ:</b> Все файлы, материалы и базы данных будут удалены.\n\n\
                Подтвердите сброс командой /confirmreset или отмените с помощью /cancel.",
            )
            .parse_mode(teloxide::types::ParseMode::Html)
            .await?;
            spawn_delayed_delete(bot, msg.chat.id, sent.id, 15);
        }
        Command::ConfirmReset => {
            let is_pending = {
                let mut pending = state.pending_resets.lock().await;
                pending.remove(&user.id)
            };
            if is_pending {
                match state.bridge.reset_library().await.tg_err() {
                    Ok(_) => {
                        let sent = bot.send_message(msg.chat.id, "Библиотека успешно очищена.")
                            .await?;
                        spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                    }
                    Err(e) => {
                        let sent = bot.send_message(msg.chat.id, format!("Ошибка при сбросе: {}", e))
                            .await?;
                        spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
                    }
                }
            } else {
                let sent = bot.send_message(
                    msg.chat.id,
                    "Нет активного запроса на сброс. Введите /reset.",
                )
                .await?;
                spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
            }
        }
        Command::Cancel => {
            let sent = bot.send_message(msg.chat.id, "Действие отменено.").await?;
            spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
        }
    }

    Ok(())
}

async fn send_menu_message(
    bot: Bot,
    chat_id: ChatId,
    user_id: UserId,
    text: String,
    reply_markup: Option<teloxide::types::InlineKeyboardMarkup>,
    state: Arc<BotState>,
) -> ResponseResult<Message> {
    // Delete previous menu message if exists
    if let Some(prev_id) = state.get_and_clear_last_menu(user_id).await {
        let _ = bot.delete_message(chat_id, teloxide::types::MessageId(prev_id)).await;
    }

    let mut send = bot.send_message(chat_id, text);
    if let Some(markup) = reply_markup {
        send = send.reply_markup(markup);
    }
    let sent = send.parse_mode(teloxide::types::ParseMode::Html).await?;

    state.set_last_menu(user_id, sent.id.0).await;
    Ok(sent)
}

fn spawn_delayed_delete(bot: Bot, chat_id: ChatId, msg_id: teloxide::types::MessageId, delay_secs: u64) {
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_secs(delay_secs)).await;
        let _ = bot.delete_message(chat_id, msg_id).await;
    });
}

async fn send_hub(
    bot: Bot,
    chat_id: ChatId,
    state: Arc<BotState>,
    user_id: UserId,
) -> ResponseResult<()> {
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
    send_menu_message(
        bot,
        chat_id,
        user_id,
        welcome,
        Some(keyboards::hub_keyboard()),
        state,
    )
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
        // Delete the temporary status message
        let _ = bot.delete_message(chat_id, status_msg.id).await;

        for res in &imported {
            let file_path = std::path::Path::new(&res.file_path);
            if file_path.exists() {
                let doc = InputFile::file(file_path);
                let read_time = (res.word_count as f64 / 200.0).ceil() as i64;
                let caption = format!(
                    "📥 <b>Материал добавлен!</b>\n\n\
                     <b>Название:</b> {}\n\
                     <b>Источник:</b> {}\n\
                     <b>Слов:</b> {} (~{} мин. чтения)\n\
                     <b>ID:</b> <code>{}</code>",
                    views::escape_html(&res.title),
                    views::escape_html(&views::format_domain(&res.url)),
                    res.word_count,
                    read_time,
                    res.id
                );
                let markup = keyboards::document_keyboard(res.id, "unread");
                let _ = bot
                    .send_document(chat_id, doc)
                    .caption(caption)
                    .reply_markup(markup)
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await;
            } else {
                let text = format!(
                    "⚠️ Файл не найден на диске, но сохранен в базе:\n\n\
                     📥 <b>Материал добавлен!</b>\n\n\
                     <b>Название:</b> {}\n\
                     <b>Источник:</b> {}\n\
                     <b>Слов:</b> {}\n\
                     <b>ID:</b> <code>{}</code>",
                    views::escape_html(&res.title),
                    views::escape_html(&views::format_domain(&res.url)),
                    res.word_count,
                    res.id
                );
                let markup = keyboards::article_card_keyboard(res.id, "unread", &res.url);
                let _ = bot
                    .send_message(chat_id, text)
                    .reply_markup(markup)
                    .parse_mode(teloxide::types::ParseMode::Html)
                    .await;
            }
        }

        if !errors.is_empty() {
            let mut error_text = "⚠️ <b>Ошибок при импорте некоторых материалов:</b>\n\n".to_string();
            for (i, err) in errors.iter().enumerate() {
                error_text.push_str(&format!("{}. {}\n", i + 1, views::escape_html(err)));
            }
            let _ = bot
                .send_message(chat_id, error_text)
                .reply_markup(keyboards::back_to_hub_keyboard())
                .parse_mode(teloxide::types::ParseMode::Html)
                .await;
        }
    } else {
        // If all imports failed
        let mut error_text = "❌ <b>Не удалось импортировать материалы</b>\n\n".to_string();
        for (i, err) in errors.iter().enumerate() {
            error_text.push_str(&format!(
                "{}. {}\n",
                i + 1,
                views::escape_html(err)
            ));
        }

        let _ = bot
            .edit_message_text(chat_id, status_msg.id, error_text)
            .reply_markup(keyboards::back_to_hub_keyboard())
            .parse_mode(teloxide::types::ParseMode::Html)
            .await;
    }

    Ok(())
}
