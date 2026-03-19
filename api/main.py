"""FastAPI backend for Voice Journal UI."""
import os
import sys
import logging
import base64
import tempfile
import uuid
import asyncio
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from src.audio_handler import AudioHandler
from src.journal_manager import JournalManager
from src.analytics import JournalAnalytics
from src.memory_client import MemoryClient
from src.voice_agent import VoiceJournalAgent
from src.calendar_client import CalendarClient

# Global clients
memory_client: Optional[MemoryClient] = None
calendar_client: Optional[CalendarClient] = None
agents: Dict[str, VoiceJournalAgent] = {}  # (user_id, session_id) -> agent

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global memory_client
    memory_client = MemoryClient()

    # Check memory server health
    is_healthy = await memory_client.health_check()
    if is_healthy:
        logger.info("Connected to Redis Agent Memory Server")
    else:
        logger.warning("Redis Agent Memory Server not available - memory features disabled")

    yield
    # Cleanup
    if memory_client:
        await memory_client.close()

app = FastAPI(title="Voice Journal API", version="1.0.0", lifespan=lifespan)

# CORS for frontend
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
audio_handler = AudioHandler()
journal_manager = JournalManager()
analytics = JournalAnalytics()


class TranscribeRequest(BaseModel):
    audio_base64: str
    language_code: Optional[str] = None
    session_id: Optional[str] = None
    user_id: str = "default_user"
    store_in_memory: bool = True


class EntryCreate(BaseModel):
    transcript: str
    language_code: str = "en-IN"
    duration_seconds: float
    mood: Optional[str] = None
    tags: Optional[List[str]] = None
    session_id: Optional[str] = None
    user_id: str = "default_user"


class EntryUpdate(BaseModel):
    transcript: Optional[str] = None
    mood: Optional[str] = None
    tags: Optional[List[str]] = None


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    memory_healthy = await memory_client.health_check() if memory_client else False
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "memory_server": memory_healthy
    }


@app.post("/api/transcribe")
async def transcribe_audio(request: TranscribeRequest):
    """Transcribe audio using Sarvam AI and store in memory."""
    try:
        import time
        stt_start = time.time()

        # Decode base64 audio
        audio_data = base64.b64decode(request.audio_base64)

        # Browser MediaRecorder sends webm/opus, not WAV
        # Detect format by checking magic bytes
        is_wav = audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE'

        if is_wav:
            # WAV format: use WebSocket streaming for lower latency
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_data)
                temp_path = f.name

            try:
                transcript, language_code = await audio_handler.transcribe_stream(
                    temp_path, language_code=request.language_code
                )
                logger.debug(f"STT (streaming): {time.time() - stt_start:.2f}s")
            finally:
                os.unlink(temp_path)
        else:
            # Non-WAV format (browser webm/opus): use REST API (auto-detects format)
            logger.debug(f"Non-WAV audio, first bytes: {audio_data[:12]}, using REST API")

            # Determine extension from magic bytes
            if audio_data[:4] == b'\x1aE\xdf\xa3':  # webm magic bytes
                suffix = ".webm"
            elif audio_data[:3] == b'ID3' or audio_data[:2] == b'\xff\xfb':  # mp3
                suffix = ".mp3"
            elif audio_data[:4] == b'OggS':  # ogg
                suffix = ".ogg"
            else:
                suffix = ".webm"  # default to webm for browser audio

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_data)
                temp_path = f.name

            try:
                # REST API handles format detection automatically
                transcript, language_code, _ = audio_handler.transcribe(
                    temp_path, language_code=request.language_code
                )
                logger.debug(f"STT (REST): {time.time() - stt_start:.2f}s")
            finally:
                os.unlink(temp_path)

        # Generate session ID if not provided
        session_id = request.session_id or str(uuid.uuid4())

        # Store in Redis Agent Memory Server (long-term memory for retrieval)
        memory_entry = None
        if request.store_in_memory and memory_client:
            try:
                memory_entry = await memory_client.create_journal_memory(
                    user_id=request.user_id,
                    transcript=transcript,
                    language_code=language_code,
                    topics=["journal", "voice_entry"],
                    session_id=session_id
                )
                logger.info(f"Stored voice entry in long-term memory: {memory_entry.get('memory_id', 'unknown')}")
            except Exception as mem_err:
                logger.warning(f"Failed to store in memory: {mem_err}")

        return {
            "transcript": transcript,
            "language_code": language_code,
            "session_id": session_id,
            "stored_in_memory": memory_entry is not None,
            "memory_entry": memory_entry
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/tts")
def text_to_speech(text: str, language_code: str = "en-IN", speaker: str = "shubh"):
    """Convert text to speech using Sarvam AI."""
    try:
        audio_bytes = audio_handler.text_to_speech(text, language_code, speaker)
        return {"audio_base64": base64.b64encode(audio_bytes).decode()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entries")
def list_entries(user_id: str = "default_user", limit: int = 50):
    """List journal entries."""
    entries = journal_manager.list_entries(user_id, limit=limit)
    return {"entries": entries, "total": len(entries)}


@app.get("/api/entries/{entry_id}")
def get_entry(entry_id: str):
    """Get a specific entry."""
    entry = journal_manager.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


@app.post("/api/entries")
async def create_entry(entry: EntryCreate):
    """Create a new journal entry and store in memory."""
    # Generate session ID if not provided
    session_id = entry.session_id or str(uuid.uuid4())

    # Store in Redis Agent Memory Server
    if memory_client:
        try:
            await memory_client.add_journal_entry(
                session_id=session_id,
                user_id=entry.user_id,
                transcript=entry.transcript,
                language_code=entry.language_code,
                metadata={
                    "mood": entry.mood,
                    "tags": entry.tags,
                    "duration_seconds": entry.duration_seconds,
                    "source": "manual_entry"
                }
            )
            logger.info(f"Stored entry in memory: {session_id}")
        except Exception as mem_err:
            logger.warning(f"Failed to store in memory: {mem_err}")

    # Also store in journal manager for local persistence
    new_entry = journal_manager.create_entry(
        user_id=entry.user_id,
        transcript=entry.transcript,
        language_code=entry.language_code,
        mood=entry.mood,
        tags=entry.tags
    )
    new_entry["session_id"] = session_id
    return new_entry


@app.get("/api/memory/session/{session_id}")
async def get_session_history(session_id: str, user_id: str = "default_user"):
    """Get conversation history from memory for a session."""
    if not memory_client:
        raise HTTPException(status_code=503, detail="Memory server not available")

    try:
        history = await memory_client.get_session_history(session_id, user_id)
        return {"session_id": session_id, "messages": history, "count": len(history)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/memory/session/{session_id}")
async def end_session(session_id: str):
    """End a session and cleanup working memory."""
    if not memory_client:
        raise HTTPException(status_code=503, detail="Memory server not available")

    try:
        await memory_client.end_session(session_id)
        return {"status": "session_ended", "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/entries/{entry_id}")
def update_entry(entry_id: str, entry: EntryUpdate):
    """Update a journal entry."""
    updated = journal_manager.update_entry(
        entry_id,
        transcript=entry.transcript,
        mood=entry.mood,
        tags=entry.tags
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Entry not found")
    return updated


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: str):
    """Delete a journal entry."""
    success = journal_manager.delete_entry(entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "deleted"}


@app.get("/api/analytics")
def get_analytics(user_id: str = "default_user"):
    """Get analytics for user."""
    return {
        "summary": analytics.get_activity_summary(user_id, days=30),
        "streak": analytics.get_streak(user_id),
        "insights": analytics.generate_insights(user_id),
        "language_distribution": analytics.get_language_distribution(user_id),
        "mood_distribution": analytics.get_mood_distribution(user_id)
    }


# ============ MOOD ENDPOINT ============

class MoodRequest(BaseModel):
    """Request for saving mood."""
    mood: str  # e.g., "Happy", "Sad"
    emoji: str  # e.g., "😊", "😢"
    user_id: str = "default_user"


@app.post("/api/mood")
async def save_mood(request: MoodRequest):
    """Save user's current mood to memory."""
    if not memory_client:
        raise HTTPException(status_code=503, detail="Memory server not available")

    result = await memory_client.save_mood(
        user_id=request.user_id,
        mood=request.mood,
        emoji=request.emoji
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to save mood"))

    return result


# ============ AGENT ENDPOINTS ============

class AgentChatRequest(BaseModel):
    """Request for agent chat endpoint."""
    text: Optional[str] = None
    audio_base64: Optional[str] = None
    user_id: str = "default_user"
    session_id: Optional[str] = None  # For working memory / conversation continuity
    language_code: Optional[str] = None


class AgentChatResponse(BaseModel):
    """Response from agent chat endpoint."""
    response: str
    intent: str
    mode: str
    audio_base64: Optional[str] = None
    entry_count: int
    session_id: str  # Return session_id for frontend to persist
    transcribed_text: Optional[str] = None  # For debugging STT


def get_or_create_agent(user_id: str, session_id: str) -> VoiceJournalAgent:
    """Get or create an agent for a user and session."""
    agent_key = f"{user_id}:{session_id}"
    if agent_key not in agents:
        # Pass memory_client for searching long-term memory (memory_idx)
        # Pass session_id for working memory (conversation continuity)
        agents[agent_key] = VoiceJournalAgent(
            user_id=user_id,
            session_id=session_id,
            memory_client=memory_client
        )
    return agents[agent_key]


@app.post("/api/agent/chat", response_model=AgentChatResponse)
async def agent_chat(request: AgentChatRequest):
    """
    Main agent endpoint for voice journal interaction.

    Accepts either text or audio input.
    Returns response text and optional TTS audio.
    """
    import time
    timings = {}
    total_start = time.time()

    text = request.text
    transcribed_text = None  # Track what was transcribed from audio

    # If audio provided, transcribe first
    if request.audio_base64 and not text:
        try:
            stt_start = time.time()
            audio_data = base64.b64decode(request.audio_base64)

            # Browser MediaRecorder sends webm/opus, not WAV
            # Detect format by checking magic bytes
            is_wav = audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE'

            if is_wav:
                # WAV format: use WebSocket streaming for lower latency
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio_data)
                    temp_path = f.name

                try:
                    transcript, lang_code = await audio_handler.transcribe_stream(
                        temp_path, language_code=request.language_code
                    )
                    text = transcript
                    transcribed_text = transcript
                    timings['stt'] = time.time() - stt_start
                    logger.debug(f"STT (streaming): {timings['stt']:.2f}s - '{transcript}'")
                finally:
                    os.unlink(temp_path)
            else:
                # Non-WAV format (browser webm/opus): use REST API (auto-detects format)
                logger.debug(f"Non-WAV audio, first bytes: {audio_data[:12]}, using REST API")

                # Determine extension from magic bytes
                if audio_data[:4] == b'\x1aE\xdf\xa3':  # webm magic bytes
                    suffix = ".webm"
                elif audio_data[:3] == b'ID3' or audio_data[:2] == b'\xff\xfb':  # mp3
                    suffix = ".mp3"
                elif audio_data[:4] == b'OggS':  # ogg
                    suffix = ".ogg"
                else:
                    suffix = ".webm"  # default to webm for browser audio

                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                    f.write(audio_data)
                    temp_path = f.name

                try:
                    # REST API handles format detection automatically
                    transcript, lang_code, _ = audio_handler.transcribe(
                        temp_path, language_code=request.language_code
                    )
                    text = transcript
                    transcribed_text = transcript
                    detected_language = lang_code  # Use detected language for TTS
                    timings['stt'] = time.time() - stt_start
                    logger.debug(f"STT (REST): {timings['stt']:.2f}s - '{transcript}' [lang={lang_code}]")
                finally:
                    os.unlink(temp_path)

        except Exception as e:
            import traceback
            traceback.print_exc()
            # Check for common issues
            error_msg = str(e).lower()
            if "timeout" in error_msg or "no speech" in error_msg:
                raise HTTPException(status_code=400, detail="No speech detected in audio. Please try speaking again.")
            raise HTTPException(status_code=400, detail=f"Audio transcription failed: {e}")

    if not text:
        raise HTTPException(status_code=400, detail="No speech detected. Please try speaking again.")

    # Generate session_id if not provided (for working memory / conversation continuity)
    session_id = request.session_id or f"session_{uuid.uuid4().hex[:12]}"

    # Get agent and process
    agent = get_or_create_agent(request.user_id, session_id)

    try:
        agent_start = time.time()
        response_text, _ = await agent.process_input(text)
        timings['agent'] = time.time() - agent_start
        logger.debug(f"Agent processing: {timings['agent']:.2f}s")

        # Get TTS audio for response (using streaming for faster first-byte)
        audio_base64 = None
        try:
            tts_start = time.time()
            # Use streaming TTS for lower latency
            audio_bytes = await audio_handler.text_to_speech_stream_full(
                response_text, "en-IN", "shubh"
            )
            audio_base64 = base64.b64encode(audio_bytes).decode()
            timings['tts'] = time.time() - tts_start
            logger.debug(f"TTS (streaming): {timings['tts']:.2f}s")
        except Exception as tts_err:
            logger.warning(f"TTS streaming failed, trying non-streaming: {tts_err}")
            # Fallback to non-streaming
            try:
                audio_bytes = audio_handler.text_to_speech(response_text, "en-IN", "shubh")
                audio_base64 = base64.b64encode(audio_bytes).decode()
                timings['tts'] = time.time() - tts_start
                logger.debug(f"TTS (fallback): {timings['tts']:.2f}s")
            except Exception as e2:
                logger.error(f"TTS fallback also failed: {e2}")

        # Entry count not tracked (store removed)
        entry_count = 0

        # Infer intent from mode (semantic router already detected it in process_input)
        intent_str = agent.state.mode.value  # "log" or "chat"

        timings['total'] = time.time() - total_start
        logger.info(f"Request completed: {timings['total']:.2f}s (STT: {timings.get('stt', 0):.2f}s, Agent: {timings.get('agent', 0):.2f}s, TTS: {timings.get('tts', 0):.2f}s)")

        return AgentChatResponse(
            response=response_text,
            intent=intent_str,
            mode=agent.get_mode(),
            audio_base64=audio_base64,
            entry_count=entry_count,
            session_id=session_id,
            transcribed_text=transcribed_text
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agent/chat/stream")
async def agent_chat_stream(request: AgentChatRequest):
    """
    Streaming version of agent chat - streams TTS audio chunks as they arrive.

    First sends JSON metadata (response text, intent, etc.), then streams audio chunks.
    Uses multipart response: first part is JSON, subsequent parts are audio chunks.
    """
    import time
    import json

    text = request.text
    transcribed_text = None

    # If audio provided, transcribe first (same as non-streaming endpoint)
    if request.audio_base64 and not text:
        try:
            audio_data = base64.b64decode(request.audio_base64)
            is_wav = audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE'

            if is_wav:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio_data)
                    temp_path = f.name
                try:
                    text, _ = await audio_handler.transcribe_stream(temp_path, language_code="en-IN")
                finally:
                    os.unlink(temp_path)
            else:
                with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                    f.write(audio_data)
                    temp_path = f.name
                try:
                    text, _ = audio_handler.transcribe(temp_path)
                finally:
                    os.unlink(temp_path)
            transcribed_text = text
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Audio transcription failed: {e}")

    if not text:
        raise HTTPException(status_code=400, detail="Either text or audio_base64 is required")

    session_id = request.session_id or f"session_{uuid.uuid4().hex[:12]}"
    agent = get_or_create_agent(request.user_id, session_id)

    # Get agent response (text only)
    response_text, _ = await agent.process_input(text)
    entry_count = 0  # Entry count not tracked (store removed)
    intent_str = agent.state.mode.value

    async def generate_stream():
        """Generator that yields JSON metadata then audio chunks."""
        # First, yield JSON metadata as a line
        metadata = {
            "type": "metadata",
            "response": response_text,
            "intent": intent_str,
            "mode": agent.get_mode(),
            "entry_count": entry_count,
            "session_id": session_id,
            "transcribed_text": transcribed_text
        }
        yield json.dumps(metadata).encode() + b"\n"

        # Then stream TTS audio chunks
        try:
            async for chunk in audio_handler.text_to_speech_stream(response_text, "en-IN", "shubh"):
                # Yield audio chunk with a simple prefix to identify it
                yield b"AUDIO:" + base64.b64encode(chunk) + b"\n"
        except Exception as e:
            logger.error(f"TTS stream error: {e}")
            # Yield error message
            yield json.dumps({"type": "error", "message": str(e)}).encode() + b"\n"

        # Signal end of stream
        yield json.dumps({"type": "done"}).encode() + b"\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/plain",
        headers={"X-Content-Type-Options": "nosniff"}
    )


@app.get("/api/agent/mode")
def get_agent_mode(user_id: str = "default_user", session_id: str = "default_session"):
    """Get current agent mode."""
    agent = get_or_create_agent(user_id, session_id)
    return {"mode": agent.get_mode(), "user_id": user_id}


@app.post("/api/agent/mode")
def set_agent_mode(user_id: str = "default_user", session_id: str = "default_session", mode: str = "log"):
    """Set agent mode (log or chat)."""
    if mode not in ("log", "chat"):
        raise HTTPException(status_code=400, detail="Mode must be 'log' or 'chat'")

    agent = get_or_create_agent(user_id, session_id)
    agent.set_mode(mode)
    return {"mode": agent.get_mode(), "user_id": user_id}


# Calendar API
class CalendarEvent(BaseModel):
    """Calendar event response model."""
    summary: str
    start_time: str
    end_time: Optional[str] = None
    location: Optional[str] = None
    is_all_day: bool = False


@app.get("/api/calendar/today", response_model=List[CalendarEvent])
async def get_today_events():
    """Get today's calendar events."""
    global calendar_client

    try:
        if calendar_client is None:
            calendar_client = CalendarClient()

        events = calendar_client.get_today_events()

        result = []
        for event in events:
            start = event.get("start")
            end = event.get("end")

            # Format time
            if event.get("is_all_day"):
                start_time = "All day"
                end_time = None
            else:
                if hasattr(start, "strftime"):
                    start_time = start.strftime("%I:%M %p").lstrip("0")
                else:
                    start_time = str(start)

                if end and hasattr(end, "strftime"):
                    end_time = end.strftime("%I:%M %p").lstrip("0")
                else:
                    end_time = None

            result.append(CalendarEvent(
                summary=event.get("summary", "Untitled"),
                start_time=start_time,
                end_time=end_time,
                location=event.get("location"),
                is_all_day=event.get("is_all_day", False)
            ))

        return result

    except FileNotFoundError as e:
        logger.warning(f"Calendar credentials not found: {e}")
        return []
    except Exception as e:
        logger.error(f"Calendar error: {e}")
        return []
