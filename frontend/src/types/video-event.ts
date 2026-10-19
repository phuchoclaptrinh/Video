import type { RenderTarget } from './workspace';

export type Conversation = { id: string; title: string; created: number; updated: number };
export type ChatMessage = {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  created: number;
};
export type Memory = { id: string; content: string; created: number };
export type VideoPlan = {
  input_asset_ids?: string[];
  planning_goal?: 'general' | 'sales';
  render_target?: RenderTarget;
  creation_mode?: 'direct';
  automatic_duration?: boolean;
  id: string;
  project_id: string;
  mode: 'demo' | 'live';
  spec: {
    intent: { goal: string };
    output: { duration_seconds: number; aspect_ratio: string };
    references?: { asset_id: string; role: string }[];
  };
  story_plan?: {
    book_title?: string;
    core_value: string;
    visual_continuity: string;
    full_voice_script?: string;
    requested_duration_seconds?: number | null;
    clips: {
      index: number;
      beat: string;
      video_prompt: string;
      voice_script: string;
      purpose?: string;
      start_state?: string;
      end_state?: string;
      duration_seconds?: number;
    }[];
  } | null;
};
export type VideoJob = {
  cancel_requested?: boolean;
  id: string;
  status: string;
  duration?: number | null;
  video_url: string | null;
  error: string | null;
  demo: boolean;
  story_batch?: boolean;
  audio_mode?: 'native' | 'narration';
  voice_mode?: 'native_veo' | 'consistent_voice';
  requested_voice_mode?: 'native_veo' | 'consistent_voice';
  voice_mode_limitations?: string[];
  retryable?: boolean;
  /** Technical error (code and provider message); only sent to admins. */
  error_code?: string | null;
  error_kind?: 'credits' | 'merge' | 'rejected' | 'quality' | 'system' | null;
  /** A failed render the worker will start again by itself. */
  auto_retry?: { attempts: number; limit: number; pending: boolean } | null;
  clip_progress?: { completed: number; total: number } | null;
};
export type QualityReport = {
  stage: string;
  status: string;
  scene_index: number | null;
  issues: string[];
  metrics: Record<string, number | string | boolean | null>;
};
export type VideoEvent = {
  type: 'video_generation';
  prompt: string;
  project_id?: string;
  project_status?: string;
  project_current_draft_id?: string | null;
  reference_image_url?: string | null;
  plan: VideoPlan;
  job: VideoJob | null;
  quality_reports?: QualityReport[];
};

const TERMINAL = ['COMPLETED', 'FAILED', 'CANCELLED', 'NEEDS_USER_ACTION'];

/** Still in progress: running, or failed with an automatic retry scheduled. */
export function jobActive(
  job?: { status: string; auto_retry?: { pending: boolean } | null } | null,
) {
  return Boolean(job) && (!TERMINAL.includes(job!.status) || Boolean(job!.auto_retry?.pending));
}

export function videoEvent(message: ChatMessage): VideoEvent | null {
  if (message.role !== 'assistant') return null;
  try {
    const value = JSON.parse(message.content) as VideoEvent;
    return value?.type === 'video_generation' ? value : null;
  } catch {
    return null;
  }
}
