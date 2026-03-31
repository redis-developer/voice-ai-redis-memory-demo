"""Memory client wrapper for Redis Agent Memory Server."""
import os
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
import httpx
from agent_memory_client import create_memory_client
from agent_memory_client.filters import Namespace, UserId
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
        resolved_base_url = base_url or os.getenv("MEMORY_SERVER_URL")
        if not resolved_base_url:
            raise ValueError("MEMORY_SERVER_URL must be set for Redis Agent Memory Server")
        self.base_url = resolved_base_url
        self.namespace = namespace
        self._client = None

    def _build_long_term_search_kwargs(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Build doc-supported long-term search kwargs for the AMS client."""
        search_kwargs: Dict[str, Any] = {
            "namespace": Namespace(eq=self.namespace),
        }
        if user_id:
            search_kwargs["user_id"] = UserId(eq=user_id)
        return search_kwargs

    @staticmethod
    def _memory_preview(text: Optional[str], limit: int = 60) -> str:
        """Return a compact one-line memory preview for logs."""
        if not text:
            return ""
        collapsed = " ".join(text.split())
        return collapsed[:limit]

    @staticmethod
    def _memory_distance(memory: Any) -> Optional[float]:
        """Read the similarity distance from an SDK memory object."""
        return getattr(memory, "dist", None)

    async def _log_near_miss_candidates(
        self,
        client: Any,
        query: str,
        user_id: Optional[str],
        limit: int,
    ) -> None:
        """Run a broader diagnostic search and log the nearest candidates."""
        try:
            diagnostic_results = await client.search_long_term_memory(
                text=query,
                limit=max(limit, 5),
                distance_threshold=1.0,
                **self._build_long_term_search_kwargs(user_id=user_id),
            )
            candidates = []
            for memory in diagnostic_results.memories[:5]:
                candidates.append({
                    "id": memory.id,
                    "distance": self._memory_distance(memory),
                    "preview": self._memory_preview(memory.text),
                })
            logger.info(
                "memory.search.near_misses query_chars=%s user_id=%s candidates=%s",
                len(query),
                user_id,
                candidates,
            )
        except Exception as exc:
            logger.warning(
                "memory.search.near_misses_failed query_chars=%s user_id=%s error_type=%s error=%s",
                len(query),
                user_id,
                type(exc).__name__,
                exc,
            )
    
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
            raise

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
            raise

    def _format_session_transcript(self, messages: List[Dict[str, Any]]) -> str:
        """Format a session transcript for long-term promotion."""
        lines = []
        for message in messages:
            role = message.get("role", "user")
            role_label = "User" if role == "user" else "Assistant"
            content = (message.get("content") or "").strip()
            if content:
                lines.append(f"{role_label}: {content}")
        return "\n".join(lines)

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
    
    async def promote_session_to_long_term(self, session_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Promote the full working-memory transcript into long-term memory."""
        messages = await self.get_session_history(session_id, user_id)
        transcript = self._format_session_transcript(messages)
        if not transcript:
            return None

        start_ms = now_ms()
        client = await self._get_client()
        now = datetime.now(timezone.utc)

        memory = ClientMemoryRecord(
            id=f"session:{session_id}",
            text=transcript,
            memory_type=MemoryTypeEnum.EPISODIC,
            user_id=user_id,
            session_id=session_id,
            namespace=self.namespace,
            topics=["session", "conversation", "chat_history"],
            created_at=now,
        )

        response = await client.create_long_term_memory(
            memories=[memory],
            deduplicate=True,
        )
        log_timing(
            "memory.promote_session_to_long_term",
            start_ms,
            logger_instance=logger,
            session_id=session_id,
            message_count=len(messages),
            transcript_chars=len(transcript),
        )

        return {
            "status": response.status,
            "memory_id": memory.id,
            "message_count": len(messages),
            "transcript_chars": len(transcript),
        }

    async def end_session(self, session_id: str, user_id: str, promote: bool = True):
        """Optionally promote and then clean up a session."""
        client = await self._get_client()
        promotion = None
        if promote:
            promotion = await self.promote_session_to_long_term(session_id, user_id)
        await client.delete_working_memory(session_id)
        return promotion

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
            search_kwargs = self._build_long_term_search_kwargs(user_id=user_id)

            t1 = now_ms()
            results = await client.search_long_term_memory(
                text=query,
                limit=limit,
                distance_threshold=distance_threshold,
                **search_kwargs,
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

            logger.info(
                "memory.search.results query_chars=%s user_id=%s threshold=%.2f distances=%s",
                len(query),
                user_id,
                distance_threshold,
                [memory.get("distance") for memory in memories],
            )
            if not memories:
                await self._log_near_miss_candidates(
                    client=client,
                    query=query,
                    user_id=user_id,
                    limit=limit,
                )

            log_timing("memory.search.total", t0, logger_instance=logger, results=len(memories), user_id=user_id)
            return memories

        except Exception as e:
            logger.error(f"Error searching long-term memory: {e}")
            log_timing("memory.search.total", t0, logger_instance=logger, results=0, user_id=user_id, error=True)
            raise

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
            True if saved successfully
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
            raise

    async def get_conversation_context(
        self,
        session_id: str,
        user_id: str,
        max_turns: Optional[int] = None
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
            log_timing("memory.working_context.api_call", t1, logger_instance=logger, session_id=session_id, max_turns=max_turns or "all")

            if not working_memory.messages:
                log_timing("memory.working_context.total", t0, logger_instance=logger, session_id=session_id, message_count=0, chars=0)
                return ""

            if max_turns is None:
                selected_messages = working_memory.messages
            else:
                # Limit to max_turns * 2 for user+assistant pairs
                selected_messages = working_memory.messages[-(max_turns * 2):]

            # Format as conversation history
            lines = []
            for msg in selected_messages:
                role_label = "User" if msg.role == "user" else "Assistant"
                lines.append(f"{role_label}: {msg.content}")

            context = "\n".join(lines)
            log_timing(
                "memory.working_context.total",
                t0,
                logger_instance=logger,
                session_id=session_id,
                message_count=len(selected_messages),
                chars=len(context),
            )
            return context

        except Exception as e:
            logger.warning(f"Working memory error getting context: {e}")
            log_timing("memory.working_context.total", t0, logger_instance=logger, session_id=session_id, message_count=0, chars=0, error=True)
            raise
