"""Account sessions, chat history, user memories, and uploaded assets."""

from app.services.platform_shared import (
    HTTPException,
    Image,
    ImageOps,
    IntegrityError,
    UnidentifiedImageError,
    assets,
    chat_messages,
    conversations,
    delete,
    hashlib,
    hmac,
    httpx,
    insert,
    io,
    json,
    memories,
    password_hash,
    secrets,
    select,
    sessions,
    story_batches,
    story_plan_versions,
    time,
    update,
    users,
    video_projects,
)


class PlatformIdentityMixin:
    def login(self, email: str, password: str, register: bool):
        with self.engine.begin() as db:
            if register:
                uid = secrets.token_hex(16)
                try:
                    db.execute(
                        insert(users).values(
                            id=uid, email=email, password=password_hash(password),
                            created=time.time(),
                        )
                    )
                    self.grant_signup_credits(db, uid)
                except IntegrityError:
                    raise HTTPException(409, "Email đã được đăng ký") from None
            row = db.execute(select(users).where(users.c.email == email)).mappings().first()
            stored = row["password"] if row else password_hash("dummy-password")
            if not row or not hmac.compare_digest(
                password_hash(password, stored.split(":")[0]), stored
            ):
                raise HTTPException(401, "Email hoặc mật khẩu không đúng")
            if row["disabled"]:
                raise HTTPException(403, "Tài khoản đã bị khóa. Liên hệ quản trị viên.")
            token = secrets.token_urlsafe(32)
            db.execute(delete(sessions).where(sessions.c.expires < time.time()))
            db.execute(
                insert(sessions).values(
                    token=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=row["id"],
                    expires=time.time() + 86400,
                )
            )
            return token, {"id": row["id"], "email": row["email"]}

    def user(self, token: str):
        with self.engine.connect() as db:
            row = (
                db.execute(
                    select(users.c.id, users.c.email, users.c.is_admin)
                    .join(sessions, sessions.c.user_id == users.c.id)
                    .where(
                        sessions.c.token == hashlib.sha256(token.encode()).hexdigest(),
                        sessions.c.expires > time.time(),
                        users.c.disabled == 0,
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            raise HTTPException(401, "Vui lòng đăng nhập")
        return dict(row)

    def owned(self, table, identifier: str, uid: str):
        with self.engine.connect() as db:
            row = (
                db.execute(select(table).where(table.c.id == identifier, table.c.user_id == uid))
                .mappings()
                .first()
            )
        if row is None:
            raise HTTPException(404, "Không tìm thấy dữ liệu")
        return dict(row)

    def conversation_list(self, uid: str) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(
                select(conversations)
                .where(conversations.c.user_id == uid)
                .order_by(conversations.c.updated.desc())
                .limit(100)
            ).mappings().all()
        return [dict(row) for row in rows]

    def conversation_messages(self, uid: str, conversation_id: str) -> list[dict]:
        self.owned(conversations, conversation_id, uid)
        with self.engine.connect() as db:
            rows = db.execute(
                select(chat_messages)
                .where(
                    chat_messages.c.user_id == uid,
                    chat_messages.c.conversation_id == conversation_id,
                )
                .order_by(chat_messages.c.created, chat_messages.c.id)
            ).mappings().all()
        return [dict(row) for row in rows]

    def save_video_event(
        self, uid: str, conversation_id: str | None, prompt: str, plan: dict
    ) -> dict:
        now = time.time()
        is_new = conversation_id is None
        conversation_id = conversation_id or secrets.token_hex(16)
        message_id = secrets.token_hex(16)
        title = prompt[:58].strip() + ("…" if len(prompt) > 58 else "")
        content = json.dumps(
            {
                "type": "video_generation",
                "prompt": prompt,
                "plan": plan,
                "project_id": plan.get("project_id"),
                "reference_image_url": f"/api/v1/assets/{plan['input_asset_ids'][0]}/file" if plan.get("input_asset_ids") else None,
                "job": None,
            },
            ensure_ascii=False,
        )
        with self.engine.begin() as db:
            project = db.execute(
                select(video_projects).where(
                    video_projects.c.id == plan.get("project_id"),
                    video_projects.c.user_id == uid,
                )
            ).mappings().first()
            if project is None:
                raise HTTPException(404, "Không tìm thấy video project")
            plan_version = db.execute(
                select(story_plan_versions.c.id).where(
                    story_plan_versions.c.project_id == project["id"],
                    story_plan_versions.c.draft_id == plan.get("id"),
                )
            ).first()
            if plan_version is None:
                raise HTTPException(409, "Bản kế hoạch không thuộc video project")
            if not is_new:
                owner = db.execute(
                    select(conversations.c.id).where(
                        conversations.c.id == conversation_id,
                        conversations.c.user_id == uid,
                    )
                ).first()
                if owner is None:
                    raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
            else:
                db.execute(
                    insert(conversations).values(
                        id=conversation_id,
                        user_id=uid,
                        title=title or "Tạo video",
                        created=now,
                        updated=now,
                    )
                )
            user_message = {
                "id": secrets.token_hex(16),
                "conversation_id": conversation_id,
                "user_id": uid,
                "role": "user",
                "content": prompt,
                "created": now,
            }
            assistant_message = {
                "id": message_id,
                "conversation_id": conversation_id,
                "user_id": uid,
                "role": "assistant",
                "content": content,
                "created": now + 0.000001,
            }
            db.execute(insert(chat_messages), [user_message, assistant_message])
            db.execute(
                update(conversations)
                .where(conversations.c.id == conversation_id, conversations.c.user_id == uid)
                .values(updated=now)
            )
            db.execute(
                update(video_projects)
                .where(video_projects.c.id == project["id"], video_projects.c.user_id == uid)
                .values(conversation_id=conversation_id, updated=now)
            )
        return {"conversation_id": conversation_id, "messages": [user_message, assistant_message]}

    def update_video_event(self, uid: str, message_id: str, job_id: str) -> dict:
        job = self.job(uid, job_id)
        with self.engine.begin() as db:
            row = db.execute(
                select(chat_messages).where(
                    chat_messages.c.id == message_id,
                    chat_messages.c.user_id == uid,
                    chat_messages.c.role == "assistant",
                )
            ).mappings().first()
            if row is None:
                raise HTTPException(404, "Không tìm thấy yêu cầu video trong cuộc trò chuyện")
            try:
                event = json.loads(row["content"])
            except (TypeError, ValueError):
                raise HTTPException(422, "Dữ liệu yêu cầu video không hợp lệ") from None
            if event.get("type") != "video_generation":
                raise HTTPException(422, "Tin nhắn này không phải yêu cầu tạo video")
            expected_draft_id = (event.get("plan") or {}).get("id")
            actual_draft_id = job.get("draft_id")
            if not actual_draft_id:
                batch = db.execute(
                    select(story_batches.c.draft_id).where(
                        story_batches.c.id == job_id, story_batches.c.user_id == uid
                    )
                ).scalar_one_or_none()
                actual_draft_id = batch
            if not expected_draft_id or actual_draft_id != expected_draft_id:
                raise HTTPException(409, "Video job không thuộc kế hoạch trong cuộc trò chuyện")
            event["job"] = job
            db.execute(
                update(chat_messages)
                .where(chat_messages.c.id == message_id, chat_messages.c.user_id == uid)
                .values(content=json.dumps(event, ensure_ascii=False))
            )
            db.execute(
                update(conversations)
                .where(
                    conversations.c.id == row["conversation_id"], conversations.c.user_id == uid
                )
                .values(updated=time.time())
            )
            project_id = event.get("project_id")
            status = job.get("status", "GENERATING")
            db.execute(
                update(video_projects)
                .where(video_projects.c.id == project_id, video_projects.c.user_id == uid)
                .values(status=status, updated=time.time())
            )
            return dict(row) | {"content": json.dumps(event, ensure_ascii=False)}

    def update_video_event_plan(self, uid: str, message_id: str, plan: dict) -> dict:
        with self.engine.begin() as db:
            row = db.execute(
                select(chat_messages).where(
                    chat_messages.c.id == message_id,
                    chat_messages.c.user_id == uid,
                    chat_messages.c.role == "assistant",
                )
            ).mappings().first()
            if row is None:
                raise HTTPException(404, "Không tìm thấy yêu cầu video trong cuộc trò chuyện")
            try:
                event = json.loads(row["content"])
            except (TypeError, ValueError):
                raise HTTPException(422, "Dữ liệu yêu cầu video không hợp lệ") from None
            project_id = event.get("project_id")
            if event.get("type") != "video_generation" or plan.get("project_id") != project_id:
                raise HTTPException(409, "Bản kế hoạch không thuộc video project này")
            project = db.execute(
                select(video_projects).where(
                    video_projects.c.id == project_id, video_projects.c.user_id == uid
                )
            ).mappings().first()
            if project is None:
                raise HTTPException(404, "Không tìm thấy video project")
            version = db.execute(
                select(story_plan_versions.c.id).where(
                    story_plan_versions.c.project_id == project_id,
                    story_plan_versions.c.version == project["current_plan_version"],
                    story_plan_versions.c.draft_id == plan.get("id"),
                    story_plan_versions.c.status == "CURRENT",
                )
            ).first()
            if version is None:
                raise HTTPException(404, "Không tìm thấy phiên bản kịch bản hiện tại")
            event["prompt"] = project["brief"]
            event["plan"] = plan
            event["job"] = None
            event.pop("quality_reports", None)
            content = json.dumps(event, ensure_ascii=False)
            db.execute(
                update(chat_messages)
                .where(chat_messages.c.id == message_id, chat_messages.c.user_id == uid)
                .values(content=content)
            )
            db.execute(
                update(conversations)
                .where(
                    conversations.c.id == row["conversation_id"], conversations.c.user_id == uid
                )
                .values(updated=time.time())
            )
            return dict(row) | {"content": content}

    def delete_conversation(self, uid: str, conversation_id: str) -> None:
        self.owned(conversations, conversation_id, uid)
        with self.engine.begin() as db:
            db.execute(
                delete(chat_messages).where(
                    chat_messages.c.user_id == uid,
                    chat_messages.c.conversation_id == conversation_id,
                )
            )
            db.execute(
                delete(conversations).where(
                    conversations.c.user_id == uid, conversations.c.id == conversation_id
                )
            )

    def user_memories(self, uid: str) -> list[dict]:
        with self.engine.connect() as db:
            rows = db.execute(
                select(memories)
                .where(memories.c.user_id == uid)
                .order_by(memories.c.created.desc())
                .limit(50)
            ).mappings().all()
        return [dict(row) for row in rows]

    def add_memory(self, uid: str, content: str) -> dict:
        identifier = secrets.token_hex(16)
        row = {"id": identifier, "user_id": uid, "content": content, "created": time.time()}
        with self.engine.begin() as db:
            db.execute(insert(memories).values(**row))
        return row

    def delete_memory(self, uid: str, memory_id: str) -> None:
        with self.engine.begin() as db:
            result = db.execute(
                delete(memories).where(memories.c.id == memory_id, memories.c.user_id == uid)
            )
        if not result.rowcount:
            raise HTTPException(404, "Không tìm thấy ghi nhớ")

    def chat(self, uid: str, conversation_id: str | None, content: str) -> dict:
        if not self.planner_credential_status()["configured"]:
            raise HTTPException(409, "Hãy thêm OpenAI API key để sử dụng chat AI")
        new_conversation = conversation_id is None
        now = time.time()
        with self.engine.begin() as db:
            if conversation_id:
                row = db.execute(
                    select(conversations).where(
                        conversations.c.id == conversation_id,
                        conversations.c.user_id == uid,
                    )
                ).mappings().first()
                if row is None:
                    raise HTTPException(404, "Không tìm thấy cuộc trò chuyện")
                title = row["title"]
                prior = db.execute(
                    select(chat_messages.c.role, chat_messages.c.content)
                    .where(
                        chat_messages.c.user_id == uid,
                        chat_messages.c.conversation_id == conversation_id,
                    )
                    .order_by(chat_messages.c.created.desc(), chat_messages.c.id.desc())
                    .limit(24)
                ).all()
            else:
                conversation_id = secrets.token_hex(16)
                title = content[:58].strip() + ("…" if len(content) > 58 else "")
                prior = []
            saved_memories = db.execute(
                select(memories.c.content)
                .where(memories.c.user_id == uid)
                .order_by(memories.c.created.desc())
                .limit(20)
            ).scalars().all()

        messages = [
            {"role": "system", "content": "Bạn là trợ lý AI hữu ích, trả lời bằng tiếng Việt tự nhiên khi phù hợp. Dùng lịch sử hội thoại và phần ghi nhớ do người dùng chủ động lưu để giữ ngữ cảnh. Không khẳng định đã nhớ thông tin nếu nó không có trong ngữ cảnh."}
        ]
        if saved_memories:
            messages.append(
                {"role": "system", "content": "Ghi nhớ do người dùng lưu:\n- " + "\n- ".join(saved_memories)}
            )
        context = []
        context_length = 0
        for role, text in reversed(prior):
            if role not in {"user", "assistant"} or context_length >= 32000:
                continue
            text = text[:6000]
            if role == "assistant":
                try:
                    event = json.loads(text)
                    if event.get("type") == "video_generation":
                        job = event.get("job") or {}
                        text = (
                            f"[Yêu cầu tạo video] {event.get('prompt', '')}. "
                            f"Trạng thái: {job.get('status', 'đang xem kế hoạch')}"
                        )
                except (TypeError, ValueError):
                    pass
            context.append({"role": role, "content": text})
            context_length += len(text)
        messages.extend(reversed(context))
        messages.append({"role": "user", "content": content})
        try:
            response = self.http.post(
                "/internal/v1/chat/completions", json={"messages": messages}, timeout=150
            )
            response.raise_for_status()
            answer = response.json()["message"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                raise HTTPException(409, "Hãy kiểm tra OpenAI API key trong phần API key") from None
            raise HTTPException(502, "Không thể kết nối AI lúc này. Hãy thử lại.") from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise HTTPException(502, "AI trả về phản hồi không hợp lệ. Hãy thử lại.") from None

        now = time.time()
        user_message = {
            "id": secrets.token_hex(16),
            "conversation_id": conversation_id,
            "user_id": uid,
            "role": "user",
            "content": content,
            "created": now,
        }
        assistant_message = {
            "id": secrets.token_hex(16),
            "conversation_id": conversation_id,
            "user_id": uid,
            "role": "assistant",
            "content": answer,
            "created": now + 0.000001,
        }
        with self.engine.begin() as db:
            if new_conversation:
                db.execute(
                    insert(conversations).values(
                        id=conversation_id,
                        user_id=uid,
                        title=title or "Cuộc trò chuyện mới",
                        created=now,
                        updated=now,
                    )
                )
            db.execute(insert(chat_messages), [user_message, assistant_message])
            db.execute(
                update(conversations)
                .where(conversations.c.id == conversation_id, conversations.c.user_id == uid)
                .values(updated=now)
            )
        return {"conversation_id": conversation_id, "title": title, "messages": [user_message, assistant_message]}

    def upload(self, data: bytes, uid: str):
        try:
            with Image.open(io.BytesIO(data)) as img:
                if img.format not in {"JPEG", "PNG", "WEBP"} or img.width * img.height > 20000000:
                    raise ValueError
                img.load()
                normalized = ImageOps.exif_transpose(img).convert("RGB")
                normalized.thumbnail((2048, 2048))
                identifier = secrets.token_hex(16)
                filename = identifier + ".jpg"
                normalized.save(self.storage / filename, "JPEG", quality=90)
                width, height = normalized.size
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(
                422, "Ảnh không hợp lệ; dùng JPEG, PNG hoặc WebP dưới 20 megapixel"
            ) from None
        with self.engine.begin() as db:
            db.execute(
                insert(assets).values(
                    id=identifier, user_id=uid, filename=filename, width=width, height=height
                )
            )
        return {
            "id": identifier,
            "url": f"/api/v1/assets/{identifier}/file",
            "width": width,
            "height": height,
        }
