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
    update.message.reply_text.assert_called_once_with("📭 Ваша библиотека пока пуста.")
    
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
    assert "Статистика библиотеки RIL:" in reply_msg
    assert "Всего статей: 1" in reply_msg
    assert "350" in reply_msg

@pytest.mark.asyncio
async def test_telegram_list_command(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # 1. Empty List
    update, context = create_mock_update(user_id=12345)
    await telegram_bot.list_command(update, context)
    update.message.reply_text.assert_called_once_with("📭 В библиотеке нет статей.")
    
    # 2. Populated List
    db.add_article("https://url1.com", "First Art", "/path1.md", 50, 200, "Content 1")
    db.add_article("https://url2.com", "Second Art", "/path2.md", 60, 250, "Content 2")
    
    update2, context2 = create_mock_update(user_id=12345)
    await telegram_bot.list_command(update2, context2)
    reply_msg = update2.message.reply_text.call_args[0][0]
    assert "Последние 10 статей:" in reply_msg
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
    update.message.reply_text.assert_called_once_with("ℹ️ Пришлите мне ссылку, чтобы сохранить статью в архив.")
    
    # 2. Text with URL - Pipeline Success
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
    
    status_msg = AsyncMock()
    update2, context2 = create_mock_update(user_id=12345, text="Hey check this out: https://agents.ai/future")
    update2.message.reply_text = AsyncMock(return_value=status_msg)
    
    await telegram_bot.handle_message(update2, context2)
    
    update2.message.reply_text.assert_called_once_with("⏳ Начинаю импорт: https://agents.ai/future...")
    mock_process.assert_called_once_with("https://agents.ai/future")
    
    # Assert final edited reply status
    status_msg.edit_text.assert_called_once()
    assert "Сохранил!" in status_msg.edit_text.call_args[0][0]
    assert "Autonomous Agents" in status_msg.edit_text.call_args[0][0]
    assert "850 слов" in status_msg.edit_text.call_args[0][0]

@pytest.mark.asyncio
async def test_telegram_handle_message_failure(mocker, setup_test_environment):
    mocker.patch("ril.telegram_bot.ALLOWED_TELEGRAM_USERS", [12345])
    
    # Mock pipeline exception
    mocker.patch("ril.core.process_url", new_callable=AsyncMock, side_effect=Exception("Failed connecting to site"))
    
    status_msg = AsyncMock()
    update, context = create_mock_update(user_id=12345, text="https://fail.com")
    update.message.reply_text = AsyncMock(return_value=status_msg)
    
    await telegram_bot.handle_message(update, context)
    
    status_msg.edit_text.assert_called_once()
    reply_msg = status_msg.edit_text.call_args[0][0]
    assert "Ошибка при импорте ссылки: https://fail.com" in reply_msg
    assert "Failed connecting to site" in reply_msg

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
    assert mock_app_built.add_handler.call_count == 5

def test_telegram_run_bot_no_token(mocker):
    mocker.patch("ril.telegram_bot.TELEGRAM_TOKEN", None)
    with patch("builtins.print") as mock_print:
        telegram_bot.run_bot()
        mock_print.assert_any_call("Error: TELEGRAM_TOKEN environment variable is not set. Cannot start bot.")

