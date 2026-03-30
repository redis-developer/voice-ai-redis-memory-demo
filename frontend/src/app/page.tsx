'use client';

import { useEffect, useState } from 'react';
import Sidebar from '@/components/Sidebar';
import SearchBar from '@/components/SearchBar';
import EntryCard from '@/components/EntryCard';
import RecordingModal from '@/components/RecordingModal';
import ChatInterface from '@/components/ChatInterface';
import GoogleAuthButton from '@/components/GoogleAuthButton';
import { JournalEntry, PlaybackState } from '@/types';
import {
  AUTH_CHANGE_EVENT,
  getAuthHeaders,
  getOrCreateUserId,
  getStoredGoogleAuthProfile,
  type GoogleAuthProfile,
} from '@/lib/userId';

// Mock data for demonstration
const mockEntries: JournalEntry[] = [
  {
    id: '1',
    transcript: 'Today was a productive day. I managed to complete the voice journal project and learned a lot about Redis Agent Memory Server.',
    language_code: 'en-IN',
    created_at: new Date().toISOString(),
    duration_seconds: 45,
    mood: 'happy',
    tags: ['work', 'coding'],
  },
  {
    id: '2',
    transcript: 'Had a great meeting with the team today. We discussed the roadmap for Q1 and everyone seemed excited.',
    language_code: 'en-IN',
    created_at: new Date(Date.now() - 3600000).toISOString(),
    duration_seconds: 32,
    mood: 'excited',
    tags: ['work', 'meeting'],
  },
  {
    id: '3',
    transcript: 'Guess what? Voice journal के साथ हिंदी में journal entry करना बहुत easy है!',
    language_code: 'hi-IN',
    created_at: new Date(Date.now() - 86400000).toISOString(),
    duration_seconds: 28,
    mood: 'grateful',
    tags: ['personal', 'hindi'],
  },
];

export default function Home() {
  const [userId, setUserId] = useState('default_user');
  const [authUser, setAuthUser] = useState<GoogleAuthProfile | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [activeTab, setActiveTab] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [entries, setEntries] = useState<JournalEntry[]>(mockEntries);
  const [isRecordingModalOpen, setIsRecordingModalOpen] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [selectedMood, setSelectedMood] = useState<string | null>(null);
  const [moodSaving, setMoodSaving] = useState(false);
  const [playback, setPlayback] = useState<PlaybackState>({
    isPlaying: false,
    currentTime: 0,
    duration: 0,
    entryId: null,
  });

  useEffect(() => {
    const syncAuthState = () => {
      setAuthUser(getStoredGoogleAuthProfile());
      setUserId(getOrCreateUserId());
      setAuthReady(true);
    };

    syncAuthState();
    window.addEventListener(AUTH_CHANGE_EVENT, syncAuthState);

    return () => {
      window.removeEventListener(AUTH_CHANGE_EVENT, syncAuthState);
    };
  }, []);

  if (!authReady || !authUser) {
    const heading = authReady ? 'Choose a sign-in method to continue' : 'Sign in to keep your entries separate';
    const copy = authReady
      ? 'Sign in with Google to keep your notes and voice memories tied to your account.'
      : 'Use Google sign-in to sync your identity across devices and keep your journal private.';

    return (
      <div className="min-h-screen gradient-bg flex items-center justify-center p-6">
        <div className="w-full max-w-md rounded-3xl border border-white/50 bg-white/85 backdrop-blur-xl p-8 shadow-2xl shadow-purple-500/10">
          <div className="mb-6 flex items-center gap-3">
            <div className="w-12 h-12 bg-gradient-to-br from-violet-500 to-purple-600 rounded-2xl flex items-center justify-center shadow-lg shadow-purple-500/25">
              <span className="text-2xl">🎙️</span>
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-800">Voice Journal</h1>
              <p className="text-sm text-gray-500">{heading}</p>
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-2xl bg-purple-50/80 border border-purple-100 p-4">
              <p className="text-sm text-gray-700">{copy}</p>
            </div>

            <div className="flex justify-start">
              <GoogleAuthButton
                currentUser={authUser}
                onAuthenticated={(profile) => {
                  setAuthUser(profile);
                  setUserId(profile.userId);
                  setAuthReady(true);
                }}
                onSignedOut={() => {
                  setAuthUser(null);
                  setUserId(getOrCreateUserId());
                }}
              />
            </div>
          </div>
        </div>
      </div>
    );
  }

  const filteredEntries = entries.filter((entry) =>
    entry.transcript.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handlePlay = (entryId: string) => {
    const entry = entries.find((e) => e.id === entryId);
    if (entry) {
      setPlayback({ isPlaying: true, currentTime: 0, duration: entry.duration_seconds, entryId });
    }
  };

  const handlePause = () => setPlayback((prev) => ({ ...prev, isPlaying: false }));

  const handleDelete = (entryId: string) => {
    setEntries((prev) => prev.filter((e) => e.id !== entryId));
  };

  const handleFavorite = (entryId: string) => {
    // TODO: Implement favorite functionality
    void entryId;
  };

  const handleMoodSelect = async (mood: string, emoji: string) => {
    if (moodSaving) return;

    setMoodSaving(true);
    setSelectedMood(mood);

    try {
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080';
      const response = await fetch(`${apiUrl}/api/mood`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ mood, emoji }),
      });

      if (!response.ok) {
        throw new Error('Failed to save mood');
      }
    } catch {
      // Silently handle error - UI will reset selected mood
      setSelectedMood(null);
    } finally {
      setMoodSaving(false);
    }
  };

  const handleSaveRecording = (
    audioBlob: Blob,
    duration: number,
    transcript?: string,
    sessionId?: string,
    languageCode?: string
  ) => {
    const newEntry: JournalEntry = {
      id: sessionId || Date.now().toString(),
      transcript: transcript || 'New voice entry',
      language_code: languageCode || 'en-IN',
      created_at: new Date().toISOString(),
      duration_seconds: duration,
    };
    setEntries((prev) => [newEntry, ...prev]);
    setIsRecordingModalOpen(false);
  };

  return (
    <div className="flex h-screen gradient-bg">
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        userName={authUser?.name || undefined}
        userEmail={authUser?.email || undefined}
        userAvatarUrl={authUser?.picture}
      />

      <main className="flex-1 flex flex-col overflow-hidden">
        <header className="p-6 border-b border-gray-200/50">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-gray-800">Voice Journal</h1>
              <p className="text-sm text-gray-500">Capture your thoughts with your voice</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <GoogleAuthButton
                currentUser={authUser}
                onAuthenticated={(profile) => {
                  setAuthUser(profile);
                  setUserId(profile.userId);
                }}
                onSignedOut={() => {
                  setAuthUser(null);
                  setUserId(getOrCreateUserId());
                }}
              />
              <button
                onClick={() => setIsChatOpen(true)}
                className="flex items-center gap-2 px-5 py-3 bg-gradient-to-r from-emerald-500 to-teal-600 rounded-xl text-white font-medium hover:from-emerald-400 hover:to-teal-500 transition-all shadow-lg shadow-teal-500/25"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                Chat with Journal
              </button>
              <button
                onClick={() => setIsRecordingModalOpen(true)}
                className="flex items-center gap-2 px-5 py-3 bg-gradient-to-r from-violet-500 to-purple-600 rounded-xl text-white font-medium hover:from-violet-400 hover:to-purple-500 transition-all shadow-lg shadow-purple-500/25"
              >
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
                </svg>
                New Recording
              </button>
            </div>
          </div>

          <div className="mb-6">
            <p className="text-sm text-gray-600 mb-3">How are you feeling today?</p>
            <div className="flex gap-3">
              {[
                { emoji: '😊', label: 'Happy', color: 'bg-yellow-100 hover:bg-yellow-200 border-yellow-300', selectedColor: 'bg-yellow-300 border-yellow-500 ring-2 ring-yellow-400' },
                { emoji: '😢', label: 'Sad', color: 'bg-blue-100 hover:bg-blue-200 border-blue-300', selectedColor: 'bg-blue-300 border-blue-500 ring-2 ring-blue-400' },
                { emoji: '🤩', label: 'Excited', color: 'bg-pink-100 hover:bg-pink-200 border-pink-300', selectedColor: 'bg-pink-300 border-pink-500 ring-2 ring-pink-400' },
                { emoji: '😌', label: 'Calm', color: 'bg-green-100 hover:bg-green-200 border-green-300', selectedColor: 'bg-green-300 border-green-500 ring-2 ring-green-400' },
                { emoji: '😤', label: 'Frustrated', color: 'bg-red-100 hover:bg-red-200 border-red-300', selectedColor: 'bg-red-300 border-red-500 ring-2 ring-red-400' },
                { emoji: '🤔', label: 'Thoughtful', color: 'bg-purple-100 hover:bg-purple-200 border-purple-300', selectedColor: 'bg-purple-300 border-purple-500 ring-2 ring-purple-400' },
              ].map((mood) => (
                <button
                  key={mood.label}
                  onClick={() => handleMoodSelect(mood.label, mood.emoji)}
                  disabled={moodSaving}
                  className={`flex flex-col items-center px-4 py-3 rounded-xl border transition-all ${
                    selectedMood === mood.label ? mood.selectedColor : mood.color
                  } ${moodSaving ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                >
                  <span className="text-2xl mb-1">{mood.emoji}</span>
                  <span className="text-xs text-gray-600">{mood.label}</span>
                </button>
              ))}
            </div>
          </div>

          <SearchBar value={searchQuery} onChange={setSearchQuery} />
        </header>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="space-y-3">
            {filteredEntries.length > 0 ? (
              filteredEntries.map((entry, index) => (
                <EntryCard key={entry.id} entry={entry} playback={playback} onPlay={handlePlay} onPause={handlePause} onDelete={handleDelete} onFavorite={handleFavorite} colorIndex={index} />
              ))
            ) : (
              <div className="text-center py-12">
                <p className="text-gray-500 mb-4">No entries found</p>
                <button onClick={() => setIsRecordingModalOpen(true)} className="text-purple-600 hover:text-purple-500">
                  Create your first entry
                </button>
              </div>
            )}
          </div>
        </div>
      </main>

      <RecordingModal
        isOpen={isRecordingModalOpen}
        onClose={() => setIsRecordingModalOpen(false)}
        onSave={handleSaveRecording}
      />
      <ChatInterface isOpen={isChatOpen} onClose={() => setIsChatOpen(false)} />
    </div>
  );
}
