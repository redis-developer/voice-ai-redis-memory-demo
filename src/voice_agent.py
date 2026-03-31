"""Voice Journal Agent - Main conversational agent.

Handles:
- Mode switching (Log mode vs Chat mode)
- Context assembly from retrieved entries
- Natural voice-first response generation via OpenAI
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
from src.observability import now_ms, log_timing, update_request_context

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")


class AgentMode(Enum):
    LOG = "log"      # User is logging entries
    CHAT = "chat"    # User is querying/chatting about entries


@dataclass
class AgentState:
    """Tracks agent conversation state."""
    mode: AgentMode = AgentMode.LOG
    last_entries_shown: List[str] = field(default_factory=list)


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

    # Explicit log keywords; all other queries go through memory-backed chat.
    LOG_KEYWORDS = ["log my note", "note this", "record this", "journal this", "save this", "remember this"]
    GREETING_PREFIXES = (
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
    )

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

        Uses explicit log keywords for note-taking.
        All non-log queries go through memory-backed chat.
        """
        intent_start_ms = now_ms()
        text_lower = text.lower()
        if any(kw in text_lower for kw in self.LOG_KEYWORDS):
            intent, confidence = "log", 0.8
        else:
            intent, confidence = "chat", 1.0

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
            result = IntentResult(Intent.ASK_JOURNAL, confidence, {"query": text, "is_calendar": False}, text)
            response = await self._handle_ask(result)

        return response, None  # Audio generation handled separately

    async def _handle_log(self, result: IntentResult) -> str:
        """Handle logging a new entry."""
        self.state.mode = AgentMode.LOG
        original_text = result.original_text
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
            if not self.memory_client:
                raise RuntimeError("Redis Agent Memory Server client is not configured")

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
            response = "Got it! I've saved your note. Anything else?"
            if self.session_id:
                await self.memory_client.save_conversation_turn(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    user_message=original_text,
                    assistant_response=response,
                )
            return response
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
            raise RuntimeError("Redis Agent Memory Server client is not configured")

        # Use intent from semantic router (passed via entities)
        is_calendar_query = result.entities.get("is_calendar", False)

        # Run fetches IN PARALLEL
        parallel_start_ms = now_ms()

        async def fetch_conversation():
            fetch_start_ms = now_ms()
            if self.session_id:
                conversation = await self.memory_client.get_conversation_context(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    max_turns=None,
                )
                log_timing(
                    "agent.fetch_working_memory",
                    fetch_start_ms,
                    logger_instance=logger,
                    chars=len(conversation),
                )
                return conversation
            log_timing("agent.fetch_working_memory", fetch_start_ms, logger_instance=logger, chars=0)
            return ""

        async def search_memories():
            # Skip memory search for pure calendar queries (faster)
            search_start_ms = now_ms()
            if is_calendar_query:
                log_timing("agent.search_long_term_memory", search_start_ms, logger_instance=logger, skipped=True, results=0)
                return []
            memories = await self.memory_client.search_long_term_memory(
                query=query,
                user_id=self.user_id,
                limit=5,
                distance_threshold=0.6
            )
            log_timing(
                "agent.search_long_term_memory",
                search_start_ms,
                logger_instance=logger,
                skipped=False,
                results=len(memories),
            )
            return memories

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

        journal_context = self._format_memory_context(memories)

        response_start_ms = now_ms()
        response = await self._generate_response(
            query=query,
            conversation_context=conversation_context,
            journal_context=journal_context,
            calendar_context=calendar_context,
        )
        response = self._sanitize_memory_claims(
            query=query,
            response=response,
            has_journal_memories=bool(journal_context),
        )
        log_timing(
            "agent.generate_response",
            response_start_ms,
            logger_instance=logger,
            conversation_context_chars=len(conversation_context),
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

    def _format_memory_context(self, memories: List[Dict[str, Any]], max_memories: int = 3) -> str:
        """Format the most relevant long-term memories for prompting."""
        if not memories:
            return ""

        lines = []
        for memory in memories[:max_memories]:
            text = memory.get("text", "").strip()
            if not text:
                continue
            if len(text) > 180:
                text = text[:180] + "..."

            created_at = memory.get("created_at", "")
            label = "Recent entry"
            if created_at:
                try:
                    label = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%b %d")
                except Exception:
                    label = "Recent entry"

            lines.append(f"{label}: {text}")

        return "\n".join(lines)

    def _is_greeting(self, text: str) -> bool:
        """Return True for short greeting-only messages."""
        normalized = " ".join(text.lower().strip().split())
        return normalized in self.GREETING_PREFIXES

    def _sanitize_memory_claims(self, query: str, response: str, has_journal_memories: bool) -> str:
        """Prevent contradictory answers about saved memories."""
        if not has_journal_memories:
            return response

        normalized = response.lower()
        contradictory_phrases = (
            "first conversation",
            "first time",
            "don't have any entries",
            "do not have any entries",
            "no saved journal",
            "memory doesn't exist",
            "memory does not exist",
            "doesn't exist for this user",
            "does not exist for this user",
        )
        if any(phrase in normalized for phrase in contradictory_phrases):
            if self._is_greeting(query):
                return "Hello! I can see your saved journal entries and I'm ready to help with them. You can ask about past notes, summaries, or patterns."
            return "I found saved journal entries for you and can use them to help answer questions. What would you like to know about them?"

        return response

    async def _generate_response(
        self,
        query: str,
        conversation_context: str,
        journal_context: str,
        calendar_context: str = ""
    ) -> str:
        """Generate natural response using OpenAI."""
        # Build context
        context_parts = []
        if conversation_context:
            context_parts.append(f"Current session conversation:\n{conversation_context}")
        if calendar_context:
            context_parts.append(f"Calendar: {calendar_context}")
        if journal_context:
            context_parts.append(f"Saved journal memories:\n{journal_context}")

        context = "\n\n".join(context_parts) if context_parts else "No prior session conversation or saved journal memories are available for this turn."

        prompt = f"""You are a friendly voice journal assistant speaking to the user in a warm, natural, conversational way.

Your job:
- respond directly to the user's latest message
- use the current session conversation for continuity
- use saved long-term memories when they are relevant
- sound supportive, clear, and human
- keep responses concise unless the user asks for more detail

You may be given:
- Current session conversation: recent turns from this ongoing session
- Saved memories: relevant long-term journal memories from past sessions
- User message: the latest thing the user said

Behavior rules:
- Prioritize the user's latest message.
- Use current session conversation first for short-term continuity.
- Use saved memories only when they genuinely help answer or enrich the response.
- Never claim a memory does not exist if saved memories are present in context.
- If memory is uncertain or not clearly relevant, do not force it.
- Do not mention "context", "working memory", "long-term memory", or internal system behavior.
- Do not invent facts, memories, or events not present in the provided information.
- If the user is greeting you, greet them naturally and offer help without making awkward claims like "this is our first conversation" unless that is explicitly supported.
- If the user asks about something from the past, use saved memories carefully and speak as a helpful assistant, not as a database.
- If the user logs a note, acknowledge it naturally and briefly.

Tone:
- warm
- calm
- encouraging
- natural spoken language
- not robotic
- not overly verbose

Response style:
- usually 1 to 3 short paragraphs or 1 to 4 sentences
- ask a gentle follow-up question when helpful
- if the user seems emotional, respond with empathy first

Context:
{context}

User message:
{query}

Now respond to the user naturally."""

        try:
            if not OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY is not configured")

            openai_start_ms = now_ms()
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    json={
                        "model": OPENAI_CHAT_MODEL,
                        "input": prompt,
                        "temperature": 0.7,
                        "max_output_tokens": 180,
                    },
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    timeout=45.0,
                )
                response.raise_for_status()
                data = response.json()
                text = (data.get("output_text") or "").strip()
                if not text:
                    output = data.get("output", [])
                    parts = []
                    for item in output:
                        for content in item.get("content", []):
                            if content.get("type") == "output_text":
                                parts.append(content.get("text", ""))
                    text = "".join(parts).strip()
                log_timing(
                    "agent.openai_round_trip",
                    openai_start_ms,
                    logger_instance=logger,
                    model=OPENAI_CHAT_MODEL,
                    response_chars=len(text),
                )
                if text:
                    return text
                raise RuntimeError("OpenAI returned an empty response")
        except Exception as e:
            logger.exception("OpenAI response generation failed")
            # Fallback to simple response
            if conversation_context:
                return "I’m having trouble generating a full reply right now, but I do have our current conversation in context. Could you try that once more?"
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
