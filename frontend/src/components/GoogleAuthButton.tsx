'use client';

import { useEffect, useRef, useState } from 'react';
import {
  AUTH_CHANGE_EVENT,
  buildGoogleUserId,
  clearGoogleAuthProfile,
  type GoogleAuthProfile,
  storeGoogleAuthProfile,
} from '@/lib/userId';

declare global {
  interface Window {
    google?: {
      accounts?: {
        id?: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential?: string }) => void;
            auto_select?: boolean;
            cancel_on_tap_outside?: boolean;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: Record<string, string | number | boolean>
          ) => void;
          disableAutoSelect?: () => void;
        };
      };
    };
  }
}

interface GoogleAuthButtonProps {
  currentUser: GoogleAuthProfile | null;
  onAuthenticated: (profile: GoogleAuthProfile) => void;
  onSignedOut: () => void;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080';
const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || '';
const GOOGLE_SCRIPT_SRC = 'https://accounts.google.com/gsi/client';

function loadGoogleScript() {
  return new Promise<void>((resolve, reject) => {
    if (typeof window === 'undefined') {
      resolve();
      return;
    }

    if (window.google?.accounts?.id) {
      resolve();
      return;
    }

    const existingScript = document.querySelector<HTMLScriptElement>(
      `script[src="${GOOGLE_SCRIPT_SRC}"]`
    );

    if (existingScript) {
      existingScript.addEventListener('load', () => resolve(), { once: true });
      existingScript.addEventListener('error', () => reject(new Error('Failed to load Google Sign-In')), { once: true });
      return;
    }

    const script = document.createElement('script');
    script.src = GOOGLE_SCRIPT_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Failed to load Google Sign-In'));
    document.head.appendChild(script);
  });
}

export default function GoogleAuthButton({
  currentUser,
  onAuthenticated,
  onSignedOut,
}: GoogleAuthButtonProps) {
  const buttonRef = useRef<HTMLDivElement>(null);
  const onAuthenticatedRef = useRef(onAuthenticated);
  const onSignedOutRef = useRef(onSignedOut);
  const [isVerifying, setIsVerifying] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    onAuthenticatedRef.current = onAuthenticated;
  }, [onAuthenticated]);

  useEffect(() => {
    onSignedOutRef.current = onSignedOut;
  }, [onSignedOut]);

  useEffect(() => {
    if (currentUser) {
      setError('');
      return;
    }

    if (!GOOGLE_CLIENT_ID) {
      setError('Missing NEXT_PUBLIC_GOOGLE_CLIENT_ID');
      return;
    }

    let cancelled = false;

    const renderButton = async () => {
      try {
        await loadGoogleScript();
        if (cancelled || !buttonRef.current || !window.google?.accounts?.id) {
          return;
        }

        buttonRef.current.innerHTML = '';
        window.google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: async (response) => {
            if (!response.credential) {
              setError('Google login did not return a credential');
              return;
            }

            setIsVerifying(true);
            setError('');

            try {
              const apiResponse = await fetch(`${API_BASE_URL}/api/auth/google`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ credential: response.credential }),
              });

              if (!apiResponse.ok) {
                const detail = await apiResponse.text();
                throw new Error(detail || 'Google login failed');
              }

              const profile = await apiResponse.json();
              const normalizedProfile: GoogleAuthProfile = {
                provider: 'google',
                userId: profile.user_id || buildGoogleUserId(profile.google_sub),
                googleSub: profile.google_sub,
                sessionToken: profile.session_token,
                email: profile.email ?? null,
                name: profile.name ?? null,
                picture: profile.picture ?? null,
              };

              storeGoogleAuthProfile(normalizedProfile);
              onAuthenticatedRef.current(normalizedProfile);
            } catch (loginError) {
              console.error('Google login failed:', loginError);
              setError('Google sign-in failed. Please try again.');
            } finally {
              setIsVerifying(false);
            }
          },
          auto_select: false,
          cancel_on_tap_outside: true,
        });

        window.google.accounts.id.renderButton(buttonRef.current, {
          theme: 'outline',
          size: 'large',
          text: 'signin_with',
          shape: 'pill',
          width: 240,
        });

        window.dispatchEvent(new Event(AUTH_CHANGE_EVENT));
      } catch (loadError) {
        console.error('Unable to load Google Sign-In:', loadError);
        if (!cancelled) {
          setError('Unable to load Google Sign-In');
        }
      }
    };

    renderButton();

    return () => {
      cancelled = true;
    };
  }, [currentUser]);

  const handleSignOut = () => {
    clearGoogleAuthProfile();
    window.google?.accounts?.id?.disableAutoSelect?.();
    onSignedOutRef.current();
  };

  if (currentUser) {
    return (
      <div className="flex items-center gap-3 rounded-2xl border border-gray-200/70 bg-white/80 px-4 py-2 shadow-sm backdrop-blur">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 overflow-hidden rounded-full bg-gradient-to-br from-violet-400 to-purple-500 flex items-center justify-center text-white font-semibold">
            {currentUser.picture ? (
              <img src={currentUser.picture} alt={currentUser.name || 'Google user'} className="w-full h-full object-cover" />
            ) : (
              <span>{(currentUser.name || currentUser.email || 'G').charAt(0).toUpperCase()}</span>
            )}
          </div>
          <div className="min-w-0">
            <p className="max-w-40 truncate text-sm font-medium text-gray-800">
              {currentUser.name || currentUser.email || 'Google user'}
            </p>
            <p className="text-xs text-gray-500">Signed in with Google</p>
          </div>
        </div>
        <button
          onClick={handleSignOut}
          className="rounded-xl border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-800"
        >
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div ref={buttonRef} />
      {isVerifying && <p className="text-xs text-gray-500">Verifying Google login...</p>}
      {error && <p className="max-w-64 text-right text-xs text-red-500">{error}</p>}
    </div>
  );
}
