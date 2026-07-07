use crate::config::Config;
use crate::domain::{ArticleSummary, SaveFormat};
use crate::python_bridge::PythonBridge;

#[test]
fn test_config_parsing() {
    // Sequentially test default and custom env parsing to prevent parallel races
    std::env::remove_var("RIL_LIBRARY_DIR");
    std::env::remove_var("RIL_DB_PATH");
    std::env::remove_var("TELEGRAM_TOKEN");
    std::env::remove_var("ALLOWED_TELEGRAM_USERS");
    std::env::remove_var("RIL_DEFAULT_FORMAT");
    std::env::remove_var("RIL_PYTHON_CMD");
    std::env::remove_var("RIL_PYTHON_BIN");
    std::env::remove_var("RIL_PYTHON_WORKDIR");

    let cfg = Config::load_from_env_only().unwrap();
    assert_eq!(cfg.default_format, SaveFormat::Epub);
    assert_eq!(cfg.bridge_timeout_seconds, 600);
    assert!(cfg.telegram_token.is_none());
    assert!(cfg.allowed_telegram_users.is_empty());

    std::env::set_var("TELEGRAM_TOKEN", "12345:abcde");
    std::env::set_var("ALLOWED_TELEGRAM_USERS", "123,456 , 789");
    std::env::set_var("RIL_DEFAULT_FORMAT", "html");
    std::env::set_var("RIL_BRIDGE_TIMEOUT_SECONDS", "15");

    let cfg = Config::load_from_env_only().unwrap();
    assert_eq!(cfg.telegram_token.unwrap(), "12345:abcde");
    assert_eq!(cfg.allowed_telegram_users, vec![123, 456, 789]);
    assert_eq!(cfg.default_format, SaveFormat::Html);
    assert_eq!(cfg.bridge_timeout_seconds, 15);
}

#[test]
fn test_serde_models() {
    let raw_summary = r#"{
        "id": 42,
        "url": "http://test.com",
        "title": "Test Title",
        "added_at": "2026-06-28T12:00:00",
        "status": "unread",
        "file_path": "/path/to/file.md",
        "word_count": 100,
        "char_count": 600
    }"#;
    let summary: ArticleSummary = serde_json::from_str(raw_summary).unwrap();
    assert_eq!(summary.id, 42);
    assert_eq!(summary.url, "http://test.com");
    assert_eq!(summary.status, "unread");
}

#[tokio::test]
async fn test_mock_bridge_get_stats() {
    let bridge = PythonBridge::new_mock();
    let stats = bridge.get_reading_stats().await.unwrap();
    assert_eq!(stats.total_articles, 1);
    assert_eq!(stats.unread_articles, 1);
}

#[tokio::test]
async fn test_mock_bridge_lifecycle() {
    let bridge = PythonBridge::new_mock();

    // Add article
    let res = bridge
        .process_url("http://example.com/test", SaveFormat::Html, false)
        .await
        .unwrap();
    assert_eq!(res.id, 2);
    assert_eq!(res.url, "http://example.com/test");

    // Adding duplicate without force should fail
    let dup_res = bridge
        .process_url("http://example.com/test", SaveFormat::Html, false)
        .await;
    assert!(dup_res.is_err());
    let err_str = dup_res.unwrap_err().to_string();
    assert!(err_str.contains("URL already exists in library"));

    // Adding duplicate with force should succeed
    let force_res = bridge
        .process_url("http://example.com/test", SaveFormat::Html, true)
        .await
        .unwrap();
    assert_eq!(force_res.id, 2);

    // Check stats
    let stats = bridge.get_reading_stats().await.unwrap();
    assert_eq!(stats.total_articles, 2);

    // List articles
    let list = bridge.list_articles(None, None).await.unwrap();
    assert_eq!(list.len(), 2);

    // Mark as read
    let success = bridge.mark_article_read(2).await.unwrap();
    assert!(success);

    // Check updated status
    let list_read = bridge.list_articles(Some("read"), None).await.unwrap();
    assert_eq!(list_read.len(), 1);
    assert_eq!(list_read[0].id, 2);

    // Delete article
    let deleted = bridge.delete_article(2).await.unwrap();
    assert!(deleted);

    let list_after = bridge.list_articles(None, None).await.unwrap();
    assert_eq!(list_after.len(), 1);
}

#[tokio::test]
#[ignore = "e2e: requires live Python subprocess and local absolute path. Run with: cargo test -- --ignored"]
async fn test_real_bridge_e2e() {
    let mut config = Config::load_from_env_only().unwrap();
    // Explicitly configure path to python workspace root for subprocess execution
    config.python_workdir = Some(std::path::PathBuf::from(
        std::env::var("RIL_PYTHON_WORKDIR")
            .unwrap_or_else(|_| "/Users/vladimirkasterin/python/ril".to_string()),
    ));

    let bridge = PythonBridge::new(config);
    let stats_res = bridge.get_reading_stats().await;

    match stats_res {
        Ok(stats) => {
            assert!(stats.total_articles >= 0);
            assert!(stats.total_words >= 0);
        }
        Err(e) => {
            panic!(
                "E2E integration test failed to call Python subprocess: {:?}",
                e
            );
        }
    }
}

#[test]
fn test_telegram_helpers() {
    let text = "Check out this: https://google.com and http://example.org/path?query=1";
    let urls = crate::telegram::extract_urls(text);
    assert_eq!(
        urls,
        vec!["https://google.com", "http://example.org/path?query=1"]
    );

    let text_no_url = "No url here, just text.";
    assert!(crate::telegram::extract_urls(text_no_url).is_empty());

    use teloxide::types::UserId;
    assert!(crate::telegram::is_allowed(UserId(123), &[]));
    assert!(crate::telegram::is_allowed(UserId(123), &[123, 456]));
    assert!(!crate::telegram::is_allowed(UserId(999), &[123, 456]));
}

#[test]
fn test_mcp_protocol_responses() {
    use crate::mcp::protocol::JsonRpcResponse;
    use serde_json::json;

    let success_resp = JsonRpcResponse::success(json!(1), json!({"result": "ok"}));
    assert_eq!(success_resp.jsonrpc, "2.0");
    assert_eq!(success_resp.id, json!(1));
    assert!(success_resp.result.is_some());
    assert!(success_resp.error.is_none());

    let error_resp = JsonRpcResponse::error(json!(2), -32601, "Method not found".to_string(), None);
    assert_eq!(error_resp.jsonrpc, "2.0");
    assert_eq!(error_resp.id, json!(2));
    assert!(error_resp.result.is_none());
    assert_eq!(error_resp.error.unwrap().code, -32601);
}

#[tokio::test]
async fn test_mcp_tool_calls_mock() {
    let bridge = PythonBridge::new_mock();

    // 1. process_url
    let args = serde_json::json!({
        "url": "https://rust-lang.org",
        "format": "markdown"
    });
    let res = crate::mcp::handle_tool_call(&bridge, "process_url", &args)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));
    let text = res["content"][0]["text"].as_str().unwrap();
    assert!(text.contains("rust-lang.org"));
    assert!(text.contains("Mock Article"));

    // 2. get_reading_stats
    let res = crate::mcp::handle_tool_call(&bridge, "get_reading_stats", &serde_json::Value::Null)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));
    let text = res["content"][0]["text"].as_str().unwrap();
    assert!(text.contains("total_articles"));

    // 3. list_articles
    let args = serde_json::json!({
        "status": "unread",
        "limit": 10
    });
    let res = crate::mcp::handle_tool_call(&bridge, "list_articles", &args)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));

    // 4. search_articles
    let args = serde_json::json!({
        "query": "rust"
    });
    let res = crate::mcp::handle_tool_call(&bridge, "search_articles", &args)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));

    // 5. mark_article_read & unread
    let args = serde_json::json!({ "article_id": 1 });
    let res = crate::mcp::handle_tool_call(&bridge, "mark_article_read", &args)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));

    let res = crate::mcp::handle_tool_call(&bridge, "mark_article_unread", &args)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));

    // 6. get_article_content
    let args = serde_json::json!({ "article_id": 1 });
    let res = crate::mcp::handle_tool_call(&bridge, "get_article_content", &args)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));

    // 7. delete_article
    let args = serde_json::json!({ "article_id": 1 });
    let res = crate::mcp::handle_tool_call(&bridge, "delete_article", &args)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));

    // 8. reset_library
    let res = crate::mcp::handle_tool_call(&bridge, "reset_library", &serde_json::Value::Null)
        .await
        .unwrap();
    assert_eq!(res["isError"].as_bool(), Some(false));
}

#[test]
fn test_detect_format_override() {
    use crate::domain::SaveFormat;
    use crate::telegram::detect_format_override;

    // Default formatting when no overrides
    assert_eq!(
        detect_format_override("https://example.com/some/path", SaveFormat::Markdown),
        SaveFormat::Markdown
    );
    assert_eq!(
        detect_format_override("Check this link: https://google.com", SaveFormat::Epub),
        SaveFormat::Epub
    );

    // Overrides
    assert_eq!(
        detect_format_override("https://example.com html", SaveFormat::Markdown),
        SaveFormat::Html
    );
    assert_eq!(
        detect_format_override("https://example.com/page epub", SaveFormat::Markdown),
        SaveFormat::Epub
    );
    assert_eq!(
        detect_format_override("https://example.com/page markdown", SaveFormat::Html),
        SaveFormat::Markdown
    );
    assert_eq!(
        detect_format_override("https://example.com/page md", SaveFormat::Html),
        SaveFormat::Markdown
    );

    // Mixed case overrides
    assert_eq!(
        detect_format_override("https://example.com HTML", SaveFormat::Markdown),
        SaveFormat::Html
    );
    assert_eq!(
        detect_format_override("https://example.com/page EPUB", SaveFormat::Markdown),
        SaveFormat::Epub
    );
    assert_eq!(
        detect_format_override("https://example.com/page Markdown", SaveFormat::Html),
        SaveFormat::Markdown
    );
}

#[test]
fn test_id_extraction() {
    let parse_id = |s: &str| -> Option<i64> {
        s.split_whitespace()
            .next()
            .unwrap_or("")
            .parse::<i64>()
            .ok()
    };

    assert_eq!(parse_id("5"), Some(5));
    assert_eq!(parse_id("5 "), Some(5));
    assert_eq!(parse_id(" 5"), Some(5));
    assert_eq!(parse_id("5 extra"), Some(5));
    assert_eq!(parse_id("5  extra_arg"), Some(5));
    assert_eq!(parse_id("abc"), None);
    assert_eq!(parse_id(""), None);
}
