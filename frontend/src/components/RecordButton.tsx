'use client';

import { useState, useEffect, useRef } from 'react';
import { markTime } from '@/lib/latency';

interface RecordButtonProps {
  onRecordingComplete: (audioBlob: Blob, duration: number, timings?: Record<string, number>) => void;
  isDisabled?: boolean;
}

// Convert Float32Array PCM to 16-bit PCM WAV
function encodeWAV(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);  // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset += 2;
  }
  return buffer;
}

export default function RecordButton({ onRecordingComplete, isDisabled }: RecordButtonProps) {
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [audioLevel, setAudioLevel] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationRef = useRef<number | null>(null);

  // Web Audio API refs for WAV recording
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const audioBufferRef = useRef<Float32Array[]>([]);
  const durationRef = useRef(0);
  const timingsRef = useRef<Record<string, number>>({});

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
    };
  }, []);

  const startRecording = async () => {
    try {
      timingsRef.current = {};
      markTime(timingsRef.current, 'record_start');
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

      // Set up audio analyser for visualization
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      // Use ScriptProcessorNode to capture raw PCM data
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;
      audioBufferRef.current = [];

      processor.onaudioprocess = (e) => {
        const inputData = e.inputBuffer.getChannelData(0);
        audioBufferRef.current.push(new Float32Array(inputData));
      };

      source.connect(processor);
      processor.connect(audioContext.destination);

      setIsRecording(true);
      setDuration(0);
      durationRef.current = 0;

      // Duration timer
      timerRef.current = setInterval(() => {
        setDuration(d => {
          durationRef.current = d + 1;
          return d + 1;
        });
      }, 1000);

      // Audio level visualization
      const updateLevel = () => {
        if (analyserRef.current) {
          const data = new Uint8Array(analyserRef.current.frequencyBinCount);
          analyserRef.current.getByteFrequencyData(data);
          const avg = data.reduce((a, b) => a + b) / data.length;
          setAudioLevel(avg / 255);
        }
        animationRef.current = requestAnimationFrame(updateLevel);
      };
      updateLevel();
    } catch (err) {
      console.error('Error accessing microphone:', err);
    }
  };

  const stopRecording = async () => {
    if (!isRecording) return;

    markTime(timingsRef.current, 'record_stop');
    setIsRecording(false);
    if (timerRef.current) clearInterval(timerRef.current);
    if (animationRef.current) cancelAnimationFrame(animationRef.current);
    setAudioLevel(0);

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
    markTime(timingsRef.current, 'wav_encoded');

    // Create WAV blob
    const blob = new Blob([wavBuffer], { type: 'audio/wav' });
    markTime(timingsRef.current, 'blob_ready');

    // Clean up AudioContext
    if (audioContextRef.current) {
      await audioContextRef.current.close();
      audioContextRef.current = null;
    }

    // Call completion handler with duration
    markTime(timingsRef.current, 'callback_dispatched');
    onRecordingComplete(blob, durationRef.current, { ...timingsRef.current });
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Waveform visualization when recording */}
      {isRecording && (
        <div className="flex items-center gap-1 h-16">
          {[...Array(20)].map((_, i) => (
            <div
              key={i}
              className="w-1 bg-rose-400 rounded-full transition-all duration-75"
              style={{
                height: `${Math.max(8, audioLevel * 64 * (0.5 + Math.random() * 0.5))}px`,
              }}
            />
          ))}
        </div>
      )}

      {/* Duration */}
      {isRecording && (
        <div className="text-2xl font-mono text-gray-800">
          {formatDuration(duration)}
        </div>
      )}

      {/* Record button */}
      <button
        onClick={isRecording ? stopRecording : startRecording}
        disabled={isDisabled}
        className={`relative w-20 h-20 rounded-full flex items-center justify-center transition-all shadow-lg ${
          isRecording
            ? 'bg-rose-400 recording shadow-rose-400/40'
            : 'bg-gradient-to-br from-violet-500 to-purple-600 hover:from-violet-400 hover:to-purple-500 shadow-purple-500/40'
        } ${isDisabled ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        {isRecording ? (
          <div className="w-6 h-6 bg-white rounded-sm" />
        ) : (
          <svg className="w-8 h-8 text-white" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.91-3c-.49 0-.9.36-.98.85C16.52 14.2 14.47 16 12 16s-4.52-1.8-4.93-4.15c-.08-.49-.49-.85-.98-.85-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.1-.6-.39-1.14-1-1.14z"/>
          </svg>
        )}
      </button>

      {/* Label */}
      <p className="text-sm text-gray-500">
        {isRecording ? 'Tap to stop' : 'Tap to record'}
      </p>
    </div>
  );
}
