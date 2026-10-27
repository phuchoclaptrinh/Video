"""Clip payload construction and provider credential forwarding."""

from app.services.inline_assets import FILE_REF_PREFIX, file_ref
from app.services.platform_shared import (
    HTTPException,
    assets,
    copy,
    drafts,
    hashlib,
    httpx,
    insert,
    logger,
    secrets,
    select,
    time,
    update,
    video_projects,
    video_scenes,
)
from app.services.video_continuity import (
    CUT_TRANSITIONS,
    ContinuityReference,
    ContinuityRequest,
    build_scene_prompt,
    scene_transition,
)
from app.services.video_provider_capabilities import safe_video_url
from app.services.voice_pipeline import VoiceMode, choose_voice_mode

EXECUTION_FIELDS = ("model", "spec", "assets", "preflight_decision", "policy_decision_id")

# Each scene sends at most three images in product mode: the planned scene and two product photos.
PRODUCT_PHOTO_LIMIT = 2

class PlatformGenerationMixin:
    def _with_product_photos(self, draft: dict, strategy, request: ContinuityRequest) -> ContinuityRequest:
        """Product mode (testing): send the seller's own product photos to the video model with the scene image.

        Seedvis only takes several images as reference images, so the scene image stops being a start frame:
        the product follows the photos more closely while the opening composition is no longer pinned.
        """
        payload = draft["payload"]
        if not payload.get("product_reference") or self.provider != "seedvis":
            return request
        if not strategy.capabilities.reference_images:
            return request
        opening = next((ref for ref in request.references if ref.role == "FIRST_FRAME"), None)
        photos = list(payload.get("input_asset_ids") or [
            ref["asset_id"] for ref in payload["spec"].get("references") or []
        ])[:PRODUCT_PHOTO_LIMIT]
        if opening is None or not photos:
            return request
        return ContinuityRequest(
            "product_references",
            (ContinuityReference(opening.kind, opening.identifier, "STYLE_REFERENCE"),
             *(ContinuityReference("asset", photo, "PRODUCT_REFERENCE") for photo in photos)),
            request.parent_generation_id,
            parent_video_url=request.parent_video_url,
            parent_asset_path=request.parent_asset_path,
        )

    def generate(
        self,
        uid: str,
        draft_id: str,
        confirm_paid: bool = False,
        clip_index: int | None = None,
        continuity_generation_id: str | None = None,
        native_audio: bool | None = None,
        voice_mode: str | None = None,
        product_reference: bool | None = None,
    ):
        draft = self.owned(drafts, draft_id, uid)
        if product_reference is not None and bool(draft["payload"].get("product_reference")) != product_reference:
            # Stored on the draft so every scene of this render, and its automatic retries, use the same mode.
            stored = copy.deepcopy(draft["payload"])
            stored["product_reference"] = product_reference
            with self.engine.begin() as db:
                db.execute(update(drafts).where(drafts.c.id == draft_id).values(payload=stored))
            draft = draft | {"payload": stored}
        scoped = self._for_draft(draft)
        if scoped is not self:
            return scoped.generate(uid, draft_id, confirm_paid, clip_index, continuity_generation_id, native_audio, voice_mode)
        if self.provider != "fake" and not self.paid_enabled:
            raise HTTPException(409, "Paid generation is disabled on this server")
        if self.provider in {"poyo", "seedvis"} and not self.provider_credential_status()["configured"]:
            # Shown to members too: say what is wrong without asking them for an admin-only key.
            raise HTTPException(409, f"Model video này chưa sẵn sàng: quản trị viên cần thêm {self.provider.title()} API key. Hãy thử lại sau.")
        if self.provider != "fake" and not confirm_paid:
            raise HTTPException(409, "Bạn cần xác nhận thao tác tạo video có tính phí")
        payload = copy.deepcopy(draft["payload"])
        story_plan = payload.pop("story_plan", None)
        payload.pop("reference_grounding_sha256", None)
        voice_policy = payload.pop("voice_policy", {})
        output_draft = draft
        persisted_payload = None
        if not story_plan and draft["generation_id"] and self._is_unbilled_scene_failure(self.store.get(draft["generation_id"])):
            payload["spec"]["request"]["request_id"] = secrets.token_hex(16)
            persisted_payload = copy.deepcopy(draft["payload"])
            persisted_payload["spec"]["request"]["request_id"] = payload["spec"]["request"]["request_id"]
        if story_plan:
            if clip_index is None:
                if native_audio is False:
                    raise HTTPException(422, "native_audio_required_for_native_voice_mode: consistent voice needs real alignment and lip-sync")
                return self.generate_story_batch(uid, draft, confirm_paid, voice_mode=voice_mode)
            clip = next(
                (item for item in story_plan["clips"] if item["index"] == clip_index), None
            )
            if clip is None:
                raise HTTPException(422, "Phân đoạn kịch bản không hợp lệ")
            parent_output = self._validate_scene_parent(draft, clip_index, continuity_generation_id)
            scene_reference = self.storage / "continuity" / f"anchor-{draft_id}.jpg"
            strategy = self._continuity_strategy()
            payload["spec"]["output"]["duration_seconds"] = clip.get("duration_seconds", 8)
            native_audio = native_audio if native_audio is not None else self.provider != "fake"
            clip_draft_id = hashlib.sha256(f"{draft_id}:clip:{clip_index}".encode()).hexdigest()[:32]
            with self.engine.connect() as db:
                existing = db.execute(select(drafts).where(drafts.c.id == clip_draft_id)).mappings().first()
            if existing and existing["generation_id"]:
                previous_job = self.store.get(existing["generation_id"])
                if not self._is_unbilled_scene_failure(previous_job):
                    return self.present(dict(existing))
            if self.provider in {"seedance", "poyo", "seedvis"}:
                payload["spec"]["output"]["generate_audio"] = native_audio
            try:
                continuity_request = (
                    strategy.build_continuation_request(
                        draft_id, continuity_generation_id, has_anchor=scene_reference.is_file(),
                        parent_video_url=(parent_output or {}).get("output_video_url"),
                        parent_asset_path=(parent_output or {}).get("output_asset_path"),
                        duration_seconds=payload["spec"]["output"]["duration_seconds"],
                    )
                    if clip_index > 1 and strategy.requires_parent
                    else strategy.build_initial_request(draft_id, scene_reference.is_file(),
                                                        duration_seconds=payload["spec"]["output"]["duration_seconds"])
                )
                keyframe = self.scene_keyframe_path(draft_id, clip_index) if clip_index > 1 else None
                if keyframe and scene_transition(clip) in CUT_TRANSITIONS and strategy.capabilities.first_frame:
                    # A cut opens on its own generated keyframe instead of the previous clip's last frame.
                    continuity_request = ContinuityRequest(
                        "first_frame",
                        (ContinuityReference("keyframe", f"{draft_id}-{clip_index}", "FIRST_FRAME"),),
                        continuity_request.parent_generation_id,
                        parent_video_url=continuity_request.parent_video_url,
                        parent_asset_path=continuity_request.parent_asset_path,
                    )
                continuity_request = self._with_product_photos(draft, strategy, continuity_request)
                if native_audio and not clip["voice_script"].strip():
                    raise ValueError("native_audio_requires_scene_dialogue")
                if native_audio and not strategy.native_audio_supported:
                    raise ValueError("native_audio_unsupported_model")
                if strategy.capabilities.generation_durations and payload["spec"]["output"]["duration_seconds"] not in strategy.capabilities.generation_durations:
                    raise ValueError("unsupported_scene_duration")
                payload["spec"]["intent"]["goal"] = build_scene_prompt(
                    story_plan, clip, continuity_request, native_audio=native_audio,
                    product=bool(draft["payload"]["spec"].get("references")),
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            payload["spec"].setdefault("story", {})["shots"] = []
            if continuity_request.mode == "image_references":
                self._ensure_character_reference_images(draft_id, scene_reference)
            if continuity_request.references:
                payload["spec"]["intent"]["operation"] = "REFERENCE_GENERATION"
                payload["spec"]["references"] = []
                payload["assets"] = []
                for reference in continuity_request.references:
                    asset_id = (
                        f"{reference.kind}-{reference.identifier}"
                        if reference.kind in {"character", "style"} else reference.identifier
                    )
                    if reference.kind == "asset":
                        stored = self.owned(assets, reference.identifier, uid)
                        source = file_ref(self.storage, self.storage / stored["filename"])
                    else:
                        source = self._continuity_source(reference.kind, reference.identifier)
                    payload["spec"]["references"].append({
                        "asset_id": asset_id, "role": reference.role, "strength": "high",
                    })
                    payload["assets"].append({
                        "asset_id": asset_id, "mime_type": "image/jpeg",
                        "source_url": source,
                        "moderation_status": "APPROVED",
                    })
            payload["spec"].setdefault("constraints", {})["preserve_identity"] = bool(
                payload["spec"].get("references")
            )
            generated_already = False
            with self.engine.begin() as db:
                child = (
                    db.execute(select(drafts).where(drafts.c.id == clip_draft_id))
                    .mappings()
                    .first()
                )
                if child is None:
                    payload["spec"]["request"]["request_id"] = clip_draft_id
                    clip_title = f"{draft['title']} · Clip {clip_index}"
                    clip_created = time.time()
                    db.execute(
                        insert(drafts).values(
                            id=clip_draft_id,
                            user_id=uid,
                            title=clip_title,
                            payload=payload,
                            created=clip_created,
                        )
                    )
                    output_draft = {
                        "id": clip_draft_id,
                        "user_id": uid,
                        "title": clip_title,
                        "payload": payload,
                        "generation_id": None,
                        "created": clip_created,
                    }
                else:
                    output_draft = dict(child)
                    if child["generation_id"]:
                        previous_job = self.store.get(child["generation_id"])
                        if self._is_unbilled_scene_failure(previous_job):
                            payload["spec"]["request"]["request_id"] = secrets.token_hex(16)
                            output_draft["generation_id"] = None
                            output_draft["payload"] = payload
                            db.execute(
                                update(drafts)
                                .where(drafts.c.id == clip_draft_id)
                                .values(generation_id=None, payload=payload)
                            )
                        else:
                            generated_already = True
                    else:
                        payload["spec"]["request"]["request_id"] = secrets.token_hex(16)
                        output_draft["payload"] = payload
                        db.execute(
                            update(drafts)
                            .where(drafts.c.id == clip_draft_id)
                            .values(payload=payload)
                        )
            if generated_already:
                return self.present(output_draft)
        elif clip_index is not None:
            raise HTTPException(422, "Bản nháp này không có phân đoạn kịch bản")
        elif native_audio is not None or voice_mode is not None:
            enabled = native_audio if native_audio is not None else True
            capabilities = self._continuity_strategy().capabilities
            if voice_mode == VoiceMode.CONSISTENT_VOICE and not enabled:
                raise HTTPException(422, "consistent_voice_infrastructure_incomplete: cannot disable audio during native fallback")
            if not enabled and self.provider != "fake" and not capabilities.audio_control:
                raise HTTPException(422, "native_audio_disable_unsupported_model: configured model has no verified audio-off control")
            decision = choose_voice_mode(1, voice_mode, tts=self.planner_configured,
                                         audio_control=capabilities.audio_control)
            voice_policy = decision.metadata()
            persisted_payload = copy.deepcopy(persisted_payload or draft["payload"])
            persisted_payload["voice_policy"] = voice_policy
            persisted_payload["spec"]["output"]["generate_audio"] = enabled
            payload["spec"]["output"]["generate_audio"] = enabled
            logger.info("[VIDEO] draft=%s requested_voice_mode=%s voice_mode=%s native_audio=%s limitations=%s",
                        draft_id, decision.requested.value, decision.effective.value, enabled, decision.blockers)
        if self.provider in {"seedance", "seedvis"}:
            payload["policy_decision_id"] = "manual-review-" + draft_id
        if self.provider in {"seedance", "seedvis"} or (
            self.provider == "poyo" and not self._poyo_public_urls
        ):
            for item in payload["assets"]:
                if item["source_url"].startswith(("data:image/jpeg;base64,", FILE_REF_PREFIX)):
                    continue
                asset = self.owned(assets, item["asset_id"], uid)
                item["source_url"] = file_ref(self.storage, self.storage / asset["filename"])
        elif self.provider == "poyo" and payload["assets"] and not all(
            item["source_url"].startswith("https://") for item in payload["assets"]
        ):
            raise HTTPException(
                422,
                "PoYo yêu cầu URL ảnh HTTPS công khai; bản local hiện chỉ hỗ trợ text-to-video",
            )
        # Drafts carry planning-only fields; the execution contract forbids anything else.
        execution_payload = {key: payload[key] for key in EXECUTION_FIELDS if key in payload}
        if payload.get("render_target"):
            execution_payload["model"] = payload["render_target"]["video_model"]
        seconds = int(payload["spec"]["output"].get("duration_seconds") or 0)
        cost = self.clip_cost(self.provider, execution_payload.get("model"), seconds)
        self.require_credits(uid, cost)
        job = self.runtime.enqueue(self.provider, execution_payload)
        # Charged once per provider job; the worker refunds it if the scene ends without a video.
        self.charge_credits(uid, cost, "render", "render:" + job["generation_id"],
                            f"{draft['title'][:80]} · {seconds}s", settled=False)
        with self.engine.begin() as db:
            values = {"generation_id": job["generation_id"]}
            if persisted_payload is not None:
                values["payload"] = persisted_payload
                output_draft = output_draft | {"payload": persisted_payload}
            db.execute(
                update(drafts)
                .where(drafts.c.id == output_draft["id"])
                .values(**values)
            )
            scene_draft_id = draft_id if story_plan else output_draft["id"]
            scene_filter = [video_scenes.c.draft_id == scene_draft_id]
            if story_plan and clip_index is not None:
                scene_filter.append(video_scenes.c.scene_index == clip_index)
            db.execute(
                update(video_scenes)
                .where(*scene_filter)
                .values(
                    generation_id=job["generation_id"],
                    status="QUEUED",
                    updated=time.time(),
                )
            )
            project_ids = db.execute(
                select(video_scenes.c.project_id).where(*scene_filter)
            ).scalars().all()
            if project_ids:
                db.execute(
                    update(video_projects)
                    .where(video_projects.c.id.in_(project_ids), video_projects.c.user_id == uid)
                    .values(status="GENERATING", updated=time.time())
                )
        if story_plan and clip_index is not None:
            self._record_scene_continuity(
                draft, clip_index, generation_id=job["generation_id"],
                parent_generation_id=continuity_request.parent_generation_id,
                parent_video_url=continuity_request.parent_video_url,
                parent_asset_path=continuity_request.parent_asset_path,
                output_video_url=None, output_asset_path=None, duration_seconds=None,
                continuation_generation_id=None, reference_mode=continuity_request.mode,
                native_audio=native_audio, native_continuation=continuity_request.native_continuation,
                voice_mode=voice_policy.get("voice_mode", VoiceMode.NATIVE_VEO.value),
                requested_voice_mode=voice_policy.get("requested_voice_mode", VoiceMode.NATIVE_VEO.value),
                voice_mode_limitations=voice_policy.get("voice_mode_limitations", []),
                first_frame_present=any(ref.role == "FIRST_FRAME" for ref in continuity_request.references),
                character_reference_present=any(ref.role == "CHARACTER_REFERENCE" for ref in continuity_request.references),
                first_frame_exact=continuity_request.mode == "first_frame",
                continuity_mode="BEST_EFFORT" if continuity_request.references else "UNSUPPORTED",
                fallback_reason=strategy.capabilities.reason if clip_index > 1 else None,
                continuity_status="GENERATING", continuity_error=None,
                retry=child is not None,
            )
            logger.info(
                "[VIDEO] story=%s scene=%s duration=%s voice_mode=%s native_audio=%s "
                "first_frame_present=%s character_reference_present=%s transition_intent=%s",
                draft_id, clip_index, payload["spec"]["output"]["duration_seconds"],
                voice_policy.get("voice_mode", VoiceMode.NATIVE_VEO.value), native_audio,
                any(ref.role == "FIRST_FRAME" for ref in continuity_request.references),
                any(ref.role == "CHARACTER_REFERENCE" for ref in continuity_request.references),
                scene_transition(clip),
            )
            logger.info(
                "[VIDEO] story=%s scene=%s provider=%s model=%s native_audio=%s continuation=%s "
                "parent_generation=%s parent_video_url=%s fallback=%s reason=%s voice_continuity=%s",
                draft_id, clip_index, self.provider, strategy.model, native_audio,
                continuity_request.native_continuation, continuity_request.parent_generation_id,
                safe_video_url(continuity_request.parent_video_url), continuity_request.mode,
                strategy.capabilities.reason if clip_index > 1 else "initial_generation",
                "BEST_EFFORT" if native_audio else "UNSUPPORTED",
            )
            if clip_index > 1 and not strategy.capabilities.video_continuation:
                logger.warning(
                    "[VIDEO] continuation unsupported story=%s scene=%s reason=%s fallback=%s voice_continuity=%s",
                    draft_id, clip_index, strategy.capabilities.reason, continuity_request.mode,
                    "BEST_EFFORT" if native_audio else "UNSUPPORTED",
                )
        return self.present(output_draft | {"generation_id": job["generation_id"]})

    def provider_credential_status(self, provider: str | None = None) -> dict:
        provider = provider or self.provider
        if provider not in {"poyo", "seedvis"}:
            return {"configured": True}
        return self._credential_request("GET", provider=provider)

    def planner_credential_status(self) -> dict:
        try:
            response = self.http.get("/internal/v1/planner/credential", timeout=3)
            response.raise_for_status()
            configured = response.json().get("configured") is True
        except (httpx.HTTPError, ValueError):
            configured = False
        self.planner_configured = configured
        return {"configured": configured}

    def set_planner_credential(self, api_key: str) -> dict:
        return self._planner_credential_request("PUT", {"api_key": api_key})

    def delete_planner_credential(self) -> dict:
        return self._planner_credential_request("DELETE")

    def _planner_credential_request(self, method: str, payload: dict | None = None) -> dict:
        try:
            response = self.http.request(method, "/internal/v1/planner/credential", json=payload)
            response.raise_for_status()
            configured = response.json().get("configured") is True
            self.planner_configured = configured
            return {"configured": configured}
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Không thể cấu hình LLM planner") from None

    def set_provider_credential(self, api_key: str, provider: str | None = None) -> dict:
        provider = provider or self.provider
        if provider not in {"poyo", "seedvis"}:
            raise HTTPException(409, "Provider hiện tại không hỗ trợ cấu hình key qua UI")
        return self._credential_request("PUT", {"api_key": api_key}, provider=provider)

    def delete_provider_credential(self, provider: str | None = None) -> dict:
        provider = provider or self.provider
        if provider not in {"poyo", "seedvis"}:
            raise HTTPException(409, "Provider hiện tại không hỗ trợ cấu hình key qua UI")
        return self._credential_request("DELETE", provider=provider)

    def _credential_request(self, method: str, payload: dict | None = None, *, provider: str | None = None) -> dict:
        provider = provider or self.provider
        if provider not in {"poyo", "seedvis"}:
            raise HTTPException(422, "Unsupported credential provider")
        try:
            response = self.http.request(method, f"/internal/v1/video/{provider}/credential", json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("configured"), bool):
                raise ValueError
            return {"configured": data["configured"]}
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, f"Không thể cấu hình {provider.title()} API key") from None
