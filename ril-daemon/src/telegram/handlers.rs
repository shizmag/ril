use crate::domain::SaveFormat;
use crate::telegram::state::{BotState, PendingState};
use crate::telegram::{keyboards, views, Command, MapTgError};
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

    // Delete incoming user message immediately to prevent clutter
    let _ = bot.delete_message(msg.chat.id, msg.id).await;

    let text = msg.text().unwrap_or("").trim();

    // Check pending states first (e.g. user is entering comments or tags)
    let pending = state.get_pending_state(user.id).await;
    if !matches!(pending, PendingState::None) {
        if text.eq_ignore_ascii_case("/cancel") {
            state.clear_pending_state(user.id).await;
            // Return to hub or article if we had one
            if let PendingState::WaitingForComment { article_id }
            | PendingState::WaitingForTag { article_id } = pending
            {
                if article_id > 0 {
                    super::helpers::show_article_card_screen(bot, msg.chat.id, state, user.id, article_id).await?;
                    return Ok(());
                }
            }
            super::helpers::show_hub(bot, msg.chat.id, state, user.id).await?;
            return Ok(());
        }

        match pending {
            PendingState::WaitingForComment { article_id } => {
                match super::helpers::validate_comment(text) {
                    Ok(valid_comment) => {
                        state
                            .bridge
                            .set_article_comment(article_id, Some(valid_comment))
                            .await
                            .tg_err()?;
                        state.clear_pending_state(user.id).await;
                        super::helpers::show_article_card_screen(bot, msg.chat.id, state, user.id, article_id).await?;
                    }
                    Err(err) => {
                        let content = state
                            .bridge
                            .get_article_content(article_id)
                            .await
                            .tg_err()?;
                        let text_err = format!(
                            "⚠️ <b>Ошибка: {}</b>\n\n💬 Введите комментарий к материалу:\n\n“{}”\n\nДо 1000 символов.\nДля отмены нажмите кнопку ниже или отправьте /cancel.",
                            err, views::escape_html(&content.article.title)
                        );
                        super::helpers::show_state_screen(
                            bot,
                            msg.chat.id,
                            state,
                            user.id,
                            text_err,
                            Some(keyboards::pending_input_keyboard(article_id)),
                        )
                        .await?;
                    }
                }
            }
            PendingState::WaitingForTag { article_id } => {
                let tags = super::helpers::normalize_tags(text);
                state.bridge.add_tags(article_id, tags).await.tg_err()?;
                state.clear_pending_state(user.id).await;
                super::helpers::show_article_card_screen(bot, msg.chat.id, state, user.id, article_id).await?;
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
                super::helpers::show_search_menu_screen(bot, msg.chat.id, state, user.id).await?;
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
                super::helpers::show_search_menu_screen(bot, msg.chat.id, state, user.id).await?;
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
        let sent = bot
            .send_message(
                msg.chat.id,
                "Неизвестная команда. Введите /help для просмотра списка команд.",
            )
            .await?;
        spawn_delayed_delete(bot, msg.chat.id, sent.id, 5);
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
            super::helpers::show_hub(bot, msg.chat.id, state, user.id).await?;
        }
        Command::Format(arg) => {
            let arg = arg.trim();
            if arg.is_empty() {
                super::helpers::show_settings_screen(bot, msg.chat.id, state, user.id).await?;
            } else {
                match arg.parse::<SaveFormat>() {
                    Ok(fmt) => {
                        {
                            let mut map = state.user_formats.lock().await;
                            map.insert(user.id, fmt);
                        }
                        super::helpers::show_settings_screen(bot, msg.chat.id, state, user.id).await?;
                    }
                    Err(_) => {
                        let text = "Недопустимый формат. Поддерживаемые форматы: <b>markdown</b>, <b>html</b>, <b>epub</b>".to_string();
                        let markup = keyboards::back_to_hub_keyboard();
                        super::helpers::show_state_screen(bot, msg.chat.id, state, user.id, text, Some(markup)).await?;
                    }
                }
            }
        }
        Command::Stats => {
            super::helpers::show_stats_screen(bot, msg.chat.id, state, user.id, "overview").await?;
        }
        Command::List => {
            super::helpers::show_articles_list_screen(bot, msg.chat.id, state, user.id, "all", 0).await?;
        }
        Command::Search(query) => {
            let query = query.trim();
            if query.is_empty() {
                super::helpers::show_search_menu_screen(bot, msg.chat.id, state, user.id).await?;
            } else {
                // Save query to search session for user
                state
                    .update_search_session(user.id, |s| {
                        s.query = Some(query.to_string());
                    })
                    .await;
                
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
                let next_cb = if total_pages > 1 {
                    Some("sf_run:1".to_string())
                } else {
                    None
                };
                let text = views::render_articles_list(
                    &paginated.articles,
                    &format!("Результаты поиска по \"{}\"", query),
                    0,
                    total_pages,
                );
                let markup = keyboards::articles_list_keyboard(&paginated.articles, None, next_cb, "search");
                super::helpers::show_state_screen(bot, msg.chat.id, state, user.id, text, Some(markup)).await?;
            }
        }
        Command::Get(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Укажите числовой ID: /get <id>").await?;
                    return Ok(());
                }
            };
            
            let user_format = {
                let map = state.user_formats.lock().await;
                *map.get(&user.id).unwrap_or(&state.config.default_format)
            };

            match state.bridge.export_article(id, user_format).await.tg_err() {
                Ok(export_res) => {
                    let file_path = std::path::Path::new(&export_res.file_path);
                    if file_path.exists() {
                        let doc = InputFile::file(file_path);
                        let caption = format!(
                            "📥 <b>{} [{}]</b>\n\n\
                             <b>Формат:</b> {}\n\
                             <b>Слов:</b> {} (~{} мин. чтения)\n\
                             <b>Статус:</b> {} {}\n\
                             <b>Оценка:</b> {}",
                            views::escape_html(&export_res.title),
                            export_res.article_id,
                            export_res.format.to_uppercase(),
                            export_res.word_count,
                            (export_res.word_count as f64 / 200.0).ceil() as i64,
                            if export_res.status == "read" { "✅" } else { "📖" },
                            if export_res.status == "read" { "Прочитано" } else { "Не прочитано" },
                            match export_res.rating {
                                Some(r) => "⭐".repeat(r as usize),
                                None => "нет оценки".to_string(),
                            }
                        );
                        let markup = keyboards::document_keyboard(id, &export_res.status);
                        bot.send_document(msg.chat.id, doc)
                            .caption(caption)
                            .reply_markup(markup)
                            .parse_mode(teloxide::types::ParseMode::Html)
                            .await?;
                    } else {
                        super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Файл не найден на диске.").await?;
                    }
                }
                Err(e) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, &format!("Ошибка при экспорте статьи: {}", e)).await?;
                }
            }
        }
        Command::Read(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Укажите числовой ID: /read <id>").await?;
                    return Ok(());
                }
            };
            match state.bridge.mark_article_read(id).await.tg_err() {
                Ok(true) => {
                    super::helpers::show_article_card_screen(bot, msg.chat.id, state, user.id, id).await?;
                }
                Ok(false) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Материал не найден.").await?;
                }
                Err(e) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, &format!("Ошибка: {}", e)).await?;
                }
            }
        }
        Command::Unread(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Укажите числовой ID: /unread <id>").await?;
                    return Ok(());
                }
            };
            match state.bridge.mark_article_unread(id).await.tg_err() {
                Ok(true) => {
                    super::helpers::show_article_card_screen(bot, msg.chat.id, state, user.id, id).await?;
                }
                Ok(false) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Материал не найден.").await?;
                }
                Err(e) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, &format!("Ошибка: {}", e)).await?;
                }
            }
        }
        Command::Delete(id) => {
            let id = match id.split_whitespace().next().unwrap_or("").parse::<i64>() {
                Ok(num) => num,
                Err(_) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Укажите числовой ID: /delete <id>").await?;
                    return Ok(());
                }
            };
            // Show delete confirmation
            match state.bridge.get_article_content(id).await.tg_err() {
                Ok(content) => {
                    let text = format!(
                        "⚠️ <b>Вы уверены, что хотите удалить этот материал?</b>\n\n<b>{}</b>",
                        views::escape_html(&content.article.title)
                    );
                    let markup = keyboards::delete_confirm_keyboard(id);
                    super::helpers::show_state_screen(bot, msg.chat.id, state, user.id, text, Some(markup)).await?;
                }
                Err(_) => {
                    super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Материал не найден.").await?;
                }
            }
        }
        Command::Reset => {
            {
                let mut pending = state.pending_resets.lock().await;
                pending.insert(user.id);
            }
            let text = "⚠️ <b>ВНИМАНИЕ:</b> Все файлы, материалы и базы данных будут удалены.\n\n\
                        Подтвердите сброс командой /confirmreset или отмените с помощью /cancel.";
            let markup = keyboards::reset_lib_confirm_keyboard();
            super::helpers::show_state_screen(bot, msg.chat.id, state, user.id, text.to_string(), Some(markup)).await?;
        }
        Command::ConfirmReset => {
            let is_pending = {
                let mut pending = state.pending_resets.lock().await;
                pending.remove(&user.id)
            };
            if is_pending {
                match state.bridge.reset_library().await.tg_err() {
                    Ok(_) => {
                        let text = "✅ <b>Библиотека успешно очищена.</b>";
                        let markup = keyboards::back_to_hub_keyboard();
                        super::helpers::show_state_screen(bot, msg.chat.id, state, user.id, text.to_string(), Some(markup)).await?;
                    }
                    Err(e) => {
                        super::helpers::show_error_state(bot, msg.chat.id, state, user.id, &format!("Ошибка при сбросе: {}", e)).await?;
                    }
                }
            } else {
                super::helpers::show_error_state(bot, msg.chat.id, state, user.id, "Нет активного запроса на сброс. Введите /reset.").await?;
            }
        }
        Command::Cancel => {
            state.clear_pending_state(user.id).await;
            super::helpers::show_hub(bot, msg.chat.id, state, user.id).await?;
        }
    }

    Ok(())
}

fn spawn_delayed_delete(bot: Bot, chat_id: ChatId, msg_id: teloxide::types::MessageId, delay_secs: u64) {
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_secs(delay_secs)).await;
        let _ = bot.delete_message(chat_id, msg_id).await;
    });
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
    let text_lower = text.to_lowercase();
    let force = text_lower.contains("force") || text_lower.contains("update") || text_lower.contains("обновить");
    let import_format = super::detect_format_override(text, default_fmt);
    let chat_id = msg.chat.id;

    // Show initial status screen in the state message
    let status_text = format!("⏳ <b>Импорт {} ссылок...</b>", urls.len());
    super::helpers::show_state_screen(bot.clone(), chat_id, state.clone(), user.id, status_text, None).await?;

    let bridge = state.bridge.clone();
    let sem = Arc::new(tokio::sync::Semaphore::new(2));

    let mut join_handles = vec![];

    for url in urls {
        let sem = sem.clone();
        let bridge = bridge.clone();

        let handle = tokio::spawn(async move {
            let _permit = sem.acquire().await.unwrap();
            bridge.process_url(&url, import_format, force).await
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

    // Save lists in user state
    let imported_ids: Vec<i64> = imported.iter().map(|a| a.id).collect();
    state.set_last_imported(user.id, imported_ids).await;
    state.set_last_errors(user.id, errors.clone()).await;

    // Format the results screen
    let mut text_res = "✅ <b>Импорт завершен</b>\n\n".to_string();
    text_res.push_str(&format!("Добавлено: <b>{}</b>\n", imported.len()));
    text_res.push_str(&format!("Ошибок: <b>{}</b>\n\n", errors.len()));

    for (i, res) in imported.iter().enumerate() {
        let domain = views::format_domain(&res.url);
        let read_time = (res.word_count as f64 / 200.0).ceil() as i64;
        text_res.push_str(&format!("{}. {} — {} — {} мин\n", i + 1, views::escape_html(&res.title), domain, read_time));
    }

    let has_errors = !errors.is_empty();
    let markup = keyboards::import_results_keyboard(has_errors);
    super::helpers::show_state_screen(bot, chat_id, state, user.id, text_res, Some(markup)).await?;

    Ok(())
}
