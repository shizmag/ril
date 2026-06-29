mod common;

use common::{make_message, make_mock_state, setup_bot, MockTelegramServer};
use ril_daemon::telegram::handlers::handle_message;
use ril_daemon::telegram::helpers::normalize_tags;
use ril_daemon::telegram::state::PendingState;

#[test]
fn test_tag_normalization() {
    // Normalization: trim, lowercase, ignore empty, deduplicate
    assert_eq!(
        normalize_tags("Rust, backend, RUST"),
        vec!["rust", "backend"]
    );
    assert_eq!(normalize_tags("  Tag1 , , Tag2  "), vec!["tag1", "tag2"]);
    assert_eq!(normalize_tags(""), Vec::<String>::new());
    assert_eq!(normalize_tags(" , , "), Vec::<String>::new());
}

#[tokio::test]
async fn test_add_tags_flow() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // 1. Manually set user state to WaitingForTag for article 1
    state
        .set_pending_state(
            teloxide::types::UserId(123),
            PendingState::WaitingForTag { article_id: 1 },
        )
        .await;

    // 2. User sends tags "Rust, backend"
    let msg = make_message("Rust, backend", 123, 456);
    let res = handle_message(bot, msg, state.clone()).await;
    assert!(res.is_ok());

    // Verify state is cleared
    let pending = state.get_pending_state(teloxide::types::UserId(123)).await;
    assert!(matches!(pending, PendingState::None));

    // Verify tags were added to article 1 in mock database
    let article_content = state.bridge.get_article_content(1).await.unwrap();
    assert_eq!(article_content.article.tags, vec!["rust", "backend"]);

    let reqs = server.requests.lock().unwrap();
    let text_exists = |pat: &str| {
        reqs.iter().any(|r| {
            r.body["text"]
                .as_str()
                .or_else(|| r.body["caption"].as_str())
                .map(|t| t.contains(pat))
                .unwrap_or(false)
        })
    };
    assert!(text_exists("#rust"));
    assert!(text_exists("#backend"));
}

#[tokio::test]
async fn test_remove_tag_via_bridge() {
    let state = make_mock_state();

    // Add tags first
    state
        .bridge
        .add_tags(1, vec!["rust".to_string(), "backend".to_string()])
        .await
        .unwrap();

    // Remove one tag
    let success = state.bridge.remove_tag(1, "rust").await.unwrap();
    assert!(success);

    // Verify only backend tag remains
    let article = state.bridge.get_article_content(1).await.unwrap();
    assert_eq!(article.article.tags, vec!["backend"]);
}
