'use client';

import { useState } from 'react';
import RecordButton from './RecordButton';
import { createRequestId, logLatencyTrace, markTime } from '@/lib/latency';
import { getAuthHeaders } from '@/lib/userId';

interface RecordingModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (audioBlob: Blob, duration: number, transcript?: string, sessionId?: string, languageCode?: string) => void;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080';

export default function RecordingModal({ isOpen, onClose, onSave }: RecordingModalProps) {
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [duration, setDuration] = useState(0);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [languageCode, setLanguageCode] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [error, setError] = useState('');

  const handleRecordingComplete = async (blob: Blob, dur: number, initialTimings: Record<string, number> = {}) => {
    const requestId = createRequestId('transcribe');
    const timings = { ...initialTimings };
    setAudioBlob(blob);
    setDuration(dur);
    setIsTranscribing(true);
    setError('');
    markTime(timings, 'recording_modal_received');

    try {
      // Convert blob to base64
      const reader = new FileReader();
      reader.onloadend = async () => {
        markTime(timings, 'audio_encoded');
        const base64Audio = (reader.result as string).split(',')[1];

        try {
          // Call backend API for transcription + memory storage
          markTime(timings, 'request_sent');
          const response = await fetch(`${API_BASE_URL}/api/transcribe`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Request-ID': requestId,
              ...getAuthHeaders(),
            },
            body: JSON.stringify({
              audio_base64: base64Audio,
              store_in_memory: true,
            })
          });
          markTime(timings, 'response_headers');

          if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
          }

          const data = await response.json();
          markTime(timings, 'response_parsed');
          setTranscript(data.transcript);
          setLanguageCode(data.language_code);
          setSessionId(data.session_id);
          logLatencyTrace('recording.transcribe', requestId, timings, {
            backendRequestId: data.request_id,
            backendTimingsMs: data.timings_ms,
            transcriptChars: data.transcript?.length ?? 0,
          });

        } catch (apiError) {
          void apiError;
          setError('Failed to transcribe. Make sure the backend server is running.');
          setTranscript('');
          markTime(timings, 'request_failed');
          logLatencyTrace('recording.transcribe.error', requestId, timings);
        } finally {
          setIsTranscribing(false);
        }
      };
      reader.readAsDataURL(blob);
    } catch (err) {
      void err;
      setError('Failed to process audio');
      setIsTranscribing(false);
    }
  };

  const handleSave = () => {
    if (audioBlob) {
      onSave(audioBlob, duration, transcript, sessionId, languageCode);
      resetState();
    }
  };

  const resetState = () => {
    setAudioBlob(null);
    setDuration(0);
    setTranscript('');
    setLanguageCode('');
    setSessionId('');
    setError('');
    setIsTranscribing(false);
  };

  const handleClose = () => {
    resetState();
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-white/90 backdrop-blur-lg rounded-3xl p-8 w-full max-w-md mx-4 border border-white/50 shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <h2 className="text-xl font-bold text-gray-800">New Recording</h2>
          <button
            onClick={handleClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
              <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
            </svg>
          </button>
        </div>

        {/* Recording area */}
        <div className="flex flex-col items-center py-8">
          {!audioBlob ? (
            <RecordButton onRecordingComplete={handleRecordingComplete} />
          ) : (
            <div className="w-full space-y-6">
              {/* Audio preview */}
              <div className="bg-purple-100/80 rounded-xl p-4">
                <div className="flex items-center gap-4">
                  <button className="w-12 h-12 bg-purple-500 rounded-xl flex items-center justify-center text-white shadow-lg shadow-purple-500/25">
                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  </button>
                  <div className="flex-1">
                    <div className="flex items-center gap-1 mb-2">
                      {[...Array(30)].map((_, i) => (
                        <div
                          key={i}
                          className="w-1 bg-purple-500 rounded-full"
                          style={{ height: `${Math.random() * 20 + 8}px` }}
                        />
                      ))}
                    </div>
                    <p className="text-xs text-gray-500">
                      {Math.floor(duration / 60)}:{(duration % 60).toString().padStart(2, '0')}
                    </p>
                  </div>
                </div>
              </div>

              {/* Error Display */}
              {error && (
                <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-center gap-3">
                  <svg className="w-5 h-5 text-red-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                  </svg>
                  <span className="text-sm text-red-600">{error}</span>
                </div>
              )}

              {/* Transcript */}
              <div>
                <label className="text-sm text-gray-600 mb-2 block">Transcript</label>
                {isTranscribing ? (
                  <div className="bg-gray-100 rounded-xl p-4 flex items-center gap-3">
                    <div className="animate-spin w-5 h-5 border-2 border-purple-500 border-t-transparent rounded-full" />
                    <span className="text-sm text-gray-500">Transcribing & storing in memory...</span>
                  </div>
                ) : (
                  <textarea
                    value={transcript}
                    onChange={(e) => setTranscript(e.target.value)}
                    className="w-full bg-white border border-gray-200 rounded-xl p-4 text-sm text-gray-700 resize-none focus:outline-none focus:border-purple-400 focus:ring-2 focus:ring-purple-400/20"
                    rows={4}
                    placeholder="Edit transcript..."
                  />
                )}
                {sessionId && !error && (
                  <p className="text-xs text-green-600 mt-2 flex items-center gap-1">
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                    </svg>
                    Stored in memory • {languageCode}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Actions */}
        {audioBlob && (
          <div className="flex gap-3">
            <button
              onClick={resetState}
              className="flex-1 py-3 rounded-xl border border-gray-200 text-gray-600 hover:bg-gray-50 transition-all"
            >
              Re-record
            </button>
            <button
              onClick={handleSave}
              disabled={isTranscribing}
              className="flex-1 py-3 rounded-xl bg-purple-500 text-white hover:bg-purple-400 transition-all disabled:opacity-50 shadow-lg shadow-purple-500/25"
            >
              Save Entry
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
