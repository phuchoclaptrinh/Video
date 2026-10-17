"""Seedvis API v2.0 contract checked 2026-10-13; /models determines availability."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedvisVideoModel:
    label: str
    durations: tuple[int, ...]
    ratios: tuple[str, ...] = ("16:9", "9:16")
    first_frame: bool = True
    native_audio: bool = False
    # False: the model has no duration field; `durations` is its natural output length.
    duration_parameter: bool = True


VIDEO_MODELS = {
    "Veo-3.1": SeedvisVideoModel("Google Veo 3.1", (4, 6, 8), native_audio=True),
    "seedance_2.5": SeedvisVideoModel("Seedance 2.5", (5, 10, 15, 20, 25, 30),
                                    ("16:9", "9:16", "1:1", "3:4", "4:3", "21:9"), False),
    "seedance_2.0_fast": SeedvisVideoModel("Seedance 2.0 fast", (5, 10, 15),
                                         ("16:9", "9:16", "1:1", "3:4", "4:3", "21:9"), False),
    # No duration field is documented; outputs measured at 10s. Speaking a supplied
    # Vietnamese line was confirmed in a manual Seedvis test on 2026-10-15.
    "Omni-Flash": SeedvisVideoModel("Omni Flash", (10,), native_audio=True, duration_parameter=False),
}
IMAGE_MODELS = {
    "gpt-image-2": "GPT Image 2",
    "GEM_PIX_2": "Nano Banana Pro",
    "NARWHAL": "Nano Banana 2.1",
    "HARBOR_SEAL": "Nano Banana 2 lite",
}
