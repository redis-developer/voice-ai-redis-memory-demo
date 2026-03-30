'use client';

export interface GoogleAuthProfile {
  provider: 'google';
  userId: string;
  googleSub: string;
  sessionToken: string;
  email: string | null;
  name: string | null;
  picture: string | null;
}

const GUEST_USER_ID_STORAGE_KEY = 'voice-journal-guest-user-id';
const GOOGLE_AUTH_STORAGE_KEY = 'voice-journal-google-auth';
export const AUTH_CHANGE_EVENT = 'voice-journal-auth-changed';

function generateGuestUserId() {
  return `guest_${crypto.randomUUID()}`;
}

function dispatchAuthChange() {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(new Event(AUTH_CHANGE_EVENT));
}

export function buildGoogleUserId(googleSub: string) {
  return `google_${googleSub}`;
}

export function getStoredGoogleAuthProfile(): GoogleAuthProfile | null {
  if (typeof window === 'undefined') {
    return null;
  }

  const rawProfile = window.localStorage.getItem(GOOGLE_AUTH_STORAGE_KEY);
  if (!rawProfile) {
    return null;
  }

  try {
    return JSON.parse(rawProfile) as GoogleAuthProfile;
  } catch {
    return null;
  }
}

export function storeGoogleAuthProfile(profile: GoogleAuthProfile) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(GOOGLE_AUTH_STORAGE_KEY, JSON.stringify(profile));
  dispatchAuthChange();
}

export function clearGoogleAuthProfile() {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.removeItem(GOOGLE_AUTH_STORAGE_KEY);
  dispatchAuthChange();
}

export function getOrCreateGuestUserId() {
  if (typeof window === 'undefined') {
    return 'default_user';
  }

  const existingUserId = window.localStorage.getItem(GUEST_USER_ID_STORAGE_KEY);
  if (existingUserId) {
    return existingUserId;
  }

  const newUserId = generateGuestUserId();
  window.localStorage.setItem(GUEST_USER_ID_STORAGE_KEY, newUserId);
  return newUserId;
}

export function getOrCreateUserId() {
  const googleAuthProfile = getStoredGoogleAuthProfile();
  if (googleAuthProfile) {
    return googleAuthProfile.userId;
  }

  return getOrCreateGuestUserId();
}

export function getAuthHeaders(): HeadersInit {
  const googleAuthProfile = getStoredGoogleAuthProfile();
  if (!googleAuthProfile?.sessionToken) {
    return {};
  }

  return {
    Authorization: `Bearer ${googleAuthProfile.sessionToken}`,
  };
}
