mod common;

use common::{make_message, make_mock_state, setup_bot, MockTelegramServer};
use ril_daemon::telegram::handlers::handle_message;
use ril_daemon::telegram::helpers::validate_comment;
use ril_daemon::telegram::state::PendingState;

#[test]
fn test_comment_validation() {
    assert_eq!(validate_comment("Hello!").unwrap(), "Hello!".to_string());
    assert_eq!(
        validate_comment("   Trim me   ").unwrap(),
        "Trim me".to_string()
    );

    assert!(validate_comment("").is_err());
    assert!(validate_comment("   ").is_err());

    let long_str = "a".repeat(1001);
    assert!(validate_comment(&long_str).is_err());
}

#[tokio::test]
async fn test_comments_flow() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // 1. Put user into WaitingForComment state
    state
        .set_pending_state(
            teloxide::types::UserId(123),
            PendingState::WaitingForComment { article_id: 1 },
        )
        .await;

    // 2. User sends comment text
    let msg = make_message("This is an awesome article! 🚀", 123, 456);
    let res = handle_message(bot, msg, state.clone()).await;
    assert!(res.is_ok());

    // State is cleared
    let pending = state.get_pending_state(teloxide::types::UserId(123)).await;
    assert!(matches!(pending, PendingState::None));

    // Verify comment is saved
    let art = state.bridge.get_article_content(1).await.unwrap().article;
    assert_eq!(
        art.comment,
        Some("This is an awesome article! 🚀".to_string())
    );

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
    assert!(text_exists("This is an awesome article! 🚀"));
}

#[tokio::test]
async fn test_cancel_comment_flow() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // 1. Put user into WaitingForComment state
    state
        .set_pending_state(
            teloxide::types::UserId(123),
            PendingState::WaitingForComment { article_id: 1 },
        )
        .await;

    // 2. User sends /cancel
    let msg = make_message("/cancel", 123, 456);
    let res = handle_message(bot, msg, state.clone()).await;
    assert!(res.is_ok());

    // Verify state is cleared
    let pending = state.get_pending_state(teloxide::types::UserId(123)).await;
    assert!(matches!(pending, PendingState::None));

    // Verify comment remains empty
    let art = state.bridge.get_article_content(1).await.unwrap().article;
    assert_eq!(art.comment, None);

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
    assert!(text_exists("Initial Mock Article"));
}
