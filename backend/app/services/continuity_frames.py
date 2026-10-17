"""Lightweight sharpness/motion ranking, not face or mouth-pose recognition."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat


@dataclass(frozen=True)
class FrameCandidate:
    path: Path
    timestamp: float


def choose_continuity_frame(candidates: list[FrameCandidate]) -> tuple[FrameCandidate, dict]:
    if not candidates:
        raise ValueError("continuity_frame_candidates_missing")
    frames = []
    for candidate in candidates:
        with Image.open(candidate.path) as image:
            frames.append(image.convert("L").resize((160, 90)))
    ranks = []
    for index, frame in enumerate(frames):
        sharpness = ImageStat.Stat(frame.filter(ImageFilter.FIND_EDGES).crop((1, 1, 159, 89))).stddev[0]
        neighbors = frames[max(0, index - 1):index] + frames[index + 1:index + 2]
        motion = max((ImageStat.Stat(ImageChops.difference(frame, other)).mean[0]
                      for other in neighbors), default=0.0)
        # Avoid adding silent tail on ties; only take a later frame if visibly better.
        ranks.append((sharpness / (1 + motion), -candidates[index].timestamp, sharpness, motion, index))
    _, _, sharpness, motion, index = max(ranks)
    return candidates[index], {"frame_sharpness": round(sharpness, 3), "frame_motion_delta": round(motion, 3),
                               "face_pose_verified": False}
