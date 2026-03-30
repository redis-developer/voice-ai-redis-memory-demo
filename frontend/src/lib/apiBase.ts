'use client';

export function getApiBaseUrl() {
  return process.env.NEXT_PUBLIC_API_URL || '';
}
