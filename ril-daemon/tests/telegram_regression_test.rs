mod common;

use common::{make_message, make_mock_state, setup_bot, MockTelegramServer};
use ril_daemon::telegram::handlers::handle_message;

#[tokio::test]
async fn test_regression_url_import() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // Sending a message containing a URL should trigger handle_urls and import the article
    let msg = make_message(
        "Hey, check this article: https://news.ycombinator.com/item?id=12345 html",
        123,
        456,
    );
    let res = handle_message(bot, msg, state.clone()).await;
    assert!(res.is_ok());

    // Verify it is imported in the mock bridge
    let errors = state.get_last_errors(teloxide::types::UserId(123)).await;
    println!("IMPORT ERRORS: {:?}", errors);
    let list = state.bridge.list_articles(None, None).await.unwrap();
    println!("ARTICLES LIST: {:?}", list);
    assert_eq!(list.len(), 2); // Initial mock article + new imported article

    let imported = &list[1];
    assert_eq!(imported.url, "https://news.ycombinator.com/item?id=12345");
    assert_eq!(imported.status, "unread");

    // The bot edits the temporary "importing" message into the final article card
    let reqs = server.requests.lock().unwrap();

    // First it sends "Импорт 1 ссылок..."
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    assert!(send_req.body["text"]
        .as_str()
        .unwrap()
        .contains("Импорт 1 ссылок"));

    // Find the SendMessage request (there is only 1 now)
    let send_message_reqs: Vec<_> = reqs
        .iter()
        .filter(|r| r.path.to_lowercase().contains("sendmessage"))
        .collect();
    assert_eq!(send_message_reqs.len(), 1);

    // Find the EditMessageText request
    let edit_message_reqs: Vec<_> = reqs
        .iter()
        .filter(|r| r.path.to_lowercase().contains("editmessagetext"))
        .collect();
    assert_eq!(edit_message_reqs.len(), 1);
    let final_req = edit_message_reqs[0];
    let text = final_req.body["text"].as_str().unwrap();
    assert!(text.contains("Импорт завершен"));
    assert!(text.contains("news.ycombinator.com"));
}

#[tokio::test]
async fn test_regression_get_command() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // Get command should try to get the article, and since file doesn't exist, show error screen
    let msg = make_message("/get 1", 123, 456);
    let res = handle_message(bot, msg, state).await;
    assert!(res.is_ok());

    let reqs = server.requests.lock().unwrap();
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    let text = send_req.body["text"].as_str().unwrap();
    assert!(text.contains("Файл не найден на диске"));
}

#[tokio::test]
async fn test_regression_commands_are_backwards_compatible() {
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    // Ensure `/read <id>` marks as read
    let res_read = handle_message(
        bot.clone(),
        make_message("/read 1", 123, 456),
        state.clone(),
    )
    .await;
    assert!(res_read.is_ok());
    assert_eq!(
        state
            .bridge
            .get_article_content(1)
            .await
            .unwrap()
            .article
            .status,
        "read"
    );

    // Ensure `/unread <id>` marks back as unread
    let res_unread = handle_message(
        bot.clone(),
        make_message("/unread 1", 123, 456),
        state.clone(),
    )
    .await;
    assert!(res_unread.is_ok());
    assert_eq!(
        state
            .bridge
            .get_article_content(1)
            .await
            .unwrap()
            .article
            .status,
        "unread"
    );

    // Ensure `/delete <id>` shows confirmation, then callback removes the article
    let res_del = handle_message(bot.clone(), make_message("/delete 1", 123, 456), state.clone()).await;
    assert!(res_del.is_ok());

    // Import CallbackAction to use handle_callback_query
    use ril_daemon::telegram::callbacks::handle_callback_query;
    let query = common::make_callback_query("art_del_conf:1", 123, 456);
    let res_cb = handle_callback_query(bot, query, state.clone()).await;
    assert!(res_cb.is_ok());

    assert!(state
        .bridge
        .list_articles(None, None)
        .await
        .unwrap()
        .is_empty());
}
