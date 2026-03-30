"""Tests for Voice Journal components."""
import asyncio
import pytest
import os
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
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
    
    def test_init(self):
        """Test MemoryClient initialization."""
        from src.memory_client import MemoryClient
        client = MemoryClient()
        assert client.namespace == "voice-journal"
        assert "localhost" in client.base_url or "8001" in client.base_url
    
    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check."""
        from src.memory_client import MemoryClient
        client = MemoryClient()
        # May fail if server not running, but shouldn't raise
        result = await client.health_check()
        assert isinstance(result, bool)
        await client.close()

    def test_build_long_term_filters(self):
        """Long-term memory searches should always scope by namespace and user."""
        from src.memory_client import MemoryClient

        client = MemoryClient(namespace="voice-journal")

        assert client._build_long_term_filters("google_123") == {
            "namespace": {"eq": "voice-journal"},
            "user_id": {"eq": "google_123"},
        }

    @pytest.mark.asyncio
    async def test_search_long_term_memory_passes_explicit_filters(self):
        """Searches should pass documented Redis AMS filters, not implicit defaults."""
        from src.memory_client import MemoryClient

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
            filters={
                "namespace": {"eq": "voice-journal"},
                "user_id": {"eq": "google_123"},
            },
            limit=5,
            distance_threshold=0.6,
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
