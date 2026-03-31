"""Tests for Voice Journal components."""
import asyncio
import pytest
import os
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAudioHandler:
    """Tests for AudioHandler class."""
    
    def test_init(self):
        """Test AudioHandler initialization."""
        from src.audio_handler import AudioHandler
        handler = AudioHandler()
        assert handler.api_key is not None
        assert handler.RATE == 16000
        assert handler.CHANNELS == 1
    
    def test_recordings_dir_created(self):
        """Test that recordings directory is created."""
        from src.audio_handler import AudioHandler
        handler = AudioHandler()
        assert os.path.exists(handler.recordings_dir)


class TestMemoryClient:
    """Tests for MemoryClient class."""
    
    def test_init(self, monkeypatch):
        """Test MemoryClient initialization."""
        monkeypatch.setenv("MEMORY_SERVER_URL", "http://memory-server:8000")
        from src.memory_client import MemoryClient
        client = MemoryClient()
        assert client.namespace == "voice-journal"
        assert client.base_url == "http://memory-server:8000"

    def test_init_requires_memory_server_url(self, monkeypatch):
        """Memory client should not silently fall back to a local URL."""
        monkeypatch.delenv("MEMORY_SERVER_URL", raising=False)

        from src.memory_client import MemoryClient

        with pytest.raises(ValueError, match="MEMORY_SERVER_URL must be set"):
            MemoryClient()
    
    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check."""
        from src.memory_client import MemoryClient
        client = MemoryClient()
        # May fail if server not running, but shouldn't raise
        result = await client.health_check()
        assert isinstance(result, bool)
        await client.close()

    def test_build_long_term_search_kwargs(self):
        """Long-term memory searches should use doc-supported kwargs."""
        from src.memory_client import MemoryClient
        from agent_memory_client.filters import Namespace, UserId

        client = MemoryClient(namespace="voice-journal")

        assert client._build_long_term_search_kwargs("google_123") == {
            "namespace": Namespace(eq="voice-journal"),
            "user_id": UserId(eq="google_123"),
        }

    @pytest.mark.asyncio
    async def test_search_long_term_memory_passes_explicit_filter_kwargs(self):
        """Searches should pass documented Redis AMS filter kwargs."""
        from src.memory_client import MemoryClient
        from agent_memory_client.filters import Namespace, UserId

        client = MemoryClient(namespace="voice-journal")

        mock_results = Mock()
        mock_results.memories = []

        mock_sdk_client = Mock()
        mock_sdk_client.search_long_term_memory = AsyncMock(return_value=mock_results)

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_sdk_client)):
            await client.search_long_term_memory(
                query="lunch noodles",
                user_id="google_123",
                limit=5,
                distance_threshold=0.6,
            )

        mock_sdk_client.search_long_term_memory.assert_awaited_once_with(
            text="lunch noodles",
            namespace=Namespace(eq="voice-journal"),
            user_id=UserId(eq="google_123"),
            limit=5,
            distance_threshold=0.6,
        )

    @pytest.mark.asyncio
    async def test_search_long_term_memory_logs_near_misses_when_primary_search_is_empty(self):
        """Zero-result semantic searches should log broader diagnostic near misses."""
        from src.memory_client import MemoryClient
        from agent_memory_client.filters import Namespace, UserId

        client = MemoryClient(namespace="voice-journal")

        empty_results = Mock()
        empty_results.memories = []

        near_miss = Mock()
        near_miss.id = "mem_1"
        near_miss.text = "I ate cake today"
        near_miss.dist = 0.57

        diagnostic_results = Mock()
        diagnostic_results.memories = [near_miss]

        mock_sdk_client = Mock()
        mock_sdk_client.search_long_term_memory = AsyncMock(
            side_effect=[empty_results, diagnostic_results]
        )

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_sdk_client)):
            await client.search_long_term_memory(
                query="what did i eat today",
                user_id="google_123",
                limit=5,
                distance_threshold=0.6,
            )

        assert mock_sdk_client.search_long_term_memory.await_count == 2
        mock_sdk_client.search_long_term_memory.assert_any_await(
            text="what did i eat today",
            namespace=Namespace(eq="voice-journal"),
            user_id=UserId(eq="google_123"),
            limit=5,
            distance_threshold=1.0,
        )

    @pytest.mark.asyncio
    async def test_get_conversation_context_uses_entire_session_when_max_turns_is_none(self):
        """Current-session context should include the full working-memory transcript."""
        from src.memory_client import MemoryClient

        client = MemoryClient(namespace="voice-journal")

        working_memory = Mock()
        working_memory.messages = [
            Mock(role="user", content="hello"),
            Mock(role="assistant", content="hi there"),
            Mock(role="user", content="what did I log yesterday?"),
        ]

        mock_sdk_client = Mock()
        mock_sdk_client.get_or_create_working_memory = AsyncMock(return_value=(False, working_memory))

        with patch.object(client, "_get_client", AsyncMock(return_value=mock_sdk_client)):
            context = await client.get_conversation_context(
                session_id="session_1",
                user_id="google_123",
                max_turns=None,
            )

        assert context == (
            "User: hello\n"
            "Assistant: hi there\n"
            "User: what did I log yesterday?"
        )


class TestVoiceJournalAgent:
    """Tests for VoiceJournalAgent context assembly."""

    def test_format_memory_context_includes_multiple_entries(self):
        """Ask mode should include several saved memories, not just the top hit."""
        from src.voice_agent import VoiceJournalAgent

        agent = VoiceJournalAgent(user_id="google_123", session_id="session_1")
        context = agent._format_memory_context([
            {"text": "Need to buy eggs", "created_at": "2026-03-30T05:00:00+00:00"},
            {"text": "Schedule dentist appointment", "created_at": "2026-03-29T05:00:00+00:00"},
        ])

        assert "Mar 30: Need to buy eggs" in context
        assert "Mar 29: Schedule dentist appointment" in context

    @pytest.mark.asyncio
    async def test_generate_response_prompt_includes_working_and_long_term_memory(self):
        """Question answering should use current session and saved journal context together."""
        from src.voice_agent import VoiceJournalAgent

        agent = VoiceJournalAgent(user_id="google_123", session_id="session_1")

        posted = {}

        class MockResponse:
            def json(self):
                return {"response": "Here is your answer."}

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json, timeout):
                posted["url"] = url
                posted["json"] = json
                posted["timeout"] = timeout
                return MockResponse()

        with patch("src.voice_agent.httpx.AsyncClient", return_value=MockAsyncClient()):
            response = await agent._generate_response(
                query="What should I do today?",
                conversation_context="User: hello\nAssistant: Good morning!",
                journal_context="Mar 30: Need to buy eggs",
                calendar_context="",
            )

        assert response == "Here is your answer."
        prompt = posted["json"]["prompt"]
        assert "Current session conversation:\nUser: hello\nAssistant: Good morning!" in prompt
        assert "Saved journal memories:\nMar 30: Need to buy eggs" in prompt
        assert "User question: What should I do today?" in prompt

    def test_sanitize_memory_claims_blocks_false_no_memory_response(self):
        """The agent should not claim memories are missing when search found some."""
        from src.voice_agent import VoiceJournalAgent

        agent = VoiceJournalAgent(user_id="google_123", session_id="session_1")
        response = agent._sanitize_memory_claims(
            query="hi",
            response="Hello! This is our first conversation together. Saved journal memory doesn't exist for this user yet.",
            has_journal_memories=True,
        )

        assert response == (
            "Hello! I can see your saved journal entries and I'm ready to help with them. "
            "You can ask about past notes, summaries, or patterns."
        )

    @pytest.mark.asyncio
    async def test_handle_log_saves_to_long_term_and_working_memory(self):
        """Log intents should be stored immediately in both memory layers."""
        from src.intent_detector import Intent, IntentResult
        from src.voice_agent import VoiceJournalAgent

        memory_client = Mock()
        memory_client.create_journal_memory = AsyncMock()
        memory_client.save_conversation_turn = AsyncMock()

        agent = VoiceJournalAgent(
            user_id="google_123",
            session_id="session_1",
            memory_client=memory_client,
        )

        result = IntentResult(
            Intent.LOG_ENTRY,
            0.9,
            {"content": "need to buy eggs"},
            "log my note need to buy eggs",
        )
        response = await agent._handle_log(result)

        assert response == "Got it! I've saved your note. Anything else?"
        memory_client.create_journal_memory.assert_awaited_once()
        memory_client.save_conversation_turn.assert_awaited_once_with(
            session_id="session_1",
            user_id="google_123",
            user_message="log my note need to buy eggs",
            assistant_response="Got it! I've saved your note. Anything else?",
        )

    @pytest.mark.asyncio
    async def test_handle_ask_uses_full_session_and_tighter_distance_threshold(self):
        """Chat mode should use all session turns and a 0.2 long-term distance threshold."""
        from src.intent_detector import Intent, IntentResult
        from src.voice_agent import VoiceJournalAgent

        memory_client = Mock()
        memory_client.get_conversation_context = AsyncMock(return_value="User: hi\nAssistant: hello")
        memory_client.search_long_term_memory = AsyncMock(return_value=[])
        memory_client.save_conversation_turn = AsyncMock()

        agent = VoiceJournalAgent(
            user_id="google_123",
            session_id="session_1",
            memory_client=memory_client,
        )

        with patch.object(agent, "_generate_response", AsyncMock(return_value="Here is the answer.")):
            result = IntentResult(
                Intent.ASK_JOURNAL,
                0.8,
                {"query": "what did I say before?", "is_calendar": False},
                "what did I say before?",
            )
            response = await agent._handle_ask(result)

        assert response == "Here is the answer."
        memory_client.get_conversation_context.assert_awaited_once_with(
            session_id="session_1",
            user_id="google_123",
            max_turns=None,
        )
        memory_client.search_long_term_memory.assert_awaited_once_with(
            query="what did I say before?",
            user_id="google_123",
            limit=5,
            distance_threshold=0.2,
        )


class TestJournalManager:
    """Tests for JournalManager class."""
    
    def test_init(self):
        """Test JournalManager initialization."""
        from src.journal_manager import JournalManager
        manager = JournalManager()
        assert manager.prefix == "voice_journal:entries"
    
    def test_create_and_get_entry(self):
        """Test creating and retrieving an entry."""
        from src.journal_manager import JournalManager
        manager = JournalManager()
        
        entry = manager.create_entry(
            user_id="test_user",
            transcript="Test journal entry",
            language_code="en-IN",
            mood="happy",
            tags=["test", "demo"]
        )
        
        assert entry["transcript"] == "Test journal entry"
        assert entry["mood"] == "happy"
        assert "entry_id" in entry
        
        # Retrieve
        retrieved = manager.get_entry(entry["entry_id"])
        assert retrieved is not None
        assert retrieved["transcript"] == "Test journal entry"
        
        # Cleanup
        manager.delete_entry(entry["entry_id"])
    
    def test_update_entry(self):
        """Test updating an entry."""
        from src.journal_manager import JournalManager
        manager = JournalManager()
        
        entry = manager.create_entry(
            user_id="test_user",
            transcript="Original text",
            language_code="en-IN"
        )
        
        updated = manager.update_entry(
            entry["entry_id"],
            transcript="Updated text",
            mood="excited"
        )
        
        assert updated["transcript"] == "Updated text"
        assert updated["mood"] == "excited"
        
        # Cleanup
        manager.delete_entry(entry["entry_id"])


class TestAnalytics:
    """Tests for JournalAnalytics class."""
    
    def test_init(self):
        """Test JournalAnalytics initialization."""
        from src.analytics import JournalAnalytics
        analytics = JournalAnalytics()
        assert analytics.entries_prefix == "voice_journal:entries"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
