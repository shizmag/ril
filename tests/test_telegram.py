import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ril import telegram_bot, db

def create_mock_update(user_id=12345, text=None, args=None):
    """Helper to construct mocked Telegram Update and Context objects."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "testuser"
    
    update.message = AsyncMock()
    update.message.text = text
    
    context = MagicMock()
    context.args = args or []
    
    return update, context

@pytest.mark.asyncio
async def test_telegram_unauthorized_user(mocker):
    # Enforce allowed users list
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    update, context = create_mock_update(user_id=99999, text="/start")
    
    # Execute the wrapped handler
    await telegram_bot.start_command(update, context)
    
    # Assert unauthorized message was sent
    update.message.reply_text.assert_called_once_with("❌ У вас нет доступа к этому боту.")

@pytest.mark.asyncio
async def test_telegram_start_command(mocker):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    update, context = create_mock_update(user_id=12345, text="/start")
    await telegram_bot.start_command(update, context)
    
    args, kwargs = update.message.reply_text.call_args
    assert "Привет! Я твой бот Read It Later" in args[0]

@pytest.mark.asyncio
async def test_telegram_stats_command(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # 1. Empty DB case
    update, context = create_mock_update(user_id=12345)
    await telegram_bot.stats_command(update, context)
    update.message.reply_text.assert_called_once_with("📭 *Ваша библиотека пока пуста.*", reply_markup=mocker.ANY, parse_mode="Markdown")
    
    # 2. Populated DB case
    db.add_article(
        url="https://example.com/stats",
        title="Test Stats Article",
        file_path="/mock.md",
        word_count=350,
        char_count=1500,
        content="Stats test content"
    )
    
    update2, context2 = create_mock_update(user_id=12345)
    await telegram_bot.stats_command(update2, context2)
    reply_msg = update2.message.reply_text.call_args[0][0]
    assert "Read It Later — Статистика" in reply_msg
    assert "Всего статей: *1*" in reply_msg
    assert "350" in reply_msg

@pytest.mark.asyncio
async def test_telegram_list_command(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # 1. Empty List
    update, context = create_mock_update(user_id=12345)
    await telegram_bot.list_command(update, context)
    update.message.reply_text.assert_called_once_with("📭 *В вашей библиотеке пока нет статей.*", reply_markup=mocker.ANY, parse_mode="Markdown")
    
    # 2. Populated List
    db.add_article("https://url1.com", "First Art", "/path1.md", 50, 200, "Content 1")
    db.add_article("https://url2.com", "Second Art", "/path2.md", 60, 250, "Content 2")
    
    update2, context2 = create_mock_update(user_id=12345)
    await telegram_bot.list_command(update2, context2)
    reply_msg = update2.message.reply_text.call_args[0][0]
    assert "Последние сохранённые статьи:" in reply_msg
    assert "First Art" in reply_msg
    assert "Second Art" in reply_msg

@pytest.mark.asyncio
async def test_telegram_search_command(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # 1. Empty args check
    update, context = create_mock_update(user_id=12345, args=[])
    await telegram_bot.search_command(update, context)
    assert "укажите поисковый запрос" in update.message.reply_text.call_args[0][0]
    
    # 2. No results found
    update2, context2 = create_mock_update(user_id=12345, args=["computer"])
    await telegram_bot.search_command(update2, context2)
    assert "ничего не найдено" in update2.message.reply_text.call_args[0][0]
    
    # 3. Matches found
    db.add_article("https://search.com", "Quantum Computers", "/path.md", 20, 100, "Supercomputing with qubits")
    update3, context3 = create_mock_update(user_id=12345, args=["qubits"])
    await telegram_bot.search_command(update3, context3)
    reply_msg = update3.message.reply_text.call_args[0][0]
    assert "Результаты поиска для 'qubits':" in reply_msg
    assert "Quantum Computers" in reply_msg

@pytest.mark.asyncio
async def test_telegram_handle_message(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # 1. Text without URLs
    update, context = create_mock_update(user_id=12345, text="Hello bot!")
    await telegram_bot.handle_message(update, context)
    update.message.reply_text.assert_called_once_with("ℹ️ Пришлите мне ссылку, чтобы сохранить статью в архив.", reply_markup=mocker.ANY)
    
    # 2. Text with URL - Pipeline Success (File doesn't exist)
    mock_process = mocker.patch(
        "ril.core.process_url",
        new_callable=AsyncMock,
        return_value={
            "id": 7,
            "title": "Autonomous Agents",
            "file_path": "/vault/agents.md",
            "word_count": 850
        }
    )
    
    status_msg = MagicMock()
    status_msg.message_id = 999
    update2, context2 = create_mock_update(user_id=12345, text="Hey check this out: https://agents.ai/future")
    update2.message.reply_text = AsyncMock(return_value=status_msg)
    
    # Mock os.path.exists as False
    mocker.patch("os.path.exists", return_value=False)
    
    await telegram_bot.handle_message(update2, context2)
    
    mock_process.assert_called_once_with("https://agents.ai/future", converter=mocker.ANY)
    update2.message.delete.assert_called_once()
    context2.bot.delete_message.assert_called_once_with(chat_id=update2.effective_chat.id, message_id=999)
    # The last reply_text call should be the fallback warning
    last_call = update2.message.reply_text.call_args_list[-1]
    assert "Файл не найден на диске" in last_call[0][0]
    
    # 3. Text with URL - Pipeline Success (File exists)
    update3, context3 = create_mock_update(user_id=12345, text="https://agents.ai/future2")
    status_msg3 = MagicMock()
    status_msg3.message_id = 9993
    update3.message.reply_text = AsyncMock(return_value=status_msg3)
    update3.message.reply_document = AsyncMock()
    
    mock_process.reset_mock()
    mock_process.return_value = {
        "id": 8,
        "title": "Autonomous Agents 2",
        "file_path": "/vault/agents2.md",
        "word_count": 900
    }
    
    mocker.patch("os.path.exists", return_value=True)
    mock_open = mocker.patch("builtins.open", mocker.mock_open(read_data="content"))
    
    await telegram_bot.handle_message(update3, context3)
    
    update3.message.reply_document.assert_called_once()
    caption = update3.message.reply_document.call_args[1]["caption"]
    assert "Autonomous Agents 2" in caption
    assert "ID статьи: `8`" in caption
    assert "🔗 https://agents.ai/future2" in caption
    update3.message.delete.assert_called_once()
    context3.bot.delete_message.assert_called_once_with(chat_id=update3.effective_chat.id, message_id=9993)

@pytest.mark.asyncio
async def test_telegram_handle_message_failure(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # Mock pipeline exception
    mocker.patch("ril.core.process_url", new_callable=AsyncMock, side_effect=Exception("Failed connecting to site"))
    
    status_msg = MagicMock()
    status_msg.message_id = 999
    update, context = create_mock_update(user_id=12345, text="https://fail.com")
    update.message.reply_text = AsyncMock(return_value=status_msg)
    
    await telegram_bot.handle_message(update, context)
    
    # Deletes status message and sends the error message
    context.bot.delete_message.assert_called_once_with(chat_id=update.effective_chat.id, message_id=999)
    # The last reply_text call should be the error message
    last_call = update.message.reply_text.call_args_list[-1]
    assert "Ошибка при импорте ссылки: https://fail.com" in last_call[0][0]
    assert "Failed connecting to site" in last_call[0][0]

@pytest.mark.asyncio
async def test_telegram_get_command(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    db.add_article("https://url.com", "Test Get", "/vault/test_get.md", 100, 500, "Content")
    
    # 1. Successful get (file exists)
    update, context = create_mock_update(user_id=12345, args=["1"])
    update.message.reply_document = AsyncMock()
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data="content"))
    
    await telegram_bot.get_command(update, context)
    update.message.delete.assert_called_once()
    update.message.reply_document.assert_called_once()
    caption = update.message.reply_document.call_args[1]["caption"]
    assert "Test Get" in caption
    assert "ID статьи: `1`" in caption
    
    # 2. Get with non-existent ID
    update2, context2 = create_mock_update(user_id=12345, args=["999"])
    await telegram_bot.get_command(update2, context2)
    update2.message.reply_text.assert_called_once_with("❌ Статья с ID 999 не найдена.", reply_markup=mocker.ANY)

@pytest.mark.asyncio
async def test_telegram_delete_command(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    db.add_article("https://url.com", "Test Delete", "/vault/test_del.md", 100, 500, "Content")
    
    mocker.patch("ril.core.delete_article", return_value=True)
    
    update, context = create_mock_update(user_id=12345, args=["1"])
    await telegram_bot.delete_command(update, context)
    update.message.delete.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "успешно удалена" in reply
    assert "Test Delete" in reply

@pytest.mark.asyncio
async def test_telegram_reset_command(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # reset
    update, context = create_mock_update(user_id=12345)
    await telegram_bot.reset_command(update, context)
    update.message.delete.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "для подтверждения отправьте" in reply.lower()
    
    # reset confirm
    mock_reset = mocker.patch("ril.core.reset_library")
    update2, context2 = create_mock_update(user_id=12345)
    await telegram_bot.reset_confirm_command(update2, context2)
    update2.message.delete.assert_called_once()
    mock_reset.assert_called_once()
    reply2 = update2.message.reply_text.call_args[0][0]
    assert "успешно очищены" in reply2

@pytest.mark.asyncio
async def test_telegram_effective_user_none(mocker):
    update = MagicMock()
    update.effective_user = None
    context = MagicMock()
    await telegram_bot.start_command(update, context)
    update.message.reply_text.assert_not_called()

@pytest.mark.asyncio
async def test_telegram_command_exceptions(mocker):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    mocker.patch("ril.db.get_stats", side_effect=Exception("DB Error"))
    mocker.patch("ril.db.list_articles", side_effect=Exception("DB Error"))
    mocker.patch("ril.db.search_articles", side_effect=Exception("DB Error"))
    
    update, context = create_mock_update(user_id=12345)
    await telegram_bot.stats_command(update, context)
    update.message.reply_text.assert_called_with("❌ Произошла ошибка при получении статистики.")
    
    update2, context2 = create_mock_update(user_id=12345)
    await telegram_bot.list_command(update2, context2)
    update2.message.reply_text.assert_called_with("❌ Ошибка при выводе списка статей.")
    
    update3, context3 = create_mock_update(user_id=12345, args=["test"])
    await telegram_bot.search_command(update3, context3)
    update3.message.reply_text.assert_called_with("❌ Ошибка при поиске.")

@pytest.mark.asyncio
async def test_telegram_handle_message_no_text(mocker):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    update, context = create_mock_update(user_id=12345, text=None)
    await telegram_bot.handle_message(update, context)
    update.message.reply_text.assert_not_called()

@pytest.mark.asyncio
async def test_telegram_format_command(mocker):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    update, context = create_mock_update(user_id=12345)
    context.user_data = {}
    await telegram_bot.format_command(update, context)
    update.message.reply_text.assert_called_once()
    reply_msg = update.message.reply_text.call_args[0][0]
    assert "Настройка формата сохранения" in reply_msg
    assert "MARKDOWN" in reply_msg

@pytest.mark.asyncio
async def test_telegram_callback_set_format(mocker):
    query = MagicMock()
    query.from_user.id = 12345
    query.data = "set_format:html"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    
    update = MagicMock()
    update.callback_query = query
    
    context = MagicMock()
    context.user_data = {}
    
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    await telegram_bot.callback_handler(update, context)
    
    query.answer.assert_called_once_with("✅ Формат изменен на HTML")
    assert context.user_data["format"] == "html"
    query.edit_message_text.assert_called_once()
    assert "Текущий формат по умолчанию изменен на: *HTML*" in query.edit_message_text.call_args[0][0]

@pytest.mark.asyncio
async def test_telegram_handle_message_with_format(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # Mock core.process_url to verify format detection
    mock_process = mocker.patch(
        "ril.core.process_url",
        new_callable=AsyncMock,
        return_value={
            "id": 10,
            "title": "HTML article",
            "file_path": "/mock.html",
            "word_count": 100
        }
    )
    
    status_msg = MagicMock()
    status_msg.message_id = 999
    
    # 1. Test explicit "html" in message
    update, context = create_mock_update(user_id=12345, text="https://test.com/page html")
    update.message.reply_text = AsyncMock(return_value=status_msg)
    mocker.patch("os.path.exists", return_value=False)
    context.user_data = {"format": "markdown"}
    
    await telegram_bot.handle_message(update, context)
    
    from ril.converters import HTMLConverter
    assert isinstance(mock_process.call_args[1]["converter"], HTMLConverter)

@pytest.mark.asyncio
async def test_telegram_handle_message_multiple_urls(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    mock_process = mocker.patch(
        "ril.core.process_url",
        new_callable=AsyncMock,
        return_value={
            "id": 9,
            "title": "Agent 1",
            "file_path": "/vault/agent1.md",
            "word_count": 100
        }
    )
    
    status_msg = MagicMock()
    status_msg.message_id = 999
    update, context = create_mock_update(
        user_id=12345,
        text="Check these links: https://link1.com/art1 and https://link2.com/art2"
    )
    update.message.reply_text = AsyncMock(return_value=status_msg)
    mocker.patch("os.path.exists", return_value=False)
    
    await telegram_bot.handle_message(update, context)
    
    assert mock_process.call_count == 2
    mock_process.assert_any_call("https://link1.com/art1", converter=mocker.ANY)
    mock_process.assert_any_call("https://link2.com/art2", converter=mocker.ANY)
    assert context.bot.delete_message.call_count == 2

def test_telegram_run_bot(mocker):
    mock_app = MagicMock()
    mock_builder = MagicMock()
    mock_app_built = MagicMock()
    
    mocker.patch("ril.telegram_bot.Application", mock_app)
    mock_app.builder.return_value = mock_builder
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app_built
    
    mock_app_built.run_polling = MagicMock()
    mock_app_built.add_handler = MagicMock()
    
    mocker.patch("ril.telegram_bot.TELEGRAM_TOKEN", "mock-token")
    
    telegram_bot.run_bot()
    
    mock_app_built.run_polling.assert_called_once()
    assert mock_app_built.add_handler.call_count == 12

def test_telegram_run_bot_no_token(mocker):
    mocker.patch("ril.telegram_bot.TELEGRAM_TOKEN", None)
    with patch("builtins.print") as mock_print:
        telegram_bot.run_bot()
        mock_print.assert_any_call("Error: TELEGRAM_TOKEN environment variable is not set. Cannot start bot.")

