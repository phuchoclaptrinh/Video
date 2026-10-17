"""Short-form creative formats; each plan uses one so repeated requests do not share a template."""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class CreativeFormat:
    key: str
    name: str
    direction: str
    sales_only: bool = False


CREATIVE_FORMATS = (
    CreativeFormat(
        "pov_reaction", "POV quay người thân",
        "Handheld POV of an off-screen narrator filming a family member or friend discovering the subject. "
        "Use voice_over: the narrator's voice speaks; the on-screen person reacts without speaking. Open on a relatable "
        "moment of resistance or boredom, then the reveal and a genuine reaction.",
    ),
    CreativeFormat(
        "transformation", "Biến hình trước và sau",
        "Before and after transformation. Open on an unflattering or chaotic before state, hard cut on a "
        "beat to the after state with the product in use, then hold the result. The cut itself is the hook.",
        sales_only=True,
    ),
    CreativeFormat(
        "skit", "Tiểu phẩm tình huống",
        "Mini comedy skit from everyday Vietnamese family or office life: a relatable problem escalates, "
        "the subject resolves it, and a punchline lands at the end. One on-screen speaker delivers the lines.",
    ),
    CreativeFormat(
        "listicle", "Ba lý do",
        "Numbered countdown such as ba lý do or ba điều cần biết. Announce the count in the hook, give one "
        "reason per scene with a matching visual detail, and save the strongest reason for last.",
    ),
    CreativeFormat(
        "mistake", "Sai lầm thường gặp",
        "Call out a common mistake viewers make about this topic, show why it matters, then show the better "
        "choice. Confident, slightly provocative creator tone, never mocking the viewer.",
    ),
    CreativeFormat(
        "honest_review", "Review thật lòng",
        "Talking-head review by an adult Vietnamese creator holding or using the product, built around one real "
        "moment where it made a difference, with close-up cutaways only where a detail changes the story. "
        "Sound candid by saying who it suits best, framed positively; never end on a drawback.",
        sales_only=True,
    ),
    CreativeFormat(
        "styling", "Một món nhiều cách dùng",
        "Show one product used or styled in several real situations, one situation per scene, ending with "
        "a question asking which version the viewer prefers.",
        sales_only=True,
    ),
    CreativeFormat(
        "storytime", "Kể chuyện cá nhân",
        "First-person storytime opening in the middle of a memorable moment, building a small emotional turn, "
        "with the subject woven naturally into the story rather than pitched.",
    ),
    CreativeFormat(
        "faq_reply", "Trả lời câu hỏi hay gặp",
        "Answer a question people commonly ask about this topic, framed as a question many viewers wonder "
        "about, never as a fabricated comment or testimonial. Demonstrate the answer visually.",
    ),
    CreativeFormat(
        "sensory_detail", "Cận cảnh chi tiết",
        "Slow, satisfying macro close-ups of texture, material and craftsmanship that the images actually show, "
        "with an intimate voice that points out each detail as the camera glides over it.",
        sales_only=True,
    ),
    CreativeFormat(
        "day_in_life", "Một ngày cùng sản phẩm",
        "Fast montage of a day in a believable Vietnamese life, morning to evening, with the subject appearing "
        "naturally in each moment and a warm closing line.",
    ),
)
FORMATS_BY_KEY = {item.key: item for item in CREATIVE_FORMATS}


def choose_creative_format(
    planning_goal: str, requested: str | None = None, previous: str | None = None,
) -> CreativeFormat:
    if requested in FORMATS_BY_KEY:
        return FORMATS_BY_KEY[requested]
    candidates = [
        item for item in CREATIVE_FORMATS
        if (planning_goal == "sales" or not item.sales_only) and item.key != previous
    ]
    return random.choice(candidates)  # noqa: S311 - creative variety, not security.


def creative_format_instruction(item: CreativeFormat) -> str:
    return (
        "\nCREATIVE FORMAT FOR THIS PLAN: " + item.name + ". " + item.direction
        + " Build the hook, scene beats, visuals and closing around this format. If the user explicitly "
        "requests a different style or a campaign angle is supplied, honor that and adapt this format to it. "
        "Keep every truth rule; the format never justifies invented facts, people's testimonials or results."
    )
