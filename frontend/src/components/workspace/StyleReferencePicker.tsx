'use client';

import { useState } from 'react';
import { Loader2, Sparkles, Trash2 } from 'lucide-react';
import type { VideoWorkspaceController } from '@/hooks/use-video-workspace';

const narration = {
  on_screen_speaker: 'Người nói trong hình',
  voice_over: 'Giọng dẫn ngoài hình',
  music_only: 'Chỉ nhạc, không lời',
};

// Learn a reusable script style from a viral reference video, then plan with it.
export function StyleReferencePicker({
  workspace: w,
  disabled,
}: {
  workspace: VideoWorkspaceController;
  disabled: boolean;
}) {
  const styles = w.styles;
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [fileKey, setFileKey] = useState(0);
  const selected = styles.selected;
  const locked = disabled || styles.learning;

  async function learn() {
    if (await styles.learn(file, url)) {
      setFile(null);
      setUrl('');
      setFileKey((key) => key + 1);
    }
  }

  return (
    <div className="workflow-style-reference">
      <div className="workflow-field">
        <label className="workflow-label" htmlFor="workflow-style-reference">
          Phong cách mẫu
          {styles.items.length > 0 && <small>{styles.items.length} đã học</small>}
        </label>
        <select
          id="workflow-style-reference"
          aria-label="Phong cách mẫu"
          value={styles.selectedId}
          disabled={locked}
          onChange={(event) => styles.setSelectedId(event.target.value)}
        >
          <option value="">Không dùng (AI tự chọn kiểu kịch bản)</option>
          {styles.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
      </div>
      {selected && (
        <div className="workflow-style-summary" role="note">
          <strong>{selected.blueprint.format}</strong>
          <p>
            {narration[selected.blueprint.narration_mode]} · {selected.blueprint.pacing}
          </p>
          <p>{selected.blueprint.summary}</p>
          {selected.blueprint.speech?.addressing && (
            <p>
              <b>Cách viết lời:</b> {selected.blueprint.speech.addressing}
              {selected.blueprint.speech_metrics?.avg_words_per_sentence
                ? ` · ${selected.blueprint.speech_metrics.avg_words_per_sentence} từ/câu`
                : ''}
              {selected.blueprint.speech.signature_words?.length
                ? ` · hay dùng: ${selected.blueprint.speech.signature_words.slice(0, 6).join(', ')}`
                : ''}
              <br />
              {selected.blueprint.speech.sentence_style}
            </p>
          )}
          <button
            type="button"
            className="flow-icon"
            title="Xóa phong cách này"
            disabled={locked}
            onClick={() => void styles.remove(selected.id)}
          >
            <Trash2 size={14} />
          </button>
        </div>
      )}
      <details className="workflow-advanced">
        <summary>
          <Sparkles size={15} />
          Học phong cách từ video viral
        </summary>
        <label>
          Tải video lên (tối đa 50 MB, 3 phút)
          <input
            key={fileKey}
            type="file"
            accept="video/mp4,video/quicktime,video/webm"
            disabled={locked}
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
        </label>
        <label>
          Hoặc dán link TikTok, Reels, Shorts
          <input
            type="url"
            value={url}
            placeholder="https://www.tiktok.com/@..."
            disabled={locked || Boolean(file)}
            onChange={(event) => setUrl(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="workflow-primary"
          disabled={locked || (!file && !url.trim())}
          onClick={() => void learn()}
        >
          {styles.learning ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}
          {styles.learning ? 'Đang phân tích video, khoảng một phút…' : 'Phân tích phong cách'}
        </button>
        <span className="workflow-model-caption">
          AI học kỹ thuật dựng (hook, nhịp cắt, góc máy) và cách viết lời (xưng hô, văn phong, cấu
          trúc câu, trợ từ, công thức mở và chốt), không học giọng, không chép nguyên câu hay hình.
          Video gốc được xóa ngay sau khi phân tích.
        </span>
      </details>
    </div>
  );
}
