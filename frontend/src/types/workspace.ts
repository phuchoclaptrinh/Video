import type { VideoJob } from './video-event';

export type RenderTarget = {
  provider: 'fake' | 'seedance' | 'poyo' | 'seedvis';
  video_model: string;
  image_provider: string;
  image_model: string;
};
export type ProviderModel = {
  available?: boolean | null;
  id: string;
  label: string;
  native_audio?: boolean;
  story_supported?: boolean;
  duration_options?: number[];
  ratio_options?: string[];
  local_image_upload?: boolean;
  continuity_enabled?: boolean;
};
export type ProviderOption = {
  catalog_error?: string;
  id: string;
  label: string;
  configured: boolean;
  enabled?: boolean;
  models: ProviderModel[];
};
export type ProviderCatalog = {
  video_providers: ProviderOption[];
  image_providers: ProviderOption[];
};

export type WorkspaceUser = { id: string; email: string; is_admin?: boolean };
export type WorkspaceConfig = {
  video_model_available?: boolean | null;
  image_model_available?: boolean | null;
  render_target?: RenderTarget;
  mode: 'demo' | 'live';
  provider: 'fake' | 'seedance' | 'poyo' | 'seedvis';
  model: string;
  planner: 'template' | 'openai';
  planner_model: string;
  image_provider?: string;
  image_model?: string;
  native_audio?: boolean;
  story_supported?: boolean;
  duration_control?: boolean;
  duration_options: number[];
  ratio_options: string[];
  local_image_upload: boolean;
  continuity_enabled: boolean;
};
export type LibraryVideo = VideoJob & {
  prompt: string;
  created: number;
  ratio: string;
  provider: string;
  cancel_requested: boolean;
};

/** Readable video model name; raw ids such as `veo3.1-fast-official` become `Veo 3.1 Fast Official`. */
export function videoModelLabel(id: string, label?: string) {
  if (label && label !== id) return label;
  return id
    .replace(/^veo-?(\d)/i, 'Veo $1')
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export function providerLabel(provider: string) {
  return (
    { poyo: 'PoYo', seedvis: 'Seedvis', seedance: 'Seedance', fake: 'Demo' }[provider] || provider
  );
}
