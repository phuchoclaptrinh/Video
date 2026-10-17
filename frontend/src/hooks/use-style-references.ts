'use client';

import { useCallback, useEffect, useState } from 'react';
import { request } from '@/lib/chat-api';

export type StyleReference = {
  id: string;
  title: string;
  source: string;
  created: number;
  blueprint: {
    title: string;
    summary: string;
    format: string;
    narration_mode: 'on_screen_speaker' | 'voice_over' | 'music_only';
    pacing: string;
    hook: { technique: string };
    speech?: { addressing: string; sentence_style: string; signature_words: string[] };
    speech_metrics?: { avg_words_per_sentence?: number };
  };
};

// Styles learned from reference videos; the selected one steers the next script plan.
export function useStyleReferences(setError: (message: string) => void) {
  const [items, setItems] = useState<StyleReference[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [learning, setLearning] = useState(false);

  useEffect(() => {
    let alive = true;
    request<StyleReference[]>('/style-references')
      .then((loaded) => alive && setItems(loaded))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const learn = useCallback(
    async (file: File | null, url: string) => {
      if (!file && !url.trim()) return false;
      setLearning(true);
      setError('');
      try {
        const form = new FormData();
        if (file) form.append('file', file);
        else form.append('url', url.trim());
        const learned = await request<StyleReference>('/style-references', {
          method: 'POST',
          body: form,
        });
        setItems((current) => [learned, ...current]);
        setSelectedId(learned.id);
        return true;
      } catch (reason) {
        setError((reason as Error).message);
        return false;
      } finally {
        setLearning(false);
      }
    },
    [setError],
  );

  const remove = useCallback(
    async (id: string) => {
      try {
        await request(`/style-references/${id}`, { method: 'DELETE' });
        setItems((current) => current.filter((item) => item.id !== id));
        setSelectedId((current) => (current === id ? '' : current));
      } catch (reason) {
        setError((reason as Error).message);
      }
    },
    [setError],
  );

  return {
    items,
    selectedId,
    setSelectedId,
    selected: items.find((item) => item.id === selectedId) || null,
    learning,
    learn,
    remove,
  };
}
