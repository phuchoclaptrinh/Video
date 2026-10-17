"""Shared, bounded vision input for story and campaign planning."""

from typing import Annotated

from pydantic import Field

InlineImage = Annotated[
    str,
    Field(
        min_length=100, max_length=2000000, pattern=r"^data:image/jpeg;base64,[A-Za-z0-9+/]+={0,2}$"
    ),
]

SALES_DIRECTION = (
    "SALES STORY DIRECTION: Write a scroll-stopping Vietnamese product video in native creator style for "
    "TikTok Shop and Reels, not a TV commercial or a generic daily-life vlog. "
    "Read ALL provided images and connect their product, packaging, features and use context. "
    "Image one is the opening visual anchor; other images provide evidence and alternate details, "
    "not permission to change the product or presenter. Open on the viewer's problem, desire or a surprising "
    "visible product detail, never on the brand name or an introduction. Show the product doing something "
    "the images support, turn each feature into a felt benefit in the viewer's day without reading visible colors or details back to them, and finish with one clear "
    "natural CTA. When the input is a product photo or flat-lay, never open on that photo or a zoom into it: "
    "every scene shows a real person wearing or using the product in a live setting. Give each scene a distinct "
    "setting or framing with a hard_cut between them, and write start_state as a complete filmable opening shot, "
    "because each cut scene starts from its own generated keyframe. Make each scene advance the buying story. Use complete engaging speech; "
    "never pad. Product attributes must come from readable image details or the user's "
    "brief. Do not invent prices, discounts, stock scarcity, certifications, health benefits, customer "
    "testimonials or personal use history. If facts are missing, use neutral descriptive language. "
    "Avoid unverified competitor comparisons. Aim for watchability and sales relevance, never promise "
    "virality or guaranteed sales. Honor the campaign angle, hook, funnel stage and CTA when supplied in context."
)


def image_parts(images: list[str]) -> list[dict]:
    if len(images) > 6:
        raise ValueError("planner_too_many_images")
    parts = []
    for index, source in enumerate(images, 1):
        parts.extend(
            [
                {
                    "type": "text",
                    "text": f"Reference image {index}"
                    + (
                        " (opening visual anchor)"
                        if index == 1
                        else " (supporting product evidence)"
                    ),
                },
                {"type": "image_url", "image_url": {"url": source, "detail": "high"}},
            ]
        )
    return parts
