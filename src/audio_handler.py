"""Audio handling for STT and TTS using Sarvam AI."""
import os
import wave
import base64
import tempfile
import subprocess
import asyncio
import logging
from datetime import datetime
from typing import Optional, Tuple, AsyncGenerator
import pyaudio
from sarvamai import SarvamAI, AsyncSarvamAI, AudioOutput, EventResponse
from sarvamai.core.api_error import ApiError
from dotenv import load_dotenv
from src.observability import now_ms, log_timing

load_dotenv()

logger = logging.getLogger(__name__)


class AudioHandler:
    """Handles audio recording, STT, and TTS using Sarvam AI."""

    # Audio recording settings
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000  # 16kHz for speech
    CHUNK = 1024

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SARVAM_API_KEY")
        self.client = SarvamAI(api_subscription_key=self.api_key)
        self.async_client = AsyncSarvamAI(api_subscription_key=self.api_key)
        self.recordings_dir = "recordings"
        os.makedirs(self.recordings_dir, exist_ok=True)
    
    def record_audio(self, duration: int = 5, show_countdown: bool = True) -> str:
        """
        Record audio from microphone.
        
        Args:
            duration: Recording duration in seconds
            show_countdown: Whether to show countdown before recording
            
        Returns:
            Path to the recorded audio file
        """
        if show_countdown:
            import time
            print(f"\n🎤 Recording will start in 3 seconds...")
            for i in range(3, 0, -1):
                print(f"⏳ {i}...")
                time.sleep(1)
        
        print(f"🔴 RECORDING ({duration}s) - Speak now!")
        
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK
        )
        
        frames = []
        for i in range(0, int(self.RATE / self.CHUNK * duration)):
            data = stream.read(self.CHUNK)
            frames.append(data)
        
        stream.stop_stream()
        stream.close()
        audio.terminate()
        
        # Save to file with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(self.recordings_dir, f"recording_{timestamp}.wav")
        
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(audio.get_sample_size(self.FORMAT))
            wf.setframerate(self.RATE)
            wf.writeframes(b''.join(frames))
        
        print(f"✅ Recording saved: {filename}")
        return filename
    
    def transcribe(
        self, 
        audio_file: str, 
        mode: str = "transcribe",
        language_code: Optional[str] = None
    ) -> Tuple[str, str, str]:
        """
        Transcribe audio using Sarvam AI Saaras v3.
        
        Args:
            audio_file: Path to audio file
            mode: transcribe, translate, verbatim, or transliterate
            language_code: Optional language code hint
            
        Returns:
            Tuple of (transcript, language_code, request_id)
        """
        try:
            with open(audio_file, "rb") as f:
                kwargs = {"file": f, "model": "saaras:v3", "mode": mode}
                if language_code:
                    kwargs["language_code"] = language_code
                response = self.client.speech_to_text.transcribe(**kwargs)
            
            return response.transcript, response.language_code, response.request_id
            
        except ApiError as e:
            raise Exception(f"STT API Error {e.status_code}: {e.body}")
    
    def text_to_speech(
        self,
        text: str,
        language_code: str = "en-IN",
        speaker: str = "shubh"
    ) -> bytes:
        """
        Convert text to speech using Sarvam AI Bulbul v3.
        
        Args:
            text: Text to convert (max 1500 chars)
            language_code: Target language code
            speaker: Speaker voice name
            
        Returns:
            Audio bytes (WAV format)
        """
        if len(text) > 1500:
            text = text[:1500]  # Truncate to max length
        
        response = self.client.text_to_speech.convert(
            target_language_code=language_code,
            text=text,
            model="bulbul:v3",
            speaker=speaker
        )
        
        audio_base64 = response.audios[0]
        return base64.b64decode(audio_base64)
    
    async def text_to_speech_stream(
        self,
        text: str,
        language_code: str = "en-IN",
        speaker: str = "shubh"
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream text-to-speech using WebSocket for lower latency.

        Yields audio chunks as they are generated, allowing playback
        to start before the full audio is ready.

        Args:
            text: Text to convert
            language_code: Target language code
            speaker: Speaker voice name

        Yields:
            Audio bytes chunks (MP3 format)
        """
        t0 = now_ms()
        first_chunk = True
        chunk_count = 0

        try:
            # IMPORTANT: send_completion_event=True is REQUIRED to receive the "final" event
            # Without it, the WebSocket never signals completion and hangs forever
            async with self.async_client.text_to_speech_streaming.connect(
                model="bulbul:v3",
                send_completion_event=True  # This enables the "final" event signal
            ) as ws:
                # Configure the stream
                await ws.configure(
                    target_language_code=language_code,
                    speaker=speaker,
                    output_audio_codec="mp3",
                    pace=1.1  # Slightly faster for natural conversation
                )

                # Send text for conversion
                await ws.convert(text)
                await ws.flush()

                # Yield audio chunks as they arrive
                # EventResponse with event_type="final" signals completion
                async for message in ws:
                    if isinstance(message, AudioOutput):
                        chunk = base64.b64decode(message.data.audio)
                        chunk_count += 1
                        if first_chunk:
                            log_timing("audio.tts_stream.first_chunk", t0, logger_instance=logger, chunks=chunk_count)
                            first_chunk = False
                        yield chunk
                    elif isinstance(message, EventResponse):
                        # This is the completion signal - break the loop
                        if hasattr(message, 'data') and hasattr(message.data, 'event_type'):
                            if message.data.event_type == "final":
                                logger.info("TTS stream received final event")
                                break

                log_timing("audio.tts_stream.complete", t0, logger_instance=logger, chunks=chunk_count)

        except Exception as e:
            logger.warning(f"TTS stream error: {e}")
            # Fall back to non-streaming TTS
            audio = self.text_to_speech(text, language_code, speaker)
            yield audio

    async def text_to_speech_stream_full(
        self,
        text: str,
        language_code: str = "en-IN",
        speaker: str = "shubh",
        timeout: float = 10.0
    ) -> bytes:
        """
        Stream TTS but return complete audio bytes with timeout protection.

        Uses streaming for faster time-to-first-byte, but collects
        all chunks and returns complete audio.
        """
        t0 = now_ms()
        chunks = []

        try:
            async for chunk in self.text_to_speech_stream(text, language_code, speaker):
                chunks.append(chunk)
                # Check timeout after each chunk
                if (now_ms() - t0) / 1000.0 > timeout:
                    logger.warning(f"TTS stream timeout after {timeout}s with {len(chunks)} chunks")
                    break

            if chunks:
                return b"".join(chunks)
            else:
                # No chunks received, fall back
                raise Exception("No audio chunks received")

        except Exception as e:
            logger.warning(f"TTS stream full error: {e}, falling back to REST API")
            return self.text_to_speech(text, language_code, speaker)

    async def transcribe_stream(
        self,
        audio_file: str,
        mode: str = "transcribe",
        language_code: Optional[str] = None,
        timeout: float = 10.0
    ) -> Tuple[str, str]:
        """
        Transcribe audio using WebSocket streaming for lower latency.

        Args:
            audio_file: Path to audio file
            mode: transcribe, translate, verbatim, or transliterate
            language_code: Optional language code hint
            timeout: Max seconds to wait for response

        Returns:
            Tuple of (transcript, language_code)
        """
        import asyncio
        t0 = now_ms()

        # Read and encode audio file
        with open(audio_file, "rb") as f:
            audio_data = base64.b64encode(f.read()).decode("utf-8")

        try:
            # Build connection params - language_code is REQUIRED for WebSocket API
            # Default to "en-IN" if not provided (auto-detection not supported in streaming)
            connect_params = {
                "model": "saaras:v3",
                "mode": mode,
                "language_code": language_code or "en-IN",  # Required parameter
                "high_vad_sensitivity": True,
                "flush_signal": True,  # Enable manual flush for faster response
            }

            async with self.async_client.speech_to_text_streaming.connect(
                **connect_params
            ) as ws:
                # Send audio for transcription FIRST
                await ws.transcribe(
                    audio=audio_data,
                    encoding="audio/wav",
                    sample_rate=16000
                )

                # Force immediate processing
                await ws.flush()

                log_timing("audio.stt_stream.audio_sent", t0, logger_instance=logger)

                # Use async for iteration with timeout protection
                # This prevents hanging on empty/silent audio
                async def receive_transcript():
                    async for message in ws:
                        log_timing("audio.stt_stream.response_received", t0, logger_instance=logger)

                        # Extract transcript from response
                        # Response has: type='data', data.transcript='...'
                        if hasattr(message, 'data') and hasattr(message.data, 'transcript'):
                            transcript = message.data.transcript
                            detected_lang = getattr(message.data, 'language_code', None) or language_code or "en-IN"
                            if transcript:
                                return transcript, detected_lang
                    return None, None

                try:
                    result = await asyncio.wait_for(receive_transcript(), timeout=timeout)
                    if result[0]:
                        return result
                    raise Exception("Empty transcript received")
                except asyncio.TimeoutError:
                    logger.warning(f"STT stream timeout after {timeout}s - no speech detected")
                    raise Exception("STT timeout - no speech detected in audio")

        except Exception as e:
            logger.warning(f"STT stream error: {e}, falling back to REST API")
            # Fall back to non-streaming STT
            transcript, lang_code, _ = self.transcribe(audio_file, mode, language_code)
            return transcript, lang_code

    def speak(self, text: str, language_code: str = "en-IN", speaker: str = "shubh"):
        """Convert text to speech and play it."""
        audio_bytes = self.text_to_speech(text, language_code, speaker)

        # Save to temp file and play
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        try:
            subprocess.run(["afplay", temp_path], check=True, capture_output=True)
        except Exception:
            print(f"[Audio saved to {temp_path}]")
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
