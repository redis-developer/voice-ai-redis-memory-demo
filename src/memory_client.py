"""Memory client wrapper for Redis Agent Memory Server."""
import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from dotenv import load_dotenv
import httpx
import redis
from agent_memory_client import create_memory_client
from agent_memory_client.models import MemoryMessage, ClientMemoryRecord, MemoryTypeEnum
from src.observability import now_ms, log_timing

load_dotenv()

logger = logging.getLogger(__name__)


class MemoryClient:
    """Wrapper for Redis Agent Memory Server operations."""
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        namespace: str = "voice-journal"
    ):
        self.base_url = base_url or os.getenv("MEMORY_SERVER_URL", "http://localhost:8001")
        self.namespace = namespace
        self._client = None

    def _build_long_term_filters(self, user_id: Optional[str] = None) -> Dict[str, Dict[str, str]]:
        """Build explicit long-term memory filters for Redis AMS.

        AMS docs recommend filtering personal data by ``user_id`` and
        using ``namespace`` to isolate app/domain-specific memories.
        """
        filters: Dict[str, Dict[str, str]] = {
            "namespace": {"eq": self.namespace},
        }
        if user_id:
            filters["user_id"] = {"eq": user_id}
        return filters
    
    async def _get_client(self):
        """Get or create the memory client."""
        if self._client is None:
            self._client = await create_memory_client(
                base_url=self.base_url,
                default_namespace=self.namespace
            )
        return self._client
    
    async def close(self):
        """Close the client connection."""
        if self._client:
            await self._client.close()
            self._client = None
    
    async def health_check(self) -> bool:
        """Check if the memory server is healthy."""
        start_ms = now_ms()
        try:
            async with httpx.AsyncClient() as http:
                response = await http.get(f"{self.base_url}/v1/health")
                log_timing("memory.health_check", start_ms, logger_instance=logger, status_code=response.status_code)
                return response.status_code == 200
        except Exception as exc:
            log_timing("memory.health_check", start_ms, logger_instance=logger, error=type(exc).__name__)
            return False
    
    async def add_journal_entry(
        self,
        session_id: str,
        user_id: str,
        transcript: str,
        language_code: str,
        audio_file: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Add a journal entry to working memory.
        
        Args:
            session_id: Unique session identifier
            user_id: User identifier
            transcript: Transcribed text from audio
            language_code: Detected language code
            audio_file: Optional path to audio file
            metadata: Optional additional metadata
            
        Returns:
            Entry information dict
        """
        start_ms = now_ms()
        client = await self._get_client()
        now = datetime.now(timezone.utc)
        
        # Build entry content with metadata
        entry_metadata = {
            "type": "journal_entry",
            "language_code": language_code,
            "timestamp": now.isoformat(),
            "audio_file": audio_file,
            **(metadata or {})
        }
        
        # Create message
        message = MemoryMessage(
            role="user",
            content=transcript,
            created_at=now
        )
        
        # Get or create working memory and append
        created, _ = await client.get_or_create_working_memory(
            session_id=session_id,
            user_id=user_id
        )
        
        await client.append_messages_to_working_memory(
            session_id=session_id,
            messages=[message],
            user_id=user_id
        )
        log_timing("memory.add_journal_entry", start_ms, logger_instance=logger, session_id=session_id, created=created)
        
        return {
            "session_id": session_id,
            "user_id": user_id,
            "transcript": transcript,
            "language_code": language_code,
            "timestamp": now.isoformat(),
            "audio_file": audio_file,
            "new_session": created
        }

    async def save_mood(
        self,
        user_id: str,
        mood: str,
        emoji: str
    ) -> Dict[str, Any]:
        """
        Save user's mood to long-term memory.

        Args:
            user_id: User identifier
            mood: Mood label (e.g., "Happy", "Sad")
            emoji: Emoji representing the mood

        Returns:
            Dict with status and memory info
        """
        start_ms = now_ms()
        client = await self._get_client()
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%B %d, %Y")

        # Create a memory record for the mood
        memory = ClientMemoryRecord(
            text=f"User is feeling {mood.lower()} {emoji} on {date_str}",
            memory_type=MemoryTypeEnum.SEMANTIC,
            user_id=user_id,
            namespace=self.namespace,
            topics=["mood", "feelings", "daily"],
            entities=[mood.lower()],
            created_at=now
        )

        try:
            response = await client.create_long_term_memory(
                memories=[memory],
                deduplicate=False  # Allow multiple mood entries per day
            )
            log_timing("memory.save_mood", start_ms, logger_instance=logger, user_id=user_id, mood=mood)

            return {
                "status": "success",
                "mood": mood,
                "emoji": emoji,
                "timestamp": now.isoformat(),
                "stored": True
            }
        except Exception as e:
            logger.error(f"Error saving mood: {e}")
            log_timing("memory.save_mood", start_ms, logger_instance=logger, user_id=user_id, mood=mood, error=True)
            return {
                "status": "error",
                "error": str(e),
                "stored": False
            }

    async def create_journal_memory(
        self,
        user_id: str,
        transcript: str,
        language_code: str,
        topics: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a journal entry directly in long-term memory.

        This stores the entry in the memory_idx index so it can be
        retrieved by search_long_term_memory.

        Args:
            user_id: User identifier
            transcript: The journal entry text
            language_code: Language code of the entry
            topics: Optional list of topics
            entities: Optional list of entities mentioned
            session_id: Optional session identifier

        Returns:
            Dict with status and memory info
        """
        start_ms = now_ms()
        client = await self._get_client()
        now = datetime.now(timezone.utc)

        # Create a memory record for long-term storage
        memory = ClientMemoryRecord(
            text=transcript,
            memory_type=MemoryTypeEnum.EPISODIC,  # Journal entries are episodic memories
            user_id=user_id,
            session_id=session_id,
            namespace=self.namespace,
            topics=topics or ["journal", "voice_entry"],
            entities=entities,
            created_at=now
        )

        try:
            response = await client.create_long_term_memory(
                memories=[memory],
                deduplicate=True
            )
            log_timing(
                "memory.create_journal_memory",
                start_ms,
                logger_instance=logger,
                user_id=user_id,
                transcript_chars=len(transcript),
                session_id=session_id,
            )

            return {
                "status": response.status,
                "memory_id": memory.id,
                "user_id": user_id,
                "transcript": transcript,
                "timestamp": now.isoformat(),
                "stored_in_long_term": True
            }
        except Exception as e:
            logger.error(f"Error creating long-term memory: {e}")
            log_timing(
                "memory.create_journal_memory",
                start_ms,
                logger_instance=logger,
                user_id=user_id,
                transcript_chars=len(transcript),
                session_id=session_id,
                error=True,
            )
            return {
                "status": "error",
                "error": str(e),
                "stored_in_long_term": False
            }

    async def add_assistant_response(
        self,
        session_id: str,
        user_id: str,
        response: str
    ):
        """Add an assistant response to working memory."""
        client = await self._get_client()
        now = datetime.now(timezone.utc)
        
        message = MemoryMessage(
            role="assistant",
            content=response,
            created_at=now
        )
        
        await client.append_messages_to_working_memory(
            session_id=session_id,
            messages=[message],
            user_id=user_id
        )
    
    async def get_session_history(
        self,
        session_id: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Get all messages from a session."""
        client = await self._get_client()
        
        _, working_memory = await client.get_or_create_working_memory(
            session_id=session_id,
            user_id=user_id
        )
        
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat() if msg.created_at else None
            }
            for msg in working_memory.messages
        ]
    
    async def end_session(self, session_id: str):
        """End and cleanup a session."""
        client = await self._get_client()
        await client.delete_working_memory(session_id)

    async def search_long_term_memory(
        self,
        query: str,
        user_id: Optional[str] = None,
        limit: int = 10,
        distance_threshold: float = 0.8
    ) -> List[Dict[str, Any]]:
        """
        Search long-term memories using semantic search.

        This searches the memory_idx index on Redis Cloud.

        Args:
            query: The search query text
            user_id: Optional user ID to filter by
            limit: Maximum number of results
            distance_threshold: Maximum distance for results (0-1, lower is more similar)

        Returns:
            List of memory records with text, distance, and metadata
        """
        t0 = now_ms()
        client = await self._get_client()
        log_timing("memory.search.client_get", t0, logger_instance=logger)

        try:
            filters = self._build_long_term_filters(user_id=user_id)

            t1 = now_ms()
            results = await client.search_long_term_memory(
                text=query,
                filters=filters,
                limit=limit,
                distance_threshold=distance_threshold
            )
            log_timing("memory.search.api_call", t1, logger_instance=logger, query_chars=len(query), limit=limit)

            # Convert to list of dicts
            memories = []
            for memory in results.memories:
                memories.append({
                    "id": memory.id,
                    "text": memory.text,
                    "distance": memory.dist,
                    "memory_type": memory.memory_type.value if memory.memory_type else None,
                    "topics": memory.topics,
                    "entities": memory.entities,
                    "created_at": memory.created_at.isoformat() if memory.created_at else None,
                    "user_id": memory.user_id,
                    "namespace": memory.namespace
                })

            log_timing("memory.search.total", t0, logger_instance=logger, results=len(memories), user_id=user_id)
            return memories

        except Exception as e:
            logger.error(f"Error searching long-term memory: {e}")
            log_timing("memory.search.total", t0, logger_instance=logger, results=0, user_id=user_id, error=True)
            return []

    async def search_memory_tool(
        self,
        query: str,
        user_id: Optional[str] = None,
        topics: Optional[List[str]] = None,
        max_results: int = 10,
        min_relevance: float = 0.3
    ) -> Dict[str, Any]:
        """
        Simplified memory search designed for LLM tool use.

        Args:
            query: The search query
            user_id: Optional user ID filter
            topics: Optional list of topics to filter by
            max_results: Maximum results to return
            min_relevance: Minimum relevance score (0-1)

        Returns:
            Dict with 'summary', 'memories' list, and 'total'
        """
        client = await self._get_client()

        try:
            result = await client.search_memory_tool(
                query=query,
                user_id=user_id,
                topics=topics,
                max_results=max_results,
                min_relevance=min_relevance
            )
            return result
        except Exception as e:
            logger.error(f"Error in search_memory_tool: {e}")
            return {
                "summary": f"Search failed: {e}",
                "memories": [],
                "total": 0
            }

    async def save_conversation_turn(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
        assistant_response: str
    ) -> bool:
        """
        Save a conversation turn (user message + assistant response) to working memory.

        This enables conversational continuity by storing the conversation history
        which can be retrieved later to maintain context.

        Args:
            session_id: Unique session identifier
            user_id: User identifier
            user_message: The user's message
            assistant_response: The assistant's response

        Returns:
            True if saved successfully, False otherwise
        """
        start_ms = now_ms()
        client = await self._get_client()
        now = datetime.now(timezone.utc)

        try:
            # Create messages for both user and assistant
            messages = [
                MemoryMessage(role="user", content=user_message, created_at=now),
                MemoryMessage(role="assistant", content=assistant_response, created_at=now)
            ]

            # Ensure working memory exists for this session
            await client.get_or_create_working_memory(
                session_id=session_id,
                user_id=user_id
            )

            # Append the conversation turn
            await client.append_messages_to_working_memory(
                session_id=session_id,
                messages=messages,
                user_id=user_id
            )

            log_timing("memory.save_conversation_turn", start_ms, logger_instance=logger, session_id=session_id)
            return True

        except Exception as e:
            logger.warning(f"Working memory error saving turn: {e}")
            log_timing("memory.save_conversation_turn", start_ms, logger_instance=logger, session_id=session_id, error=True)
            return False

    async def get_conversation_context(
        self,
        session_id: str,
        user_id: str,
        max_turns: int = 10
    ) -> str:
        """
        Get formatted conversation context from working memory for LLM prompts.

        Returns the recent conversation history formatted as a string that can
        be included in LLM system/user prompts for conversational continuity.

        Args:
            session_id: Unique session identifier
            user_id: User identifier
            max_turns: Maximum number of message pairs to include

        Returns:
            Formatted conversation history string, or empty string if no history
        """
        t0 = now_ms()
        client = await self._get_client()
        log_timing("memory.working_context.client_get", t0, logger_instance=logger, session_id=session_id)

        try:
            t1 = now_ms()
            _, working_memory = await client.get_or_create_working_memory(
                session_id=session_id,
                user_id=user_id
            )
            log_timing("memory.working_context.api_call", t1, logger_instance=logger, session_id=session_id, max_turns=max_turns)

            if not working_memory.messages:
                log_timing("memory.working_context.total", t0, logger_instance=logger, session_id=session_id, message_count=0, chars=0)
                return ""

            # Get recent messages (limit to max_turns * 2 for user+assistant pairs)
            recent_messages = working_memory.messages[-(max_turns * 2):]

            # Format as conversation history
            lines = []
            for msg in recent_messages:
                role_label = "User" if msg.role == "user" else "Assistant"
                lines.append(f"{role_label}: {msg.content}")

            context = "\n".join(lines)
            log_timing(
                "memory.working_context.total",
                t0,
                logger_instance=logger,
                session_id=session_id,
                message_count=len(recent_messages),
                chars=len(context),
            )
            return context

        except Exception as e:
            logger.warning(f"Working memory error getting context: {e}")
            log_timing("memory.working_context.total", t0, logger_instance=logger, session_id=session_id, message_count=0, chars=0, error=True)
            return ""

    async def get_combined_context(
        self,
        session_id: str,
        user_id: str,
        query: str,
        max_conversation_turns: int = 5,
        max_long_term_results: int = 5
    ) -> Tuple[str, str]:
        """
        Get both conversation context and relevant long-term memories.

        This is useful for building rich context for LLM prompts that includes
        both the recent conversation and relevant past journal entries.

        Args:
            session_id: Unique session identifier
            user_id: User identifier
            query: Current query to search long-term memory
            max_conversation_turns: Max conversation turns to include
            max_long_term_results: Max long-term memory results

        Returns:
            Tuple of (conversation_context, long_term_context)
        """
        # Get conversation history from working memory
        conversation_context = await self.get_conversation_context(
            session_id=session_id,
            user_id=user_id,
            max_turns=max_conversation_turns
        )

        # Search long-term memory for relevant entries
        memories = await self.search_long_term_memory(
            query=query,
            user_id=user_id,
            limit=max_long_term_results,
            distance_threshold=0.8
        )

        # Format long-term memories
        long_term_lines = []
        for memory in memories:
            created_at = memory.get("created_at", "")
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    date_str = dt.strftime("%b %d")
                except Exception:
                    date_str = "Recent"
            else:
                date_str = "Recent"

            text = memory.get("text", "")
            if len(text) > 150:
                text = text[:150] + "..."
            long_term_lines.append(f"- {date_str}: {text}")

        long_term_context = "\n".join(long_term_lines) if long_term_lines else ""

        return conversation_context, long_term_context
