'use client';

import { useState } from 'react';
import clsx from 'clsx';
import { ImagePlus, Star, X } from 'lucide-react';
import { useImagePreview } from '@/hooks/use-image-preview';
import type { VideoWorkspaceController } from '@/hooks/use-video-workspace';

function ImageTile({
  file,
  index,
  busy,
  onRemove,
  onPrimary,
}: {
  file: File;
  index: number;
  busy: boolean;
  onRemove: () => void;
  onPrimary: () => void;
}) {
  const preview = useImagePreview(file);
  return (
    <figure className="workflow-input-image">
      {preview && <img src={preview} alt={`Ảnh đầu vào ${index + 1}: ${file.name}`} />}
      <button
        type="button"
        className={clsx('flow-icon workflow-image-primary', index === 0 && 'selected')}
        title={index === 0 ? 'Ảnh chính' : `Đặt ảnh ${index + 1} làm ảnh chính`}
        disabled={busy}
        onClick={onPrimary}
      >
        <Star size={14} fill={index === 0 ? 'currentColor' : 'none'} />
      </button>
      <button
        type="button"
        className="flow-icon workflow-image-remove"
        title={`Bỏ ảnh ${index + 1}`}
        disabled={busy}
        onClick={onRemove}
      >
        <X size={15} />
      </button>
      <figcaption title={file.name}>
        {index === 0 ? 'Ảnh chính · ' : ' '}
        {file.name}
      </figcaption>
    </figure>
  );
}

export function WorkspaceImages({
  workspace: w,
  disabled,
}: {
  workspace: VideoWorkspaceController;
  disabled: boolean;
}) {
  const [dragging, setDragging] = useState(false);
  const full = w.inputImages.length === 6;
  return (
    <div className="workflow-field">
      <label className="workflow-label" htmlFor="workflow-image">
        Ảnh sản phẩm <small>{w.inputImages.length}/6</small>
      </label>
      <input
        id="workflow-image"
        type="file"
        multiple
        hidden
        ref={w.imageInputRef}
        accept="image/jpeg,image/png,image/webp"
        disabled={disabled}
        onChange={(event) => {
          w.selectInputImages(Array.from(event.target.files || []));
          event.target.value = '';
        }}
      />
      <div
        className={clsx(
          'workflow-image-drop',
          dragging && 'dragging',
          w.inputImages.length > 0 && 'with-gallery',
        )}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (!disabled) w.selectInputImages(Array.from(event.dataTransfer.files));
        }}
      >
        {w.inputImages.length > 0 ? (
          <div className="workflow-input-image-grid">
            {w.inputImages.map((file, index) => (
              <ImageTile
                key={`${file.name}:${file.size}:${file.lastModified}`}
                file={file}
                index={index}
                busy={disabled}
                onRemove={() => w.removeInputImage(index)}
                onPrimary={() => w.makePrimaryImage(index)}
              />
            ))}
            {!full && (
              <button
                type="button"
                className="workflow-add-image"
                title="Thêm ảnh đầu vào"
                disabled={disabled}
                onClick={() => w.imageInputRef.current?.click()}
              >
                <ImagePlus size={20} />
                <span>Thêm ảnh</span>
              </button>
            )}
          </div>
        ) : (
          <button
            type="button"
            className="workflow-image-pick"
            title="Chọn ảnh đầu vào"
            disabled={disabled}
            onClick={() => w.imageInputRef.current?.click()}
          >
            <ImagePlus size={20} strokeWidth={1.8} />
            <strong>Kéo thả, dán hoặc bấm để chọn ảnh</strong>
            <span>JPG, PNG, WebP · tối đa 6 ảnh, 8 MB mỗi ảnh</span>
          </button>
        )}
      </div>
    </div>
  );
}
