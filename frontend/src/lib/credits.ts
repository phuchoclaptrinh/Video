'use client';

import { useEffect, useSyncExternalStore } from 'react';
import { MUTATION_EVENT, request } from '@/lib/chat-api';
import type { VideoPlan } from '@/types/video-event';

/** Credits for every started block of `seconds` in one clip (40 credits / 8s = one 8s clip). */
export type ClipPrice = { credits: number; seconds: number };
export type CreditPricing = {
  video_clip: Record<string, ClipPrice>;
  plan: number;
  campaign: number;
  image: number;
};
export type CreditEntry = {
  id: string;
  delta: number;
  balance: number;
  kind: string;
  label: string;
  note: string | null;
  pending: boolean;
  created: number;
};
export type CreditSummary = {
  balance: number;
  exempt: boolean;
  pricing: CreditPricing;
  history: CreditEntry[];
};

// One shared balance for the whole page, refreshed while any component shows it.
let summary: CreditSummary | null = null;
let subscribers = 0;
let timer: number | undefined;
let pending: number | undefined;
const listeners = new Set<() => void>();

export async function refreshCredits() {
  try {
    summary = await request<CreditSummary>('/credits');
    listeners.forEach((listener) => listener());
  } catch {
    // Signed out or offline: keep the last known balance.
  }
}

// A charge is written while the request runs; refresh shortly after it returns.
function refreshSoon() {
  window.clearTimeout(pending);
  pending = window.setTimeout(() => void refreshCredits(), 1500);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useCredits(): CreditSummary | null {
  const value = useSyncExternalStore(
    subscribe,
    () => summary,
    () => null,
  );
  useEffect(() => {
    subscribers += 1;
    if (subscribers === 1) {
      void refreshCredits();
      timer = window.setInterval(() => void refreshCredits(), 15000);
      window.addEventListener('focus', refreshCredits);
      window.addEventListener(MUTATION_EVENT, refreshSoon);
    }
    return () => {
      subscribers -= 1;
      if (!subscribers) {
        window.clearInterval(timer);
        window.removeEventListener('focus', refreshCredits);
        window.removeEventListener(MUTATION_EVENT, refreshSoon);
        summary = null; // the next account must not see this balance
      }
    };
  }, []);
  return value;
}

export function clipPrice(pricing: CreditPricing, provider?: string, model?: string): ClipPrice {
  if (provider === 'fake') return { credits: 0, seconds: 1 };
  return (
    pricing.video_clip[`${provider}:${model}`] ??
    pricing.video_clip.default ?? { credits: 0, seconds: 1 }
  );
}

export function priceLabel(price: ClipPrice) {
  return `${price.credits} credit/${price.seconds} giây`;
}

/** Credits for one clip, matching the server: the price for every started block of seconds. */
export function clipCost(
  pricing: CreditPricing,
  provider: string | undefined,
  model: string | undefined,
  seconds: number,
) {
  const price = clipPrice(pricing, provider, model);
  return price.credits * Math.max(1, Math.ceil(seconds / price.seconds));
}

/** Mirrors the server estimate: every scene clip at the model price plus one image per scene. */
export function renderCost(plan: VideoPlan, pricing: CreditPricing) {
  if (plan.mode === 'demo') return 0;
  const target = plan.render_target;
  const cost = (seconds: number) =>
    clipCost(pricing, target?.provider, target?.video_model, seconds);
  const clips = plan.story_plan?.clips || [];
  if (!clips.length) return cost(plan.spec.output.duration_seconds);
  return clips.reduce(
    (total, clip) =>
      total + cost(clip.duration_seconds || plan.spec.output.duration_seconds) + pricing.image,
    0,
  );
}
