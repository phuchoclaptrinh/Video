'use client';

import clsx from 'clsx';
import { Coins } from 'lucide-react';
import {
  clipCost,
  clipPrice,
  priceLabel,
  renderCost,
  useCredits,
  type CreditPricing,
} from '@/lib/credits';
import type { VideoPlan } from '@/types/video-event';
import { videoModelLabel } from '@/types/workspace';

/** Expected credit use next to a paid action; admins see what a member would pay. */
export function CreditCost({
  cost,
  plans,
}: {
  cost?: (pricing: CreditPricing) => number;
  plans?: VideoPlan[];
}) {
  const credits = useCredits();
  if (!credits) return null;
  const amount = plans
    ? plans.reduce((total, plan) => total + renderCost(plan, credits.pricing), 0)
    : cost?.(credits.pricing) || 0;
  if (amount <= 0) return null;
  if (credits.exempt)
    return (
      <p className="flow-credit-cost">
        <Coins size={14} />
        <span>
          Tạo video: thành viên sẽ dùng <b>{amount}</b> credit · admin không bị trừ
        </span>
      </p>
    );
  const short = credits.balance < amount;
  return (
    <p className={clsx('flow-credit-cost', short && 'short')} role={short ? 'alert' : undefined}>
      <Coins size={14} />
      <span>
        Tạo video dự kiến dùng <b>{amount}</b> credit · còn {credits.balance}
        {short && '. Không đủ credit, hãy liên hệ quản trị viên để nạp thêm.'}
      </span>
    </p>
  );
}

/**
 * Composer estimate before anything is planned: what the AI script costs now, and roughly what
 * rendering it will cost later at the selected model and length.
 */
export function ComposerCreditEstimate({
  provider,
  model,
  mode,
  seconds,
  sceneSeconds,
  scripts,
  campaign,
}: {
  provider: string;
  model: string;
  mode: 'video' | 'clip' | 'chat';
  /** Total length of one video; 0 lets the planner decide. */
  seconds: number;
  sceneSeconds: number;
  scripts: number;
  campaign: boolean;
}) {
  const credits = useCredits();
  if (!credits || mode === 'chat') return null;
  const { pricing } = credits;
  const planning =
    mode === 'clip' ? 0 : campaign ? pricing.campaign + scripts * pricing.plan : pricing.plan;
  const scenes = Math.ceil(seconds / Math.max(1, sceneSeconds));
  const scene = clipCost(pricing, provider, model, Math.min(seconds, sceneSeconds));
  const video =
    mode === 'clip'
      ? clipCost(pricing, provider, model, seconds)
      : seconds
        ? scripts * scenes * (scene + pricing.image)
        : 0;
  const now = mode === 'clip' ? video : planning;
  const short = !credits.exempt && credits.balance < now;
  const videoText =
    mode === 'clip'
      ? null
      : seconds
        ? `tạo ${scripts > 1 ? `${scripts} video` : 'video'} ước tính ~${video} credit`
        : `tạo video ${priceLabel(clipPrice(pricing, provider, model))} + ${pricing.image} credit mỗi ảnh cảnh`;
  return (
    <p className={clsx('flow-credit-cost', short && 'short')} role={short ? 'alert' : undefined}>
      <Coins size={14} />
      <span>
        {mode === 'clip' ? 'Tạo clip' : 'Lập kịch bản'} <b>{now}</b> credit
        {videoText && ` · ${videoText} (${videoModelLabel(model)})`}
        {credits.exempt
          ? ' · admin không bị trừ'
          : ` · còn ${credits.balance}${short ? '. Không đủ credit, hãy liên hệ quản trị viên để nạp thêm.' : ''}`}
      </span>
    </p>
  );
}
