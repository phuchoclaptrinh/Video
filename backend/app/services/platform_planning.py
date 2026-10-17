"""Planner context, LLM story planning, and draft creation."""

import base64
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.platform_shared import (
    HTTPException,
    assets,
    chat_messages,
    conversations,
    drafts,
    httpx,
    insert,
    json,
    secrets,
    select,
    story_plan_versions,
    time,
    update,
    video_projects,
    video_scenes,
)
from app.services.spoken_text import clean_story_dialogue
from app.services.voice_pipeline import validate_story_timing


class PlatformPlanningMixin:
    def _planner_image_data(self, asset: dict) -> str:
        try:
            with Image.open(self.storage / asset["filename"]) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((1024, 1024))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=88)
            return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise HTTPException(422, "Không đọc được ảnh đầu vào để lập kịch bản.") from None

    @property
    def mode(self) -> str:
        return "live" if self.provider != "fake" else "demo"

    def _planner_context(self, uid: str, conversation_id: str | None) -> dict:
        saved_memories = self.user_memories(uid)[:8]
        recent_messages = []
        context_size = 0
        if conversation_id:
            self.owned(conversations, conversation_id, uid)
            with self.engine.connect() as db:
                rows = db.execute(
                    select(chat_messages.c.role, chat_messages.c.content)
                    .where(
                        chat_messages.c.user_id == uid,
                        chat_messages.c.conversation_id == conversation_id,
                    )
                    .order_by(chat_messages.c.created.desc(), chat_messages.c.id.desc())
                    .limit(12)
            ).all()
            for role, content in reversed(rows):
                if role not in {"user", "assistant"} or context_size >= 6000:
                    continue
                if role == "assistant":
                    try:
                        event = json.loads(content)
                        if event.get("type") == "video_generation":
                            story = (event.get("plan") or {}).get("story_plan") or {}
                            content = (
                                f"Video project idea: {event.get('prompt', '')}; "
                                f"story summary: {story.get('core_value', '')}; "
                                f"status: {(event.get('job') or {}).get('status', 'planned')}"
                            )
                    except (TypeError, ValueError):
                        pass
                content = content[:1200]
                recent_messages.append({"role": role, "content": content})
                context_size += len(content)
        return {
            "memories": [item["content"][:500] for item in saved_memories],
            "recent_messages": recent_messages,
        }

    def plan(self, uid: str, body):
        target = self._plan_target(uid, body)
        return self._with_render_target(target)._plan_selected(uid, body)

    def _plan_selected(self, uid: str, body):
        asset_ids = list(getattr(body, "asset_ids", []) or ([body.asset_id] if body.asset_id else []))
        aid = asset_ids[0] if asset_ids else None
        planning_goal = getattr(body, "planning_goal", None) or ("sales" if asset_ids else "general")
        planner_configured = self.planner_credential_status()["configured"]
        if not getattr(body, "use_planner", True) and not getattr(body, "require_planner", False):
            planner_configured = False
        if getattr(body, "require_planner", False) and not planner_configured:
            raise HTTPException(409, "Hãy cấu hình OpenAI planner trước khi lập kịch bản video trong chat")
        if body.target_duration_seconds is not None and not planner_configured:
            raise HTTPException(409, "Hãy cấu hình OpenAI planner để lập kịch bản theo tổng thời lượng yêu cầu")
        capabilities = self._continuity_strategy().capabilities
        if self.provider == "seedvis" and planner_configured and (not capabilities.native_audio or not capabilities.generation_durations):
            raise HTTPException(422, "Model Seedvis này chưa được xác nhận hỗ trợ kịch bản có thoại đồng bộ và thời lượng cố định. Hãy dùng tạo clip trực tiếp hoặc model Veo.")
        if planner_configured and self.provider != "fake" and not (capabilities.first_frame or capabilities.reference_images):
            raise HTTPException(422, "Model này không hỗ trợ ảnh tham chiếu để nối cảnh. Hãy dùng Clip trực tiếp hoặc chọn model khác.")
        if aid and self.provider != "fake" and not (capabilities.first_frame or capabilities.reference_images):
            raise HTTPException(422, "Model này không hỗ trợ ảnh đầu vào. Hãy bỏ ảnh hoặc chọn model khác.")
        if self.provider == "seedvis" and capabilities.model in {"Veo-3.1", "Omni-Flash"} and body.ratio not in {"16:9", "9:16"}:
            raise HTTPException(422, "Model này chỉ hỗ trợ tỷ lệ 16:9 hoặc 9:16")
        if capabilities.generation_durations and body.duration not in capabilities.generation_durations:
            raise HTTPException(422, "unsupported_scene_duration: duration is not supported by the configured model")
        if self.provider == "poyo" and body.ratio not in {"16:9", "9:16"}:
            raise HTTPException(422, "Veo 3.1 trên PoYo chỉ hỗ trợ tỷ lệ 16:9 hoặc 9:16")
        input_assets = [self.owned(assets, asset_id, uid) for asset_id in asset_ids]
        project_id = getattr(body, "project_id", None)
        existing_project = self.owned(video_projects, project_id, uid) if project_id else None
        conversation_id = getattr(body, "conversation_id", None)
        if existing_project:
            if conversation_id and existing_project["conversation_id"] not in {None, conversation_id}:
                raise HTTPException(409, "Video project thuộc cuộc trò chuyện khác")
            conversation_id = existing_project["conversation_id"] or conversation_id
        context_snapshot = self._planner_context(uid, conversation_id)
        if aid:
            context_snapshot["input_references"] = [{"asset_id": item["id"], "width": item["width"], "height": item["height"]} for item in input_assets]
            context_snapshot["input_reference"] = context_snapshot["input_references"][0]
        context_snapshot["planning_goal"] = planning_goal
        marketing_context = getattr(body, "marketing_context", None) or ((existing_project or {}).get("context_snapshot") or {}).get("marketing_campaign")
        if marketing_context:
            context_snapshot["marketing_campaign"] = marketing_context
        style_reference_id = getattr(body, "style_reference_id", None)
        previous_style = ((existing_project or {}).get("context_snapshot") or {}).get("style_reference")
        if style_reference_id:
            context_snapshot["style_reference"] = self.style_blueprint(uid, style_reference_id)
        elif previous_style:
            # A replan keeps the style the project was created with.
            context_snapshot["style_reference"] = previous_style
        if existing_project:
            with self.engine.connect() as db:
                previous_payload = db.execute(select(story_plan_versions.c.payload).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == existing_project["current_plan_version"],
                )).scalar_one_or_none()
            previous_story = (previous_payload or {}).get("story_plan") or {}
            previous_target = previous_story.get("requested_duration_seconds")
            if previous_target is not None:
                context_snapshot["previous_target_duration_seconds"] = previous_target
            if previous_story.get("creative_format"):
                # A replan should try a different creative format than the version it replaces.
                context_snapshot["previous_creative_format"] = previous_story["creative_format"]
        plan_version = existing_project["current_plan_version"] + 1 if existing_project else 1
        story_plan = None
        plan_price = self.credit_pricing()["plan"]
        if planner_configured:
            self.require_credits(uid, plan_price)
            try:
                response = self.http.post(
                    "/internal/v1/planner/story",
                    json={"prompt": body.prompt, "project_context": context_snapshot, "provider": self.provider,
                          "video_model": self.render_target["video_model"],
                          "target_duration_seconds": body.target_duration_seconds,
                          **({"image_data_url": self._planner_image_data(input_assets[0])} if len(input_assets) == 1 else
                             {"image_data_urls": [self._planner_image_data(item) for item in input_assets]} if input_assets else {}),
                          **({"planning_goal": planning_goal} if planning_goal == "sales" else {})},
                )
                response.raise_for_status()
                story_plan = response.json()["plan"]
                validate_story_timing(story_plan, capabilities.generation_durations)
                story_plan = clean_story_dialogue(story_plan)
                validate_story_timing(story_plan, capabilities.generation_durations)
            except httpx.HTTPStatusError as exc:
                try:
                    reason = exc.response.json().get("detail", "planner_unavailable")
                except (ValueError, AttributeError):
                    reason = "planner_unavailable"
                if not isinstance(reason, str):
                    reason = "planner_unavailable"
                messages = {
                    "planner_invalid_key": "OpenAI từ chối API key. Hãy kiểm tra key đã nhập.",
                    "planner_quota_or_rate_limit": "OpenAI hết quota hoặc đang giới hạn yêu cầu. Kiểm tra billing và usage của API key.",
                    "planner_model_unavailable": "Model planner không khả dụng với tài khoản này. Kiểm tra tên model và quyền API.",
                    "planner_invalid_json": "OpenAI trả về nội dung không đúng JSON. Hãy thử lại.",
                    "planner_schema_mismatch": "OpenAI trả về thiếu hoặc sai trường kịch bản. Hãy thử lại.",
                    "planner_dialogue_mismatch": "Planner trả về các đoạn thoại không khớp bài nói tổng thể. Chưa lưu kịch bản.",
                    "planner_spoken_text_invalid": "Planner trả về lời thoại có ký hiệu không phù hợp để đọc. Chưa gửi tạo video.",
                    "planner_refused": "OpenAI từ chối lập kịch bản cho yêu cầu này. Hãy điều chỉnh nội dung yêu cầu.",
                    "planner_output_truncated": "Phản hồi kịch bản bị cắt do giới hạn đầu ra. Chưa lưu bản nháp chưa hoàn chỉnh.",
                    "planner_empty_output": "OpenAI trả về nội dung rỗng. Hãy thử lại.",
                    "planner_connection_failed": "Kết nối tới OpenAI bị gián đoạn. Hãy kiểm tra mạng, VPN/proxy rồi thử lại.",
                    "planner_timeout": "OpenAI phản hồi quá lâu khi lập kịch bản. Hãy thử lại sau.",
                    "planner_upstream_error": "Dịch vụ OpenAI đang gặp lỗi. Hãy thử lại sau.",
                    "planner_duration_mismatch": "Planner trả về kịch bản không đáp ứng thời lượng yêu cầu. Chưa lưu bản nháp.",
                    "planner_duration_unsupported": "Thời lượng yêu cầu vượt khả năng của model với tối đa 5 cảnh. Hãy giảm thời lượng hoặc chọn model khác.",
                }
                raise HTTPException(
                    422 if reason == "planner_duration_unsupported" else 502,
                    messages.get(
                        reason,
                        "Không thể kết nối OpenAI hoặc phản hồi không đúng định dạng. Hãy thử lại.",
                    ),
                ) from None
            except ValueError as exc:
                if str(exc) == "scene_dialogue_empty_after_cleaning":
                    raise HTTPException(502, "AI trả về lời thoại rỗng sau khi bỏ ký hiệu; hãy thử lập lại kịch bản") from None
                raise HTTPException(502, "Không thể lập kịch bản. Hãy thử lại.") from None
            except (httpx.HTTPError, KeyError, TypeError):
                raise HTTPException(502, "Không thể lập kịch bản. Hãy thử lại.") from None
        identifier = secrets.token_hex(16)
        if story_plan is not None:
            self.charge_credits(uid, plan_price, "plan", "plan:" + identifier, body.prompt[:80])
        project_id = project_id or secrets.token_hex(16)
        looks = {
            "cinematic": "cinematic lighting",
            "natural": "natural light, realistic textures",
            "animation": "stylized 3D animation",
        }
        spec = {
            "request": {"request_id": identifier, "user_id": uid, "tenant_id": uid},
            "intent": {
                "operation": "REFERENCE_GENERATION" if aid else "TEXT_GENERATION",
                "goal": body.prompt,
            },
            "output": {
                "duration_seconds": body.duration,
                "aspect_ratio": body.ratio,
                "resolution_preference": "720p",
            },
            "story": {
                "shots": [
                    {
                        "shot_id": "shot-1",
                        "style": {"look": looks[body.style]},
                        "camera": {"movement": "slow tracking"},
                    }
                ]
            },
            "references": [{"asset_id": aid, "role": "FIRST_FRAME"}] if aid else [],
        }
        # Demo-only planning is deterministic, never presented as LLM moderation.
        payload = {
            "input_asset_ids": asset_ids,
            "planning_goal": planning_goal,
            "render_target": self.render_target,
            "spec": spec,
            "preflight_decision": "ALLOW",
            "policy_decision_id": "demo-" + identifier,
            "assets": [],
        }
        if marketing_context:
            payload["marketing_campaign"] = marketing_context
        if aid:
            source_url = (
                self.continuity_reference_url("asset", aid)
                if self._poyo_public_urls
                else "https://example.com/demo-only.jpg"
            )
            payload["assets"] = [
                {
                    "asset_id": aid,
                    "mime_type": "image/jpeg",
                    "source_url": source_url,
                    "moderation_status": "APPROVED",
                }
            ]
        with self.engine.begin() as db:
            if story_plan:
                payload["story_plan"] = story_plan
            db.execute(
                insert(drafts).values(
                    id=identifier,
                    user_id=uid,
                    title=body.prompt,
                    payload=payload,
                    created=time.time(),
                )
            )
            now = time.time()
            if existing_project:
                db.execute(
                    update(story_plan_versions)
                    .where(
                        story_plan_versions.c.project_id == project_id,
                        story_plan_versions.c.status.in_(("CURRENT", "APPROVED")),
                    )
                    .values(status="SUPERSEDED")
                )
                db.execute(
                    update(video_projects)
                    .where(video_projects.c.id == project_id, video_projects.c.user_id == uid)
                    .values(
                        brief=body.prompt,
                        status="REVIEW",
                        current_plan_version=plan_version,
                        context_snapshot=context_snapshot,
                        updated=now,
                    )
                )
            else:
                db.execute(
                    insert(video_projects).values(
                        id=project_id,
                        user_id=uid,
                        conversation_id=conversation_id,
                        brief=body.prompt,
                        status="REVIEW",
                        current_plan_version=plan_version,
                        context_snapshot=context_snapshot,
                        created=now,
                        updated=now,
                    )
                )
            db.execute(
                insert(story_plan_versions).values(
                    id=secrets.token_hex(16),
                    project_id=project_id,
                    version=plan_version,
                    draft_id=identifier,
                    payload={"spec": spec, "story_plan": story_plan, "render_target": self.render_target,
                             "input_asset_ids": asset_ids, "planning_goal": planning_goal},
                    status="CURRENT",
                    created=now,
                )
            )
            scene_list = story_plan["clips"] if story_plan else [
                {"index": 1, "beat": "main", "video_prompt": body.prompt, "voice_script": ""}
            ]
            db.execute(
                insert(video_scenes),
                [
                    {
                        "id": secrets.token_hex(16),
                        "project_id": project_id,
                        "plan_version": plan_version,
                        "scene_index": clip["index"],
                        "draft_id": identifier,
                        "generation_id": None,
                        "payload": clip,
                        "status": "PLANNED",
                        "created": now,
                        "updated": now,
                    }
                    for clip in scene_list
                ],
            )
        return {
            "input_asset_ids": asset_ids,
            "planning_goal": planning_goal,
            "render_target": self.render_target,
            "id": identifier,
            "project_id": project_id,
            "plan_version": plan_version,
            "spec": spec,
            "mode": self.mode,
            "planner": "openai" if story_plan else "template",
            "story_plan": story_plan,
        }
