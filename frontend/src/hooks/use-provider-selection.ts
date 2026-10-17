'use client';

import { useCallback, useEffect, useState } from 'react';
import { request } from '@/lib/chat-api';
import type { ProviderCatalog, RenderTarget, WorkspaceConfig } from '@/types/workspace';

export function useProviderSelection(defaults: WorkspaceConfig, userId: string) {
  const storageKey = `vo3:providers:${userId}`;
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [choice, setChoice] = useState<RenderTarget>({
    provider: defaults.provider,
    video_model: defaults.model,
    image_provider: defaults.image_provider || 'openai',
    image_model: defaults.image_model || '',
  });
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setCatalog(await request<ProviderCatalog>('/providers?refresh=true'));
      setError('');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (
        saved &&
        ['provider', 'video_model', 'image_provider', 'image_model'].every(
          (key) => typeof saved[key] === 'string',
        )
      )
        setChoice(saved);
    } catch {
      /* A missing or invalid local preference uses server defaults. */
    }
    void refresh();
  }, [storageKey, refresh]);
  function choose(next: Partial<RenderTarget>) {
    const value = { ...(config.render_target || choice), ...next };
    setChoice(value);
    try {
      localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      /* Storage may be disabled. */
    }
  }
  const video =
    catalog?.video_providers.find((item) => item.id === choice.provider && item.enabled) ||
    catalog?.video_providers.find((item) => item.id === defaults.provider && item.enabled) ||
    catalog?.video_providers.find((item) => item.enabled);
  const videoModel =
    video?.models.find((model) => model.id === choice.video_model) || video?.models[0];
  const image =
    catalog?.image_providers.find((item) => item.id === choice.image_provider) ||
    catalog?.image_providers[0];
  const imageModel =
    image?.models.find((model) => model.id === choice.image_model) || image?.models[0];
  const config: WorkspaceConfig =
    video && videoModel && image && imageModel
      ? {
          ...defaults,
          provider: video.id as WorkspaceConfig['provider'],
          video_model_available: videoModel.available,
          image_model_available: imageModel.available,
          model: videoModel.id,
          mode: video.id === 'fake' ? 'demo' : 'live',
          native_audio: Boolean(videoModel.native_audio),
          story_supported: videoModel.story_supported,
          duration_options: videoModel.duration_options || [],
          duration_control: Boolean(videoModel.duration_options?.length),
          ratio_options: videoModel.ratio_options || ['16:9', '9:16'],
          local_image_upload: Boolean(videoModel.local_image_upload),
          continuity_enabled: Boolean(videoModel.continuity_enabled),
          image_provider: image.id,
          image_model: imageModel.id,
          render_target: {
            provider: video.id as WorkspaceConfig['provider'],
            video_model: videoModel.id,
            image_provider: image.id,
            image_model: imageModel.id,
          },
        }
      : defaults;
  const catalogError = video?.catalog_error || image?.catalog_error;
  return {
    catalog,
    config,
    loading,
    error:
      error ||
      (catalogError
        ? 'Không cập nhật được model Seedvis. Kiểm tra API key và tải lại danh sách.'
        : ''),
    refresh,
    choose,
    video,
    image,
  };
}
export type ProviderSelection = ReturnType<typeof useProviderSelection>;
