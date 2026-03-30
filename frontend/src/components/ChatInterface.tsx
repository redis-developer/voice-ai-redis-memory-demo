'use client';

import { useState, useRef, useEffect } from 'react';
import { createRequestId, logLatencyTrace, markTime } from '@/lib/latency';
import { getAuthHeaders } from '@/lib/userId';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  audioBase64?: string;
}

interface ChatInterfaceProps {
  isOpen: boolean;
  onClose: () => void;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080';

// Generate a unique session ID
function generateSessionId(): string {
  return `session_${Date.now()}_${Math.random().toString(36).substring(2, 10)}`;
}

// Convert Float32Array PCM to 16-bit PCM WAV
function encodeWAV(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  // WAV header
  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true);  // PCM format
  view.setUint16(22, 1, true);  // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true);  // block align
  view.setUint16(34, 16, true); // bits per sample
  writeString(36, 'data');
  view.setUint32(40, samples.length * 2, true);

  // Convert float samples to 16-bit PCM
  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset += 2;
  }

  return buffer;
}

export default function ChatInterface({ isOpen, onClose }: ChatInterfaceProps) {
  // Generate session_id once when component mounts (for conversation continuity)
  const [sessionId, setSessionId] = useState<string>(() => generateSessionId());

  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      role: 'assistant',
      content: "Hi! I'm your voice journal assistant. You can log notes, ask about past entries, or get summaries. How can I help?",
      timestamp: new Date()
    }
  ]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [mode, setMode] = useState<'log' | 'chat'>('chat');
  const [entryCount, setEntryCount] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Web Audio API refs for WAV recording
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const audioBufferRef = useRef<Float32Array[]>([]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = async (text: string, audioBase64?: string, initialTimings: Record<string, number> = {}) => {
    if (!text.trim() && !audioBase64) return;

    const requestId = createRequestId('chat');
    const timings = { ...initialTimings };
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: text || 'Voice message',
      timestamp: new Date()
    };
    setMessages(prev => [...prev, userMessage]);
    setInputText('');
    setIsLoading(true);

    try {
      // Use streaming endpoint for faster audio playback
      markTime(timings, 'request_sent');
      const response = await fetch(`${API_BASE_URL}/api/agent/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Request-ID': requestId,
          ...getAuthHeaders(),
        },
        body: JSON.stringify({
          text: text || undefined,
          audio_base64: audioBase64,
          session_id: sessionId
        })
      });
      markTime(timings, 'response_headers');

      if (!response.ok) {
        const detail = (await response.text()).trim();
        throw new Error(detail || `HTTP error: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader available');

      const decoder = new TextDecoder();
      let buffer = '';

      // Progressive audio playback using MediaSource API
      let mediaSource: MediaSource | null = null;
      let sourceBuffer: SourceBuffer | null = null;
      let audio: HTMLAudioElement | null = null;
      let audioQueue: Uint8Array[] = [];
      let isSourceOpen = false;
      let firstAudioChunkSeen = false;
      let backendMetadata: Record<string, unknown> | null = null;
      let backendDone: Record<string, unknown> | null = null;

      const initMediaSource = () => {
        mediaSource = new MediaSource();
        audio = new Audio();
        audio.src = URL.createObjectURL(mediaSource);

        mediaSource.addEventListener('sourceopen', () => {
          try {
            // MP3 MIME type for streaming
            sourceBuffer = mediaSource!.addSourceBuffer('audio/mpeg');
            sourceBuffer.mode = 'sequence';
            isSourceOpen = true;

            // Process any queued chunks
            sourceBuffer.addEventListener('updateend', () => {
              if (audioQueue.length > 0 && sourceBuffer && !sourceBuffer.updating) {
                const chunk = audioQueue.shift()!;
                sourceBuffer.appendBuffer(new Uint8Array(chunk).buffer as ArrayBuffer);
              }
            });

            // Append any chunks that arrived before sourceopen
            if (audioQueue.length > 0 && !sourceBuffer.updating) {
              const chunk = audioQueue.shift()!;
              sourceBuffer.appendBuffer(new Uint8Array(chunk).buffer as ArrayBuffer);
            }
          } catch (e) {
            console.error('Failed to create source buffer:', e);
          }
        });
      };

      const appendAudioChunk = (chunk: Uint8Array) => {
        if (!firstAudioChunkSeen) {
          firstAudioChunkSeen = true;
          markTime(timings, 'first_audio_chunk');
        }
        if (!mediaSource) {
          initMediaSource();
        }

        if (isSourceOpen && sourceBuffer && !sourceBuffer.updating) {
          try {
            sourceBuffer.appendBuffer(new Uint8Array(chunk).buffer as ArrayBuffer);
            // Start playback after first chunk
            if (audio && audio.paused) {
              audio.play()
                .then(() => {
                  if (timings.audio_playback_started === undefined) {
                    markTime(timings, 'audio_playback_started');
                  }
                })
                .catch(console.error);
            }
          } catch (e) {
            audioQueue.push(chunk);
          }
        } else {
          audioQueue.push(chunk);
        }
      };

      // Process the stream
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        for (const line of lines) {
          if (!line.trim()) continue;

          if (line.startsWith('AUDIO:')) {
            // Audio chunk - decode and play progressively
            const audioBase64 = line.substring(6);
            const binaryString = atob(audioBase64);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
              bytes[i] = binaryString.charCodeAt(i);
            }
            appendAudioChunk(bytes);
          } else {
            // JSON message (metadata, done, or error)
            try {
              const msg = JSON.parse(line);
              if (msg.type === 'metadata') {
                markTime(timings, 'stream_metadata');
                backendMetadata = msg;
                // Update session_id if returned
                if (msg.session_id && msg.session_id !== sessionId) {
                  setSessionId(msg.session_id);
                }
                // Add assistant message immediately (before audio finishes)
                const assistantMessage: Message = {
                  id: (Date.now() + 1).toString(),
                  role: 'assistant',
                  content: msg.response,
                  timestamp: new Date()
                };
                setMessages(prev => [...prev, assistantMessage]);
                setMode(msg.mode);
                setEntryCount(msg.entry_count);
              } else if (msg.type === 'done') {
                markTime(timings, 'stream_done');
                backendDone = msg;
                // Signal end of stream to MediaSource
                const ms = mediaSource as MediaSource | null;
                if (ms && ms.readyState === 'open') {
                  // Wait for all buffers to be appended before ending
                  const sb = sourceBuffer as SourceBuffer | null;
                  const endStream = () => {
                    if (sb && !sb.updating && audioQueue.length === 0) {
                      try {
                        ms.endOfStream();
                      } catch {
                        // Ignore if already ended
                      }
                    } else {
                      setTimeout(endStream, 50);
                    }
                  };
                  endStream();
                }
                logLatencyTrace('chat.stream', requestId, timings, {
                  backendRequestId: backendMetadata?.request_id ?? backendDone?.request_id ?? null,
                  metadataTimingsMs: backendMetadata?.timings_ms ?? null,
                  doneTimingsMs: backendDone?.timings_ms ?? null,
                  hasAudio: Boolean(audioBase64),
                });
              }
            } catch (e) {
              console.error('Failed to parse message:', line, e);
            }
          }
        }
      }
    } catch (error) {
      console.error('Chat error:', error);
      markTime(timings, 'request_failed');
      logLatencyTrace('chat.stream.error', requestId, timings, { hasAudio: Boolean(audioBase64) });
      const detail = error instanceof Error ? error.message : 'Please try again.';
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `Sorry, I had trouble processing that. ${detail}`,
        timestamp: new Date()
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  const playAudio = (base64: string) => {
    const audio = new Audio(`data:audio/mp3;base64,${base64}`);
    audio.play().catch(console.error);
  };

  const startRecording = async () => {
    try {
      // Request 16kHz mono audio for Sarvam AI
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true
        }
      });
      mediaStreamRef.current = stream;

      // Create AudioContext at 16kHz for Sarvam AI
      const audioContext = new AudioContext({ sampleRate: 16000 });
      audioContextRef.current = audioContext;

      const source = audioContext.createMediaStreamSource(stream);

      // Use ScriptProcessorNode to capture raw PCM data
      // Buffer size 4096 gives good balance of latency and efficiency
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;
      audioBufferRef.current = [];

      processor.onaudioprocess = (e) => {
        const inputData = e.inputBuffer.getChannelData(0);
        // Clone the data since the buffer gets reused
        audioBufferRef.current.push(new Float32Array(inputData));
      };

      source.connect(processor);
      processor.connect(audioContext.destination);

      setIsRecording(true);
    } catch (err) {
      console.error('Recording error:', err);
    }
  };

  const stopRecording = async () => {
    const timings: Record<string, number> = {};
    markTime(timings, 'record_stop');
    setIsRecording(false);

    // Stop the processor and stream
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(t => t.stop());
      mediaStreamRef.current = null;
    }

    // Merge all audio chunks into single Float32Array
    const totalLength = audioBufferRef.current.reduce((acc, chunk) => acc + chunk.length, 0);
    const mergedSamples = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of audioBufferRef.current) {
      mergedSamples.set(chunk, offset);
      offset += chunk.length;
    }

    // Convert to WAV
    const sampleRate = audioContextRef.current?.sampleRate || 16000;
    const wavBuffer = encodeWAV(mergedSamples, sampleRate);
    markTime(timings, 'audio_encoded');

    // Convert to base64
    const base64 = btoa(
      new Uint8Array(wavBuffer).reduce((data, byte) => data + String.fromCharCode(byte), '')
    );

    // Clean up AudioContext
    if (audioContextRef.current) {
      await audioContextRef.current.close();
      audioContextRef.current = null;
    }

    // Send the WAV audio
    sendMessage('', base64, timings);
  };

  // Start a new chat session (reset conversation)
  const startNewChat = () => {
    setSessionId(generateSessionId());
    setMessages([{
      id: '1',
      role: 'assistant',
      content: "Hi! I'm your voice journal assistant. You can log notes, ask about past entries, or get summaries. How can I help?",
      timestamp: new Date()
    }]);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-white/90 backdrop-blur-md rounded-3xl w-full max-w-lg h-[600px] mx-4 flex flex-col shadow-2xl border border-white/50">
        {/* Header */}
        <div className="p-4 border-b border-gray-200/50 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-800">Voice Journal</h2>
            <div className="flex items-center gap-2 mt-1">
              <span className={`px-2 py-0.5 text-xs rounded-full ${mode === 'chat' ? 'bg-purple-100 text-purple-700' : 'bg-green-100 text-green-700'}`}>
                {mode === 'chat' ? 'Chat' : 'Log'} mode
              </span>
              <span className="text-xs text-gray-500">{entryCount} entries</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={startNewChat}
              className="px-3 py-1.5 text-xs bg-purple-100 text-purple-700 rounded-full hover:bg-purple-200 transition-colors"
              title="Start a new conversation"
            >
              New Chat
            </button>
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-full transition-colors">
              <svg className="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[80%] rounded-2xl px-4 py-2 ${
                msg.role === 'user'
                  ? 'bg-purple-500 text-white'
                  : 'bg-gray-100 text-gray-800'
              }`}>
                <p className="text-sm">{msg.content}</p>
                {msg.audioBase64 && msg.role === 'assistant' && (
                  <button
                    onClick={() => playAudio(msg.audioBase64!)}
                    className="mt-2 text-xs flex items-center gap-1 opacity-70 hover:opacity-100"
                  >
                    🔊 Play
                  </button>
                )}
              </div>
            </div>
          ))}
          {isLoading && (
            <div className="flex justify-start">
              <div className="bg-gray-100 rounded-2xl px-4 py-2">
                <div className="flex gap-1">
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '0ms'}}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '150ms'}}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{animationDelay: '300ms'}}></span>
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="p-4 border-t border-gray-200/50">
          <div className="flex items-center gap-2">
            <button
              onClick={isRecording ? stopRecording : startRecording}
              className={`p-3 rounded-full transition-all ${
                isRecording
                  ? 'bg-red-500 text-white animate-pulse'
                  : 'bg-purple-100 text-purple-600 hover:bg-purple-200'
              }`}
            >
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
                <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
              </svg>
            </button>
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && sendMessage(inputText)}
              placeholder={isRecording ? 'Recording...' : 'Type or speak...'}
              disabled={isRecording}
              className="flex-1 px-4 py-2 rounded-full border border-gray-200 focus:outline-none focus:border-purple-400 bg-white/80 text-gray-800 placeholder-gray-400"
            />
            <button
              onClick={() => sendMessage(inputText)}
              disabled={!inputText.trim() || isLoading}
              className="p-3 bg-purple-500 text-white rounded-full hover:bg-purple-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </button>
          </div>
          <div className="mt-2 text-center">
            <span className="text-xs text-gray-400">Try: "Log my note: had a great day" or "What did I say about work?"</span>
          </div>
        </div>
      </div>
    </div>
  );
}
