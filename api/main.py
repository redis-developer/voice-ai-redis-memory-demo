"""FastAPI backend for Voice Journal UI."""
import os
import sys
import logging
import base64
import json
import hmac
import hashlib
import tempfile
import uuid
import asyncio
import time
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

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
from src.observability import now_ms, log_timing, set_request_context, reset_request_context, update_request_context

# Global clients
memory_client: Optional[MemoryClient] = None
calendar_client: Optional[CalendarClient] = None
agents: Dict[str, VoiceJournalAgent] = {}  # (user_id, session_id) -> agent
google_auth_request = GoogleAuthRequest()

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

allow_all_origins = "*" in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if allow_all_origins else (cors_origins or ["http://localhost:3000"]),
    allow_origin_regex=r"https?://.*" if allow_all_origins else None,
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


class GoogleAuthRequestBody(BaseModel):
    credential: str


class GoogleAuthResponse(BaseModel):
    provider: str = "google"
    user_id: str
    google_sub: str
    session_token: str
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None


def build_google_user_id(google_sub: str) -> str:
    return f"google_{google_sub}"


def get_google_client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def _get_session_signing_secret() -> str:
    return (
        os.getenv("APP_AUTH_SECRET", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("GOOGLE_CLIENT_ID", "").strip()
    )


def _decode_token_part(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_session_token(user_id: str, google_sub: str, expires_in_seconds: int = 60 * 60 * 24 * 7) -> str:
    secret = _get_session_signing_secret()
    if not secret:
        raise HTTPException(status_code=500, detail="Session signing secret is not configured")

    payload = {
        "user_id": user_id,
        "google_sub": google_sub,
        "exp": int(time.time()) + expires_in_seconds,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("utf-8").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    signature_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    return f"{payload_b64}.{signature_b64}"


def verify_session_token(session_token: str) -> Dict[str, str]:
    secret = _get_session_signing_secret()
    if not secret:
        raise HTTPException(status_code=500, detail="Session signing secret is not configured")

    try:
        payload_b64, signature_b64 = session_token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid session token") from exc

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    actual_signature = _decode_token_part(signature_b64)
    if not hmac.compare_digest(actual_signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid session token")

    try:
        payload = json.loads(_decode_token_part(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid session token") from exc

    user_id = payload.get("user_id")
    google_sub = payload.get("google_sub")
    exp = payload.get("exp")
    if not user_id or not google_sub or not exp:
        raise HTTPException(status_code=401, detail="Invalid session token")
    if int(exp) < int(time.time()):
        raise HTTPException(status_code=401, detail="Session token expired")

    return {
        "user_id": str(user_id),
        "google_sub": str(google_sub),
    }


def get_authenticated_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Google session")
    return verify_session_token(authorization.removeprefix("Bearer ").strip())


@app.post("/api/auth/google", response_model=GoogleAuthResponse)
async def google_auth(request: GoogleAuthRequestBody):
    """Verify a Google ID token and return a stable app user id."""
    client_id = get_google_client_id()
    if not client_id:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID is not configured")

    try:
        claims = id_token.verify_oauth2_token(
            request.credential,
            google_auth_request,
            audience=client_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Google credential") from exc
    except Exception as exc:
        logger.warning(f"Google auth verification failed: {exc}")
        raise HTTPException(status_code=401, detail="Unable to verify Google credential") from exc

    google_sub = claims.get("sub")
    if not google_sub:
        raise HTTPException(status_code=401, detail="Google credential missing subject")

    user_id = build_google_user_id(google_sub)
    session_token = create_session_token(user_id=user_id, google_sub=google_sub)
    return GoogleAuthResponse(
        user_id=user_id,
        google_sub=google_sub,
        session_token=session_token,
        email=claims.get("email"),
        name=claims.get("name"),
        picture=claims.get("picture"),
    )


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
async def transcribe_audio(
    payload: TranscribeRequest,
    http_request: Request,
    auth_user: Dict[str, str] = Depends(get_authenticated_user),
):
    """Transcribe audio using Sarvam AI and store in memory."""
    user_id = auth_user["user_id"]
    request_id = get_request_id(http_request)
    token = set_request_context(
        request_id=request_id,
        route="/api/transcribe",
        user_id=user_id,
    )
    total_start_ms = now_ms()
    timings_ms: Dict[str, float] = {}
    try:
        # Decode base64 audio
        audio_data = base64.b64decode(payload.audio_base64)

        # Browser MediaRecorder sends webm/opus, not WAV
        # Detect format by checking magic bytes
        is_wav = audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE'
        stt_start_ms = now_ms()

        if is_wav:
            # WAV format: use WebSocket streaming for lower latency
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_data)
                temp_path = f.name

            try:
                transcript, language_code = await audio_handler.transcribe_stream(
                    temp_path, language_code=payload.language_code
                )
                timings_ms["stt_stream"] = log_timing("api.transcribe.stt_stream", stt_start_ms, logger_instance=logger)
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
                    temp_path, language_code=payload.language_code
                )
                timings_ms["stt_rest"] = log_timing("api.transcribe.stt_rest", stt_start_ms, logger_instance=logger)
            finally:
                os.unlink(temp_path)

        # Generate session ID if not provided
        session_id = payload.session_id or str(uuid.uuid4())
        update_request_context(session_id=session_id)

        # Store in Redis Agent Memory Server (long-term memory for retrieval)
        memory_entry = None
        if payload.store_in_memory and memory_client:
            try:
                memory_start_ms = now_ms()
                memory_entry = await memory_client.create_journal_memory(
                    user_id=user_id,
                    transcript=transcript,
                    language_code=language_code,
                    topics=["journal", "voice_entry"],
                    session_id=session_id
                )
                timings_ms["memory_write"] = log_timing("api.transcribe.memory_write", memory_start_ms, logger_instance=logger)
                logger.info(f"Stored voice entry in long-term memory: {memory_entry.get('memory_id', 'unknown')}")
            except Exception as mem_err:
                logger.warning(f"Failed to store in memory: {mem_err}")

        timings_ms["total"] = log_timing("api.transcribe.total", total_start_ms, logger_instance=logger)

        return {
            "transcript": transcript,
            "language_code": language_code,
            "session_id": session_id,
            "stored_in_memory": memory_entry is not None,
            "memory_entry": memory_entry,
            "request_id": request_id,
            "timings_ms": timings_ms,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        reset_request_context(token)


@app.post("/api/tts")
def text_to_speech(text: str, language_code: str = "en-IN", speaker: str = "shubh"):
    """Convert text to speech using Sarvam AI."""
    try:
        audio_bytes = audio_handler.text_to_speech(text, language_code, speaker)
        return {"audio_base64": base64.b64encode(audio_bytes).decode()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entries")
def list_entries(limit: int = 50, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """List journal entries."""
    entries = journal_manager.list_entries(auth_user["user_id"], limit=limit)
    return {"entries": entries, "total": len(entries)}


@app.get("/api/entries/{entry_id}")
def get_entry(entry_id: str, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Get a specific entry."""
    entry = journal_manager.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.get("user_id") != auth_user["user_id"]:
        raise HTTPException(status_code=403, detail="Entry does not belong to the signed-in user")
    return entry


@app.post("/api/entries")
async def create_entry(entry: EntryCreate, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Create a new journal entry and store in memory."""
    user_id = auth_user["user_id"]
    # Generate session ID if not provided
    session_id = entry.session_id or str(uuid.uuid4())

    # Store in Redis Agent Memory Server
    if memory_client:
        try:
            await memory_client.add_journal_entry(
                session_id=session_id,
                user_id=user_id,
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
        user_id=user_id,
        transcript=entry.transcript,
        language_code=entry.language_code,
        mood=entry.mood,
        tags=entry.tags
    )
    new_entry["session_id"] = session_id
    return new_entry


@app.get("/api/memory/session/{session_id}")
async def get_session_history(session_id: str, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Get conversation history from memory for a session."""
    if not memory_client:
        raise HTTPException(status_code=503, detail="Memory server not available")

    try:
        history = await memory_client.get_session_history(session_id, auth_user["user_id"])
        return {"session_id": session_id, "messages": history, "count": len(history)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/memory/session/{session_id}")
async def end_session(session_id: str, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """End a session and cleanup working memory."""
    if not memory_client:
        raise HTTPException(status_code=503, detail="Memory server not available")

    try:
        await memory_client.get_session_history(session_id, auth_user["user_id"])
        return {"status": "session_verified", "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/entries/{entry_id}")
def update_entry(entry_id: str, entry: EntryUpdate, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Update a journal entry."""
    existing_entry = journal_manager.get_entry(entry_id)
    if not existing_entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if existing_entry.get("user_id") != auth_user["user_id"]:
        raise HTTPException(status_code=403, detail="Entry does not belong to the signed-in user")

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
def delete_entry(entry_id: str, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Delete a journal entry."""
    existing_entry = journal_manager.get_entry(entry_id)
    if not existing_entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if existing_entry.get("user_id") != auth_user["user_id"]:
        raise HTTPException(status_code=403, detail="Entry does not belong to the signed-in user")

    success = journal_manager.delete_entry(entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "deleted"}


@app.get("/api/analytics")
def get_analytics(auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Get analytics for user."""
    user_id = auth_user["user_id"]
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


@app.post("/api/mood")
async def save_mood(request: MoodRequest, auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Save user's current mood to memory."""
    if not memory_client:
        raise HTTPException(status_code=503, detail="Memory server not available")

    result = await memory_client.save_mood(
        user_id=auth_user["user_id"],
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
    request_id: Optional[str] = None
    timings_ms: Optional[Dict[str, float]] = None


def get_request_id(http_request: Request) -> str:
    """Return the client request id or generate one."""
    return http_request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"


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
async def agent_chat(
    payload: AgentChatRequest,
    http_request: Request,
    auth_user: Dict[str, str] = Depends(get_authenticated_user),
):
    """
    Main agent endpoint for voice journal interaction.

    Accepts either text or audio input.
    Returns response text and optional TTS audio.
    """
    timings_ms: Dict[str, float] = {}
    request_id = get_request_id(http_request)
    total_start_ms = now_ms()

    user_id = auth_user["user_id"]
    text = payload.text
    transcribed_text = None  # Track what was transcribed from audio
    session_id = payload.session_id or f"session_{uuid.uuid4().hex[:12]}"
    token = set_request_context(
        request_id=request_id,
        route="/api/agent/chat",
        user_id=user_id,
        session_id=session_id,
    )

    # If audio provided, transcribe first
    if payload.audio_base64 and not text:
        try:
            stt_start_ms = now_ms()
            audio_data = base64.b64decode(payload.audio_base64)

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
                        temp_path, language_code=payload.language_code
                    )
                    text = transcript
                    transcribed_text = transcript
                    timings_ms['stt_stream'] = log_timing("api.agent_chat.stt_stream", stt_start_ms, logger_instance=logger, transcript_chars=len(transcript))
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
                        temp_path, language_code=payload.language_code
                    )
                    text = transcript
                    transcribed_text = transcript
                    timings_ms['stt_rest'] = log_timing("api.agent_chat.stt_rest", stt_start_ms, logger_instance=logger, transcript_chars=len(transcript), language_code=lang_code)
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

    # Get agent and process
    agent = get_or_create_agent(user_id, session_id)

    try:
        agent_start_ms = now_ms()
        response_text, _ = await agent.process_input(text)
        timings_ms['agent'] = log_timing("api.agent_chat.agent", agent_start_ms, logger_instance=logger, mode=agent.get_mode())

        # Get TTS audio for response (using streaming for faster first-byte)
        audio_base64 = None
        try:
            tts_start_ms = now_ms()
            # Use streaming TTS for lower latency
            audio_bytes = await audio_handler.text_to_speech_stream_full(
                response_text, "en-IN", "shubh"
            )
            audio_base64 = base64.b64encode(audio_bytes).decode()
            timings_ms['tts_stream'] = log_timing("api.agent_chat.tts_stream", tts_start_ms, logger_instance=logger, response_chars=len(response_text))
        except Exception as tts_err:
            logger.warning(f"TTS streaming failed, trying non-streaming: {tts_err}")
            # Fallback to non-streaming
            try:
                fallback_tts_start_ms = now_ms()
                audio_bytes = audio_handler.text_to_speech(response_text, "en-IN", "shubh")
                audio_base64 = base64.b64encode(audio_bytes).decode()
                timings_ms['tts_fallback'] = log_timing("api.agent_chat.tts_fallback", fallback_tts_start_ms, logger_instance=logger, response_chars=len(response_text))
            except Exception as e2:
                logger.error(f"TTS fallback also failed: {e2}")

        # Entry count not tracked (store removed)
        entry_count = 0

        # Infer intent from mode (semantic router already detected it in process_input)
        intent_str = agent.state.mode.value  # "log" or "chat"

        timings_ms['total'] = log_timing(
            "api.agent_chat.total",
            total_start_ms,
            logger_instance=logger,
            has_audio=bool(payload.audio_base64),
            mode=agent.get_mode(),
        )

        return AgentChatResponse(
            response=response_text,
            intent=intent_str,
            mode=agent.get_mode(),
            audio_base64=audio_base64,
            entry_count=entry_count,
            session_id=session_id,
            transcribed_text=transcribed_text,
            request_id=request_id,
            timings_ms=timings_ms,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        reset_request_context(token)


@app.post("/api/agent/chat/stream")
async def agent_chat_stream(
    payload: AgentChatRequest,
    http_request: Request,
    auth_user: Dict[str, str] = Depends(get_authenticated_user),
):
    """
    Streaming version of agent chat - streams TTS audio chunks as they arrive.

    First sends JSON metadata (response text, intent, etc.), then streams audio chunks.
    Uses multipart response: first part is JSON, subsequent parts are audio chunks.
    """
    import json

    request_id = get_request_id(http_request)
    total_start_ms = now_ms()
    timings_ms: Dict[str, float] = {}

    user_id = auth_user["user_id"]
    text = payload.text
    transcribed_text = None
    session_id = payload.session_id or f"session_{uuid.uuid4().hex[:12]}"
    token = set_request_context(
        request_id=request_id,
        route="/api/agent/chat/stream",
        user_id=user_id,
        session_id=session_id,
    )

    # If audio provided, transcribe first (same as non-streaming endpoint)
    if payload.audio_base64 and not text:
        try:
            stt_start_ms = now_ms()
            audio_data = base64.b64decode(payload.audio_base64)
            is_wav = audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE'

            if is_wav:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio_data)
                    temp_path = f.name
                try:
                    text, _ = await audio_handler.transcribe_stream(temp_path, language_code="en-IN")
                    timings_ms["stt_stream"] = log_timing("api.agent_chat_stream.stt_stream", stt_start_ms, logger_instance=logger, transcript_chars=len(text or ""))
                finally:
                    os.unlink(temp_path)
            else:
                with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                    f.write(audio_data)
                    temp_path = f.name
                try:
                    text, _, _ = audio_handler.transcribe(temp_path)
                    timings_ms["stt_rest"] = log_timing("api.agent_chat_stream.stt_rest", stt_start_ms, logger_instance=logger, transcript_chars=len(text or ""))
                finally:
                    os.unlink(temp_path)
            transcribed_text = text
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Audio transcription failed: {e}")

    if not text:
        raise HTTPException(status_code=400, detail="Either text or audio_base64 is required")

    agent = get_or_create_agent(user_id, session_id)

    # Get agent response (text only)
    agent_start_ms = now_ms()
    response_text, _ = await agent.process_input(text)
    timings_ms["agent"] = log_timing("api.agent_chat_stream.agent", agent_start_ms, logger_instance=logger, mode=agent.get_mode())
    entry_count = 0  # Entry count not tracked (store removed)
    intent_str = agent.state.mode.value

    async def generate_stream():
        """Generator that yields JSON metadata then audio chunks."""
        try:
            # First, yield JSON metadata as a line
            metadata = {
                "type": "metadata",
                "response": response_text,
                "intent": intent_str,
                "mode": agent.get_mode(),
                "entry_count": entry_count,
                "session_id": session_id,
                "transcribed_text": transcribed_text,
                "request_id": request_id,
                "timings_ms": {
                    **timings_ms,
                    "pre_tts_total": log_timing(
                        "api.agent_chat_stream.pre_tts_total",
                        total_start_ms,
                        logger_instance=logger,
                        has_audio=bool(payload.audio_base64),
                        mode=agent.get_mode(),
                    ),
                },
            }
            yield json.dumps(metadata).encode() + b"\n"

            # Then stream TTS audio chunks
            tts_start_ms = now_ms()
            async for chunk in audio_handler.text_to_speech_stream(response_text, "en-IN", "shubh"):
                # Yield audio chunk with a simple prefix to identify it
                yield b"AUDIO:" + base64.b64encode(chunk) + b"\n"
            timings_ms["tts_stream"] = log_timing("api.agent_chat_stream.tts_stream", tts_start_ms, logger_instance=logger, response_chars=len(response_text))
        except Exception as e:
            logger.error(f"TTS stream error: {e}")
            # Yield error message
            yield json.dumps({"type": "error", "message": str(e)}).encode() + b"\n"
        finally:
            # Signal end of stream
            yield json.dumps({
                "type": "done",
                "request_id": request_id,
                "timings_ms": {
                    **timings_ms,
                    "total": log_timing(
                        "api.agent_chat_stream.total",
                        total_start_ms,
                        logger_instance=logger,
                        has_audio=bool(payload.audio_base64),
                        mode=agent.get_mode(),
                    ),
                },
            }).encode() + b"\n"
            reset_request_context(token)

    return StreamingResponse(
        generate_stream(),
        media_type="text/plain",
        headers={
            "X-Content-Type-Options": "nosniff",
            "X-Request-ID": request_id,
        }
    )


@app.get("/api/agent/mode")
def get_agent_mode(session_id: str = "default_session", auth_user: Dict[str, str] = Depends(get_authenticated_user)):
    """Get current agent mode."""
    user_id = auth_user["user_id"]
    agent = get_or_create_agent(user_id, session_id)
    return {"mode": agent.get_mode(), "user_id": user_id}


@app.post("/api/agent/mode")
def set_agent_mode(
    session_id: str = "default_session",
    mode: str = "log",
    auth_user: Dict[str, str] = Depends(get_authenticated_user),
):
    """Set agent mode (log or chat)."""
    if mode not in ("log", "chat"):
        raise HTTPException(status_code=400, detail="Mode must be 'log' or 'chat'")

    user_id = auth_user["user_id"]
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
