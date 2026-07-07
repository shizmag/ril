use crate::domain::{ArticleSummary, TagStat};
use crate::telegram::state::SearchSession;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup};

pub fn hub_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![InlineKeyboardButton::callback(
            "📚 All articles",
            "list:all:0",
        )],
        vec![
            InlineKeyboardButton::callback("📖 Unread", "list:unread:0"),
            InlineKeyboardButton::callback("✅ Read", "list:read:0"),
        ],
        vec![
            InlineKeyboardButton::callback("🔎 Search", "search"),
            InlineKeyboardButton::callback("🏷 Tags", "tags_list:0"),
        ],
        vec![
            InlineKeyboardButton::callback("⭐ Ratings", "ratings_list"),
            InlineKeyboardButton::callback("📊 Statistics", "stats:overview"),
        ],
        vec![InlineKeyboardButton::callback("⚙️ Settings", "settings")],
    ])
}

pub fn article_card_keyboard(art_id: i64, status: &str, url: &str) -> InlineKeyboardMarkup {
    let status_btn = if status == "read" {
        InlineKeyboardButton::callback("📖 Mark as unread", format!("unread:{}", art_id))
    } else {
        InlineKeyboardButton::callback("✅ Mark as read", format!("read:{}", art_id))
    };

    let original_url = url
        .parse()
        .unwrap_or_else(|_| "https://google.com".parse().unwrap());

    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📥 Download", format!("get_file:{}", art_id)),
            InlineKeyboardButton::url("📄 Read", original_url),
        ],
        vec![status_btn],
        vec![
            InlineKeyboardButton::callback("🏷 Tags", format!("art_tags:{}", art_id)),
            InlineKeyboardButton::callback("⭐ Rating", format!("art_rate:{}", art_id)),
            InlineKeyboardButton::callback("💬 Comment", format!("art_comm:{}", art_id)),
        ],
        vec![
            InlineKeyboardButton::callback("🗑 Delete", format!("art_del:{}", art_id)),
            InlineKeyboardButton::callback("🏠 To hub", "hub"),
        ],
    ])
}

pub fn document_keyboard(art_id: i64, status: &str) -> InlineKeyboardMarkup {
    let status_btn = if status == "read" {
        InlineKeyboardButton::callback("📖 To unread", format!("toggle_doc:{}", art_id))
    } else {
        InlineKeyboardButton::callback("✅ Read", format!("toggle_doc:{}", art_id))
    };

    InlineKeyboardMarkup::new(vec![
        vec![
            status_btn,
            InlineKeyboardButton::callback("🗑 Delete", format!("del_doc:{}", art_id)),
        ],
        vec![
            InlineKeyboardButton::callback("⭐ 1", format!("rate_doc:{}:1", art_id)),
            InlineKeyboardButton::callback("⭐ 2", format!("rate_doc:{}:2", art_id)),
            InlineKeyboardButton::callback("⭐ 3", format!("rate_doc:{}:3", art_id)),
            InlineKeyboardButton::callback("⭐ 4", format!("rate_doc:{}:4", art_id)),
            InlineKeyboardButton::callback("⭐ 5", format!("rate_doc:{}:5", art_id)),
        ],
        vec![InlineKeyboardButton::callback(
            "🔙 Back to article",
            format!("art:{}", art_id),
        )],
    ])
}

pub fn delete_confirm_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("⚠️ Yes, delete", format!("art_del_conf:{}", art_id)),
        InlineKeyboardButton::callback("❌ Cancel", format!("art:{}", art_id)),
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
            "❌ Reset rating",
            format!("rate_set:{}:0", art_id),
        )],
        vec![InlineKeyboardButton::callback(
            "🔙 Back",
            format!("art:{}", art_id),
        )],
    ])
}

pub fn comment_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📝 Write/Edit", format!("comm_set:{}", art_id)),
            InlineKeyboardButton::callback("🗑 Delete", format!("comm_del:{}", art_id)),
        ],
        vec![InlineKeyboardButton::callback(
            "🔙 Back",
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
        row.push(InlineKeyboardButton::callback("⬅️ Back", prev));
    }
    if let Some(next) = next_cb {
        row.push(InlineKeyboardButton::callback("➡️ Next", next));
    }

    InlineKeyboardMarkup::new(vec![
        row,
        vec![InlineKeyboardButton::callback("🏠 To hub", home_cb)],
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
        pag_row.push(InlineKeyboardButton::callback("⬅️ Back", prev));
    }
    if let Some(next) = next_cb {
        pag_row.push(InlineKeyboardButton::callback("➡️ Next", next));
    }
    if !pag_row.is_empty() {
        rows.push(pag_row);
    }

    rows.push(vec![InlineKeyboardButton::callback("🏠 To hub", home_cb)]);

    InlineKeyboardMarkup::new(rows)
}

pub fn search_menu_keyboard(session: &SearchSession) -> InlineKeyboardMarkup {
    let mut rows = vec![];

    // Query Row
    match &session.query {
        Some(q) => {
            rows.push(vec![
                InlineKeyboardButton::callback(format!("🔎 Query: \"{}\"", q), "sf_query"),
                InlineKeyboardButton::callback("✕", "sf_clear_query"),
            ]);
        }
        None => {
            rows.push(vec![InlineKeyboardButton::callback(
                "🔎 Query: all",
                "sf_query",
            )]);
        }
    }

    // Domain (Source) and Tag Row
    let mut row2 = vec![];
    match &session.domain {
        Some(d) => {
            row2.push(InlineKeyboardButton::callback(
                format!("🌐 Source: {}", d),
                "sf_domain",
            ));
            row2.push(InlineKeyboardButton::callback("✕", "sf_clear_domain"));
        }
        None => {
            row2.push(InlineKeyboardButton::callback(
                "🌐 Source: all",
                "sf_domain",
            ));
        }
    }
    match &session.tag {
        Some(t) => {
            row2.push(InlineKeyboardButton::callback(
                format!("🏷 Tag: #{}", t),
                "sf_tag",
            ));
            row2.push(InlineKeyboardButton::callback("✕", "sf_clear_tag"));
        }
        None => {
            row2.push(InlineKeyboardButton::callback("🏷 Tag: all", "sf_tag"));
        }
    }
    rows.push(row2);

    // Status and Rating Row
    let mut row3 = vec![];
    match &session.status {
        Some(s) => {
            let label = if s == "read" {
                "✅ read"
            } else {
                "📖 unread"
            };
            row3.push(InlineKeyboardButton::callback(
                format!("Status: {}", label),
                "sf_status",
            ));
            row3.push(InlineKeyboardButton::callback("✕", "sf_clear_status"));
        }
        None => {
            row3.push(InlineKeyboardButton::callback("Status: all", "sf_status"));
        }
    }
    match &session.rating {
        Some(r) => {
            row3.push(InlineKeyboardButton::callback(
                format!("⭐ Rating: {}", r),
                "sf_rating",
            ));
            row3.push(InlineKeyboardButton::callback("✕", "sf_clear_rating"));
        }
        None => {
            if session.no_rating {
                row3.push(InlineKeyboardButton::callback(
                    "⭐ Rating: no rating",
                    "sf_rating",
                ));
                row3.push(InlineKeyboardButton::callback("✕", "sf_clear_rating"));
            } else {
                row3.push(InlineKeyboardButton::callback(
                    "⭐ Rating: any",
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
                "today"
            } else if d == "week" {
                "this week"
            } else {
                "this month"
            };
            rows.push(vec![
                InlineKeyboardButton::callback(format!("📅 Date: {}", label), "sf_date"),
                InlineKeyboardButton::callback("✕", "sf_clear_date"),
            ]);
        }
        None => {
            rows.push(vec![InlineKeyboardButton::callback(
                "📅 Date: all time",
                "sf_date",
            )]);
        }
    }

    // Action buttons
    rows.push(vec![
        InlineKeyboardButton::callback("❌ Reset all", "sf_reset"),
        InlineKeyboardButton::callback("🚀 Search", "sf_run:0"),
    ]);

    rows.push(vec![InlineKeyboardButton::callback("🏠 To hub", "hub")]);

    InlineKeyboardMarkup::new(rows)
}

pub fn search_status_select_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📖 Only unread", "sfs_unread"),
            InlineKeyboardButton::callback("✅ Only read", "sfs_read"),
        ],
        vec![InlineKeyboardButton::callback("📝 Any status", "sfs_any")],
        vec![InlineKeyboardButton::callback(
            "🔙 Back to search",
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
            InlineKeyboardButton::callback("❌ Without rating", "sfr_none"),
            InlineKeyboardButton::callback("📝 Any rating", "sfr_any"),
        ],
        vec![InlineKeyboardButton::callback(
            "🔙 Back to search",
            "search",
        )],
    ])
}

pub fn search_date_select_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📅 Today", "sfd_today"),
            InlineKeyboardButton::callback("📅 This week", "sfd_week"),
            InlineKeyboardButton::callback("📅 This month", "sfd_month"),
        ],
        vec![InlineKeyboardButton::callback("📅 All time", "sfd_any")],
        vec![InlineKeyboardButton::callback(
            "🔙 Back to search",
            "search",
        )],
    ])
}

pub fn stats_menu_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📊 Overview", "stats:overview"),
            InlineKeyboardButton::callback("🌐 Sources", "stats:sources"),
        ],
        vec![
            InlineKeyboardButton::callback("🏷 Tags", "stats:tags"),
            InlineKeyboardButton::callback("⭐ Ratings", "stats:ratings"),
        ],
        vec![InlineKeyboardButton::callback(
            "📅 Dynamics",
            "stats:dynamics",
        )],
        vec![InlineKeyboardButton::callback("🏠 To hub", "hub")],
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
            "⬅️ Back",
            format!("tags_list:{}", page - 1),
        ));
    }
    if page + 1 < total_pages {
        pag_row.push(InlineKeyboardButton::callback(
            "➡️ Next",
            format!("tags_list:{}", page + 1),
        ));
    }
    if !pag_row.is_empty() {
        rows.push(pag_row);
    }

    rows.push(vec![InlineKeyboardButton::callback("🏠 To hub", "hub")]);

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
            "⚠️ Reset library",
            "reset_lib_prompt",
        )],
        vec![InlineKeyboardButton::callback("🏠 To hub", "hub")],
    ])
}

pub fn reset_lib_confirm_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("⚠️ Confirm complete reset", "reset_lib_confirm"),
        InlineKeyboardButton::callback("❌ Cancel", "settings"),
    ]])
}

pub fn back_to_article_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![InlineKeyboardButton::callback(
        "🔙 Back to article",
        format!("art:{}", art_id),
    )]])
}

pub fn back_to_hub_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![InlineKeyboardButton::callback(
        "🏠 To hub",
        "hub",
    )]])
}

pub fn pending_input_keyboard(art_id: i64) -> InlineKeyboardMarkup {
    let mut rows = vec![];
    if art_id > 0 {
        rows.push(vec![
            InlineKeyboardButton::callback("❌ Cancel", "hub"),
            InlineKeyboardButton::callback("🔙 Back to article", format!("art:{}", art_id)),
        ]);
    } else {
        rows.push(vec![InlineKeyboardButton::callback("❌ Cancel", "hub")]);
    }
    InlineKeyboardMarkup::new(rows)
}

pub fn import_results_keyboard(has_errors: bool) -> InlineKeyboardMarkup {
    let mut row = vec![InlineKeyboardButton::callback(
        "Open recent",
        "open_last_imported",
    )];
    if has_errors {
        row.push(InlineKeyboardButton::callback(
            "Show errors",
            "show_import_errors",
        ));
    }
    InlineKeyboardMarkup::new(vec![
        row,
        vec![InlineKeyboardButton::callback("🏠 To hub", "hub")],
    ])
}
