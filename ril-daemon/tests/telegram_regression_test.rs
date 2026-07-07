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

    // First it sends "Importing 1 links..."
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    assert!(send_req.body["text"]
        .as_str()
        .unwrap()
        .contains("Importing 1 links"));

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
    assert!(text.contains("Import finished"));
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
    for r in reqs.iter() {
        println!("GET REQ: {}, BODY: {:?}", r.path, r.body);
    }
    let send_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("sendmessage"))
        .unwrap();
    let text = send_req.body["text"].as_str().unwrap();
    assert!(text.contains("File not found on disk"));
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
    let res_del = handle_message(
        bot.clone(),
        make_message("/delete 1", 123, 456),
        state.clone(),
    )
    .await;
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

#[tokio::test]
async fn test_two_screens_flow() {
    println!("STARTING test_two_screens_flow");
    let server = MockTelegramServer::start().await;
    let bot = setup_bot(server.port);
    let state = make_mock_state();

    println!("PROCESSING URL...");
    // Process a URL to insert an article with ID 2 into the mock bridge
    let _ = state
        .bridge
        .process_url(
            "https://example.com/unique-test-url",
            ril_daemon::domain::SaveFormat::Epub,
            false,
            false,
            false,
        )
        .await
        .unwrap();

    // Create the expected mock file for ID 2 in std::env::temp_dir()
    let mock_file_path = std::env::temp_dir().join("2.epub");
    let _ = std::fs::File::create(&mock_file_path).unwrap();

    struct TempFileCleaner(std::path::PathBuf);
    impl Drop for TempFileCleaner {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    let _cleaner = TempFileCleaner(mock_file_path.clone());

    // Set a mock state message first
    state
        .set_state_message(teloxide::types::UserId(123), 2002)
        .await;

    println!("SENDING get_file:2 CALLBACK...");
    // Send get_file callback for ID 2
    let query = common::make_callback_query("get_file:2", 123, 456);
    let res =
        ril_daemon::telegram::callbacks::handle_callback_query(bot.clone(), query, state.clone())
            .await;
    println!("get_file:2 CALLBACK RESULT: {:?}", res);
    assert!(res.is_ok());

    // Verify that deleteMessage was called for the previous state message (2002)
    let reqs = server.requests.lock().unwrap();
    for r in reqs.iter() {
        println!("MOCK REQ: {}, BODY: {:?}", r.path, r.body);
    }
    let delete_req = reqs.iter().find(|r| {
        r.path.to_lowercase().contains("deletemessage")
            && r.body["message_id"].as_i64() == Some(2002)
    });
    assert!(delete_req.is_some(), "Should delete previous state message");

    // Verify that sendDocument was called
    let send_doc_req = reqs
        .iter()
        .find(|r| r.path.to_lowercase().contains("senddocument"));
    assert!(send_doc_req.is_some(), "Should send document");

    // Drop reqs lock to prevent deadlocking mock server when performing subsequent bot requests
    drop(reqs);

    // The new state message should be set to 1001
    let final_state_msg = state.get_state_message(teloxide::types::UserId(123)).await;
    assert_eq!(final_state_msg, Some(1001));

    println!("SENDING open_last_imported CALLBACK...");
    // Test the newly implemented callbacks:
    // 1. open_last_imported
    let query_open = common::make_callback_query("open_last_imported", 123, 456);
    let res_open = ril_daemon::telegram::callbacks::handle_callback_query(
        bot.clone(),
        query_open,
        state.clone(),
    )
    .await;
    println!("open_last_imported CALLBACK RESULT: {:?}", res_open);
    assert!(res_open.is_ok());

    println!("SENDING show_import_errors CALLBACK...");
    // 2. show_import_errors
    let query_err = common::make_callback_query("show_import_errors", 123, 456);
    let res_err = ril_daemon::telegram::callbacks::handle_callback_query(
        bot.clone(),
        query_err,
        state.clone(),
    )
    .await;
    println!("show_import_errors CALLBACK RESULT: {:?}", res_err);
    assert!(res_err.is_ok());
    println!("TEST COMPLETED SUCCESSFULY!");
}
