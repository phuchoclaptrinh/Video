"""Local MVP platform: authenticated assets, reviewed drafts and execution delivery."""

import base64
import copy
import hashlib
import hmac
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import textwrap
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import (
    JSON,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    insert,
    inspect as sqlalchemy_inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError

from app.services.generation_orchestrator import ExecutionSettings, GenerationOrchestrator
from app.services.spoken_text import STORY_PLACEHOLDER_RE as STORY_PLACEHOLDER_RE
from app.services.video_execution_client import VideoExecutionClient
from app.services.video_execution_store import ExecutionStore, executions

logger = logging.getLogger(__name__)
STORY_TTS_PROFILE = "vi-natural-v2"
meta = MetaData()
DEFAULT_VISUAL_CONTINUITY = (
    "Keep the same requested subject identity, wardrobe, important props, setting, lighting, "
    "camera framing, and color palette across scenes. Do not add unrelated people, objects, "
    "logos, or readable text."
)
users = Table(
    "platform_users",
    meta,
    Column("id", String, primary_key=True),
    Column("email", String, unique=True),
    Column("password", String),
    Column("is_admin", Integer, nullable=False, server_default="0"),
    Column("disabled", Integer, nullable=False, server_default="0"),
    Column("created", Float),
    Column("credits", Integer, nullable=False, server_default="0"),
)
# Every credit movement; `users.credits` is the running balance kept in the same transaction.
credit_ledger = Table(
    "platform_credit_ledger", meta,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False, index=True),
    Column("delta", Integer, nullable=False),
    Column("balance", Integer, nullable=False),
    Column("kind", String, nullable=False),
    # Unique per charge, so a retried submission or refund is never applied twice.
    Column("ref", String, unique=True),
    Column("note", String),
    Column("actor_id", String),
    # Render charges stay 0 until the scene either produces a video or is refunded.
    Column("settled", Integer, nullable=False, server_default="1", index=True),
    Column("created", Float, nullable=False),
)
platform_settings = Table(
    "platform_settings", meta,
    Column("key", String, primary_key=True),
    Column("value", JSON, nullable=False),
    Column("updated", Float, nullable=False),
)
sessions = Table(
    "platform_sessions",
    meta,
    Column("token", String, primary_key=True),
    Column("user_id", String),
    Column("expires", Float),
)
assets = Table(
    "platform_assets",
    meta,
    Column("id", String, primary_key=True),
    Column("user_id", String),
    Column("filename", String),
    Column("width", Integer),
    Column("height", Integer),
)
drafts = Table(
    "platform_drafts",
    meta,
    Column("id", String, primary_key=True),
    Column("user_id", String),
    Column("title", String),
    Column("payload", JSON),
    Column("generation_id", String),
    Column("created", Float),
)
videos = Table(
    "platform_videos",
    meta,
    Column("generation_id", String, primary_key=True),
    Column("user_id", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("created", Float, nullable=False),
)
story_batches = Table(
    "platform_story_batches",
    meta,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False),
    Column("title", String, nullable=False),
    Column("draft_id", String),
    Column("ratio", String, nullable=False),
    Column("generation_ids", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("audio_mode", String, nullable=False, server_default="narration"),
    Column("filename", String),
    Column("size_bytes", Integer),
    Column("error_code", String),
    Column("created", Float, nullable=False),
    # Automatic retries already used, and when the next one is due (None: not scheduled).
    Column("auto_retries", Integer, nullable=False, server_default="0"),
    Column("retry_after", Float),
)
conversations = Table(
    "platform_conversations",
    meta,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False, index=True),
    Column("title", String, nullable=False),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
)
chat_messages = Table(
    "platform_chat_messages",
    meta,
    Column("id", String, primary_key=True),
    Column("conversation_id", String, nullable=False, index=True),
    Column("user_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("content", String, nullable=False),
    Column("created", Float, nullable=False),
)
memories = Table(
    "platform_user_memories",
    meta,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False, index=True),
    Column("content", String, nullable=False),
    Column("created", Float, nullable=False),
)
video_projects = Table(
    "platform_video_projects",
    meta,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False, index=True),
    Column("conversation_id", String, index=True),
    Column("brief", String, nullable=False),
    Column("status", String, nullable=False),
    Column("current_plan_version", Integer, nullable=False),
    Column("context_snapshot", JSON, nullable=False),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
)
marketing_campaigns = Table(
    "platform_marketing_campaigns", meta,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False, index=True),
    Column("conversation_id", String, nullable=False, index=True),
    Column("brief", String, nullable=False),
    Column("script_count", Integer, nullable=False),
    Column("inputs", JSON, nullable=False),
    Column("context_snapshot", JSON, nullable=False),
    Column("strategy", JSON),
    Column("items", JSON, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("error", String),
    Column("cancel_requested", Integer, nullable=False, default=0),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
)
style_references = Table(
    "platform_style_references", meta,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False, index=True),
    Column("title", String, nullable=False),
    Column("source", String, nullable=False),
    Column("blueprint", JSON, nullable=False),
    Column("created", Float, nullable=False),
)
story_plan_versions = Table(
    "platform_story_plan_versions",
    meta,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("draft_id", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("created", Float, nullable=False),
)
video_scenes = Table(
    "platform_video_scenes",
    meta,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False, index=True),
    Column("plan_version", Integer, nullable=False),
    Column("scene_index", Integer, nullable=False),
    Column("draft_id", String),
    Column("generation_id", String),
    Column("payload", JSON, nullable=False),
    Column("continuity_metadata", JSON),
    Column("status", String, nullable=False),
    Column("created", Float, nullable=False),
    Column("updated", Float, nullable=False),
)
video_media_assets = Table(
    "platform_video_media_assets",
    meta,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False, index=True),
    Column("scene_index", Integer),
    Column("kind", String, nullable=False),
    Column("path", String, nullable=False),
    Column("mime_type", String, nullable=False),
    Column("sha256", String, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created", Float, nullable=False),
)
video_quality_reports = Table(
    "platform_video_quality_reports",
    meta,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False, index=True),
    Column("scene_index", Integer),
    Column("stage", String, nullable=False),
    Column("status", String, nullable=False),
    Column("metrics", JSON, nullable=False),
    Column("issues", JSON, nullable=False),
    Column("created", Float, nullable=False),
)


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000)
    return salt + ":" + digest.hex()
