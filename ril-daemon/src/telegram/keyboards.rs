use crate::domain::{ArticleSummary, TagStat};
use crate::telegram::state::SearchSession;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup};

pub fn hub_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![InlineKeyboardButton::callback(
            "📚 Все материалы",
            "list:all:0",
        )],
        vec![
            InlineKeyboardButton::callback("📖 Непрочитанные", "list:unread:0"),
            InlineKeyboardButton::callback("✅ Прочитанные", "list:read:0"),
        ],
        vec![
            InlineKeyboardButton::callback("🔎 Поиск", "search"),
            InlineKeyboardButton::callback("🏷 Теги", "tags_list:0"),
        ],
        vec![
            InlineKeyboardButton::callback("⭐ Оценки", "ratings_list"),
            InlineKeyboardButton::callback("📊 Статистика", "stats:overview"),
        ],
        vec![InlineKeyboardButton::callback("⚙️ Настройки", "settings")],
    ])
}

pub fn article_card_keyboard(art_id: i64, status: &str, url: &str) -> InlineKeyboardMarkup {
    let status_btn = if status == "read" {
        InlineKeyboardButton::callback("📖 Отметить непрочитанным", format!("unread:{}", art_id))
    } else {
        InlineKeyboardButton::callback("✅ Отметить прочитанным", format!("read:{}", art_id))
    };

    let original_url = url
        .parse()
        .unwrap_or_else(|_| "https://google.com".parse().unwrap());

    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📥 Скачать", format!("get_file:{}", art_id)),
            InlineKeyboardButton::url("📄 Читать", original_url),
        ],
        vec![status_btn],
        vec![
            InlineKeyboardButton::callback("🏷 Теги", format!("art_tags:{}", art_id)),
            InlineKeyboardButton::callback("⭐ Оценка", format!("art_rate:{}", art_id)),
            InlineKeyboardButton::callback("💬 Комментарий", format!("art_comm:{}", art_id)),
        ],
        vec![
            InlineKeyboardButton::callback("🗑 Удалить", format!("art_del:{}", art_id)),
            InlineKeyboardButton::callback("🏠 В хаб", "hub"),
        ],
    ])
}

pub fn document_keyboard(art_id: i64, status: &str) -> InlineKeyboardMarkup {
    let status_btn = if status == "read" {
        InlineKeyboardButton::callback("📖 В непрочитанные", format!("toggle_doc:{}", art_id))
    } else {
        InlineKeyboardButton::callback("✅ Прочитано", format!("toggle_doc:{}", art_id))
    };

    InlineKeyboardMarkup::new(vec![
        vec![
            status_btn,
            InlineKeyboardButton::callback("🗑 Удалить", format!("del_doc:{}", art_id)),
        ],
        vec![
            InlineKeyboardButton::callback("⭐ 1", format!("rate_doc:{}:1", art_id)),
            InlineKeyboardButton::callback("⭐ 2", format!("rate_doc:{}:2", art_id)),
            InlineKeyboardButton::callback("⭐ 3", format!("rate_doc:{}:3", art_id)),
            InlineKeyboardButton::callback("⭐ 4", format!("rate_doc:{}:4", art_id)),
            InlineKeyboardButton::callback("⭐ 5", format!("rate_doc:{}:5", art_id)),
        ],
        vec![InlineKeyboardButton::callback(
            "🔙 Назад к материалу",
            format!("art:{}", art_id),
        )],
    ])
}

pub fn delete_confirm_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("⚠️ Да, удалить", format!("art_del_conf:{}", art_id)),
        InlineKeyboardButton::callback("❌ Отмена", format!("art:{}", art_id)),
    ]])
}

pub fn rating_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("⭐ 1", format!("rate_set:{}:1", art_id)),
            InlineKeyboardButton::callback("⭐ 2", format!("rate_set:{}:2", art_id)),
            InlineKeyboardButton::callback("⭐ 3", format!("rate_set:{}:3", art_id)),
            InlineKeyboardButton::callback("⭐ 4", format!("rate_set:{}:4", art_id)),
            InlineKeyboardButton::callback("⭐ 5", format!("rate_set:{}:5", art_id)),
        ],
        vec![InlineKeyboardButton::callback(
            "❌ Сбросить оценку",
            format!("rate_set:{}:0", art_id),
        )],
        vec![InlineKeyboardButton::callback(
            "🔙 Назад",
            format!("art:{}", art_id),
        )],
    ])
}

pub fn comment_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📝 Написать/Изменить", format!("comm_set:{}", art_id)),
            InlineKeyboardButton::callback("🗑 Удалить", format!("comm_del:{}", art_id)),
        ],
        vec![InlineKeyboardButton::callback(
            "🔙 Назад",
            format!("art:{}", art_id),
        )],
    ])
}

pub fn pagination_keyboard(
    prev_cb: Option<String>,
    next_cb: Option<String>,
    home_cb: &str,
) -> InlineKeyboardMarkup {
    let mut row = vec![];
    if let Some(prev) = prev_cb {
        row.push(InlineKeyboardButton::callback("⬅️ Назад", prev));
    }
    if let Some(next) = next_cb {
        row.push(InlineKeyboardButton::callback("➡️ Далее", next));
    }

    InlineKeyboardMarkup::new(vec![
        row,
        vec![InlineKeyboardButton::callback("🏠 В хаб", home_cb)],
    ])
}

pub fn articles_list_keyboard(
    articles: &[ArticleSummary],
    prev_cb: Option<String>,
    next_cb: Option<String>,
    home_cb: &str,
) -> InlineKeyboardMarkup {
    let mut rows = vec![];
    for a in articles {
        let mut label = a.title.clone();
        if label.chars().count() > 30 {
            label = label.chars().take(27).collect::<String>() + "...";
        }
        rows.push(vec![InlineKeyboardButton::callback(
            format!("📄 [{}] {}", a.id, label),
            format!("art:{}", a.id),
        )]);
    }

    let mut pag_row = vec![];
    if let Some(prev) = prev_cb {
        pag_row.push(InlineKeyboardButton::callback("⬅️ Назад", prev));
    }
    if let Some(next) = next_cb {
        pag_row.push(InlineKeyboardButton::callback("➡️ Далее", next));
    }
    if !pag_row.is_empty() {
        rows.push(pag_row);
    }

    rows.push(vec![InlineKeyboardButton::callback("🏠 В хаб", home_cb)]);

    InlineKeyboardMarkup::new(rows)
}

pub fn search_menu_keyboard(session: &SearchSession) -> InlineKeyboardMarkup {
    let mut rows = vec![];

    // Query Row
    match &session.query {
        Some(q) => {
            rows.push(vec![
                InlineKeyboardButton::callback(format!("🔎 Запрос: \"{}\"", q), "sf_query"),
                InlineKeyboardButton::callback("✕", "sf_clear_query"),
            ]);
        }
        None => {
            rows.push(vec![InlineKeyboardButton::callback(
                "🔎 Запрос: все",
                "sf_query",
            )]);
        }
    }

    // Domain (Source) and Tag Row
    let mut row2 = vec![];
    match &session.domain {
        Some(d) => {
            row2.push(InlineKeyboardButton::callback(
                format!("🌐 Источник: {}", d),
                "sf_domain",
            ));
            row2.push(InlineKeyboardButton::callback("✕", "sf_clear_domain"));
        }
        None => {
            row2.push(InlineKeyboardButton::callback(
                "🌐 Источник: все",
                "sf_domain",
            ));
        }
    }
    match &session.tag {
        Some(t) => {
            row2.push(InlineKeyboardButton::callback(
                format!("🏷 Тег: #{}", t),
                "sf_tag",
            ));
            row2.push(InlineKeyboardButton::callback("✕", "sf_clear_tag"));
        }
        None => {
            row2.push(InlineKeyboardButton::callback("🏷 Тег: все", "sf_tag"));
        }
    }
    rows.push(row2);

    // Status and Rating Row
    let mut row3 = vec![];
    match &session.status {
        Some(s) => {
            let label = if s == "read" {
                "✅ прочитанные"
            } else {
                "📖 непрочитанные"
            };
            row3.push(InlineKeyboardButton::callback(
                format!("Статус: {}", label),
                "sf_status",
            ));
            row3.push(InlineKeyboardButton::callback("✕", "sf_clear_status"));
        }
        None => {
            row3.push(InlineKeyboardButton::callback("Статус: все", "sf_status"));
        }
    }
    match &session.rating {
        Some(r) => {
            row3.push(InlineKeyboardButton::callback(
                format!("⭐ Оценка: {}", r),
                "sf_rating",
            ));
            row3.push(InlineKeyboardButton::callback("✕", "sf_clear_rating"));
        }
        None => {
            if session.no_rating {
                row3.push(InlineKeyboardButton::callback(
                    "⭐ Оценка: без оценки",
                    "sf_rating",
                ));
                row3.push(InlineKeyboardButton::callback("✕", "sf_clear_rating"));
            } else {
                row3.push(InlineKeyboardButton::callback(
                    "⭐ Оценка: любая",
                    "sf_rating",
                ));
            }
        }
    }
    rows.push(row3);

    // Date Row
    match &session.date_added {
        Some(d) => {
            let label = if d == "today" {
                "за сегодня"
            } else if d == "week" {
                "за неделю"
            } else {
                "за месяц"
            };
            rows.push(vec![
                InlineKeyboardButton::callback(format!("📅 Дата: {}", label), "sf_date"),
                InlineKeyboardButton::callback("✕", "sf_clear_date"),
            ]);
        }
        None => {
            rows.push(vec![InlineKeyboardButton::callback(
                "📅 Дата: всё время",
                "sf_date",
            )]);
        }
    }

    // Action buttons
    rows.push(vec![
        InlineKeyboardButton::callback("❌ Сбросить всё", "sf_reset"),
        InlineKeyboardButton::callback("🚀 Искать", "sf_run:0"),
    ]);

    rows.push(vec![InlineKeyboardButton::callback("🏠 В хаб", "hub")]);

    InlineKeyboardMarkup::new(rows)
}

pub fn search_status_select_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📖 Только непрочитанные", "sfs_unread"),
            InlineKeyboardButton::callback("✅ Только прочитанные", "sfs_read"),
        ],
        vec![InlineKeyboardButton::callback("📝 Любой статус", "sfs_any")],
        vec![InlineKeyboardButton::callback(
            "🔙 Назад к поиску",
            "search",
        )],
    ])
}

pub fn search_rating_select_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("⭐ 1", "sfr_1"),
            InlineKeyboardButton::callback("⭐ 2", "sfr_2"),
            InlineKeyboardButton::callback("⭐ 3", "sfr_3"),
            InlineKeyboardButton::callback("⭐ 4", "sfr_4"),
            InlineKeyboardButton::callback("⭐ 5", "sfr_5"),
        ],
        vec![
            InlineKeyboardButton::callback("❌ Без оценки", "sfr_none"),
            InlineKeyboardButton::callback("📝 Любая оценка", "sfr_any"),
        ],
        vec![InlineKeyboardButton::callback(
            "🔙 Назад к поиску",
            "search",
        )],
    ])
}

pub fn search_date_select_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📅 Сегодня", "sfd_today"),
            InlineKeyboardButton::callback("📅 За неделю", "sfd_week"),
            InlineKeyboardButton::callback("📅 За месяц", "sfd_month"),
        ],
        vec![InlineKeyboardButton::callback("📅 За всё время", "sfd_any")],
        vec![InlineKeyboardButton::callback(
            "🔙 Назад к поиску",
            "search",
        )],
    ])
}

pub fn stats_menu_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📊 Обзор", "stats:overview"),
            InlineKeyboardButton::callback("🌐 Источники", "stats:sources"),
        ],
        vec![
            InlineKeyboardButton::callback("🏷 Теги", "stats:tags"),
            InlineKeyboardButton::callback("⭐ Оценки", "stats:ratings"),
        ],
        vec![InlineKeyboardButton::callback(
            "📅 Динамика",
            "stats:dynamics",
        )],
        vec![InlineKeyboardButton::callback("🏠 В хаб", "hub")],
    ])
}

pub fn tags_list_keyboard(tags: &[TagStat], page: i64, total_pages: i64) -> InlineKeyboardMarkup {
    let mut rows = vec![];

    // Group tags in rows of 2
    for chunk in tags.chunks(2) {
        let mut row = vec![];
        for t in chunk {
            row.push(InlineKeyboardButton::callback(
                format!("{} ({})", t.tag, t.count),
                format!("stag:{}:0", t.tag),
            ));
        }
        rows.push(row);
    }

    // Add pagination row if needed
    let mut pag_row = vec![];
    if page > 0 {
        pag_row.push(InlineKeyboardButton::callback(
            "⬅️ Назад",
            format!("tags_list:{}", page - 1),
        ));
    }
    if page + 1 < total_pages {
        pag_row.push(InlineKeyboardButton::callback(
            "➡️ Далее",
            format!("tags_list:{}", page + 1),
        ));
    }
    if !pag_row.is_empty() {
        rows.push(pag_row);
    }

    rows.push(vec![InlineKeyboardButton::callback("🏠 В хаб", "hub")]);

    InlineKeyboardMarkup::new(rows)
}

pub fn settings_keyboard(current_format: &str) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback(
                if current_format == "markdown" {
                    "✅ Markdown"
                } else {
                    "Markdown"
                },
                "set_fmt:markdown",
            ),
            InlineKeyboardButton::callback(
                if current_format == "html" {
                    "✅ HTML"
                } else {
                    "HTML"
                },
                "set_fmt:html",
            ),
            InlineKeyboardButton::callback(
                if current_format == "epub" {
                    "✅ EPUB"
                } else {
                    "EPUB"
                },
                "set_fmt:epub",
            ),
        ],
        vec![InlineKeyboardButton::callback(
            "⚠️ Сбросить библиотеку",
            "reset_lib_prompt",
        )],
        vec![InlineKeyboardButton::callback("🏠 В хаб", "hub")],
    ])
}

pub fn reset_lib_confirm_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("⚠️ Подтвердить полный сброс", "reset_lib_confirm"),
        InlineKeyboardButton::callback("❌ Отмена", "settings"),
    ]])
}

pub fn back_to_article_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![InlineKeyboardButton::callback(
        "🔙 Назад к материалу",
        format!("art:{}", art_id),
    )]])
}

pub fn back_to_hub_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![InlineKeyboardButton::callback(
        "🏠 В хаб",
        "hub",
    )]])
}

pub fn pending_input_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    let mut rows = vec![];
    if art_id > 0 {
        rows.push(vec![
            InlineKeyboardButton::callback("❌ Отмена", "hub"),
            InlineKeyboardButton::callback("🔙 Назад к материалу", format!("art:{}", art_id)),
        ]);
    } else {
        rows.push(vec![InlineKeyboardButton::callback("❌ Отмена", "hub")]);
    }
    InlineKeyboardMarkup::new(rows)
}

pub fn import_results_keyboard(has_errors: bool) -> InlineKeyboardMarkup {
    let mut row = vec![InlineKeyboardButton::callback(
        "Открыть последние",
        "open_last_imported",
    )];
    if has_errors {
        row.push(InlineKeyboardButton::callback(
            "Показать ошибки",
            "show_import_errors",
        ));
    }
    InlineKeyboardMarkup::new(vec![
        row,
        vec![InlineKeyboardButton::callback("🏠 В хаб", "hub")],
    ])
}
