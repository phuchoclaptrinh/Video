Hãy sửa trực tiếp code hiện tại của project để giải quyết vấn đề CONTINUITY khi tạo video nhiều clip 8 giây.

QUAN TRỌNG:

Tôi muốn GIỮ NATIVE AUDIO CỦA MODEL VIDEO.

Không chuyển sang ElevenLabs, XTTS, OpenVoice hay TTS provider bên ngoài.

Model video hiện tại chịu trách nhiệm tạo:

* video
* giọng nói
* khẩu hình/lip-sync
* ambience nếu model hỗ trợ

Mục tiêu là khi tạo:

Clip 1 → Clip 2 → Clip 3 → Clip 4

người xem phải cảm nhận đây là MỘT VIDEO LIÊN TỤC, không phải 4 video độc lập ghép lại.

==================================================

1. KHÔNG TẠO CÁC CLIP ĐỘC LẬP
   ==================================================

Hiện tại hệ thống đang có:

story_plan
→ clip_index
→ generate từng clip

Hãy sửa pipeline để các clip sau phụ thuộc vào clip trước.

Pipeline mong muốn:

Scene 1
↓
Generate native video + native audio
↓
Wait until generation completed
↓
Get generated clip / final frame / continuation generation ID
↓
Scene 2
↓
Generate using Scene 1 continuation/reference
↓
Wait
↓
Scene 3
↓
...

KHÔNG generate tất cả clip độc lập rồi mới ghép.

==================================================
2. NATIVE VOICE CONTINUITY
==========================

Vì voice do chính video model tạo ra, không thể giả định rằng chỉ cần ghi:

"same voice"

trong prompt là đủ.

Hãy tận dụng tất cả cơ chế continuity/reference/continuation mà provider hiện tại hỗ trợ.

Ưu tiên theo thứ tự:

1. Native video continuation API nếu provider hỗ trợ.
2. Previous generated clip / generation ID làm continuation reference nếu provider hỗ trợ.
3. Previous clip final frame làm FIRST_FRAME.
4. Character/reference image.
5. Voice-related reference nếu provider hỗ trợ.
6. Prompt voice bible như fallback.

KHÔNG tự tạo voice profile giả nếu provider không có API hỗ trợ voice identity.

Không claim rằng voice được khóa tuyệt đối nếu provider không hỗ trợ voice reference.

==================================================
3. VOICE BIBLE
==============

Giữ voice_bible trong story plan nhưng biến nó thành một instruction nhất quán cho toàn bộ project.

Ví dụ:

{
"voice_bible": {
"language": "Vietnamese",
"accent": "...",
"gender": "...",
"age": "...",
"timbre": "...",
"speaking_style": "...",
"pace": "...",
"emotion": "..."
}
}

Các scene phải sử dụng cùng voice_bible.

Không để scene 2 tự suy luận một giọng mới.

==================================================
4. CONTINUATION PROMPT
======================

Đối với clip > 1, prompt phải nói rõ rằng đây là phần tiếp theo của cùng một video.

Không dùng prompt kiểu:

"Create a new video of..."

Thay bằng cấu trúc:

CONTINUE THE PREVIOUS VIDEO.

This is a direct continuation of the previous shot.

Preserve the exact same:

* person
* face
* age
* hairstyle
* clothing
* accessories
* voice
* accent
* vocal timbre
* speaking style
* environment
* lighting
* camera position
* lens/framing
* props

Start from the exact visible state supplied by the previous clip/reference.

Continue the existing action naturally.

Do not restart the action.

Do not reinterpret the character.

Do not introduce another speaker.

Do not change the voice.

Do not change accent.

Do not change vocal age.

Do not change vocal timbre.

Do not add additional dialogue.

Do not repeat the previous sentence.

==================================================
5. VOICE SCRIPT CONTINUITY
==========================

Không để mỗi clip tạo một câu thoại hoàn toàn độc lập về mặt ngữ điệu.

Ví dụ KHÔNG:

Clip 1:
"Hôm nay tôi sẽ giới thiệu..."

Clip 2:
"Đây là sản phẩm..."

Clip 3:
"Nó có rất nhiều..."

Mà Story Planner phải tạo dialogue có ngữ cảnh liên tục:

Clip 1:
"Hôm nay tôi sẽ giới thiệu với các bạn..."

Clip 2:
"...một sản phẩm có thiết kế rất đơn giản..."

Clip 3:
"...nhưng điểm đặc biệt nằm ở khả năng..."

Nếu model hỗ trợ continuation audio, hãy để câu thoại tiếp tục tự nhiên từ clip trước.

Không bắt buộc mỗi clip phải kết thúc bằng một câu hoàn chỉnh.

==================================================
6. 8-SECOND BOUNDARY
====================

8 giây là giới hạn generation của model, KHÔNG phải giới hạn storytelling.

Một câu có thể trải qua nhiều clip.

Ví dụ:

Clip 1:
"Hôm nay chúng ta sẽ cùng tìm hiểu..."

Clip 2:
"...vì sao công nghệ này đang thay đổi..."

Clip 3:
"...cách chúng ta xây dựng sản phẩm AI."

Không chèn khoảng lặng lớn chỉ để phân biệt scene.

Không restart speech ở đầu mỗi clip nếu continuation API cho phép.

==================================================
7. AUDIO GAP
============

Không tạo khoảng lặng giả giữa các clip.

Sau khi render:

Clip 1 audio
████████████

Clip 2 audio
████████████

phải được nối thành:

████████████████████████

Nếu native audio của provider có khoảng silence ở đầu/cuối clip:

* detect leading silence
* detect trailing silence
* loại bỏ silence không cần thiết
* căn lại audio/video timeline

Nhưng KHÔNG cắt mất âm tiết hoặc từ.

Không sử dụng crossfade dài làm mất phụ âm đầu/cuối.

==================================================
8. AUDIO CONTINUITY VALIDATION
==============================

Sau khi clip được generate, chạy validation.

Kiểm tra:

* voice identity consistency nếu có thể đo được
* accent consistency
* speech rate
* volume
* silence
* dialogue completeness
* duplicate words
* repeated sentence
* abrupt speech start
* abrupt speech end

Nếu phát hiện:

Clip 2 bắt đầu bằng việc lặp lại cuối Clip 1:

"đây là sản phẩm... đây là sản phẩm..."

→ mark FAILED.

Nếu clip 2 đổi speaker:

→ mark FAILED.

Nếu clip 2 không có speech trong khi scene yêu cầu speech:

→ mark FAILED.

==================================================
9. VIDEO CONTINUITY
===================

Giữ cơ chế hiện tại:

character reference
+
style reference
+
previous frame / continuation reference

Nhưng ưu tiên previous generated clip/continuation hơn static image.

Hierarchy:

PREVIOUS VIDEO CONTINUATION
↓
PREVIOUS FINAL FRAME
↓
CHARACTER REFERENCE
↓
STYLE REFERENCE
↓
TEXT VISUAL BIBLE

Nếu hai nguồn mâu thuẫn:

previous generated frame/continuation là authority cho trạng thái hiện tại.

Character reference là authority cho identity.

==================================================
10. END STATE → START STATE
===========================

Scene N phải có:

start_state
end_state

Ví dụ:

Scene 1 end_state:

"Person is seated facing camera, holding product at chest height, mouth relaxed after finishing the sentence."

Scene 2 start_state:

"Start exactly from the seated position, same product position, same camera framing, same facial identity."

Không được:

Scene 1:
person holding product

Scene 2:
person already drinking product

nếu Scene 1 không mô tả hành động chuyển tiếp.

Không teleport.

Không thay đổi pose đột ngột.

==================================================
11. CAMERA
==========

Đối với các scene liên tục:

GIỮ:

* camera distance
* camera height
* camera angle
* lens
* framing

Không tự động đổi camera giữa các clip.

Nếu story yêu cầu camera change:

đánh dấu:

transition_intent = "intentional_camera_transition"

và thực hiện thay đổi có chủ đích.

==================================================
12. TRANSITION
==============

Không crossfade tất cả clip.

Nếu cùng một hành động:

use continuation.

Nếu cùng nhân vật nhưng đổi shot:

use hard cut hoặc match cut.

Nếu chuyển địa điểm:

use intentional cinematic transition.

Transition phải được quyết định bởi Story Planner.

==================================================
13. GENERATION ORDER
====================

Nếu scene N cần scene N-1:

KHÔNG:

Promise.all([
generate(scene1),
generate(scene2),
generate(scene3)
])

vì scene 2 và scene 3 chưa có continuation reference.

Phải:

generate scene 1
→ wait
→ obtain continuation reference
→ generate scene 2
→ wait
→ obtain continuation reference
→ generate scene 3

Có thể parallel các job KHÔNG phụ thuộc continuity.

==================================================
14. FAILED GENERATION
=====================

Nếu Scene 3 failed:

KHÔNG regenerate toàn bộ video.

Chỉ regenerate Scene 3.

Nhưng phải dùng:

Scene 2 final state
+
Scene 2 continuation reference
+
Global voice bible
+
Scene 3 voice script
+
Scene 3 visual state

để regenerate.

Scene 1 và Scene 2 không được thay đổi.

==================================================
15. DATABASE
============

Nếu schema hiện tại chưa đủ, bổ sung metadata cho video_scenes:

* generation_id
* parent_generation_id
* continuation_generation_id
* previous_scene_id
* next_scene_id
* start_state
* end_state
* voice_script
* voice_bible
* continuity_status
* continuity_error
* retry_count

Ví dụ:

{
"scene_index": 2,
"generation_id": "...",
"parent_generation_id": "...",
"continuation_generation_id": "...",
"continuity_status": "PASSED"
}

==================================================
16. DO NOT BREAK CURRENT PROVIDERS
==================================

Project hiện tại có logic cho:

* poyo
* seedance
* fake

Không được xóa provider cũ.

Tạo abstraction:

VideoContinuityStrategy

Ví dụ:

class VideoContinuityStrategy:
def build_initial_request(...):
...

```
def build_continuation_request(...):
    ...

def extract_continuation_reference(...):
    ...

def validate_continuation(...):
    ...
```

Provider nào hỗ trợ native continuation thì implement đầy đủ.

Provider nào không hỗ trợ thì fallback:

previous final frame
+
character reference
+
style reference
+
voice_bible
+
continuity prompt

Không giả vờ rằng provider hỗ trợ continuation nếu API thực tế không hỗ trợ.

==================================================
17. POYO / VIDEO PROVIDER
=========================

Đọc code provider hiện tại trước khi sửa.

Kiểm tra chính xác:

* API request schema
* generation ID
* output asset
* reference asset
* first frame
* continuation
* native audio
* video duration
* model capabilities

Không tự invent API field.

Nếu provider API không có native continuation:

implement best-effort frame continuation.

Nếu provider API có continuation:

ưu tiên continuation API.

==================================================
18. PLANNER JSON
================

Giữ schema hiện tại tương thích:

{
"video_type": "...",
"book_title": "",
"audience": "...",
"core_value": "...",
"visual_continuity": "...",
"visual_bible": "...",
"voice_bible": "...",
"reference_image_prompt": "...",
"duration_target_seconds": 24,
"clips": [
{
"index": 1,
"beat": "...",
"purpose": "...",
"video_prompt": "...",
"voice_script": "...",
"start_state": "...",
"end_state": "...",
"transition_intent": "...",
"duration_seconds": 8
}
]
}

Không phá schema hiện tại nếu frontend đang phụ thuộc vào nó.

==================================================
19. MOST IMPORTANT RULE
=======================

Ưu tiên native model voice.

Không thay native voice bằng external TTS.

Mục tiêu:

Clip 1
↓
native voice A
↓
continuation
↓
Clip 2
↓
same native voice A
↓
continuation
↓
Clip 3

Nếu provider không thể guarantee voice identity across independent generations, hãy nói rõ trong code comments/documentation và sử dụng continuation/reference tối đa có thể.

Không tuyên bố "voice locked" nếu API không thực sự hỗ trợ voice locking.

==================================================
20. IMPLEMENTATION TASK
=======================

Hãy:

1. Đọc toàn bộ code hiện tại trước khi sửa.
2. Xác định provider abstraction.
3. Xác định generation lifecycle.
4. Xác định cách lấy output sau khi generation hoàn thành.
5. Xác định cách lấy final frame.
6. Xác định provider có hỗ trợ continuation hay không.
7. Implement continuation strategy.
8. Sửa story batch generation để chạy sequential khi có dependency.
9. Giữ native audio.
10. Thêm voice continuity prompt.
11. Thêm scene continuity metadata.
12. Thêm validation.
13. Thêm retry riêng cho từng scene.
14. Không phá API/frontend hiện tại.
15. Chạy test hiện có.
16. Thêm test cho:

    * first scene
    * continuation scene
    * failed scene retry
    * voice script continuity
    * previous scene reference
    * no accidental independent generation

Cuối cùng hãy report:

* những file đã sửa
* những file mới tạo
* provider nào hỗ trợ continuation thực sự
* provider nào chỉ dùng first-frame fallback
* native voice continuity đạt được ở mức nào
* cách pipeline mới hoạt động
* test đã chạy và kết quả

Đừng chỉ đưa ra kiến trúc.

Hãy trực tiếp sửa implementation hiện tại.
