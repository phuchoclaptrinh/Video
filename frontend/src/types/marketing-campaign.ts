import type { VideoPlan } from './video-event';
import type { RenderTarget } from './workspace';

export type CampaignItem = {
  index: number;
  title: string;
  angle: string;
  hook: string;
  cta: string;
  caption: string;
  visual_direction: string;
  channel: string;
  funnel_stage: 'awareness' | 'consideration' | 'conversion';
  publish_day: number;
  status: 'PENDING' | 'RUNNING' | 'DRAFT' | 'FAILED' | 'CANCELLED';
  error: string | null;
  plan: VideoPlan | null;
  message_id: string | null;
};
export type MarketingCampaign = {
  cancel_requested?: boolean;
  id: string;
  conversation_id: string;
  brief: string;
  script_count: number;
  completed: number;
  status: 'QUEUED' | 'WORKING' | 'DRAFTING' | 'COMPLETED' | 'PARTIAL' | 'FAILED' | 'CANCELLED';
  error: string | null;
  created: number;
  updated: number;
  input_asset_ids: string[];
  render_target: RenderTarget;
  strategy: {
    product_summary: string;
    audience: string;
    objective: string;
    positioning: string;
    observed_details: string[];
    needs_confirmation: string[];
    content_pillars: string[];
  } | null;
  items: CampaignItem[];
};
