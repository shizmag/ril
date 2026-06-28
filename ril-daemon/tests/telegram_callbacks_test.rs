mod common;

use common::{
    make_callback_query, make_mock_state, make_mock_state_with_allowed_users, setup_bot,
    MockTelegramServer,
};
use ril_daemon::telegram::callback_data::CallbackAction;
use ril_daemon::telegram::callbacks::handle_callback_query;
use std::str::FromStr;

#[test]
fn test_callback_parsing_success() {
    assert_eq!(
        CallbackAction::from_str("hub").unwrap(),
        CallbackAction::Hub
    );
    assert_eq!(
        CallbackAction::from_str("list:all:2").unwrap(),
        CallbackAction::List {
            status: "all".to_string(),
            page: 2
        }
    );
    assert_eq!(
        CallbackAction::from_str("art:42").unwrap(),
        CallbackAction::Article { id: 42 }
    );
    assert_eq!(
        CallbackAction::from_str("read:42").unwrap(),
        CallbackAction::MarkRead { id: 42 }
    );
    assert_eq!(
        CallbackAction::from_str("rate_set:123:5").unwrap(),
        CallbackAction::RateSet { id: 123, val: 5 }
    );
    assert_eq!(
        CallbackAction::from_str("tag_rem:99:rust").unwrap(),
        CallbackAction::TagRemove {
            id: 99,
            tag: "rust".to_string()
        }
    );
    assert_eq!(
        CallbackAction::from_str("sft_select:habr").unwrap(),
        CallbackAction::SearchFilterTagSelect {
            tag: "habr".to_string()
        }
    );
    assert_eq!(
        CallbackAction::from_str("sfs_read").unwrap(),
        CallbackAction::SearchFilterStatusSelect {
            status: "read".to_string()
        }
    );
}

#[test]
fn test_callback_parsing_invalid() {
    // Should not panic on invalid strings, just return Err
    assert!(CallbackAction::from_str("").is_err());
    assert!(CallbackAction::from_str("invalid_command").is_err());
    assert!(CallbackAction::from_str("list:all:abc").is_err());
    assert!(CallbackAction::from_str("art:abc").is_err());
    assert!(CallbackAction::from_str("rate_set:123").is_err());
    assert!(CallbackAction::from_str("tag_rem:abc:rust").is_err());
}

#[test]
fn test_callback_length_validation() {
    let safe_action = CallbackAction::TagRemove {
        id: 42,
        tag: "short".to_string(),
    };
    assert!(safe_action.validate_length().is_ok());

    // Long tag that makes the callback string exceed 64 bytes
    let long_tag = "a".repeat(60);
    let unsafe_action = CallbackAction::TagRemove {
        id: 42,
        tag: long_tag,
    };
    assert!(unsafe_action.validate_length().is_err());
}

#[tokio::test]
async fn test_callback_routing_hub() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    let query = make_callback_query("hub", 123, 456);
    let res = handle_callback_query(bot, query, state).await;
    let reqs = server.requests.lock().unwrap();
    println!("Requests received: {:?}", *reqs);
    assert!(res.is_ok(), "Error was: {:?}", res);
    // It should call answerCallbackQuery and then editMessageText (to update the message UI)
    assert!(reqs
        .iter()
        .any(|r| r.path.to_lowercase().contains("answercallbackquery")));
    assert!(reqs
        .iter()
        .any(|r| r.path.to_lowercase().contains("editmessagetext")));
}

#[tokio::test]
async fn test_callback_routing_access_denied() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    // User 123 is allowed, but callback query comes from user 999
    let state = make_mock_state_with_allowed_users(vec![123]);

    let query = make_callback_query("hub", 999, 456);
    let res = handle_callback_query(bot, query, state).await;
    let reqs = server.requests.lock().unwrap();
    println!("Requests received in access_denied: {:?}", *reqs);
    assert!(res.is_ok(), "Error was: {:?}", res);

    // It should answer the callback query with "Доступ запрещен" text and NOT edit the message UI
    let answer_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("answercallbackquery"))
        .unwrap();
    assert!(answer_req.body["text"]
        .as_str()
        .unwrap()
        .contains("Доступ запрещен"));
    assert!(!reqs.iter().any(|r| r.path.contains("editMessageText")));
}
