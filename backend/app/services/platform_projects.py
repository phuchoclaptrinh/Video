"""Video project status, reference preparation, and plan approval."""

import base64
import copy
import hashlib
import json
import re

from app.services.platform_shared import (
    STORY_PLACEHOLDER_RE,
    HTTPException,
    Image,
    Path,
    UnidentifiedImageError,
    assets,
    chat_messages,
    drafts,
    httpx,
    secrets,
    select,
    shutil,
    story_batches,
    story_plan_versions,
    time,
    update,
    video_media_assets,
    video_projects,
    video_quality_reports,
    video_scenes,
)
from app.services.spoken_text import clean_spoken_text
from app.services.video_continuity import is_scene_quality_error
from app.services.voice_pipeline import joined_voice_script


class PlatformProjectsMixin:
    def revise_failed_story_scene(
        self, uid: str, message_id: str, scene_index: int, video_prompt: str, voice_script: str
    ) -> dict:
        with self.batch_lock:
            message = self.owned(chat_messages, message_id, uid)
            if message["role"] != "assistant":
                raise HTTPException(404, "Khong tim thay yeu cau video")
            try:
                event = json.loads(message["content"])
            except (TypeError, ValueError):
                raise HTTPException(422, "Du lieu video khong hop le") from None
            if event.get("type") != "video_generation":
                raise HTTPException(404, "Khong tim thay yeu cau video")
            project = self.owned(video_projects, event.get("project_id"), uid)
            draft_id = (event.get("plan") or {}).get("id")
            with self.engine.begin() as db:
                version = db.execute(
                    select(story_plan_versions).where(
                        story_plan_versions.c.project_id == project["id"],
                        story_plan_versions.c.version == project["current_plan_version"],
                        story_plan_versions.c.draft_id == draft_id,
                        story_plan_versions.c.status == "APPROVED",
                    )
                ).mappings().first()
                batch = db.execute(
                    select(story_batches).where(
                        story_batches.c.draft_id == draft_id,
                        story_batches.c.user_id == uid,
                        story_batches.c.status == "FAILED",
                    )
                ).mappings().first()
                if version is None or batch is None or not batch["generation_ids"]:
                    raise HTTPException(409, "Chi co the sua canh trong video da duyet va bi loi")
                if batch["audio_mode"] != "native":
                    raise HTTPException(409, "Khong the sua loi thoai cua video dung audio chung")
                generation_ids = batch["generation_ids"]
                failed_scene_index = self._failed_story_scene_index(draft_id, generation_ids)
                previous_jobs = [self.store.get(gid) for gid in generation_ids[:failed_scene_index - 1]]
                failed_job = self.store.get(generation_ids[failed_scene_index - 1])
                if any(self.store.get(gid)["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}
                       for gid in generation_ids[failed_scene_index:]):
                    raise HTTPException(409, "continuity_downstream_active: wait for downstream tasks to finish before revision")
                if (
                    scene_index < failed_scene_index
                    or not all(job["status"] == "COMPLETED" for job in previous_jobs)
                    or not (
                        self._is_unbilled_scene_failure(failed_job)
                        or (
                            is_scene_quality_error(batch["error_code"])
                            and failed_job["status"] == "COMPLETED"
                        )
                    )
                ):
                    raise HTTPException(409, "Chỉ có thể sửa cảnh bị lỗi hoặc cảnh chưa tạo")
                draft = db.execute(
                    select(drafts).where(drafts.c.id == draft_id, drafts.c.user_id == uid)
                ).mappings().first()
                if draft is None:
                    raise HTTPException(404, "Khong tim thay kich ban")
                draft_payload = copy.deepcopy(draft["payload"])
                clips = (draft_payload.get("story_plan") or {}).get("clips") or []
                clip = next((item for item in clips if item.get("index") == scene_index), None)
                if clip is None:
                    raise HTTPException(409, "Khong tim thay canh bi loi trong kich ban")
                spoken = clean_spoken_text(voice_script)
                if not spoken:
                    raise HTTPException(422, "Lời thoại phải có nội dung đọc được sau khi bỏ ký hiệu")
                clip["video_prompt"] = video_prompt
                clip["voice_script"] = spoken
                clip.pop("unresolved_voice_placeholder", None)
                if STORY_PLACEHOLDER_RE.search(voice_script):
                    clip["unresolved_voice_placeholder"] = True
                draft_payload["story_plan"]["full_voice_script"] = joined_voice_script(draft_payload["story_plan"])
                version_payload = copy.deepcopy(version["payload"])
                version_payload["story_plan"] = draft_payload["story_plan"]
                event["plan"]["story_plan"] = draft_payload["story_plan"]
                db.execute(
                    update(drafts).where(drafts.c.id == draft_id).values(payload=draft_payload)
                )
                db.execute(
                    update(story_plan_versions)
                    .where(story_plan_versions.c.id == version["id"])
                    .values(payload=version_payload)
                )
                db.execute(
                    update(video_scenes)
                    .where(
                        video_scenes.c.project_id == project["id"],
                        video_scenes.c.plan_version == version["version"],
                        video_scenes.c.scene_index == scene_index,
                    )
                    .values(payload=clip, updated=time.time())
                )
                content = json.dumps(event, ensure_ascii=False)
                db.execute(
                    update(chat_messages)
                    .where(chat_messages.c.id == message_id, chat_messages.c.user_id == uid)
                    .values(content=content)
                )
            return dict(message) | {"content": content}

    def video_project(self, uid: str, project_id: str) -> dict:
        project = self.owned(video_projects, project_id, uid)
        with self.engine.connect() as db:
            versions = db.execute(
                select(story_plan_versions)
                .where(story_plan_versions.c.project_id == project_id)
                .order_by(story_plan_versions.c.version.desc())
            ).mappings().all()
            current_version_created = versions[0]["created"] if versions else 0
            scenes = db.execute(
                select(video_scenes)
                .where(video_scenes.c.project_id == project_id)
                .order_by(video_scenes.c.plan_version, video_scenes.c.scene_index)
            ).mappings().all()
            reports = db.execute(
                select(video_quality_reports)
                .where(
                    video_quality_reports.c.project_id == project_id,
                    video_quality_reports.c.created >= current_version_created,
                )
                .order_by(video_quality_reports.c.created.desc())
            ).mappings().all()
            media = db.execute(
                select(video_media_assets)
                .where(
                    video_media_assets.c.project_id == project_id,
                    video_media_assets.c.created >= current_version_created,
                )
                .order_by(video_media_assets.c.created)
            ).mappings().all()
            root_draft_id = db.execute(
                select(story_plan_versions.c.draft_id)
                .where(story_plan_versions.c.project_id == project_id)
                .order_by(story_plan_versions.c.version.desc())
                .limit(1)
            ).scalar_one_or_none()
            batch = db.execute(
                select(story_batches)
                .where(
                    story_batches.c.user_id == uid,
                    story_batches.c.draft_id == root_draft_id,
                )
                .order_by(story_batches.c.created.desc())
                .limit(1)
            ).mappings().first() if root_draft_id else None
        scene_rows = [dict(row) for row in scenes]
        for scene in scene_rows:
            if scene["generation_id"]:
                try:
                    scene["status"] = self.job(uid, scene["generation_id"])["status"]
                except HTTPException:
                    scene["status"] = "NEEDS_REVIEW"
        if batch:
            project["status"] = batch["status"]
        else:
            active_status = next(
                (scene["status"] for scene in scene_rows if scene.get("generation_id")), None
            )
            if active_status:
                project["status"] = active_status
        return {
            "project": project,
            "plan_versions": [dict(row) for row in versions],
            "scenes": scene_rows,
            "media_assets": [dict(row) for row in media],
            "quality_reports": [dict(row) for row in reports],
            "reference_image_url": (
                self._reference_image_url(project_id, root_draft_id)
                if root_draft_id else None
            ),
        }

    def _reference_image_url(self, project_id: str, draft_id: str) -> str | None:
        anchor = self.storage / "continuity" / f"anchor-{draft_id}.jpg"
        if not anchor.is_file():
            return None
        fingerprint = hashlib.sha256(anchor.read_bytes()).hexdigest()[:16]
        return f"/api/v1/video-projects/{project_id}/reference-image?v={fingerprint}"

    def prepare_video_reference(self, uid: str, project_id: str) -> dict:
        project = self.owned(video_projects, project_id, uid)
        with self.engine.connect() as db:
            version = db.execute(
                select(story_plan_versions).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                    story_plan_versions.c.status.in_(("CURRENT", "APPROVED")),
                )
            ).mappings().first()
        if not version:
            raise HTTPException(404, "Khong tim thay ban kich ban hien tai")
        draft = self.owned(drafts, version["draft_id"], uid)
        anchor = self.storage / "continuity" / f"anchor-{draft['id']}.jpg"
        if not anchor.is_file():
            anchor.parent.mkdir(parents=True, exist_ok=True)
            references = draft["payload"].get("spec", {}).get("references", [])
            asset_id = references[0]["asset_id"] if references else None
            if asset_id:
                if not self._create_product_anchor(draft, uid, anchor):
                    source = self.owned(assets, asset_id, uid)
                    shutil.copyfile(self.storage / source["filename"], anchor)
            else:
                story_plan = draft["payload"].get("story_plan") or {}
                prompt = story_plan.get("reference_image_prompt")
                if not prompt:
                    raise HTTPException(409, "Ban kich ban chua co prompt anh tham chieu")
                ratio = draft["payload"]["spec"]["output"]["aspect_ratio"]
                self.require_image_credits(draft)
                try:
                    response = self.http.post(
                        "/internal/v1/planner/scene-image",
                        json={"prompt": prompt, "aspect_ratio": ratio, "request_id": draft["id"], **self._image_target(draft)},
                        timeout=300,
                    )
                    response.raise_for_status()
                    anchor.write_bytes(response.content)
                    with Image.open(anchor) as image:
                        image.verify()
                except (httpx.HTTPError, OSError, UnidentifiedImageError):
                    anchor.unlink(missing_ok=True)
                    raise HTTPException(502, "Khong tao duoc anh tham chieu tu prompt") from None
                self.charge_image(draft, draft["id"])
        self._record_media_asset(draft, None, "shared_reference_image", anchor, "image/jpeg")
        self._prepare_scene_keyframes(draft, uid)
        story_plan = draft["payload"].get("story_plan") or {}
        grounding_error = None
        try:
            grounded_plan = self._ground_reference_story(uid, project, version, draft, anchor, story_plan)
        except HTTPException as exc:
            grounded_plan = story_plan
            grounding_error = str(exc.detail)
        result = {
            "draft_id": draft["id"],
            "reference_image_url": self._reference_image_url(project_id, draft["id"]),
            "story_plan": grounded_plan,
        }
        if grounding_error:
            result["grounding_error"] = grounding_error
        return result

    def regenerate_failed_first_reference(self, uid: str, project_id: str) -> dict:
        project = self.owned(video_projects, project_id, uid)
        with self.engine.connect() as db:
            version = db.execute(
                select(story_plan_versions).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                    story_plan_versions.c.status == "APPROVED",
                )
            ).mappings().first()
            batch = db.execute(
                select(story_batches).where(
                    story_batches.c.draft_id == version["draft_id"],
                    story_batches.c.user_id == uid,
                )
            ).mappings().first() if version else None
        if not batch or batch["status"] != "FAILED" or len(batch["generation_ids"]) != 1:
            raise HTTPException(409, "Chỉ có thể thay ảnh khi cảnh đầu bị từ chối")
        failed_job = self.store.get(batch["generation_ids"][0])
        if not self._is_unbilled_poyo_failure(failed_job):
            raise HTTPException(409, "Cảnh đầu đã tính phí hoặc không phải lỗi từ chối của PoYo")
        draft = self.owned(drafts, version["draft_id"], uid)
        if draft["payload"].get("spec", {}).get("references"):
            raise HTTPException(409, "Ảnh này do bạn tải lên; hãy tạo kịch bản mới với ảnh khác")
        story = draft["payload"].get("story_plan") or {}
        prompt = story.get("reference_image_prompt")
        if not prompt:
            raise HTTPException(409, "Kịch bản chưa có mô tả ảnh tham chiếu")
        safe_prompt = (
            f"{prompt}\n"
            "Create an original, fictional adult Vietnamese presenter in the same setting. "
            "If inside a car, show it safely parked, with no active driving. "
            "Use one original, unbranded book cover with a short legible fictional Vietnamese title. "
            "Do not depict a real published book cover, real author, logo, celebrity, "
            "public figure, or recognizable existing person. Keep the shot natural and uncluttered."
        )
        anchor = self.storage / "continuity" / f"anchor-{draft['id']}.jpg"
        temporary = anchor.with_name(f"{anchor.stem}-{secrets.token_hex(4)}.jpg")
        self.require_image_credits(draft)
        try:
            response = self.http.post(
                "/internal/v1/planner/scene-image",
                json={
                    "prompt": safe_prompt[:4000],
                    **self._image_target(draft),
                    "request_id": temporary.stem,
                    "aspect_ratio": draft["payload"]["spec"]["output"]["aspect_ratio"],
                },
                timeout=300,
            )
            response.raise_for_status()
            temporary.write_bytes(response.content)
            with Image.open(temporary) as image:
                image.verify()
            self.charge_image(draft, temporary.stem)
            with self.batch_lock:
                current_project = self.owned(video_projects, project_id, uid)
                current = self._story_batch_row(uid, batch["id"])
                if (
                    current_project["current_plan_version"] != project["current_plan_version"]
                    or current["status"] != "FAILED"
                    or current["generation_ids"] != batch["generation_ids"]
                ):
                    raise HTTPException(409, "Trạng thái video đã đổi; không thay ảnh đang được sử dụng")
                temporary.replace(anchor)
                for kind in ("character", "style"):
                    (anchor.parent / f"{kind}-{draft['id']}.jpg").unlink(missing_ok=True)
                # Keyframes were derived from the replaced anchor and would show another person.
                self._clear_scene_keyframes(draft["id"])
        except (httpx.HTTPError, OSError, UnidentifiedImageError):
            raise HTTPException(502, "Không tạo được ảnh thay thế; ảnh cũ vẫn được giữ") from None
        finally:
            temporary.unlink(missing_ok=True)
        return self.prepare_video_reference(uid, project_id)

    def _ground_reference_story(
        self, uid: str, project: dict, version: dict, draft: dict, anchor: Path, story_plan: dict
    ) -> dict | None:
        clips = story_plan.get("clips") or []
        if not clips or (not any(clip.get("unresolved_voice_placeholder") for clip in clips) and not re.search(
            r"book|s[aá]ch|đọc|bìa|tiểu thuyết|\[[^\]\n]{2,100}\]",
            " ".join([draft["title"], story_plan.get("core_value", ""),
                      *(clip.get("voice_script", "") for clip in clips)]),
            re.IGNORECASE,
        )):
            return story_plan or None
        image_bytes = anchor.read_bytes()
        fingerprint = hashlib.sha256(image_bytes).hexdigest()
        if draft["payload"].get("reference_grounding_sha256") == fingerprint:
            return story_plan
        if len(image_bytes) > 11_000_000:
            raise HTTPException(413, "Ảnh tham chiếu quá lớn để đọc chữ; hãy dùng ảnh dưới 11 MB")
        with Image.open(anchor) as image:
            media_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image.format)
        if not media_type:
            raise HTTPException(422, "Định dạng ảnh tham chiếu không hỗ trợ đọc chữ")
        with self.engine.connect() as db:
            batch = db.execute(
                select(story_batches).where(
                    story_batches.c.draft_id == draft["id"], story_batches.c.user_id == uid
                )
            ).mappings().first()
        editable_from = 1
        if batch:
            if batch["status"] == "GENERATING" and not batch["generation_ids"]:
                pass
            elif batch["status"] != "FAILED":
                return story_plan
            else:
                generation_ids = batch["generation_ids"]
                if generation_ids:
                    failed_job = self.store.get(generation_ids[-1])
                    if not self._is_unbilled_scene_failure(failed_job):
                        return story_plan
                    editable_from = len(generation_ids)
        data_url = f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        try:
            response = self.http.post(
                "/internal/v1/planner/ground-reference",
                json={
                    "image_data_url": data_url,
                    "story_plan": story_plan,
                    "user_request": draft["title"],
                },
                timeout=150,
            )
            response.raise_for_status()
            grounding = response.json()["grounding"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise HTTPException(
                502, "Không thể đọc và hoàn thiện kịch bản từ ảnh. Ảnh vẫn được lưu; hãy thử lại."
            ) from None
        if not isinstance(grounding, dict):
            raise HTTPException(502, "Kết quả đọc ảnh không đúng định dạng; hãy thử lại")
        revised = copy.deepcopy(story_plan)
        grounded_clips = grounding.get("clips") or []
        valid_clips = (
            isinstance(grounded_clips, list)
            and all(
                isinstance(clip, dict)
                and isinstance(clip.get("index"), int)
                and isinstance(clip.get("video_prompt"), str)
                and isinstance(clip.get("voice_script"), str)
                for clip in grounded_clips
            )
        )
        if not valid_clips or [clip["index"] for clip in grounded_clips] != [clip["index"] for clip in clips]:
            raise HTTPException(502, "Kết quả đọc ảnh không khớp số cảnh trong kịch bản")
        revised["book_title"] = grounding.get("book_title", "")
        for clip, grounded in zip(revised["clips"], grounded_clips, strict=True):
            if clip["index"] < editable_from:
                continue
            spoken = clean_spoken_text(grounded["voice_script"])
            if not spoken:
                raise HTTPException(502, "AI trả về lời thoại rỗng sau khi bỏ ký hiệu; hãy thử lại")
            clip["video_prompt"] = grounded["video_prompt"]
            clip["voice_script"] = spoken
            clip.pop("unresolved_voice_placeholder", None)
            if STORY_PLACEHOLDER_RE.search(grounded["voice_script"]):
                clip["unresolved_voice_placeholder"] = True
        revised["full_voice_script"] = joined_voice_script(revised)
        draft_payload = copy.deepcopy(draft["payload"])
        draft_payload["story_plan"] = revised
        draft_payload["reference_grounding_sha256"] = fingerprint
        version_payload = copy.deepcopy(version["payload"])
        version_payload["story_plan"] = revised
        with self.engine.begin() as db:
            db.execute(update(drafts).where(drafts.c.id == draft["id"]).values(payload=draft_payload))
            db.execute(
                update(story_plan_versions)
                .where(story_plan_versions.c.id == version["id"])
                .values(payload=version_payload)
            )
            for clip in revised["clips"]:
                if clip["index"] >= editable_from:
                    db.execute(
                        update(video_scenes)
                        .where(
                            video_scenes.c.project_id == project["id"],
                            video_scenes.c.plan_version == version["version"],
                            video_scenes.c.scene_index == clip["index"],
                        )
                        .values(payload=clip, updated=time.time())
                    )
            message_query = select(chat_messages).where(chat_messages.c.user_id == uid)
            if project.get("conversation_id"):
                message_query = message_query.where(
                    chat_messages.c.conversation_id == project["conversation_id"]
                )
            messages = db.execute(message_query).mappings().all()
            for message in messages:
                try:
                    event = json.loads(message["content"])
                except (TypeError, ValueError):
                    continue
                if event.get("type") != "video_generation" or (event.get("plan") or {}).get("id") != draft["id"]:
                    continue
                event["plan"]["story_plan"] = revised
                db.execute(
                    update(chat_messages)
                    .where(chat_messages.c.id == message["id"])
                    .values(content=json.dumps(event, ensure_ascii=False))
                )
        return revised

    def video_scene_reference_path(self, uid: str, project_id: str, scene_index: int) -> Path:
        project = self.owned(video_projects, project_id, uid)
        with self.engine.connect() as db:
            draft_id = db.execute(
                select(story_plan_versions.c.draft_id).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                )
            ).scalar_one_or_none()
        path = self.scene_keyframe_path(draft_id, scene_index) if draft_id and scene_index >= 1 else None
        if path is None:
            raise HTTPException(404, "Canh nay khong co anh mo dau rieng")
        return path

    def video_reference_path(self, uid: str, project_id: str) -> Path:
        project = self.owned(video_projects, project_id, uid)
        with self.engine.connect() as db:
            draft_id = db.execute(
                select(story_plan_versions.c.draft_id).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                )
            ).scalar_one_or_none()
        path = self.storage / "continuity" / f"anchor-{draft_id}.jpg" if draft_id else None
        if path is None or not path.is_file():
            raise HTTPException(404, "Chua co anh tham chieu cho ban kich ban nay")
        return path

    def approve_video_plan(self, uid: str, project_id: str) -> dict:
        project = self.owned(video_projects, project_id, uid)
        with self.engine.begin() as db:
            version = db.execute(
                select(story_plan_versions).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                    story_plan_versions.c.status.in_(("CURRENT", "APPROVED")),
                )
            ).mappings().first()
            if version is None:
                raise HTTPException(409, "Không có bản kịch bản đang chờ duyệt")
            clips = (version["payload"].get("story_plan") or {}).get("clips") or []
            if any(
                clip.get("unresolved_voice_placeholder") or
                STORY_PLACEHOLDER_RE.search(clip.get(field, ""))
                for clip in clips
                for field in ("voice_script", "video_prompt")
            ):
                raise HTTPException(409, "Kịch bản còn chỗ trống. Hãy đọc ảnh tham chiếu trước khi duyệt")
            result = db.execute(
                update(story_plan_versions)
                .where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                    story_plan_versions.c.status.in_(("CURRENT", "APPROVED")),
                )
                .values(status="APPROVED")
            )
            if result.rowcount == 0:
                raise HTTPException(409, "Khong co ban kich ban dang cho duyet")
            db.execute(
                update(video_projects)
                .where(video_projects.c.id == project_id, video_projects.c.user_id == uid)
                .values(status="APPROVED", updated=time.time())
            )
        return {"status": "APPROVED"}

    def generate_approved_video(
        self, uid: str, project_id: str, confirm_paid: bool,
        voice_mode: str | None = None, native_audio: bool | None = None,
        expected_draft_id: str | None = None,
    ) -> dict:
        project = self.owned(video_projects, project_id, uid)
        with self.engine.connect() as db:
            version = db.execute(
                select(story_plan_versions).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                    story_plan_versions.c.status == "APPROVED",
                )
            ).mappings().first()
        if not version:
            raise HTTPException(409, "Hay duyet kich ban truoc khi tao video")
        if expected_draft_id and version["draft_id"] != expected_draft_id:
            raise HTTPException(409, "Kịch bản đã thay đổi. Hãy mở bản nháp mới nhất và duyệt lại trước khi tạo video.")
        return self.generate(uid, version["draft_id"], confirm_paid, native_audio=native_audio, voice_mode=voice_mode)
