mod common;

use common::{
    make_message, make_mock_state, make_mock_state_with_allowed_users, setup_bot,
    MockTelegramServer,
};
use ril_daemon::telegram::handlers::handle_message;
use teloxide::prelude::*;

#[tokio::test]
async fn test_cmd_start_help_hub() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // /start command
    let msg = make_message("/start", 123, 456);
    let res = handle_message(bot.clone(), msg, state.clone()).await;
    assert!(res.is_ok());

    let reqs = server.requests.lock().unwrap();
    // It should call sendMessage with the welcome message and hub keyboard
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    let text = send_req.body["text"].as_str().unwrap();
    assert!(text.contains("Read It Later Bot"));
    assert!(text.contains("Total articles:"));

    let markup = &send_req.body["reply_markup"]["inline_keyboard"];
    assert!(markup.is_array());
}

#[tokio::test]
async fn test_cmd_stats() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    let msg = make_message("/stats", 123, 456);
    let res = handle_message(bot, msg, state).await;
    assert!(res.is_ok());

    let reqs = server.requests.lock().unwrap();
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    let text = send_req.body["text"].as_str().unwrap();
    assert!(text.contains("General Library Statistics"));
}

#[tokio::test]
async fn test_cmd_list() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    let msg = make_message("/list", 123, 456);
    let res = handle_message(bot, msg, state).await;
    assert!(res.is_ok());

    let reqs = server.requests.lock().unwrap();
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    let text = send_req.body["text"].as_str().unwrap();
    assert!(text.contains("All library materials"));
}

#[tokio::test]
async fn test_cmd_search_empty_and_args() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // 1. Empty /search
    let msg = make_message("/search", 123, 456);
    let res = handle_message(bot.clone(), msg, state.clone()).await;
    assert!(res.is_ok());

    // 2. Search with query /search rust
    let msg_args = make_message("/search rust", 123, 456);
    let res_args = handle_message(bot, msg_args, state).await;
    assert!(res_args.is_ok());

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
    assert!(text_exists("Search Articles"));
    assert!(text_exists("Search results for"));
}

#[tokio::test]
async fn test_cmd_get_invalid_id() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    let msg = make_message("/get invalid_id", 123, 456);
    let res = handle_message(bot, msg, state).await;
    assert!(res.is_ok());

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
    assert!(text_exists("Specify a numeric ID"));
}

#[tokio::test]
async fn test_cmd_read_unread_delete() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // /read 1
    let msg = make_message("/read 1", 123, 456);
    let res = handle_message(bot.clone(), msg, state.clone()).await;
    assert!(res.is_ok());

    // /unread 1
    let msg2 = make_message("/unread 1", 123, 456);
    let res2 = handle_message(bot.clone(), msg2, state.clone()).await;
    assert!(res2.is_ok());

    // /delete 1
    let msg3 = make_message("/delete 1", 123, 456);
    let res3 = handle_message(bot, msg3, state).await;
    assert!(res3.is_ok());

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
    assert!(text_exists("Read"));
    assert!(text_exists("Unread"));
    assert!(text_exists("Are you sure you want to delete this article"));
}

#[tokio::test]
async fn test_cmd_format_and_change() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // Check default format
    let msg = make_message("/format", 123, 456);
    let res = handle_message(bot.clone(), msg, state.clone()).await;
    assert!(res.is_ok());

    // Change format to html
    let msg_change = make_message("/format html", 123, 456);
    let res_change = handle_message(bot, msg_change, state).await;
    assert!(res_change.is_ok());

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
    assert!(text_exists("Current download format"));
}

#[tokio::test]
async fn test_cmd_reset_flow() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // 1. Send /reset prompt
    let msg = make_message("/reset", 123, 456);
    let res = handle_message(bot.clone(), msg, state.clone()).await;
    assert!(res.is_ok());

    // Verify user has a pending reset
    {
        let pending = state.pending_resets.lock().await;
        assert!(pending.contains(&UserId(123)));
    }

    // 2. Confirm reset
    let msg_confirm = make_message("/confirmreset", 123, 456);
    let res_confirm = handle_message(bot, msg_confirm, state).await;
    assert!(res_confirm.is_ok());

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
    assert!(text_exists(
        "All files, articles, and databases will be deleted"
    ));
    assert!(text_exists("Library successfully cleared"));
}

#[tokio::test]
async fn test_cmd_reset_cancel() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // Send /reset prompt
    let msg = make_message("/reset", 123, 456);
    let _ = handle_message(bot.clone(), msg, state.clone()).await;

    // Send /cancel
    let msg_cancel = make_message("/cancel", 123, 456);
    let _ = handle_message(bot, msg_cancel, state.clone()).await;

    // Verify pending reset is cleared
    {
        let pending = state.pending_resets.lock().await;
        assert!(!pending.contains(&UserId(123)));
    }
}

#[tokio::test]
async fn test_allowed_users_authorization() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state_with_allowed_users(vec![123]);

    // Unauthorized user 999
    let msg = make_message("/start", 999, 456);
    let res = handle_message(bot, msg, state).await;
    assert!(res.is_ok());

    let reqs = server.requests.lock().unwrap();
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    assert!(send_req.body["text"]
        .as_str()
        .unwrap()
        .contains("Access denied"));
}
