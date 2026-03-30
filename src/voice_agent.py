"""Voice Journal Agent - Main conversational agent.

Handles:
- Mode switching (Log mode vs Chat mode)
- Context assembly from retrieved entries
- Natural voice-first response generation (via Ollama)
"""
import os
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import httpx
from dotenv import load_dotenv

from src.intent_detector import Intent, IntentResult
from src.memory_client import MemoryClient
from src.calendar_client import CalendarClient
from src.intent_router import get_intent_router
from src.observability import now_ms, log_timing, update_request_context

load_dotenv()

logger = logging.getLogger(__name__)

# Ollama configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")


class AgentMode(Enum):
    LOG = "log"      # User is logging entries
    CHAT = "chat"    # User is querying/chatting about entries


@dataclass
class AgentState:
    """Tracks agent conversation state."""
    mode: AgentMode = AgentMode.LOG
    last_entries_shown: List[str] = field(default_factory=list)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)


class VoiceJournalAgent:
    """Main voice journal agent."""

    def __init__(
        self,
        user_id: str = "default_user",
        session_id: Optional[str] = None,
        memory_client: Optional[MemoryClient] = None
    ):
        self.user_id = user_id
        self.session_id = session_id  # For working memory (conversation continuity)
        self.memory_client = memory_client  # For searching long-term memory (memory_idx)
        self.state = AgentState()

        # Initialize calendar client (lazy load on first use)
        self._calendar_client: Optional[CalendarClient] = None

        # Semantic router for intent detection (lazy load)
        self._intent_router = None

    # Fallback keywords (used if semantic router fails)
    LOG_KEYWORDS = ["log my note", "note this", "record this", "journal this", "save this", "remember this"]

    @property
    def calendar_client(self) -> Optional[CalendarClient]:
        """Lazy-load calendar client."""
        if self._calendar_client is None:
            try:
                self._calendar_client = CalendarClient()
            except Exception as e:
                logger.warning(f"Calendar init error: {e}")
        return self._calendar_client

    async def process_input(self, text: str) -> Tuple[str, Optional[bytes]]:
        """
        Process user input and generate response.

        Uses semantic router for intent detection:
        - "log" intent -> save as journal entry
        - "calendar" intent -> fetch calendar + respond
        - "chat" intent -> search journal + respond
        """
        import asyncio

        # Use semantic router for intent detection
        intent_start_ms = now_ms()
        try:
            if self._intent_router is None:
                self._intent_router = get_intent_router()
            # Run in thread to avoid blocking (embedding API call)
            intent, confidence = await asyncio.to_thread(
                self._intent_router.detect, text, 0.5
            )
        except Exception as e:
            logger.warning(f"Intent router error: {e}, falling back to keywords")
            # Fallback to keyword matching
            text_lower = text.lower()
            if any(kw in text_lower for kw in self.LOG_KEYWORDS):
                intent, confidence = "log", 0.8
            else:
                intent, confidence = "chat", 0.5

        update_request_context(intent=intent)
        log_timing(
            "agent.intent_detection",
            intent_start_ms,
            logger_instance=logger,
            intent=intent,
            confidence=confidence,
        )

        if intent == "log":
            # Extract content (remove common prefixes)
            content = text
            for prefix in ["log my note", "note this down", "remember this", "record this", "note this"]:
                if text.lower().startswith(prefix):
                    content = text[len(prefix):].strip()
                    if content.startswith(":"):
                        content = content[1:].strip()
                    break
            if not content:
                content = text

            result = IntentResult(Intent.LOG_ENTRY, confidence, {"content": content}, text)
            response = await self._handle_log(result)
        else:
            # Both "calendar" and "chat" go through _handle_ask (it detects calendar internally)
            result = IntentResult(Intent.ASK_JOURNAL, confidence, {"query": text, "is_calendar": intent == "calendar"}, text)
            response = await self._handle_ask(result)

        # Add to conversation history
        self.state.conversation_history.append({"role": "user", "content": text})
        self.state.conversation_history.append({"role": "assistant", "content": response})

        # Keep history bounded
        if len(self.state.conversation_history) > 20:
            self.state.conversation_history = self.state.conversation_history[-20:]

        return response, None  # Audio generation handled separately

    async def _handle_log(self, result: IntentResult) -> str:
        """Handle logging a new entry."""
        self.state.mode = AgentMode.LOG
        content = result.entities.get("content", result.original_text)

        # Clean up log-specific phrases from transcript
        for phrase in ["log my note", "note this", "record this", "journal this"]:
            if content.lower().startswith(phrase):
                content = content[len(phrase):].strip()
                if content.startswith(":"): content = content[1:].strip()

        if not content or len(content) < 5:
            return "I didn't catch what you wanted to log. What would you like to note down?"

        # Store in long-term memory via memory_client
        try:
            if self.memory_client:
                write_start_ms = now_ms()
                await self.memory_client.create_journal_memory(
                    user_id=self.user_id,
                    transcript=content,
                    language_code="en-IN",
                    topics=["journal", "chat_entry"]
                )
                log_timing(
                    "agent.log.memory_write",
                    write_start_ms,
                    logger_instance=logger,
                    transcript_chars=len(content),
                )
                return "Got it! I've saved your note. Anything else?"
            else:
                return "Sorry, memory service is not available. Could you try again later?"
        except Exception as e:
            logger.error(f"Error saving entry: {e}")
            return "Sorry, I had trouble saving that. Could you try again?"
    
    async def _handle_ask(self, result: IntentResult) -> str:
        """Handle questions about journal entries and calendar."""
        import asyncio
        self.state.mode = AgentMode.CHAT
        query = result.entities.get("query", result.original_text)

        # Search for relevant entries using Agent Memory Server's long-term memory
        if not self.memory_client:
            return "Sorry, memory service is not available. Could you try again later?"

        # Use intent from semantic router (passed via entities)
        is_calendar_query = result.entities.get("is_calendar", False)

        # Run fetches IN PARALLEL
        parallel_start_ms = now_ms()

        async def fetch_conversation():
            fetch_start_ms = now_ms()
            if self.session_id:
                try:
                    conversation = await self.memory_client.get_conversation_context(
                        session_id=self.session_id,
                        user_id=self.user_id,
                        max_turns=3
                    )
                    log_timing(
                        "agent.fetch_working_memory",
                        fetch_start_ms,
                        logger_instance=logger,
                        chars=len(conversation),
                    )
                    return conversation
                except Exception as e:
                    logger.warning(f"Working memory error: {e}")
            log_timing("agent.fetch_working_memory", fetch_start_ms, logger_instance=logger, chars=0)
            return ""

        async def search_memories():
            # Skip memory search for pure calendar queries (faster)
            search_start_ms = now_ms()
            if is_calendar_query:
                log_timing("agent.search_long_term_memory", search_start_ms, logger_instance=logger, skipped=True, results=0)
                return []
            try:
                memories = await self.memory_client.search_long_term_memory(
                    query=query,
                    user_id=self.user_id,
                    limit=5,
                    distance_threshold=0.8
                )
                log_timing(
                    "agent.search_long_term_memory",
                    search_start_ms,
                    logger_instance=logger,
                    skipped=False,
                    results=len(memories),
                )
                return memories
            except Exception as e:
                logger.warning(f"Memory search error: {e}")
                log_timing("agent.search_long_term_memory", search_start_ms, logger_instance=logger, skipped=False, results=0, error=True)
                return []

        async def fetch_calendar():
            fetch_start_ms = now_ms()
            if is_calendar_query and self.calendar_client:
                try:
                    # Run sync calendar API in thread pool to avoid blocking
                    calendar_context = await asyncio.to_thread(self.calendar_client.get_calendar_context)
                    log_timing(
                        "agent.fetch_calendar",
                        fetch_start_ms,
                        logger_instance=logger,
                        chars=len(calendar_context),
                    )
                    return calendar_context
                except Exception as e:
                    logger.warning(f"Calendar error: {e}")
            log_timing("agent.fetch_calendar", fetch_start_ms, logger_instance=logger, chars=0, skipped=not is_calendar_query)
            return ""

        # Execute in parallel
        conversation_context, memories, calendar_context = await asyncio.gather(
            fetch_conversation(),
            search_memories(),
            fetch_calendar()
        )
        log_timing(
            "agent.parallel_fetch_total",
            parallel_start_ms,
            logger_instance=logger,
            memories=len(memories),
            calendar=bool(calendar_context),
            conversation_chars=len(conversation_context),
        )

        # Build context - only use top result for speed
        journal_context = self._get_top_memory(memories) if memories else ""

        response_start_ms = now_ms()
        response = await self._generate_response(query, journal_context, calendar_context)
        log_timing(
            "agent.generate_response",
            response_start_ms,
            logger_instance=logger,
            journal_context_chars=len(journal_context),
            calendar_context_chars=len(calendar_context),
        )

        self.state.last_entries_shown = [m.get("id", "") for m in memories] if memories else []

        # Save conversation turn in background (fire and forget - don't block response)
        if self.session_id:
            asyncio.create_task(self._save_turn_background(query, response))

        return response

    async def _save_turn_background(self, user_message: str, assistant_response: str):
        """Save conversation turn in background without blocking."""
        try:
            await self.memory_client.save_conversation_turn(
                session_id=self.session_id,
                user_id=self.user_id,
                user_message=user_message,
                assistant_response=assistant_response
            )
        except Exception as e:
            logger.warning(f"Working memory background save error: {e}")

    def _get_top_memory(self, memories: List[Dict[str, Any]]) -> str:
        """Get only the top (most relevant) memory result."""
        if not memories:
            return ""

        memory = memories[0]  # Top result only
        text = memory.get("text", "")
        return text[:200] if len(text) > 200 else text

    async def _generate_response(
        self,
        query: str,
        journal_context: str,
        calendar_context: str = ""
    ) -> str:
        """Generate natural response using Ollama."""
        # Build context
        context_parts = []
        if calendar_context:
            context_parts.append(f"Calendar: {calendar_context}")
        if journal_context:
            context_parts.append(f"Journal: {journal_context}")

        if not context_parts:
            return "I don't have any entries matching that. Try recording something first!"

        context = "\n".join(context_parts)

        prompt = f"""You are a voice journal assistant. Answer briefly in 1-2 sentences.

Context:
{context}

User question: {query}

Answer:"""

        try:
            ollama_start_ms = now_ms()
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "num_predict": 100,  # Keep responses short
                            "temperature": 0.7
                        }
                    },
                    timeout=30.0
                )
                data = response.json()
                log_timing(
                    "agent.ollama_round_trip",
                    ollama_start_ms,
                    logger_instance=logger,
                    model=OLLAMA_MODEL,
                    response_chars=len(data.get("response", "").strip()),
                )
                return data.get("response", "").strip()
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            # Fallback to simple response
            if calendar_context:
                return f"Here's your schedule: {calendar_context}"
            if journal_context:
                return f"From your journal: {journal_context}"
            return "Sorry, I couldn't process that."

    def get_mode(self) -> str:
        """Get current agent mode."""
        return self.state.mode.value

    def set_mode(self, mode: str) -> None:
        """Set agent mode."""
        if mode == "log":
            self.state.mode = AgentMode.LOG
        elif mode == "chat":
            self.state.mode = AgentMode.CHAT
