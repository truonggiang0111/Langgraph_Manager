from __future__ import annotations

import secrets
import base64
import binascii
import io
import re
import json
import os
import socket
import subprocess
import threading
import time
import uuid
import logging
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import hashlib
from collections import Counter

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .claude_skill_index import scan_claude_skill_index, search_claude_skill_index
from .hybrid_routing import build_routing_plan
from .agent_runtime import run_agent_job_state as run_native_job_state
from .executor import http_json
from .graph import build_graph
from .tool_registry import cliproxy_chat, cliproxy_chat_stream, describe_tools, run_tool


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
STATE_DIR = Path(os.getenv("LANGGRAPH_STATE_DIR", "/data/state")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="LangGraph Manager", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/artifacts", StaticFiles(directory=STATE_DIR), name="artifacts")

TRACE_DIR = STATE_DIR / "traces"
TRACE_DIR.mkdir(parents=True, exist_ok=True)
TRACE_LOG = TRACE_DIR / "manager_trace.log"
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")

trace_logger = logging.getLogger("langgraph.trace")
if not trace_logger.handlers:
    trace_logger.setLevel(logging.INFO)
    handler = logging.FileHandler(TRACE_LOG, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    trace_logger.addHandler(handler)
    trace_logger.propagate = False


def trace_event(event: str, **fields: Any) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "trace_id": trace_id_ctx.get(),
    }
    payload.update(fields)
    try:
        trace_logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        trace_logger.info(json.dumps({"event": event, "trace_id": trace_id_ctx.get()}, ensure_ascii=False))


@app.middleware("http")
async def request_trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    token = trace_id_ctx.set(trace_id)
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        trace_event(
            "api_request",
            method=request.method,
            path=request.url.path,
            status=500,
            elapsed_ms=elapsed_ms,
            ok=False,
        )
        trace_id_ctx.reset(token)
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["x-request-id"] = trace_id
    trace_event(
        "api_request",
        method=request.method,
        path=request.url.path,
        status=status_code,
        elapsed_ms=elapsed_ms,
        ok=status_code < 400,
    )
    trace_id_ctx.reset(token)
    return response


class CreateJobRequest(BaseModel):
    request: str = Field(min_length=1)
    title: str | None = None
    permission_mode: str = "auto_review"


class MessageRequest(BaseModel):
    content: str = Field(min_length=1)
    executor: str = "claude"


class ChatSessionRequest(BaseModel):
    permission_mode: str = "auto_review"


class PermissionModeRequest(BaseModel):
    permission_mode: str


class RenameJobRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class FocusFileRequest(BaseModel):
    name: str = Field(min_length=1)
    content: str = ""


class AttachmentReadRequest(BaseModel):
    name: str = Field(min_length=1)
    mime_type: str = ""
    content_base64: str = Field(min_length=1)
    job_id: str = ""


class AttachmentDeleteRequest(BaseModel):
    stored_path: str = Field(min_length=1)


class MemoryRequest(BaseModel):
    kind: str = "note"
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: str = ""
    source: str = "manual"


class UserPreferenceRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = ""
    source: str = "manual"


class SkillRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    triggers: str = ""
    source: str = ""
    body: str = Field(min_length=1)
    status: str = "active"


class SkillResearchRequest(BaseModel):
    prompt: str = Field(min_length=1)
    source: str = ""


class SessionMemoryRequest(BaseModel):
    summary: str = ""
    current_goal: str = ""
    open_tasks: str = ""
    important_files: str = ""
    decisions: str = ""
    last_result: str = ""


class PlanEditRequest(BaseModel):
    plan: list[str] = Field(default_factory=list)
    comment: str = ""


class CommandRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    action_kind: str = "note"
    payload: dict = Field(default_factory=dict)
    status: str = "active"


class RecurringTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1)
    schedule: str = "manual"
    action_kind: str = "note"
    payload: dict = Field(default_factory=dict)
    status: str = "active"
    next_run_at: str = ""


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    root_path: str = ""
    summary: str = ""
    memory: str = ""
    status: str = "active"


class RolePluginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    role: str = ""
    description: str = ""
    instructions: str = ""
    tool_hints: str = ""
    status: str = "active"


PERMISSION_MODES = {"default_permissions", "auto_review", "full_access"}
MAX_FOCUS_FILE_CHARS = 80_000
MAX_ATTACHMENT_BYTES = int(os.getenv("LANGGRAPH_MAX_ATTACHMENT_BYTES", str(8 * 1024 * 1024)))
MAX_ATTACHMENT_TEXT_CHARS = int(os.getenv("LANGGRAPH_MAX_ATTACHMENT_TEXT_CHARS", "80000"))
ATTACHMENT_ROOT_NAME = "session_attachments"
ATTACHMENT_WORKSPACE_DIR = os.getenv("LANGGRAPH_ATTACHMENT_WORKSPACE_DIR", f"/workspace/LangGraph_Manager/data/{ATTACHMENT_ROOT_NAME}")
WORKSPACE_ROOT = Path(os.getenv("LANGGRAPH_WORKSPACE_ROOT", "/workspace/LangGraph_Manager")).resolve()
MEMORY_DOCS_ROOT = STATE_DIR / "memory_docs"
MAX_MEMORY_CONTEXT_CHARS = 12_000
MAX_MEMORY_CONTEXT_TOKENS = int(os.getenv("MAX_MEMORY_CONTEXT_TOKENS", "1600"))
SESSION_CONTEXT_TOKEN_BUDGET = int(os.getenv("SESSION_CONTEXT_TOKEN_BUDGET", "420"))
MEMORY_CONTEXT_TOKEN_BUDGET = int(os.getenv("MEMORY_CONTEXT_LONGTERM_TOKEN_BUDGET", "520"))
SKILL_CONTEXT_TOKEN_BUDGET = int(os.getenv("SKILL_CONTEXT_TOKEN_BUDGET", "420"))
AUX_CONTEXT_TOKEN_BUDGET = int(os.getenv("AUX_CONTEXT_TOKEN_BUDGET", "240"))
MAX_ACTION_RECOVERY_DEPTH = 3
MODEL_HISTORY_MESSAGES = int(os.getenv("MODEL_HISTORY_MESSAGES", "8"))
ACTION_RESULT_ARTIFACT_THRESHOLD = int(os.getenv("ACTION_RESULT_ARTIFACT_THRESHOLD", "3000"))
SUPPORTED_ACTION_KINDS = {"workspace_inspect", "workspace_read_file", "workspace_diff", "workspace_command", "coding_agent_executor", "create_memory", "create_skill", "note", "git_checkpoint", "git_restore_checkpoint"}
ASSISTANT_MESSAGE_ROLES = {"manager", "langgraph", "claude"}
HYBRID_ROUTING_ENABLED = os.getenv("HYBRID_ROUTING_ENABLED", "1").lower() in {"1", "true", "yes"}
# guided => use self-built routing plan (default + route-memory boost) and send recommendations to executor
# self => let executor do self-discovery without injected recommendations
_skill_discovery_raw = os.getenv("SKILL_DISCOVERY_MODE", "guided").strip().lower()
if _skill_discovery_raw in {"self_build", "self-built", "hybrid", "guided"}:
    SKILL_DISCOVERY_MODE = "guided"
elif _skill_discovery_raw in {"self", "default"}:
    SKILL_DISCOVERY_MODE = "self"
else:
    SKILL_DISCOVERY_MODE = "guided"
ROUTING_TRACE_ENABLED = os.getenv("ROUTING_TRACE_ENABLED", "1").lower() in {"1", "true", "yes"}


def validate_permission_mode(value: str) -> str:
    if value not in PERMISSION_MODES:
        raise HTTPException(status_code=422, detail="invalid permission_mode")
    return value


def validate_coding_executor(value: str) -> str:
    _ = value
    return "claude"


def default_coding_executor() -> str:
    return "claude"


def is_casual_chat(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return False
    work_markers = [
        "sửa", "fix", "lỗi", "bug", "test", "code", "docker", "workflow",
        "build", "plan", "kế hoạch", "chạy", "tạo", "cập nhật", "deploy",
        "http://", "https://", "/image_", "/video_", "langgraph",
        "memory", "skill", "cơ chế", "co che", "thực thi", "thuc thi",
        "executor", "mcp", "plugin", "prompt", "context", "route", "routing",
        "debug", "trace", "log", "backend", "frontend", "api", "database", "db",
    ]
    if any(marker in normalized for marker in work_markers):
        return False
    # Longer/substantive questions should never be treated as casual pings.
    word_count = len([token for token in normalized.split(" ") if token])
    if word_count >= 10 or ("?" in normalized and word_count >= 6):
        return False
    casual_patterns = [
        r"^(alo|hello|hi|hey|ê|e|nghe|bạn nghe|có đó|có nghe)(\b|[ ?!.,]*)",
        r"(nghe tôi|nghe mình|nói k|nói không|có nghe không|có đó không)",
    ]
    return len(normalized) <= 160 and any(re.search(pattern, normalized) for pattern in casual_patterns)


def casual_reply(text: str) -> str:
    lower = text.lower()
    capability_markers = [
        "làm được gì",
        "lam duoc gi",
        "giúp được gì",
        "giup duoc gi",
        "có thể làm",
        "co the lam",
        "chức năng",
        "chuc nang",
        "bạn làm gì",
        "ban lam gi",
    ]
    if any(marker in lower for marker in capability_markers):
        return (
            "Em có thể chat để anh thảo luận ý tưởng, lập plan trước khi làm, "
            "chạy job LangGraph có approve, theo dõi logs/result, và giao việc code cho Claude executor khi được cấu hình. "
            "Anh cứ mô tả việc cần làm, em sẽ tách bước cho rõ rồi mới chạy."
        )
    if "nghe" in lower or "alo" in lower:
        return "Em nghe đây. Anh nói tiếp đi, em đang theo dõi."
    return "Em ở đây. Anh cứ nhắn tiếp, em sẽ phản hồi ngay."


def latest_manager_message(job: dict) -> str:
    for item in reversed(job.get("messages", [])):
        if item.get("role") in ASSISTANT_MESSAGE_ROLES and item.get("content"):
            return str(item.get("content") or "")
    return ""


def is_assistant_message(item: dict) -> bool:
    return item.get("role") in ASSISTANT_MESSAGE_ROLES and bool(item.get("content"))


def model_message_role(item: dict) -> str:
    return "assistant" if item.get("role") in ASSISTANT_MESSAGE_ROLES else "user"


def looks_like_execute_followup(text: str) -> bool:
    lower = re.sub(r"\s+", " ", text.lower()).strip(" .!?,")
    markers = {
        "làm đi", "lam di", "chạy đi", "chay di", "làm tiếp", "lam tiep",
        "tiếp tục", "tiep tuc", "ok làm", "ok lam", "bắt đầu đi", "bat dau di",
    }
    return lower in markers or any(marker in lower for marker in markers)


CHAT_SYSTEM_PROMPT = (
    "Bạn là LangGraph Manager chat mode. Trả lời bằng tiếng Việt tự nhiên, ngắn gọn, hữu ích. "
    "Ở chế độ chat, bạn chỉ thảo luận, giải thích, brainstorm và giúp người dùng hiểu hướng làm. "
    "Không tự nhận đã sửa file, chạy lệnh, deploy, hay thực thi tool. "
    "Khi người dùng muốn bắt tay làm việc cụ thể, nhắc họ tạo New task hoặc mô tả rõ việc để hệ thống lập plan/approve/run."
)

PLANNER_SYSTEM_PROMPT = (
    "Bạn là planner trước khi thực thi cho LangGraph Manager. "
    "Đọc cuộc trò chuyện và quyết định có đủ thông tin để bắt tay làm chưa. "
    "Nếu thiếu thông tin quan trọng, trả về JSON thuần dạng "
    '{"mode":"questions","questions":["..."],"reason":"..."}. '
    "Chỉ hỏi 2-4 câu cụ thể, ví dụ đã có project chưa, đường dẫn project, muốn dùng stack nào, phạm vi cần build/test. "
    "Nếu đủ thông tin, trả về JSON thuần dạng "
    '{"mode":"plan","plan":["..."],"summary":"..."}. '
    "Plan phải là các bước cụ thể theo task người dùng, không dùng bước chung chung kiểu kiểm tra bối cảnh/chạy test/tổng kết nếu không gắn với task. "
    "Nếu người dùng yêu cầu onboarding agent end-to-end, plan phải bao gồm backend, UI, tests, deploy checklist, và verify chống fake report bằng evidence từ command/test/diff/log."
)

SKILL_RESEARCH_SYSTEM_PROMPT = (
    "Bạn là skill architect cho LangGraph Manager. "
    "Phân tích yêu cầu hoặc GitHub repo/source người dùng đưa, rồi viết ra một skill có thể dùng lại. "
    "Trả về JSON thuần dạng "
    '{"name":"...","description":"...","triggers":"comma keywords","body":"# Skill\\n...","memory_suggestions":[{"kind":"project","title":"...","content":"...","tags":"..."}]}. '
    "Body nên là markdown hướng dẫn workflow, tool cần dùng, kiểm tra cần chạy, và điều kiện an toàn."
)

ASSISTANT_AGENT_SYSTEM_PROMPT = (
    "Bạn là trợ lý cá nhân chạy trên LangGraph Manager. Trả về JSON thuần, không markdown. "
    "Bạn có memory, skills và có thể đề xuất hành động. "
    "Chỉ hỏi/đòi xác nhận khi hành động có rủi ro rõ: đăng nhập Gmail/tài khoản, nhập mật khẩu/OTP/token/API key, gửi email/form chứa dữ liệu cá nhân, thanh toán, mua hàng, upload dữ liệu nhạy cảm, xóa/sửa dữ liệu, đổi cài đặt bảo mật, hoặc cấp quyền hệ thống. "
    "Không được tự nhận đã thực thi khi backend chưa trả kết quả action. "
    "Vai trò chính của bạn là router nhẹ: trả lời câu thường, còn việc nặng phải giao Claude executor. "
    "Nếu yêu cầu có dấu hiệu cần sửa/debug/review/refactor code, sửa UI/frontend, đọc/phân tích repo/source/file/PDF, tìm hàm/class/route/component, tra docs/version thư viện, hoặc cần plugin/MCP như Serena/Context7, luôn trả về pending_action kind coding_agent_executor. "
    "Không dùng answer cho các việc workspace/code nặng; chỉ dùng answer cho câu hỏi khái niệm hoặc trao đổi không cần đọc/sửa file. "
    "Nếu chỉ cần trả lời/thảo luận, dùng "
    '{"mode":"answer","content":"..."}. '
    "Nếu thiếu thông tin quan trọng trước khi làm, dùng "
    '{"mode":"questions","content":"..."} với 1-4 câu hỏi cụ thể. '
    "Nếu cần đọc workspace/repo, dùng workspace_inspect trước, workspace_read_file để đọc file cụ thể, workspace_diff để xem thay đổi; không tự sửa file bằng patch. "
    "Nếu cần code/sửa repo/UI/debug/review/refactor/docs/version/file analysis, tạo action coding_agent_executor để giao việc cho Claude executor; chỉ dùng workspace_command cho lệnh đọc/kiểm tra nhẹ không cần suy luận. "
    "Nếu cần chạy lệnh trên workspace hoặc lưu memory/skill, dùng "
    '{"mode":"pending_action","reply":"...","action":{"kind":"workspace_inspect|workspace_read_file|workspace_diff|workspace_command|coding_agent_executor|create_memory|create_skill|note","title":"...","preview":"...","payload":{...}}}. '
    "workspace_inspect payload có thể có max_files. workspace_read_file payload cần path và có thể có max_chars. workspace_diff payload có thể có path. "
    "workspace_command payload nên có command, cwd, risk low|medium|high, timeout. "
    "coding_agent_executor payload có thể có cwd, timeout, request override; Claude executor sẽ nhận prompt qua stdin. "
    "create_memory payload nên có kind, title, content, tags. create_skill payload nên có name, description, triggers, source, body. "
    "create_recurring_task payload nên có name, prompt, schedule, action_kind, payload, next_run_at. "
    "Sau khi một action chạy xong, hãy đọc kết quả: nếu hoàn tất thì answer; nếu cần bước tiếp thì tạo pending_action mới; nếu cần nhớ bài học thì tạo create_memory/create_skill. "
    "Khi action lỗi hoặc bị chặn quyền, hãy phân tích nguyên nhân và tìm đường khác an toàn: dùng read-only inspection, đổi cwd/port, dùng Docker/container, gọi n8n, hoặc hỏi người dùng cấp quyền/thông tin nếu bắt buộc. "
    "Không lặp lại y nguyên command vừa lỗi. Nếu cần quyền admin/root thật sự, hãy nói rõ và tạo câu hỏi thay vì giả vờ đã xử lý. "
    "Nếu việc lớn cần kế hoạch nhiều bước, gợi ý người dùng bật Plan thay vì tạo action mơ hồ."
)


def hard_user_policy_prompt() -> str:
    user_doc = load_user_memory_doc(max_chars=6000).strip()
    if not user_doc:
        return ""
    return (
        "USER POLICY (HARD RULES - MUST FOLLOW):\n"
        "Các quy tắc trong user.md là bắt buộc, ưu tiên cao hơn style mặc định của model. "
        "Không được bỏ qua hoặc tự ý đổi xưng hô nếu user.md đã quy định.\n\n"
        f"{user_doc}"
    )


def compose_system_prompt(base_prompt: str) -> str:
    hard_policy = hard_user_policy_prompt()
    if not hard_policy:
        return base_prompt
    return f"{base_prompt}\n\n{hard_policy}"


def template_payload(value: Any, command_input: str, job: dict | None = None) -> Any:
    if isinstance(value, str):
        return (
            value.replace("{{input}}", command_input)
            .replace("{{job_id}}", str((job or {}).get("id", "")))
            .replace("{{title}}", str((job or {}).get("title", "")))
        )
    if isinstance(value, list):
        return [template_payload(item, command_input, job) for item in value]
    if isinstance(value, dict):
        return {key: template_payload(item, command_input, job) for key, item in value.items()}
    return value


def slash_command_help() -> str:
    commands = db.list_commands(include_inactive=False)
    if not commands:
        return "Chưa có command nào. Vào Settings > Commands để tạo /command dùng mãi."
    lines = ["Commands đang có:"]
    for item in commands:
        lines.append(f"/{item['name']} - {item.get('description') or item.get('action_kind')}")
    return "\n".join(lines)


def handle_slash_command(job_id: str, content: str, job: dict) -> bool:
    match = re.match(r"^/([a-zA-Z0-9_-]+)(?:\s+(.*))?$", content.strip(), re.S)
    if not match:
        return False
    name = match.group(1).lower()
    command_input = (match.group(2) or "").strip()
    if name in {"commands", "help"}:
        db.add_message(job_id, "langgraph", slash_command_help())
        refresh_session_memory(job_id)
        return True
    command = db.get_command(name)
    if not command:
        db.add_message(job_id, "langgraph", f"Chưa có /{name}. Vào Settings > Commands để tạo command này.")
        refresh_session_memory(job_id)
        return True
    action_kind = str(command.get("action_kind") or "note")
    if action_kind not in SUPPORTED_ACTION_KINDS:
        action_kind = "note"
    payload = template_payload(command.get("payload") or {}, command_input, job)
    decision = {
        "reply": f"Mình đã mở /{name}. Kiểm tra action rồi approve nếu muốn chạy.",
        "action": {
            "kind": action_kind,
            "title": f"/{name}: {command.get('description') or action_kind}"[:160],
            "preview": command_input or command.get("description") or f"Run /{name}",
            "payload": payload if isinstance(payload, dict) else {},
        },
    }
    save_pending_action_from_decision(job_id, {"mode": "pending_action", **decision}, auto_execute_safe=True)
    refresh_session_memory(job_id)
    return True


def model_chat_reply(messages: list[dict], fallback_text: str) -> str:
    history = [
        item for item in messages
        if (item.get("role") == "user" or is_assistant_message(item)) and item.get("content")
    ][-MODEL_HISTORY_MESSAGES:]
    model_messages = [
        {
            "role": model_message_role(item),
            "content": compact_text(str(item.get("content", "")), 1600),
        }
        for item in history
    ]
    mem = memory_context()
    if mem:
        model_messages.insert(0, {"role": "user", "content": f"Context memory/skills:\n{mem}"})
    started = time.perf_counter()
    result = cliproxy_chat(model_messages, compose_system_prompt(CHAT_SYSTEM_PROMPT))
    trace_event(
        "model_call",
        route="chat_reply",
        ok=bool(result.get("ok")),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        messages=len(model_messages),
    )
    if result.get("ok"):
        return sanitize_manager_content(
            str(result.get("data", {}).get("content", "")).strip() or casual_reply(fallback_text),
            casual_reply(fallback_text),
        )
    return casual_reply(fallback_text)


def assistant_decision(job: dict, fallback_text: str, extra_context: str = "") -> dict:
    history = [
        item for item in job.get("messages", [])
        if (item.get("role") == "user" or is_assistant_message(item)) and item.get("content")
    ][-MODEL_HISTORY_MESSAGES:]
    model_messages = [
        {
            "role": model_message_role(item),
            "content": compact_text(str(item.get("content", "")), 1600),
        }
        for item in history
    ]
    context = memory_context(job.get("id"))
    focus_files = job.get("focus_files", [])
    context_parts: list[str] = []
    if context:
        context_parts.append(f"Context memory/skills:\n{context}")
    if focus_files:
        excerpts = []
        for item in focus_files[:4]:
            excerpts.append(f"--- {item.get('name', 'file')} ---\n{str(item.get('content', ''))[:5000]}")
        context_parts.append("Focus files:\n" + "\n\n".join(excerpts))
    if context_parts:
        model_messages.insert(0, {"role": "user", "content": "\n\n".join(context_parts)})
    if extra_context:
        model_messages.append({"role": "user", "content": extra_context})
    started = time.perf_counter()
    result = cliproxy_chat(model_messages, compose_system_prompt(ASSISTANT_AGENT_SYSTEM_PROMPT))
    trace_event(
        "model_call",
        route="assistant_decision",
        ok=bool(result.get("ok")),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        messages=len(model_messages),
    )
    if not result.get("ok"):
        return {"mode": "answer", "content": casual_reply(fallback_text)}
    raw = str(result.get("data", {}).get("content", "")).strip()
    data = parse_model_json(raw)
    if data.get("mode") == "pending_action" and isinstance(data.get("action"), dict):
        action = data["action"]
        kind = str(action.get("kind") or "note")
        if kind not in SUPPORTED_ACTION_KINDS:
            kind = "note"
        return {
            "mode": "pending_action",
            "reply": str(data.get("reply") or "Mình đã chuẩn bị một hành động, cần bạn approve trước khi chạy."),
            "action": {
                "kind": kind,
                "title": str(action.get("title") or "Pending action")[:160],
                "preview": str(action.get("preview") or "")[:4000],
                "payload": action.get("payload") if isinstance(action.get("payload"), dict) else {},
            },
        }
    if data.get("mode") == "questions":
        return {"mode": "questions", "content": sanitize_manager_content(str(data.get("content") or data.get("question") or raw or casual_reply(fallback_text)))}
    if data.get("mode") == "answer":
        return {"mode": "answer", "content": sanitize_manager_content(str(data.get("content") or raw or casual_reply(fallback_text)))}
    return {"mode": "answer", "content": sanitize_manager_content(raw or casual_reply(fallback_text))}


DIRECT_CLAUDE_MARKERS = {
    "claude",
    "mcp",
    "plugin",
    "plugins",
    "serena",
    "context7",
    "claude-code-setup",
    "claude-md-management",
    "security-guidance",
    "code-review",
    "feature-dev",
    "frontend-design",
    "code-simplifier",
}

DIRECT_CLAUDE_PATTERNS = [
    r"\b(fix|debug|review|refactor|implement|edit|modify|update|analyze|analyse)\b.{0,80}\b(code|repo|source|file|ui|frontend|backend|api|bug|error)\b",
    r"\b(code|repo|source|file|ui|frontend|backend|api|bug|error)\b.{0,80}\b(fix|debug|review|refactor|implement|edit|modify|update|analyze|analyse)\b",
    r"\b(find|search|inspect|trace|locate)\b.{0,80}\b(function|class|method|symbol|handler|route|endpoint|component)\b",
    r"\b(docs?|documentation|version|latest)\b.{0,80}\b(fastapi|langgraph|playwright|react|vue|nextjs|python|typescript|javascript)\b",
    r"\b(pdf|document|docx|xlsx|pptx)\b.{0,80}\b(read|extract|analyze|analyse|summarize|parse)\b",
    r"\b(read|extract|analyze|analyse|summarize|parse)\b.{0,80}\b(pdf|document|docx|xlsx|pptx)\b",
    r"(sửa|fix|debug|vá|review|refactor|phân tích|kiểm tra|xử lý|cập nhật).{0,80}(code|repo|source|hàm|class|file|ui|frontend|backend|api|lỗi|bug)",
    r"(code|repo|source|hàm|class|file|ui|frontend|backend|api|lỗi|bug).{0,80}(sửa|fix|debug|vá|review|refactor|phân tích|kiểm tra|xử lý|cập nhật)",
    r"(sửa|fix|xử lý|vá|debug).{0,80}(giao diện|màn hình|scroll|cuộn|nút|form|chat|frontend|ui)",
    r"(giao diện|màn hình|scroll|cuộn|nút|form|chat|frontend|ui).{0,80}(bị|lỗi|sai|hỏng|nhảy|văng|đè|không|k\s)",
    r"(sua|fix|xu ly|va|debug).{0,80}(giao dien|man hinh|scroll|cuon|nut|form|chat|frontend|ui)",
    r"(giao dien|man hinh|scroll|cuon|nut|form|chat|frontend|ui).{0,80}(bi|loi|sai|hong|nhay|vang|de|khong|ko|k\s)",
    r"(tìm|kiếm|trace|lần theo|định vị).{0,80}(hàm|class|method|symbol|handler|route|endpoint|component|luồng|flow)",
    r"(docs?|tài liệu|phiên bản|version|mới nhất|latest).{0,80}(fastapi|langgraph|playwright|react|vue|nextjs|python|typescript|javascript)",
    r"(check|kiểm tra|xem|inspect|trace).{0,100}(cli\s*-?proxy|cliproxy|proxy|model|config|cấu hình|env|log)",
    r"(cli\s*-?proxy|cliproxy|proxy|model|config|cấu hình|env|log).{0,100}(check|kiểm tra|xem|inspect|trace)",
    r"(pdf|tài liệu|docx|xlsx|pptx).{0,80}(đọc|trích xuất|phân tích|tóm tắt|parse)",
    r"(đọc|trích xuất|phân tích|tóm tắt|parse).{0,80}(pdf|tài liệu|docx|xlsx|pptx)",
]


def wants_direct_claude(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").lower())
    if any(marker in normalized for marker in DIRECT_CLAUDE_MARKERS):
        return True
    return any(re.search(pattern, normalized) for pattern in DIRECT_CLAUDE_PATTERNS)


def classify_chat_task_complexity(text: str) -> dict:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    signals: list[str] = []
    score = 0
    if not normalized:
        return {"task_complexity": "simple", "complexity_score": 0, "complexity_signals": []}
    if any(marker in normalized for marker in ["fix", "sửa", "debug", "refactor", "bug", "lỗi", "ui", "frontend", "backend", "repo", "code"]):
        score += 2
        signals.append("engineering_terms")
    if any(marker in normalized for marker in ["mcp", "plugin", "claude", "executor", "serena", "context7", "pdf", "docx", "xlsx"]):
        score += 2
        signals.append("tool_or_mcp")
    if any(marker in normalized for marker in ["và", " and ", " rồi ", " sau đó", "next", "then", "đồng thời", "cùng lúc"]):
        score += 1
        signals.append("multi_step_intent")
    if len(normalized) > 220:
        score += 1
        signals.append("long_request")
    task_complexity = "simple" if score <= 1 else ("composite" if score <= 3 else "complex_composite")
    return {"task_complexity": task_complexity, "complexity_score": score, "complexity_signals": signals}


def should_force_claude_after_model(text: str, decision: dict) -> bool:
    if decision.get("mode") != "pending_action":
        return False
    action = decision.get("action") if isinstance(decision.get("action"), dict) else {}
    if action.get("kind") == "coding_agent_executor":
        return False
    normalized = re.sub(r"\s+", " ", str(text or "").lower())
    heavy_markers = [
        "ui", "frontend", "giao diện", "giao dien", "scroll", "cuộn", "cuon",
        "code", "repo", "source", "bug", "lỗi", "loi", "debug", "refactor",
        "pdf", "docs", "documentation", "version", "hàm", "ham", "class",
        "route", "endpoint", "component",
    ]
    return any(marker in normalized for marker in heavy_markers)


def direct_claude_decision(content: str, executor: str = "claude", complexity: dict | None = None) -> dict:
    request = str(content or "").strip()
    executor = "claude"
    routing_plan: dict[str, Any] = {}
    task_complexity = str((complexity or {}).get("task_complexity") or "simple")
    if HYBRID_ROUTING_ENABLED:
        routing_plan = build_routing_plan(request, task_complexity=task_complexity, limit=6)
        if ROUTING_TRACE_ENABLED:
            trace_event(
                "routing_plan",
                mode=SKILL_DISCOVERY_MODE,
                task_complexity=task_complexity,
                query=request[:240],
                primary=[item.get("name") for item in routing_plan.get("primary", [])],
                fallback=[item.get("name") for item in routing_plan.get("fallback", [])],
                ordered=routing_plan.get("ordered_tool_names", []),
                rationale=routing_plan.get("rationale", [])[:4],
            )

    payload: dict[str, Any] = {
        "executor": executor,
        "request": request,
        "cwd": "",
        "risk": "medium",
        "timeout": 900,
        "git_checkpoint_before_run": False,
        "git_checkpoint_policy": "after_repeated_failures",
        "checkpoint_fail_threshold": 3,
        "checkpoint_label": "auto-after-retries",
    }
    if HYBRID_ROUTING_ENABLED and SKILL_DISCOVERY_MODE == "guided":
        payload.update(
            {
                "recommended_tools": routing_plan.get("primary", []),
                "ordered_tool_recommendations": routing_plan.get("candidates", []),
                "task_complexity": task_complexity,
                "ordered_tool_names": routing_plan.get("ordered_tool_names", []),
                "initial_tool_read_count": int(routing_plan.get("initial_tool_read_count") or 1),
                "fallback_tool_candidates": routing_plan.get("fallback", []),
            }
        )
    else:
        # Self-discovery mode: do not push explicit recommendations into prompt.
        payload["task_complexity"] = task_complexity

    return {
        "mode": "pending_action",
        "reply": "Em chuyển thẳng yêu cầu này cho Claude executor/plugin/MCP để tránh tốn thêm một lượt model LangGraph.",
        "action": {
            "kind": "coding_agent_executor",
            "title": "Gọi Claude executor",
            "preview": request[:4000] or "Gửi yêu cầu cho Claude executor.",
            "payload": payload,
        },
    }


def handle_agent_message(job_id: str, content: str) -> dict:
    db.add_message(job_id, "user", content)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if handle_slash_command(job_id, content, job):
        return db.get_job(job_id) or job
    if job.get("title") in {"New chat", "New session"}:
        db.update_job_title(job_id, content.strip().splitlines()[0][:80] or "New session")
        job = db.get_job(job_id) or job
    complexity = classify_chat_task_complexity(content)
    db.update_step(job_id, "analyze", "running", "Analyzing request and selecting execution route")
    casual = is_casual_chat(content)
    route = "casual_reply" if casual else "executor_direct"
    trace_event(
        "route_decision",
        job_id=job_id,
        route=route,
        one_hop_executor=(not casual),
        task_complexity=complexity.get("task_complexity"),
        complexity_score=complexity.get("complexity_score"),
        complexity_signals=complexity.get("complexity_signals"),
    )
    if casual:
        db.update_step(job_id, "analyze", "done", "Handled as casual chat")
        db.add_message(job_id, "langgraph", casual_reply(content))
        refresh_session_memory(job_id)
        return db.get_job(job_id) or job
    save_pending_action_from_decision(
        job_id,
        direct_claude_decision(content, default_coding_executor(), complexity=complexity),
        auto_execute_safe=True,
    )
    db.update_step(job_id, "analyze", "done", "Routed to coding executor")
    refresh_session_memory(job_id)
    return db.get_job(job_id) or job


def handle_code_message(job_id: str, content: str, executor: str = "claude") -> dict:
    executor = "claude"
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.get("status") not in {"chat", "draft", "planned", "needs_input"}:
        raise HTTPException(status_code=409, detail=f"cannot code from {job['status']}")
    db.add_message(job_id, "user", content)
    if job.get("title") in {"New chat", "New session"}:
        db.update_job_title(job_id, content.strip().splitlines()[0][:80] or "Code task")
    complexity = classify_chat_task_complexity(content)
    save_pending_action_from_decision(job_id, direct_claude_decision(content, executor, complexity=complexity), auto_execute_safe=True)
    refresh_session_memory(job_id)
    return db.get_job(job_id) or job


SENSITIVE_BROWSER_MARKERS = {
    "login",
    "signin",
    "sign-in",
    "password",
    "passwd",
    "otp",
    "2fa",
    "token",
    "api key",
    "secret",
    "gmail",
    "mail.google",
    "checkout",
    "payment",
    "billing",
    "purchase",
    "delete",
    "security",
    "permission",
}

PUBLISH_APPROVAL_MARKERS = {
    "post",
    "publish",
    "tweet",
    "facebook",
    "tiktok",
    "telegram",
    "instagram",
    "linkedin",
    "youtube",
    "send message",
    "send dm",
    "broadcast",
    "submit form",
    "create campaign",
    "public",
}


def action_requires_publish_approval(action: dict) -> bool:
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    kind = str(action.get("kind") or "").lower()
    title = str(action.get("title") or "")
    preview = str(action.get("preview") or "")
    payload_blob = json.dumps(payload, ensure_ascii=False)
    haystack = " ".join([kind, title, preview, payload_blob]).lower()
    return any(marker in haystack for marker in PUBLISH_APPROVAL_MARKERS)


def is_safe_auto_action(action: dict, permission_mode: str = "auto_review") -> bool:
    kind = str(action.get("kind") or "")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    if kind != "workspace_inspect":
        return False
    blob = json.dumps(payload, ensure_ascii=False).lower()
    return not any(marker in blob for marker in SENSITIVE_BROWSER_MARKERS)


def should_auto_execute_action(action: dict, permission_mode: str = "auto_review") -> bool:
    # Even in full_access, public/social publish/send style actions must be
    # explicitly approved by user to avoid accidental external side effects.
    if action_requires_publish_approval(action):
        return False
    if permission_mode == "full_access":
        return str(action.get("kind") or "") in SUPPORTED_ACTION_KINDS
    return is_safe_auto_action(action, permission_mode)


def execute_action_and_follow_up(action_id: int) -> None:
    action = db.get_pending_action(action_id)
    if not action or action.get("status") not in {"pending", "running"}:
        return
    signature = action_signature(action)
    trace_event(
        "action_start",
        action_id=action_id,
        job_id=action.get("job_id"),
        kind=action.get("kind"),
        status=action.get("status"),
    )
    job = db.get_job(action["job_id"])
    if not job:
        db.update_pending_action_status(action_id, "failed", "", "job not found")
        trace_event("action_finish", action_id=action_id, job_id=action.get("job_id"), ok=False, error="job not found")
        return
    db.update_pending_action_status(action_id, "running")
    db.update_step(action["job_id"], "act", "running", f"Executing action #{action_id} ({action.get('kind')})")
    try:
        ok, output = execute_pending_action(action, job)
        parsed_result: dict[str, Any] = {}
        try:
            candidate = json.loads(str(output or ""))
            if isinstance(candidate, dict):
                parsed_result = candidate
        except Exception:
            parsed_result = {}
        parsed_data = parsed_result.get("data") if isinstance(parsed_result.get("data"), dict) else {}
        if str(action.get("kind") or "") == "coding_agent_executor":
            payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
            executor = validate_coding_executor(str(payload.get("executor") or "claude"))
            session_id = str(parsed_data.get("session_id") or "").strip()
            if session_id:
                db.upsert_executor_session(action.get("job_id", ""), executor, session_id)
                trace_event(
                    "executor_session_linked",
                    job_id=action.get("job_id"),
                    executor=executor,
                    session_id=session_id,
                )
            route = detect_route_from_command(str(parsed_data.get("command") or ""))
            task_signature = task_signature_for_request(str(payload.get("request") or ""))
            error_class = "" if ok else detect_error_class(str(output or ""))
            row = db.record_route_memory(
                env_fingerprint_for_job(job),
                task_signature,
                route,
                ok=ok,
                error_class=error_class,
            )
            trace_event(
                "route_memory_update",
                job_id=action.get("job_id"),
                executor=executor,
                task_signature=task_signature,
                route=route,
                outcome="success" if ok else "failure",
                error_class=error_class,
                success_count=row.get("success_count"),
                failure_count=row.get("failure_count"),
            )
        status = "done" if ok else "failed"
        stored_output = maybe_store_action_artifact(action_id, output, is_error=not ok)
        if ok:
            db.update_step(action["job_id"], "verify", "running", "Verifying action result")
            record_skill_feedback_from_action(action, ok=True, output=output)
            db.resolve_action_lesson(signature)
            db.update_pending_action_status(action_id, status, stored_output, "")
            db.update_step(action["job_id"], "act", "done", f"Action #{action_id} completed")
            agent_follow_up_after_action(action["job_id"], action, ok, output)
            db.update_step(action["job_id"], "verify", "done", "Action result verified")
            db.update_step(action["job_id"], "done", "done", "Action flow completed")
            trace_event("action_finish", action_id=action_id, job_id=action.get("job_id"), ok=True, status=status)
        else:
            db.update_step(action["job_id"], "verify", "failed", "Action failed; evaluating recovery")
            record_skill_feedback_from_action(action, ok=False, output=output)
            db.upsert_action_lesson_failure(
                signature,
                str(action.get("kind") or ""),
                compact_text(str(output), 320),
                lesson_strategy_for_action(action),
            )
            # Keep the UI in Working while the agent reads the failure and decides
            # whether a recovery action is possible.
            agent_follow_up_after_action(action["job_id"], action, ok, output)
            db.update_pending_action_status(action_id, status, "", stored_output)
            db.update_step(action["job_id"], "act", "failed", f"Action #{action_id} failed")
            trace_event(
                "action_finish",
                action_id=action_id,
                job_id=action.get("job_id"),
                ok=False,
                status=status,
                error=compact_text(str(output), 600),
            )
    except Exception as exc:
        error_text = compact_text(f"{type(exc).__name__}: {exc}", 1000)
        db.update_pending_action_status(action_id, "failed", "", error_text)
        db.update_step(action["job_id"], "act", "failed", f"Action #{action_id} failed with exception")
        db.upsert_action_lesson_failure(
            signature,
            str(action.get("kind") or ""),
            compact_text(error_text, 320),
            lesson_strategy_for_action(action),
        )
        trace_event(
            "action_finish",
            action_id=action_id,
            job_id=action.get("job_id"),
            ok=False,
            status="failed",
            error=error_text,
        )
    finally:
        refresh_session_memory(action["job_id"])


def start_action_background(action_id: int) -> None:
    inline_actions = os.getenv("LANGGRAPH_INLINE_ACTIONS", "").lower() in {"1", "true", "yes"}
    disable_scheduler = os.getenv("LANGGRAPH_DISABLE_SCHEDULER", "").lower() in {"1", "true", "yes"}
    if inline_actions or disable_scheduler:
        execute_action_and_follow_up(action_id)
        return
    thread = threading.Thread(target=execute_action_and_follow_up, args=(action_id,), daemon=True)
    thread.start()


def save_pending_action_from_decision(job_id: str, decision: dict, auto_execute_safe: bool = False) -> dict:
    action = decision["action"]
    saved = db.add_pending_action(job_id, action["kind"], action["title"], action["preview"], action["payload"])
    db.update_step(job_id, "act", "pending", f"Queued action #{saved['id']} ({action['kind']})")
    job = db.get_job(job_id)
    permission_mode = str((job or {}).get("permission_mode") or "auto_review")
    if auto_execute_safe and should_auto_execute_action(action, permission_mode):
        if job:
            db.update_pending_action_status(saved["id"], "running")
            start_action_background(saved["id"])
            return db.get_pending_action(saved["id"]) or saved
    approval_note = ""
    if action_requires_publish_approval(action):
        approval_note = "\n\nAction này có dấu hiệu publish/send ra bên ngoài nên luôn cần bạn Approve thủ công trước khi chạy."
    reply = (
        f"{decision.get('reply') or 'Mình đã chuẩn bị một hành động, cần bạn approve trước khi chạy.'}\n\n"
        f"Action chờ duyệt #{saved['id']}: {saved['title']}\n"
        f"{saved['preview'] or 'Mở card action bên dưới để approve hoặc reject.'}"
        f"{approval_note}"
    )
    db.add_message(job_id, "langgraph", reply)
    return saved


def recovery_depth(job: dict) -> int:
    actions = list(job.get("pending_actions", []))
    if not actions:
        return 0
    payload = actions[-1].get("payload") or {}
    try:
        return int(payload.get("recovery_depth") or 0)
    except (TypeError, ValueError):
        return 0


def tag_recovery_action(decision: dict, failed_action: dict) -> dict:
    if decision.get("mode") != "pending_action":
        return decision
    action = decision.get("action") or {}
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    payload = {
        **payload,
        "recovery_of": failed_action.get("id"),
        "recovery_kind": failed_action.get("kind"),
        "recovery_depth": int((failed_action.get("payload") or {}).get("recovery_depth") or 0) + 1,
    }
    action["payload"] = payload
    decision["action"] = action
    return decision


def compact_action_output_for_model(output: str, limit: int = 2600) -> str:
    text = str(output or "")
    try:
        parsed = json.loads(text)
    except Exception:
        return compact_text(text, limit)

    data = parsed.get("data") if isinstance(parsed, dict) else {}
    parts: list[str] = []
    if isinstance(parsed, dict):
        if parsed.get("summary"):
            parts.append(f"summary: {parsed.get('summary')}")
        if "ok" in parsed:
            parts.append(f"ok: {parsed.get('ok')}")
    if isinstance(data, dict):
        for key in ("title", "url", "policy"):
            if data.get(key):
                parts.append(f"{key}: {data.get(key)}")
        if data.get("results"):
            parts.append("results:\n" + compact_text(json.dumps(data.get("results"), ensure_ascii=False), 1200))
        if data.get("text"):
            parts.append("text:\n" + compact_text(str(data.get("text")), 9000))
        if data.get("stdout"):
            parts.append("stdout:\n" + compact_text(str(data.get("stdout")), 900))
        if data.get("stderr"):
            parts.append("stderr:\n" + compact_text(str(data.get("stderr")), 700))
        logs = data.get("logs")
        if isinstance(logs, list) and logs:
            parts.append("logs:\n" + "\n".join(str(item) for item in logs[:8]))
        screenshots = data.get("screenshot_urls") or data.get("screenshots")
        if screenshots:
            parts.append("screenshots: " + compact_text(json.dumps(screenshots, ensure_ascii=False), 500))
    return compact_text("\n\n".join(parts) or text, limit)


def action_failure_report(action: dict, output: str) -> str:
    summary = compact_action_output_for_model(output, 260)
    return f"Action #{action.get('id')} lỗi ({action.get('kind')}): {summary}"


def action_signature(action: dict) -> str:
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    basis = {
        "kind": str(action.get("kind") or ""),
        "title": str(action.get("title") or ""),
        "command": str(payload.get("command") or ""),
        "cwd": str(payload.get("cwd") or ""),
        "path": str(payload.get("path") or ""),
        "request": str(payload.get("request") or "")[:220],
    }
    raw = json.dumps(basis, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def lesson_strategy_for_action(action: dict) -> str:
    kind = str(action.get("kind") or "")
    if kind == "workspace_command":
        return "Đổi command/cwd hoặc dùng workspace_inspect trước; không lặp command y nguyên."
    if kind == "coding_agent_executor":
        return "Rút gọn yêu cầu, thêm context file/skill phù hợp hoặc đổi hướng thực thi."
    return "Đổi chiến lược thực thi và tránh lặp lại payload y nguyên."


def workspace_root_path() -> Path:
    return Path(os.getenv("LANGGRAPH_WORKSPACE_ROOT", "/workspace/LangGraph_Manager")).resolve()


def safe_workspace_cwd(raw: str) -> Path:
    root = workspace_root_path()
    value = (raw or "").strip()
    candidate = Path(value).expanduser() if value else root
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"cwd escapes workspace root: {resolved}")
    return resolved


def run_git(args: list[str], cwd: Path, timeout: int = 20) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return proc.returncode == 0, out
    except Exception as exc:
        return False, str(exc)


def create_git_checkpoint_for_job(job_id: str, cwd_raw: str, label: str = "") -> tuple[bool, str, dict | None]:
    try:
        cwd = safe_workspace_cwd(cwd_raw)
    except ValueError as exc:
        return False, str(exc), None
    ok_hash, commit_hash = run_git(["git", "rev-parse", "HEAD"], cwd)
    if not ok_hash:
        return False, f"git rev-parse HEAD failed: {commit_hash}", None
    ok_branch, branch = run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if not ok_branch:
        branch = ""
    checkpoint = db.create_git_checkpoint(
        job_id=job_id,
        cwd=str(cwd),
        branch=branch.strip(),
        commit_hash=commit_hash.strip(),
        label=label or "auto-before-coding",
    )
    return True, f"Checkpoint #{checkpoint['id']} @ {checkpoint['commit_hash'][:8]}", checkpoint


def extract_used_skills_from_output(action: dict, output: str) -> list[str]:
    names: list[str] = []
    # Prefer explicit used_skills from executor output.
    try:
        parsed = json.loads(str(output or ""))
    except Exception:
        parsed = {}
    if isinstance(parsed, dict):
        data = parsed.get("data")
        if isinstance(data, dict):
            used = data.get("used_skills")
            if isinstance(used, list):
                for item in used:
                    if isinstance(item, str) and item.strip():
                        names.append(item.strip())
    # Backward-compatible fallback: payload recommended_tools.
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    tools = payload.get("recommended_tools") if isinstance(payload.get("recommended_tools"), list) else []
    for item in tools:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            names.append(name)
    # Dedupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def record_skill_feedback_from_action(action: dict, ok: bool, output: str) -> None:
    if str(action.get("kind") or "") != "coding_agent_executor":
        return
    names = extract_used_skills_from_output(action, output)
    if not names:
        trace_event(
            "skill_feedback_skipped",
            action_id=action.get("id"),
            kind=action.get("kind"),
            reason="no_used_skills_detected",
            ok=ok,
        )
        return
    note = compact_text(str(output), 240)
    for name in names:
        row = db.record_skill_outcome(name, ok=ok, notes=note)
        trace_event(
            "skill_feedback_update",
            skill=name,
            ok=ok,
            score=row.get("score"),
            trials=row.get("trials"),
            status=row.get("status"),
        )


def extract_coding_agent_output(output: str) -> str:
    text = str(output or "").strip()
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    if isinstance(parsed, dict):
        data = parsed.get("data")
        if isinstance(data, dict):
            stream_output = str(data.get("output") or "").strip()
            if stream_output:
                assistant_chunks: list[str] = []
                result_chunks: list[str] = []
                for line in stream_output.splitlines():
                    raw = line.strip()
                    if not raw.startswith("{"):
                        continue
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    event_type = str(event.get("type") or "")
                    if event_type == "assistant":
                        message = event.get("message")
                        if isinstance(message, dict):
                            content = message.get("content")
                            if isinstance(content, list):
                                for block in content:
                                    if not isinstance(block, dict):
                                        continue
                                    if str(block.get("type") or "") == "text":
                                        snippet = str(block.get("text") or "").strip()
                                        if snippet:
                                            assistant_chunks.append(snippet)
                    if event_type == "result":
                        for key in ("result", "text", "content"):
                            snippet = str(event.get(key) or "").strip()
                            if snippet:
                                result_chunks.append(snippet)
                                break
                if result_chunks:
                    joined = str(result_chunks[-1]).strip()
                else:
                    joined = "\n\n".join(item for item in assistant_chunks if item).strip()
                if joined:
                    return joined
            for key in ("output", "stdout", "text", "content"):
                value = str(data.get(key) or "").strip()
                if value:
                    return value
        value = str(parsed.get("summary") or "").strip()
        if value:
            return value
    return text


def detect_route_from_command(command: str) -> str:
    raw = str(command or "").lower()
    if "docker" in raw:
        return "python_docker" if "python" in raw else "docker"
    if "python" in raw:
        return "python_local"
    return "generic_local"


def detect_error_class(text: str) -> str:
    raw = str(text or "").lower()
    if "python" in raw and ("not found" in raw or "no such file" in raw):
        return "python_not_found"
    if "permission denied" in raw:
        return "permission_denied"
    if "timed out" in raw or "timeout" in raw:
        return "timeout"
    if "command not found" in raw:
        return "command_not_found"
    return "runtime_error"


def task_signature_for_request(request: str) -> str:
    text = str(request or "").lower()
    if "pytest" in text or "unit test" in text or "test" in text:
        return "run_tests"
    if "python" in text or ".py" in text:
        return "run_python"
    if "docker" in text:
        return "docker_task"
    return "general_code"


def route_priority_score(success_count: int, failure_count: int) -> float:
    # Similar spirit to skill scoring: reward proven success, penalize failures,
    # and smooth low-sample routes to avoid overreacting to tiny histories.
    s = max(0, int(success_count))
    f = max(0, int(failure_count))
    total = s + f
    if total == 0:
        return 0.0
    win_rate = (s + 1.0) / (total + 2.0)  # Laplace smoothing
    evidence = min(total, 12) / 12.0
    return (win_rate * 2.0 - 1.0) * (0.5 + 0.5 * evidence) + (s * 0.04) - (f * 0.06)


def env_fingerprint_for_job(job: dict) -> str:
    parts = [
        f"os={os.name}",
        f"container={os.getenv('HOSTNAME', '')}",
        f"workspace={os.getenv('LANGGRAPH_WORKSPACE_ROOT', '')}",
    ]
    return "|".join(parts)


def build_route_memory_hint(job: dict, request: str) -> str:
    task_signature = task_signature_for_request(request)
    env_fp = env_fingerprint_for_job(job)
    rows = db.list_route_memory(env_fp, task_signature, limit=3)
    if not rows:
        return ""
    lines: list[str] = []
    ranked_rows = sorted(
        rows,
        key=lambda row: (
            route_priority_score(
                int(row.get("success_count") or 0),
                int(row.get("failure_count") or 0),
            ),
            str(row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    for row in ranked_rows:
        route = str(row.get("route") or "")
        success = int(row.get("success_count") or 0)
        failure = int(row.get("failure_count") or 0)
        outcome = str(row.get("last_outcome") or "")
        score = round(route_priority_score(success, failure), 3)
        if success > 0 and score > 0:
            lines.append(
                f"- Prefer route `{route}` for this env/task "
                f"(score={score}, success={success}, failure={failure})."
            )
        elif failure > success and outcome == "failure":
            lines.append(
                f"- Avoid route `{route}` for now "
                f"(score={score}, success={success}, failure={failure})."
            )
    return "\n".join(lines)[:1200]


def maybe_store_action_artifact(action_id: int, output: str, is_error: bool = False) -> str:
    text = str(output or "")
    if len(text) <= ACTION_RESULT_ARTIFACT_THRESHOLD:
        return text
    artifact_dir = Path(os.getenv("LANGGRAPH_STATE_DIR", str(STATE_DIR))).resolve() / "action_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    suffix = "error" if is_error else "result"
    path = artifact_dir / f"action_{action_id}_{suffix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(text, encoding="utf-8")
    summary = compact_action_output_for_model(text, 1200)
    artifact_url = f"/artifacts/action_artifacts/{path.name}"
    return json.dumps(
        {
            "artifact": True,
            "summary": summary,
            "artifact_path": str(path),
            "artifact_url": artifact_url,
            "bytes": len(text.encode("utf-8")),
            "truncated": True,
        },
        ensure_ascii=False,
        indent=2,
    )


def _queue_coding_recovery_action(job_id: str, failed_action: dict) -> bool:
    payload = failed_action.get("payload") if isinstance(failed_action.get("payload"), dict) else {}
    depth = int(payload.get("recovery_depth") or 0)
    if depth >= MAX_ACTION_RECOVERY_DEPTH:
        return False
    fallback = payload.get("fallback_tool_candidates") if isinstance(payload.get("fallback_tool_candidates"), list) else []
    if not fallback:
        return False
    tried = set(str(item).strip().lower() for item in (payload.get("tried_tools") or []))
    next_tool = None
    for candidate in fallback:
        name = str((candidate or {}).get("name") or "").strip()
        if not name or name.lower() in tried:
            continue
        next_tool = name
        break
    if not next_tool:
        return False

    request_text = str(payload.get("request") or "").strip()
    if not request_text:
        return False
    new_payload = {
        **payload,
        "recommended_tools": [{"name": next_tool}],
        "ordered_tool_recommendations": [{"name": next_tool}],
        "tried_tools": sorted(tried.union({next_tool.lower()})),
    }
    decision = {
        "mode": "pending_action",
        "reply": f"Claude vừa lỗi, mình chuyển sang fallback skill `{next_tool}` để thử hướng khác an toàn hơn.",
        "action": {
            "kind": "coding_agent_executor",
            "title": f"Gọi Claude executor (fallback: {next_tool})",
            "preview": request_text[:4000],
            "payload": new_payload,
        },
    }
    decision = tag_recovery_action(decision, failed_action)
    # Recovery for coding executor should continue automatically to avoid
    # forcing manual approve loops on legacy sessions that still have older
    # permission_mode values.
    auto_execute_safe = True
    saved = save_pending_action_from_decision(job_id, decision, auto_execute_safe=auto_execute_safe)
    if str(saved.get("status") or "") == "running":
        db.update_step(job_id, "recover", "running", f"Running fallback recovery via {next_tool}")
    else:
        db.update_step(job_id, "recover", "pending", f"Queued fallback recovery via {next_tool}")
    return True


def agent_follow_up_after_action(job_id: str, action: dict, ok: bool, output: str) -> None:
    job = db.get_job(job_id)
    if not job:
        return
    if ok and action.get("kind") == "coding_agent_executor":
        final = sanitize_manager_content(
            extract_coding_agent_output(output),
            "Claude executor đã chạy xong nhưng không trả về nội dung hiển thị phù hợp. Chi tiết nằm trong Logs.",
        )
        db.add_message(job_id, "claude", final)
        refresh_session_memory(job_id)
        return
    # Orchestration guard:
    # If request has already been delegated to Claude executor, do not let
    # LangGraph auto-spawn workspace recovery chains. Keep control with
    # Claude path and ask user for next instruction when it fails.
    if not ok and action.get("kind") == "coding_agent_executor":
        if _queue_coding_recovery_action(job_id, action):
            latest = db.get_job(job_id) or {}
            has_running = any(str(item.get("status") or "") == "running" for item in latest.get("pending_actions", []))
            db.add_message(
                job_id,
                "langgraph",
                (
                    "Claude executor vừa lỗi. Mình đang auto chạy fallback vì session này là full access."
                    if has_running
                    else "Claude executor vừa lỗi. Mình đã xếp một action fallback để bạn duyệt, tránh loop tự chạy."
                ),
            )
        else:
            payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
            tried_tools = payload.get("tried_tools") if isinstance(payload.get("tried_tools"), list) else []
            tried_text = ", ".join(str(item) for item in tried_tools[:6]) if tried_tools else "không có"
            db.add_message(
                job_id,
                "langgraph",
                (
                    "Claude executor vừa lỗi, và mình đã thử fallback nhưng không còn candidate khả dụng.\n"
                    f"Fallback đã thử: {tried_text}.\n"
                    "Vì vậy mình dừng auto-recovery để tránh LangGraph tự chạy lan man.\n"
                    "Mình đã giữ nguyên trạng thái và log lỗi ở panel Logs. "
                    "Anh nhắn lệnh tiếp theo cho Claude (hoặc cho phép mình thử 1 hướng cụ thể) là em chạy ngay."
                ),
            )
            db.update_step(job_id, "recover", "failed", "No safe fallback available")
        refresh_session_memory(job_id)
        return
    summary = compact_action_output_for_model(output, 800)
    if ok:
        db.add_message(
            job_id,
            "langgraph",
            f"Action `{action.get('kind')}` đã chạy xong. Tóm tắt: {summary}",
        )
    else:
        db.add_message(
            job_id,
            "langgraph",
            (
                "Action vừa chạy bị lỗi và mình không auto-chain thêm để tránh gọi model vòng 2.\n"
                f"Tóm tắt lỗi: {summary}\n"
                "Bạn nhắn bước tiếp theo, mình sẽ chuyển thẳng cho executor."
            ),
        )
    refresh_session_memory(job_id)


def execute_pending_action(action: dict, job: dict) -> tuple[bool, str]:
    signature = action_signature(action)
    lesson = db.get_action_lesson(signature)
    kind = action.get("kind")
    if kind != "coding_agent_executor" and lesson and lesson.get("status") == "active" and int(lesson.get("fail_count") or 0) >= 2:
        strategy = str(lesson.get("strategy") or "")
        return False, (
            f"Action bị chặn để tránh lặp lỗi cũ (signature={signature[:8]}). "
            f"Chiến lược né: {strategy or 'đổi hướng xử lý trước khi thử lại'}"
        )
    payload = action.get("payload") or {}
    if kind not in SUPPORTED_ACTION_KINDS:
        return False, f"Action kind '{kind}' is outside the Claude executor core and is disabled."
    if kind == "workspace_inspect":
        result = run_tool(
            "workspace_inspect",
            {
                "max_files": int(payload.get("max_files") or 120),
                "permission_mode": job.get("permission_mode", "auto_review"),
            },
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "workspace_read_file":
        result = run_tool(
            "workspace_read_file",
            {
                "path": str(payload.get("path") or ""),
                "max_chars": int(payload.get("max_chars") or 40000),
                "permission_mode": job.get("permission_mode", "auto_review"),
            },
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "workspace_diff":
        result = run_tool(
            "workspace_diff",
            {
                "path": str(payload.get("path") or ""),
                "timeout": int(payload.get("timeout") or 20),
                "permission_mode": job.get("permission_mode", "auto_review"),
            },
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "workspace_command":
        command = str(payload.get("command") or "").strip()
        if not command:
            return False, "workspace_command thiếu payload.command"
        cwd = str(payload.get("cwd") or "").strip()
        if cwd:
            try:
                candidate = Path(cwd)
                if not candidate.exists():
                    return False, f"workspace_command cwd không tồn tại: {cwd}"
            except Exception:
                return False, f"workspace_command cwd không hợp lệ: {cwd}"
        result = run_tool(
            "workspace_executor",
            {
                "command": command,
                "cwd": cwd,
                "risk": str(payload.get("risk") or "medium"),
                "timeout": int(payload.get("timeout") or 120),
                "permission_mode": job.get("permission_mode", "auto_review"),
            },
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "coding_agent_executor":
        executor = validate_coding_executor(str(payload.get("executor") or "claude"))
        remembered = db.get_executor_session(job.get("id", ""), executor)
        resume_session_id = str((remembered or {}).get("session_id") or "")
        request_text = str(payload.get("request") or job.get("request") or "")
        route_memory_hint = build_route_memory_hint(job, request_text)
        checkpoint_meta: dict[str, Any] = {}
        fail_count = int((lesson or {}).get("fail_count") or 0)
        threshold = max(1, int(payload.get("checkpoint_fail_threshold") or 3))
        policy = str(payload.get("git_checkpoint_policy") or "after_repeated_failures")
        should_checkpoint = bool(payload.get("git_checkpoint_before_run", False))
        if not should_checkpoint and policy == "after_repeated_failures":
            should_checkpoint = fail_count >= threshold
        if should_checkpoint:
            ok_cp, msg_cp, checkpoint = create_git_checkpoint_for_job(
                job.get("id", ""),
                str(payload.get("cwd") or ""),
                str(payload.get("checkpoint_label") or "auto-after-retries"),
            )
            if ok_cp and checkpoint:
                checkpoint_meta = {
                    "git_checkpoint": {
                        "id": checkpoint.get("id"),
                        "commit_hash": checkpoint.get("commit_hash"),
                        "branch": checkpoint.get("branch"),
                        "cwd": checkpoint.get("cwd"),
                    }
                }
            else:
                checkpoint_meta = {"git_checkpoint_warning": msg_cp}
        result = run_tool(
            "coding_agent_executor",
            {
                "request": str(payload.get("request") or job.get("request") or ""),
                "executor": executor,
                # Keep discussion concise when delegating to Claude executor.
                # Claude has its own memory/session; avoid duplicating with
                # LangGraph memory_context here.
                "discussion": discussion_with_focus(job, include_memory_context=False),
                "resume_session_id": resume_session_id,
                "route_memory_hint": route_memory_hint,
                "cwd": str(payload.get("cwd") or ""),
                "risk": str(payload.get("risk") or "medium"),
                "timeout": int(payload.get("timeout") or 900),
                "permission_mode": job.get("permission_mode", "auto_review"),
            },
        )
        if checkpoint_meta and isinstance(result, dict):
            data = result.get("data")
            if not isinstance(data, dict):
                data = {}
            data.update(checkpoint_meta)
            result["data"] = data
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "git_checkpoint":
        ok_cp, msg_cp, checkpoint = create_git_checkpoint_for_job(
            job.get("id", ""),
            str(payload.get("cwd") or ""),
            str(payload.get("label") or "manual-checkpoint"),
        )
        if not ok_cp:
            return False, msg_cp
        return True, json.dumps({"ok": True, "summary": msg_cp, "data": checkpoint}, ensure_ascii=False, indent=2)
    if kind == "git_restore_checkpoint":
        checkpoint_id = int(payload.get("checkpoint_id") or 0)
        checkpoint = db.get_git_checkpoint(checkpoint_id)
        if not checkpoint:
            return False, f"checkpoint #{checkpoint_id} not found"
        cwd = Path(str(checkpoint.get("cwd") or ""))
        if not cwd.exists():
            return False, f"checkpoint cwd not found: {cwd}"
        ok_reset, out_reset = run_git(["git", "reset", "--hard", str(checkpoint.get("commit_hash") or "")], cwd, timeout=40)
        if not ok_reset:
            return False, f"restore failed: {out_reset}"
        db.update_git_checkpoint_status(checkpoint_id, "restored")
        return True, f"Restored checkpoint #{checkpoint_id} to {str(checkpoint.get('commit_hash') or '')[:8]}"
    if kind == "create_memory":
        title = str(payload.get("title") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not title or not content:
            return False, "create_memory cần payload.title và payload.content"
        tags = _normalize_memory_tags(
            str(payload.get("kind") or "note"),
            title,
            content,
            str(payload.get("tags") or ""),
        )
        memory = db.create_memory(
            str(payload.get("kind") or "note"),
            title[:240],
            content,
            tags,
            "agent_action",
        )
        sync_memory_docs()
        return True, f"Đã lưu memory #{memory['id']}: {memory['title']}"
    if kind == "create_skill":
        name = str(payload.get("name") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not name or not body:
            return False, "create_skill cần payload.name và payload.body"
        skill = db.create_skill(
            name[:160],
            str(payload.get("description") or ""),
            str(payload.get("triggers") or ""),
            str(payload.get("source") or "agent_action"),
            body,
            str(payload.get("status") or "active"),
        )
        return True, f"Đã lưu skill #{skill['id']}: {skill['name']}"
    if kind == "create_recurring_task":
        name = str(payload.get("name") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        if not name or not prompt:
            return False, "create_recurring_task cần payload.name và payload.prompt"
        task = db.create_recurring_task(
            name[:160],
            prompt,
            str(payload.get("schedule") or "manual"),
            str(payload.get("action_kind") or "note"),
            payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
            str(payload.get("status") or "active"),
            str(payload.get("next_run_at") or ""),
        )
        return True, f"Đã tạo recurring task #{task['id']}: {task['name']}"
    if kind == "note":
        return True, str(payload.get("note") or action.get("preview") or "Đã ghi nhận note.")
    return False, f"Unsupported action kind: {kind}"


def parse_model_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            data, _ = decoder.raw_decode(cleaned[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def fetch_source_excerpt(source: str) -> str:
    source = source.strip()
    if not source.startswith(("http://", "https://")):
        return ""
    urls = [source]
    parsed = urlparse(source)
    if parsed.netloc.lower() == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            urls = [
                f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
                f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md",
                f"https://api.github.com/repos/{owner}/{repo}/contents",
            ]
    chunks: list[str] = []
    for url in urls:
        try:
            req = Request(url, headers={"User-Agent": "LangGraph-Manager/0.1"})
            with urlopen(req, timeout=12) as resp:
                body = resp.read(80_000).decode("utf-8", errors="replace")
                chunks.append(f"--- {url} ---\n{body[:25_000]}")
                if "raw.githubusercontent.com" in url and body.strip():
                    break
        except Exception as exc:
            chunks.append(f"--- {url} ---\nFETCH_FAILED: {exc}")
    return "\n\n".join(chunks)[:40_000]


ONBOARDING_AGENT_PLAN = [
    "Xác định mục tiêu onboarding agent end-to-end và các acceptance criteria cần chứng minh bằng evidence.",
    "Cập nhật backend để tạo/chạy flow onboarding agent, lưu trạng thái rõ ràng và không đánh dấu done khi verification chưa đạt.",
    "Cập nhật UI để người dùng khởi tạo, theo dõi và xem report onboarding gồm backend, UI, tests và deploy checklist.",
    "Bổ sung test backend/UI contract cho luồng plan/approve/run và các case verification fail để chặn fake report.",
    "Chạy kiểm tra phù hợp và chỉ report các thay đổi có bằng chứng từ command/test/diff/log.",
    "Lập deploy checklist gồm build, health check, rollback và phần cấu hình còn cần xác nhận trước khi deploy thật.",
]


def looks_like_onboarding_agent_request(text: str) -> bool:
    lower = text.lower()
    onboarding_markers = ["onboarding agent", "onboard agent", "flow onboarding", "luồng onboarding"]
    scope_markers = ["backend", "ui", "test", "deploy", "checklist"]
    return any(marker in lower for marker in onboarding_markers) and any(marker in lower for marker in scope_markers)


def fallback_planner_questions(discussion: str) -> list[str]:
    lower = discussion.lower()
    if looks_like_onboarding_agent_request(discussion):
        return []
    questions: list[str] = []
    if not any(marker in lower for marker in ["d:\\", "c:\\", "/workspace", "github", "repo", "project"]):
        questions.append("Bạn đã có sẵn project/repo để mình sửa chưa? Nếu có, gửi đường dẫn hoặc tên thư mục.")
    if not any(marker in lower for marker in ["python", "node", "playwright", "puppeteer", "selenium", "docker"]):
        questions.append("Bạn muốn build theo stack nào: Python, Node.js, Playwright/Puppeteer, Selenium hay web dashboard?")
    if "multi device" in lower or "finger" in lower or "fingerprint" in lower:
        questions.append("Mỗi device cần tách những gì: profile folder, cookie/session, proxy, user-agent, viewport/timezone/language?")
    if not any(marker in lower for marker in ["test", "kiểm tra", "api", "ui", "cli"]):
        questions.append("Sau khi làm xong bạn muốn kiểm tra bằng cách nào: CLI command, API endpoint, hay giao diện web?")
    return questions[:4] or ["Bạn muốn mình thực thi trong project nào và kết quả cuối cùng cần có dạng gì?"]


def planner_decision(discussion: str, recent_text: str = "") -> dict:
    recent = (recent_text or "").strip()
    onboarding_scope = recent or discussion
    messages = [{"role": "user", "content": f"Cuộc trò chuyện:\n{discussion}"}]
    started = time.perf_counter()
    result = cliproxy_chat(messages, PLANNER_SYSTEM_PROMPT)
    trace_event(
        "model_call",
        route="planner_decision",
        ok=bool(result.get("ok")),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        messages=len(messages),
    )
    if result.get("ok"):
        data = parse_model_json(str(result.get("data", {}).get("content", "")))
        if data.get("mode") == "plan" and isinstance(data.get("plan"), list) and data["plan"]:
            return {"mode": "plan", "plan": [str(item) for item in data["plan"][:10]], "summary": str(data.get("summary", ""))}
        if data.get("mode") == "questions" and isinstance(data.get("questions"), list) and data["questions"]:
            return {"mode": "questions", "questions": [str(item) for item in data["questions"][:4]], "reason": str(data.get("reason", ""))}
    questions = fallback_planner_questions(discussion)
    if not questions and looks_like_onboarding_agent_request(onboarding_scope):
        questions = [
            "Bạn xác nhận mục tiêu onboarding cụ thể trong phiên này là gì (deliverable cuối cùng)?",
            "Repo/path nào sẽ được áp dụng cho plan này?",
            "Bạn muốn ưu tiên backend, UI, test hay deploy trước?",
        ]
    return {"mode": "questions", "questions": questions, "reason": "fallback"}


def render_questions(questions: list[str]) -> str:
    lines = ["Mình cần bạn xác nhận thêm trước khi thực thi:"]
    lines.extend(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    return "\n".join(lines)


def compact_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(
        r"(.)\1{80,}",
        lambda match: f"{match.group(1) * 30}...[{len(match.group(0))} repeated chars]...{match.group(1) * 30}",
        text,
    )
    return text[:limit]


def is_duplicate_user_message(job: dict, content: str, window_seconds: int = 3) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    messages = list(job.get("messages") or [])
    if not messages:
        return False
    last = messages[-1]
    if str(last.get("role") or "") != "user":
        return False
    if str(last.get("content") or "").strip() != text:
        return False
    created_at = str(last.get("created_at") or "")
    if not created_at:
        return False
    try:
        ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return 0 <= age <= max(1, int(window_seconds))


def decode_pdf_literal(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == 92 and i + 1 < len(raw):
            nxt = raw[i + 1]
            maps = {110: 10, 114: 13, 116: 9, 98: 8, 102: 12, 40: 40, 41: 41, 92: 92}
            if nxt in maps:
                out.append(maps[nxt])
                i += 2
                continue
            if 48 <= nxt <= 55:
                j = i + 1
                digits = bytearray()
                while j < len(raw) and len(digits) < 3 and 48 <= raw[j] <= 55:
                    digits.append(raw[j])
                    j += 1
                try:
                    out.append(int(digits.decode("ascii"), 8))
                except ValueError:
                    pass
                i = j
                continue
            if nxt in {10, 13}:
                i += 2
                if nxt == 13 and i < len(raw) and raw[i] == 10:
                    i += 1
                continue
        out.append(ch)
        i += 1
    if out.startswith(b"\xfe\xff"):
        return out[2:].decode("utf-16-be", errors="ignore")
    return out.decode("latin-1", errors="ignore")


def pdf_literal_strings(blob: bytes) -> list[str]:
    strings: list[str] = []
    i = 0
    while i < len(blob):
        if blob[i] != 40:
            i += 1
            continue
        depth = 1
        escaped = False
        j = i + 1
        buf = bytearray()
        while j < len(blob):
            ch = blob[j]
            if escaped:
                buf.append(92)
                buf.append(ch)
                escaped = False
            elif ch == 92:
                escaped = True
            elif ch == 40:
                depth += 1
                buf.append(ch)
            elif ch == 41:
                depth -= 1
                if depth == 0:
                    text = decode_pdf_literal(bytes(buf)).strip()
                    if len(text) > 1 and any(c.isalnum() for c in text):
                        strings.append(text)
                    break
                buf.append(ch)
            else:
                buf.append(ch)
            j += 1
        i = j + 1
    return strings


def extract_pdf_text(data: bytes) -> str:
    for module_name in ("pypdf", "PyPDF2"):
        try:
            pdf_module = __import__(module_name)
            reader = pdf_module.PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(page.strip() for page in pages if page.strip())
            if text.strip():
                return re.sub(r"\n{3,}", "\n\n", text).strip()
        except Exception:
            pass

    chunks: list[bytes] = [data]
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, flags=re.S):
        stream = match.group(1).strip()
        prefix = data[max(0, match.start() - 700):match.start()]
        if b"FlateDecode" in prefix:
            try:
                chunks.append(zlib.decompress(stream))
                continue
            except zlib.error:
                pass
        chunks.append(stream)
    texts: list[str] = []
    for chunk in chunks:
        texts.extend(pdf_literal_strings(chunk))
        for hex_match in re.finditer(rb"<([0-9A-Fa-f\s]{8,})>", chunk):
            try:
                raw = bytes.fromhex(re.sub(rb"\s+", b"", hex_match.group(1)).decode("ascii"))
            except ValueError:
                continue
            text = raw.decode("utf-16-be" if b"\x00" in raw[:20] else "latin-1", errors="ignore").strip()
            if len(text) > 1 and any(c.isalnum() for c in text):
                texts.append(text)
    text = "\n".join(dict.fromkeys(t for t in texts if t.strip()))
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def decode_text_attachment(data: bytes, name: str, mime_type: str) -> str:
    lower_name = name.lower()
    if lower_name.endswith(".pdf") or "pdf" in mime_type.lower() or data.startswith(b"%PDF"):
        text = extract_pdf_text(data)
        if not text:
            raise HTTPException(status_code=422, detail="Không trích xuất được text từ PDF này. PDF có thể là scan/ảnh hoặc dùng font encoding đặc biệt.")
        return text
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


def safe_attachment_filename(name: str) -> str:
    raw = Path(str(name or "attachment")).name.strip() or "attachment"
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw).strip(" .")
    return safe[:160] or "attachment"


def attachment_root() -> Path:
    return Path(os.getenv("LANGGRAPH_STATE_DIR", str(STATE_DIR))).resolve() / ATTACHMENT_ROOT_NAME


def safe_attachment_session_id(job_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(job_id or "").strip())
    return value[:120] or f"transient_{secrets.token_hex(8)}"


def store_attachment_file(data: bytes, name: str, job_id: str = "") -> tuple[str, str, str]:
    session_id = safe_attachment_session_id(job_id)
    attachment_dir = attachment_root() / session_id
    attachment_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(4)}_{safe_attachment_filename(name)}"
    path = attachment_dir / filename
    path.write_bytes(data)
    artifact_url = f"/artifacts/{ATTACHMENT_ROOT_NAME}/{session_id}/{filename}"
    workspace_path = f"{ATTACHMENT_WORKSPACE_DIR.rstrip('/')}/{session_id}/{filename}"
    return str(path), artifact_url, workspace_path


def delete_attachment_file(stored_path: str) -> bool:
    root = attachment_root().resolve()
    path = Path(stored_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="attachment path không hợp lệ")
    if path.is_file():
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True
    return False


def delete_job_attachments(job_id: str) -> None:
    session_dir = attachment_root() / safe_attachment_session_id(job_id)
    if not session_dir.exists():
        return
    for path in sorted(session_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        session_dir.rmdir()
    except OSError:
        pass


def resolve_open_path(raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip())
    if not str(candidate):
        raise HTTPException(status_code=422, detail="path is required")
    if not candidate.is_absolute():
        candidate = (WORKSPACE_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    roots = [WORKSPACE_ROOT, STATE_DIR.resolve(), attachment_root().resolve()]
    for root in roots:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="path is outside allowed roots")


def looks_like_technical_dump(value: str) -> bool:
    text = str(value or "").strip()
    lower = text.lower()
    if not text:
        return False
    parsed = parse_model_json(text)
    if parsed.get("mode") in {"pending_action", "answer", "questions"}:
        return True
    if lower.startswith("<!doctype") or lower.startswith("<html") or "<body" in lower[:1200]:
        return True
    if "error code: 524" in lower or "a timeout occurred" in lower:
        return True
    if len(re.findall(r"</?[a-z][^>]*>", text[:5000], flags=re.I)) >= 8:
        return True
    if len(text) > 1200 and (text.count("{") + text.count("}") + text.count("[") + text.count("]")) > 80:
        return True
    return False


def sanitize_manager_content(value: str, fallback: str | None = None) -> str:
    text = str(value or "").strip()
    parsed = parse_model_json(text)
    if parsed.get("mode") == "pending_action":
        return str(parsed.get("reply") or fallback or "Mình đã chuẩn bị một action, cần kiểm tra trước khi chạy.")
    if parsed.get("mode") == "answer":
        return str(parsed.get("content") or fallback or "Mình chưa có nội dung trả lời phù hợp.")
    if parsed.get("mode") == "questions":
        return str(parsed.get("content") or parsed.get("question") or fallback or "Mình cần thêm thông tin để làm tiếp.")
    if not looks_like_technical_dump(text):
        return text or (fallback or "Mình chưa có nội dung trả lời phù hợp.")
    lower = text.lower()
    if "error code: 524" in lower or "a timeout occurred" in lower:
        return (
            "Trang/webhook bị Cloudflare timeout 524 nên request public bị ngắt. "
            "Mình đã không hiển thị HTML lỗi trong chat chính. Có thể thử lại qua local, chia task nhỏ hơn, hoặc chạy bằng browser/tool khác."
        )
    return fallback or (
        "Tool trả về dữ liệu kỹ thuật/raw HTML thay vì kết quả người dùng cần. "
        "Mình đã ẩn nội dung thô khỏi chat chính; chi tiết nên xem trong Logs/artifact."
    )


def refresh_session_memory(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        return db.default_session_memory(job_id)
    db.upsert_user_preference("last_active_job_id", str(job_id), source="system")
    db.upsert_user_preference("last_permission_mode", str(job.get("permission_mode") or "auto_review"), source="system")
    user_messages = [m for m in job.get("messages", []) if m.get("role") == "user" and m.get("content")]
    manager_messages = [m for m in job.get("messages", []) if is_assistant_message(m)]
    recent = [
        f"{m.get('role')}: {compact_text(m.get('content', ''), 180)}"
        for m in job.get("messages", [])[-8:]
        if m.get("content")
    ]
    pending = [a for a in job.get("pending_actions", []) if a.get("status") == "pending"]
    completed = [a for a in job.get("pending_actions", []) if a.get("status") in {"done", "failed", "rejected"}]
    current_goal = compact_text((user_messages[-1]["content"] if user_messages else job.get("request") or job.get("title") or ""), 600)
    open_tasks = "\n".join(f"- #{a.get('id')} {a.get('title')} ({a.get('kind')})" for a in pending[:8])
    important_files = "\n".join(f"- {f.get('name')} ({len(str(f.get('content', '')))} chars)" for f in job.get("focus_files", [])[:12])
    decisions = "\n".join(
        f"- {compact_text(m.get('content', ''), 240)}"
        for m in manager_messages[-5:]
        if any(marker in str(m.get("content", "")).lower() for marker in ["đã", "cần", "sẽ", "approve", "action", "plan"])
    )
    raw_last_result = job.get("result") or job.get("error") or (completed[-1].get("result") or completed[-1].get("error") if completed else "") or (manager_messages[-1]["content"] if manager_messages else "")
    if completed and (completed[-1].get("result") or completed[-1].get("error")) and not (job.get("result") or job.get("error")):
        last_result = compact_action_output_for_model(raw_last_result, 1200)
    else:
        last_result = compact_text(raw_last_result, 1200)
    summary = (
        f"Mục tiêu: {current_goal or 'Chưa rõ'}\n"
        f"Trạng thái: {job.get('status')}\n"
        f"Gần đây:\n" + "\n".join(f"- {line}" for line in recent)
    )[:3000]
    return db.upsert_session_memory(
        job_id,
        summary=summary,
        current_goal=current_goal,
        open_tasks=open_tasks,
        important_files=important_files,
        decisions=decisions,
        last_result=last_result,
    )


def session_memory_context(job_id: str | None) -> str:
    if not job_id:
        return ""
    memory = db.get_session_memory(job_id)
    fields = [
        ("summary", memory.get("summary")),
        ("current_goal", memory.get("current_goal")),
        ("open_tasks", memory.get("open_tasks")),
        ("important_files", memory.get("important_files")),
        ("decisions", memory.get("decisions")),
        ("last_result", memory.get("last_result")),
    ]
    lines = ["session_memory:"]
    has_content = False
    for key, value in fields:
        value = str(value or "").strip()
        if value:
            has_content = True
            lines.append(f"{key}:\n{value[:1800]}")
    return "\n".join(lines) if has_content else ""


def _keyword_set(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z0-9_]+", str(text or "").lower())
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "when",
        "anh", "em", "cho", "voi", "với", "cua", "của", "la", "là", "va", "và",
        "mot", "một", "nhung", "nhưng", "can", "cần", "hay", "hoac", "hoặc",
        "job", "task", "plan", "chat", "code", "file",
    }
    return {token for token in raw if len(token) >= 3 and token not in stop}


def _score_relevance(base_terms: set[str], text: str) -> int:
    if not base_terms:
        return 0
    terms = _keyword_set(text)
    if not terms:
        return 0
    overlap = base_terms.intersection(terms)
    return len(overlap)


def _estimate_tokens(text: str) -> int:
    # Fast local heuristic: ~4 chars per token for mixed VI/EN/code payloads.
    return max(1, (len(str(text or "")) + 3) // 4)


def _append_lines_with_budget(target: list[str], header: str, body_lines: list[str], budget_tokens: int) -> int:
    if budget_tokens <= 0 or not body_lines:
        return 0
    used = 0
    header_text = str(header or "").strip()
    if header_text:
        header_tokens = _estimate_tokens(header_text)
        if header_tokens >= budget_tokens:
            return 0
        target.append(header_text)
        used += header_tokens
    for line in body_lines:
        candidate = str(line or "").strip()
        if not candidate:
            continue
        tokens = _estimate_tokens(candidate)
        if used + tokens > budget_tokens:
            break
        target.append(candidate)
        used += tokens
    return used


def memory_context(job_id: str | None = None) -> str:
    job = db.get_job(job_id) if job_id else None
    session_memory = db.get_session_memory(job_id) if job_id else {}
    last_user_prompt = ""
    if job:
        user_msgs = [str(item.get("content") or "") for item in job.get("messages", []) if item.get("role") == "user"]
        if user_msgs:
            last_user_prompt = user_msgs[-1]
    goal_text = str((session_memory or {}).get("current_goal") or "")
    base_text = "\n".join([last_user_prompt, goal_text]).strip()
    base_terms = _keyword_set(base_text)

    lessons = db.list_action_lessons("active")[:30]
    lesson_terms = _keyword_set(
        " ".join(
            f"{item.get('signature') or ''} {item.get('kind') or ''} {item.get('error_excerpt') or ''} {item.get('strategy') or ''}"
            for item in lessons
        )
    )
    boosted_terms = base_terms.union(lesson_terms)

    user_memories = db.list_user_memories(limit=30)
    user_memory_ids = {int(item.get("id")) for item in user_memories if str(item.get("id", "")).isdigit()}
    raw_memories = [item for item in db.list_memories()[:120] if int(item.get("id") or 0) not in user_memory_ids]
    raw_skills = db.list_skills(include_inactive=False)[:60]
    scored_memories = sorted(
        raw_memories,
        key=lambda item: (
            _score_relevance(
                boosted_terms,
                f"{item.get('title') or ''} {item.get('tags') or ''} {item.get('content') or ''}",
            ),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )
    scored_skills = sorted(
        raw_skills,
        key=lambda item: (
            _score_relevance(
                boosted_terms,
                f"{item.get('name') or ''} {item.get('triggers') or ''} {item.get('description') or ''} {item.get('body') or ''}",
            ),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )
    if boosted_terms:
        related_memories = [
            item for item in scored_memories
            if _score_relevance(boosted_terms, f"{item.get('title') or ''} {item.get('tags') or ''} {item.get('content') or ''}") > 0
        ]
        related_skills = [
            item for item in scored_skills
            if _score_relevance(boosted_terms, f"{item.get('name') or ''} {item.get('triggers') or ''} {item.get('description') or ''} {item.get('body') or ''}") > 0
        ]
        memories = (related_memories[:18] or scored_memories[:8])
        skills = (related_skills[:12] or scored_skills[:6])
    else:
        memories = scored_memories[:12]
        skills = scored_skills[:8]
    commands = db.list_commands(include_inactive=False)[:30]
    projects = db.list_projects(include_inactive=False)[:20]
    user_preferences = db.list_user_preferences()[:40]
    role_plugins = db.list_role_plugins(include_inactive=False)[:20]
    recurring = db.list_recurring_tasks(include_inactive=False)[:20]
    route_rows: list[dict[str, Any]] = []
    if job:
        latest_request = str(last_user_prompt or job.get("request") or "")
        task_sig = task_signature_for_request(latest_request)
        env_fp = env_fingerprint_for_job(job)
        route_rows = db.list_route_memory(env_fp, task_sig, limit=5)
    lines: list[str] = []
    total_budget = max(600, MAX_MEMORY_CONTEXT_TOKENS)
    used_tokens = 0
    user_doc = load_user_memory_doc()
    if user_doc:
        used_tokens += _append_lines_with_budget(
            lines,
            "user.md (always_read):",
            [user_doc],
            min(max(220, MEMORY_CONTEXT_TOKEN_BUDGET // 2), total_budget - used_tokens),
        )
    session_context = session_memory_context(job_id)
    if session_context:
        used_tokens += _append_lines_with_budget(
            lines,
            "",
            [session_context],
            min(SESSION_CONTEXT_TOKEN_BUDGET, total_budget - used_tokens),
        )
    # Always load user-specific memory first to preserve user preferences/context.
    if user_memories:
        user_memory_lines: list[str] = []
        for item in user_memories[:12]:
            user_memory_lines.append(
                f"- {item.get('title')} (tags: {item.get('tags') or '-'}) :: {str(item.get('content', ''))[:420]}"
            )
        used_tokens += _append_lines_with_budget(
            lines,
            "user_memory (always_include):",
            user_memory_lines,
            min(max(180, MEMORY_CONTEXT_TOKEN_BUDGET // 3), total_budget - used_tokens),
        )
    if user_preferences:
        pref_lines: list[str] = []
        for item in user_preferences[:12]:
            pref_lines.append(
                f"- {item.get('key')} = {str(item.get('value') or '')[:300]} "
                f"(source: {item.get('source') or '-'})"
            )
        used_tokens += _append_lines_with_budget(
            lines,
            "user_preferences:",
            pref_lines,
            min(max(120, AUX_CONTEXT_TOKEN_BUDGET // 2), total_budget - used_tokens),
        )
    if memories:
        memory_lines: list[str] = []
        for item in memories:
            memory_lines.append(
                f"- [{item.get('kind')}] {item.get('title')} "
                f"(tags: {item.get('tags') or '-'}) :: {str(item.get('content', ''))[:520]}"
            )
        used_tokens += _append_lines_with_budget(
            lines,
            "long_term_memory:",
            memory_lines,
            min(MEMORY_CONTEXT_TOKEN_BUDGET, total_budget - used_tokens),
        )
    # Intentionally avoid injecting skill recommendations directly into model context.
    # Claude will discover applicable skills/tools via its own retrieval flow.
    _ = skills
    if used_tokens < total_budget:
        aux_lines: list[str] = []
        ranked_projects = sorted(
            projects,
            key=lambda item: (
                _score_relevance(
                    boosted_terms,
                    f"{item.get('name') or ''} {item.get('root_path') or ''} {item.get('summary') or ''} {item.get('memory') or ''}",
                ),
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )[:4]
        ranked_commands = sorted(
            commands,
            key=lambda item: (
                _score_relevance(
                    boosted_terms,
                    f"{item.get('name') or ''} {item.get('description') or ''} {item.get('action_kind') or ''}",
                ),
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )[:5]
        ranked_plugins = sorted(
            role_plugins,
            key=lambda item: (
                _score_relevance(
                    boosted_terms,
                    f"{item.get('name') or ''} {item.get('role') or ''} {item.get('tool_hints') or ''} {item.get('instructions') or ''}",
                ),
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )[:4]
        for item in ranked_projects:
            aux_lines.append(
                f"- project {item.get('name')} root={item.get('root_path') or '-'} :: "
                f"{str(item.get('summary', ''))[:220]} | memory: {str(item.get('memory', ''))[:280]}"
            )
        for item in ranked_commands:
            aux_lines.append(f"- command /{item.get('name')} -> {item.get('action_kind')} :: {str(item.get('description', ''))[:180]}")
        for item in ranked_plugins:
            aux_lines.append(
                f"- plugin {item.get('name')} role={item.get('role') or '-'} tools={item.get('tool_hints') or '-'} :: "
                f"{str(item.get('instructions', item.get('description', '')))[:220]}"
            )
        # Route memory: prioritize proven paths by win-rate and evidence volume.
        ranked_routes = sorted(
            route_rows,
            key=lambda item: (
                (int(item.get("success_count") or 0) / max(1, int(item.get("success_count") or 0) + int(item.get("failure_count") or 0))),
                int(item.get("success_count") or 0),
                -int(item.get("failure_count") or 0),
                str(item.get("updated_at") or ""),
            ),
            reverse=True,
        )
        for item in ranked_routes:
            success = int(item.get("success_count") or 0)
            failure = int(item.get("failure_count") or 0)
            total = max(1, success + failure)
            if success <= 0:
                continue
            win_rate = round(success / total, 3)
            aux_lines.append(
                f"- successful_route task={item.get('task_signature')} route={item.get('route')} "
                f"win_rate={win_rate} success={success} failure={failure} last={item.get('updated_at') or '-'}"
            )
        for item in recurring[:3]:
            aux_lines.append(
                f"- recurring {item.get('name')} schedule={item.get('schedule')} next={item.get('next_run_at') or '-'} :: {str(item.get('prompt', ''))[:180]}"
            )
        used_tokens += _append_lines_with_budget(
            lines,
            "aux_context:",
            aux_lines,
            min(AUX_CONTEXT_TOKEN_BUDGET, total_budget - used_tokens),
        )
    rendered = "\n".join(lines)[:MAX_MEMORY_CONTEXT_CHARS]
    return rendered


def discussion_with_focus(job: dict, include_memory_context: bool = True) -> str:
    lines = [f"{m['role']}: {m['content']}" for m in job.get("messages", [])]
    if include_memory_context:
        mem = memory_context(job.get("id"))
        if mem:
            lines.insert(0, mem)
    focus_files = job.get("focus_files", [])
    if focus_files:
        lines.append("focus_files:")
        for item in focus_files:
            content = str(item.get("content", ""))
            lines.append(f"--- {item.get('name', 'file')} ---\n{content[:12000]}")
    return "\n".join(lines)


def _split_tags(tags: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n;]+", str(tags or "")) if part.strip()]


def _infer_memory_folder(kind: str, title: str, content: str, tags: str) -> str:
    k = str(kind or "").strip().lower()
    blob = f"{title}\n{content}\n{tags}".lower()
    if k == "user" or any(token in blob for token in ["user:", "profile", "persona", "preference", "sở thích", "thói quen"]):
        return "user/profile"
    if any(token in blob for token in ["route", "success", "win_rate", "đường đi", "thành công"]):
        return "debug/routes-success"
    if any(token in blob for token in ["error", "failed", "bug", "lỗi", "timeout", "permission"]):
        return "debug/findings"
    if "skill" in k or any(token in blob for token in ["trigger", "workflow", "pattern", "reuse"]):
        return "skills/patterns"
    if any(token in blob for token in ["file", "path", "repo", "workspace", ".py", ".ts", ".tsx", ".js"]):
        return "project/context"
    return "notes/general"


def _normalize_memory_tags(kind: str, title: str, content: str, tags: str) -> str:
    parts = _split_tags(tags)
    if str(kind or "").strip().lower() == "user" and not any(part.lower().startswith("user:") for part in parts):
        parts.append("user:true")
    if not any(part.startswith("folder:") for part in parts):
        parts.append(f"folder:{_infer_memory_folder(kind, title, content, tags)}")
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return ",".join(out)


def _memory_folder(item: dict[str, Any]) -> str:
    tags = _split_tags(str(item.get("tags") or ""))
    for part in tags:
        if part.lower().startswith("folder:"):
            return part.split(":", 1)[1].strip() or "notes/general"
    return _infer_memory_folder(
        str(item.get("kind") or ""),
        str(item.get("title") or ""),
        str(item.get("content") or ""),
        str(item.get("tags") or ""),
    )


def _safe_memory_folder_path(folder: str) -> Path:
    parts = [re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-._") or "misc" for part in folder.split("/")]
    out = MEMORY_DOCS_ROOT
    for part in parts:
        out = out / part
    return out


def _render_memory_item_md(item: dict[str, Any]) -> str:
    lines = [
        f"### #{item.get('id')} {item.get('title') or '(untitled)'}",
        f"- kind: {item.get('kind') or '-'}",
        f"- tags: {item.get('tags') or '-'}",
        f"- source: {item.get('source') or '-'}",
        f"- updated_at: {item.get('updated_at') or '-'}",
        "",
        str(item.get("content") or "").strip(),
        "",
    ]
    return "\n".join(lines)


def sync_memory_docs() -> None:
    MEMORY_DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    memories = db.list_memories()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in memories:
        grouped.setdefault(_memory_folder(item), []).append(item)
    for folder, items in grouped.items():
        items_sorted = sorted(items, key=lambda it: str(it.get("updated_at") or ""), reverse=True)
        folder_path = _safe_memory_folder_path(folder)
        folder_path.mkdir(parents=True, exist_ok=True)
        content = [
            f"# Memory Folder: {folder}",
            "",
            f"Total: {len(items_sorted)}",
            "",
        ]
        for item in items_sorted:
            content.append(_render_memory_item_md(item))
        (folder_path / "MEMORY.md").write_text("\n".join(content).strip() + "\n", encoding="utf-8")

    user_items = db.list_user_memories(limit=500)
    user_sorted = sorted(user_items, key=lambda it: str(it.get("updated_at") or ""), reverse=True)
    user_lines = [
        "# user.md",
        "",
        "Long-term user profile/preferences memory.",
        f"Total: {len(user_sorted)}",
        "",
    ]
    for item in user_sorted:
        user_lines.append(_render_memory_item_md(item))
    (MEMORY_DOCS_ROOT / "user.md").write_text("\n".join(user_lines).strip() + "\n", encoding="utf-8")


def load_user_memory_doc(max_chars: int = 12000) -> str:
    path = MEMORY_DOCS_ROOT / "user.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    return text[:max_chars]


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def model_chat_stream_events(job_id: str, fallback_text: str):
    job = db.get_job(job_id)
    if not job:
        yield sse_event("error", {"error": "job not found"})
        return
    history = [
        item for item in job.get("messages", [])
        if (item.get("role") == "user" or is_assistant_message(item)) and item.get("content")
    ][-MODEL_HISTORY_MESSAGES:]
    model_messages = [
        {
            "role": model_message_role(item),
            "content": compact_text(str(item.get("content", "")), 1600),
        }
        for item in history
    ]
    mem = memory_context(job_id)
    if mem:
        model_messages.insert(0, {"role": "user", "content": f"Context memory/skills:\n{mem}"})
    collected: list[str] = []
    try:
        for delta in cliproxy_chat_stream(model_messages, compose_system_prompt(CHAT_SYSTEM_PROMPT)):
            collected.append(delta)
            yield sse_event("delta", {"delta": delta})
    except Exception:
        fallback_result = cliproxy_chat(model_messages, compose_system_prompt(CHAT_SYSTEM_PROMPT))
        fallback = (
            str(fallback_result.get("data", {}).get("content", "")).strip()
            if fallback_result.get("ok")
            else casual_reply(fallback_text)
        )
        collected = [fallback]
        yield sse_event("delta", {"delta": fallback})
    final = sanitize_manager_content("".join(collected).strip() or casual_reply(fallback_text), casual_reply(fallback_text))
    db.add_message(job_id, "langgraph", final)
    refresh_session_memory(job_id)
    yield sse_event("done", {"job": db.get_job(job_id)})


def compute_next_run(schedule: str, from_time: datetime | None = None) -> str:
    base = from_time or datetime.now(timezone.utc)
    text = schedule.strip().lower()
    if text.startswith("interval:"):
        try:
            minutes = max(1, int(text.split(":", 1)[1]))
            return (base + timedelta(minutes=minutes)).isoformat()
        except ValueError:
            return ""
    if text == "hourly":
        return (base + timedelta(hours=1)).isoformat()
    if text == "daily":
        return (base + timedelta(days=1)).isoformat()
    if text == "weekly":
        return (base + timedelta(days=7)).isoformat()
    return ""


def queue_due_recurring_tasks() -> list[dict]:
    now = datetime.now(timezone.utc)
    due = db.list_due_recurring_tasks(now.isoformat())
    queued: list[dict] = []
    for task in due:
        job_id = "auto_" + secrets.token_hex(5)
        job = db.create_chat_session(job_id, title=f"Recurring: {task['name']}", permission_mode="auto_review")
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        if not payload:
            payload = {"note": task.get("prompt", "")}
        action = db.add_pending_action(
            job["id"],
            str(task.get("action_kind") or "note"),
            f"Recurring: {task['name']}"[:160],
            str(task.get("prompt") or "")[:4000],
            payload,
        )
        db.add_message(job["id"], "langgraph", f"Recurring task tới hạn, đã tạo action chờ approve: {task['name']}")
        refresh_session_memory(job["id"])
        next_run_at = compute_next_run(str(task.get("schedule") or ""), now)
        db.mark_recurring_task_run(int(task["id"]), next_run_at)
        queued.append({"task_id": task["id"], "job_id": job["id"], "action_id": action["id"], "next_run_at": next_run_at})
    return queued


def recurring_scheduler_loop() -> None:
    while True:
        try:
            queue_due_recurring_tasks()
        except Exception:
            pass
        time.sleep(60)


def seed_operating_defaults() -> None:
    legacy_default_commands = {"host-browser", "open-host", "screenshot-host", "browse", "n8n", "schedule"}
    for command in db.list_commands(include_inactive=True):
        if command.get("name") in legacy_default_commands:
            try:
                db.delete_command(int(command["id"]))
            except Exception:
                pass
    default_commands = [
        (
            "claude",
            "Giao task code cho Claude executor",
            "coding_agent_executor",
            {"request": "{{input}}", "cwd": "", "risk": "medium", "timeout": 900},
        ),
        (
            "computer",
            "Chạy lệnh kiểm tra workspace qua command chờ approve",
            "workspace_command",
            {"command": "{{input}}", "cwd": "", "risk": "medium", "timeout": 120},
        ),
        (
            "remember",
            "Lưu memory dài hạn sau khi approve",
            "create_memory",
            {"kind": "note", "title": "{{input}}", "content": "{{input}}", "tags": "manual,slash-command"},
        ),
        (
            "skill",
            "Tạo skill nháp từ mô tả",
            "create_skill",
            {"name": "{{input}}", "description": "Skill draft from /skill", "triggers": "{{input}}", "source": "slash-command", "body": "# {{input}}\n\n## Workflow\n- Define the repeatable steps.\n- Add safety checks.\n- Add verification."},
        ),
    ]
    for name, description, kind, payload in default_commands:
        existing = db.get_command(name)
        if existing:
            try:
                db.update_command(int(existing["id"]), name, description, kind, payload, "active")
            except Exception:
                pass
        else:
            try:
                db.create_command(name, description, kind, payload, "active")
            except Exception:
                pass
    if not db.list_role_plugins(include_inactive=False):
        defaults = [
            ("Claude coder", "code", "Sửa code bằng Claude executor, đọc repo, chạy check phù hợp, báo lỗi thật.", "Đọc code trước khi sửa. Ưu tiên patch nhỏ, chạy check phù hợp, không báo xong nếu chưa verify.", "coding_agent_executor,workspace_command,docker"),
            ("Ops cowork", "ops", "Theo dõi Docker, Cloudflare, n8n, deploy.", "Kiểm tra health endpoint, log service, biến môi trường và tunnel trước khi đổi cấu hình.", "docker,n8n,cloudflare,workspace_command"),
            ("Video cowork", "video", "Xử lý workflow video/cut/render/upload.", "Ưu tiên pipeline có sẵn, giữ output path rõ ràng, kiểm tra file output tồn tại và log ffmpeg.", "n8n,cut-video,docker"),
            ("Marketing cowork", "marketing", "Research, caption, content, campaign.", "Hỏi rõ khách hàng/mục tiêu/kênh trước khi tạo nội dung hàng loạt. Lưu template tốt thành skill.", "web,n8n,gmail"),
        ]
        for item in defaults:
            try:
                db.create_role_plugin(*item, status="active")
            except Exception:
                pass


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    seed_operating_defaults()
    db.fail_running_jobs_on_startup()
    sync_memory_docs()
    if os.getenv("LANGGRAPH_DISABLE_SCHEDULER", "").lower() not in {"1", "true", "yes"}:
        thread = threading.Thread(target=recurring_scheduler_loop, daemon=True)
        thread.start()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "langgraph-manager"}


@app.get("/api/network")
def network_info():
    urls = ["http://host.docker.internal:8899", "http://127.0.0.1:8899"]
    public_url = os.getenv("LANGGRAPH_PUBLIC_URL", "").strip()
    if public_url:
        urls.append(public_url)
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        hostname = "unknown"
    for ip in sorted(addresses):
        urls.append(f"http://{ip}:8899")
    return {"ok": True, "hostname": hostname, "urls": list(dict.fromkeys(urls))}


@app.get("/api/memories")
def list_memories():
    return {"memories": db.list_memories()}


@app.get("/api/memories/grouped")
def list_memories_grouped():
    rows = db.list_memories()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in rows:
        folder = _memory_folder(item)
        grouped.setdefault(folder, []).append(item)
    ordered = {
        folder: sorted(items, key=lambda it: str(it.get("updated_at") or ""), reverse=True)
        for folder, items in sorted(grouped.items(), key=lambda kv: kv[0])
    }
    return {"folders": ordered}


@app.get("/api/memory-docs")
def list_memory_docs():
    sync_memory_docs()
    files = []
    if MEMORY_DOCS_ROOT.exists():
        for path in sorted(MEMORY_DOCS_ROOT.rglob("*.md")):
            files.append(
                {
                    "path": str(path),
                    "relative_path": str(path.relative_to(MEMORY_DOCS_ROOT)).replace("\\", "/"),
                    "size": path.stat().st_size,
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                }
            )
    return {"root": str(MEMORY_DOCS_ROOT), "files": files}


@app.post("/api/memories")
def create_memory(payload: MemoryRequest):
    tags = _normalize_memory_tags(payload.kind, payload.title, payload.content, payload.tags)
    memory = db.create_memory(payload.kind, payload.title, payload.content, tags, payload.source)
    sync_memory_docs()
    return {"memory": memory}


@app.put("/api/memories/{memory_id}")
def update_memory(memory_id: int, payload: MemoryRequest):
    tags = _normalize_memory_tags(payload.kind, payload.title, payload.content, payload.tags)
    if not db.update_memory(memory_id, payload.kind, payload.title, payload.content, tags, payload.source):
        raise HTTPException(status_code=404, detail="memory not found")
    sync_memory_docs()
    return {"memories": db.list_memories()}


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int):
    if not db.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    sync_memory_docs()
    return {"ok": True}


@app.get("/api/user-preferences")
def list_user_preferences():
    return {"user_preferences": db.list_user_preferences()}


@app.put("/api/user-preferences/{key}")
def upsert_user_preference(key: str, payload: UserPreferenceRequest):
    if key != payload.key:
        raise HTTPException(status_code=422, detail="path key must match payload.key")
    record = db.upsert_user_preference(payload.key.strip(), payload.value, payload.source)
    return {"user_preference": record}


@app.delete("/api/user-preferences/{key}")
def delete_user_preference(key: str):
    if not db.delete_user_preference(key):
        raise HTTPException(status_code=404, detail="user preference not found")
    return {"ok": True}


@app.get("/api/action-lessons")
def list_action_lessons(status: str = ""):
    value = status.strip() or None
    return {"action_lessons": db.list_action_lessons(value)}


@app.get("/api/git-checkpoints")
def list_git_checkpoints(job_id: str = ""):
    return {"git_checkpoints": db.list_git_checkpoints(job_id)}


@app.get("/api/skills")
def list_skills():
    return {"skills": db.list_skills()}


@app.get("/api/skills/performance")
def list_skill_performance():
    return {"skill_performance": db.list_skill_performance()}


def _tail_trace_events(limit: int = 80) -> list[dict[str, Any]]:
    if not TRACE_LOG.exists():
        return []
    try:
        lines = TRACE_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-max(1, min(limit, 300)):]:
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
            if isinstance(item, dict):
                events.append(item)
        except Exception:
            continue
    return events


@app.get("/api/quality/overview")
def quality_overview():
    jobs = db.list_jobs()
    status_counter = Counter(str(job.get("status") or "unknown") for job in jobs)
    action_lessons = db.list_action_lessons()
    skill_performance = db.list_skill_performance()
    checkpoints = db.list_git_checkpoints()[:20]
    success_routes = db.list_successful_routes(16)
    traces = _tail_trace_events(2000)
    last_errors = [event for event in reversed(traces) if not event.get("ok", True)][:10]
    route_events = [event for event in traces if event.get("event") == "route_decision" and event.get("route")]
    non_casual_routes = [event for event in route_events if event.get("route") == "executor_direct"]
    one_hop_routes = [event for event in non_casual_routes if bool(event.get("one_hop_executor"))]
    one_hop_rate = (len(one_hop_routes) / len(non_casual_routes) * 100.0) if non_casual_routes else 0.0
    return {
        "summary": {
            "jobs_total": len(jobs),
            "jobs_by_status": dict(status_counter),
            "lessons_total": len(action_lessons),
            "lessons_active": sum(1 for x in action_lessons if x.get("status") == "active"),
            "lessons_resolved": sum(1 for x in action_lessons if x.get("status") == "resolved"),
            "skill_total": len(skill_performance),
            "skill_active": sum(1 for x in skill_performance if x.get("status") == "active"),
            "skill_deprioritized": sum(1 for x in skill_performance if x.get("status") == "deprioritized"),
            "skill_retired": sum(1 for x in skill_performance if x.get("status") == "retired"),
            "checkpoints_total": len(checkpoints),
            "trace_events": len(traces),
            "trace_errors": len(last_errors),
            "route_events_total": len(route_events),
            "non_casual_route_total": len(non_casual_routes),
            "one_hop_executor_total": len(one_hop_routes),
            "one_hop_executor_rate": round(one_hop_rate, 2),
            "successful_routes_total": len(success_routes),
        },
        "last_errors": list(reversed(last_errors)),
        "lessons_top": action_lessons[:10],
        "skill_top": skill_performance[:12],
        "successful_routes": success_routes,
        "checkpoints": checkpoints,
        "trace_tail": traces[-40:],
    }


@app.post("/api/skills")
def create_skill(payload: SkillRequest):
    return {
        "skill": db.create_skill(
            payload.name,
            payload.description,
            payload.triggers,
            payload.source,
            payload.body,
            payload.status,
        )
    }


@app.put("/api/skills/{skill_id}")
def update_skill(skill_id: int, payload: SkillRequest):
    if not db.update_skill(
        skill_id,
        payload.name,
        payload.description,
        payload.triggers,
        payload.source,
        payload.body,
        payload.status,
    ):
        raise HTTPException(status_code=404, detail="skill not found")
    return {"skills": db.list_skills()}


@app.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: int):
    if not db.delete_skill(skill_id):
        raise HTTPException(status_code=404, detail="skill not found")
    return {"ok": True}


@app.post("/api/skills/research")
def research_skill(payload: SkillResearchRequest):
    source_excerpt = fetch_source_excerpt(payload.source)
    messages = [
        {
            "role": "user",
            "content": (
                f"Source/repo/link:\n{payload.source}\n\n"
                f"Fetched source excerpt:\n{source_excerpt}\n\n"
                f"Yêu cầu skill:\n{payload.prompt}\n\n"
                f"Memory hiện có:\n{memory_context()}"
            ),
        }
    ]
    result = cliproxy_chat(messages, SKILL_RESEARCH_SYSTEM_PROMPT)
    data = parse_model_json(str(result.get("data", {}).get("content", ""))) if result.get("ok") else {}
    name = str(data.get("name") or payload.prompt.strip().splitlines()[0][:80])
    description = str(data.get("description") or "Skill draft generated from research prompt.")
    triggers = str(data.get("triggers") or "")
    body = str(data.get("body") or f"# {name}\n\n## Purpose\n{payload.prompt}\n\n## Source\n{payload.source or 'manual'}\n")
    skill = db.create_skill(name, description, triggers, payload.source, body, "active")
    memories = []
    for item in data.get("memory_suggestions", []) if isinstance(data.get("memory_suggestions"), list) else []:
        if isinstance(item, dict) and item.get("title") and item.get("content"):
            memories.append(
                db.create_memory(
                    str(item.get("kind") or "skill"),
                    str(item.get("title")),
                    str(item.get("content")),
                    str(item.get("tags") or "skill"),
                    "skill_research",
                )
            )
    if memories:
        sync_memory_docs()
    return {"skill": skill, "memories": memories, "raw": str(result.get("data", {}).get("content", ""))[:4000]}


@app.get("/api/commands")
def list_commands():
    return {"commands": db.list_commands()}


@app.post("/api/commands")
def create_command(payload: CommandRequest):
    return {"command": db.create_command(payload.name, payload.description, payload.action_kind, payload.payload, payload.status)}


@app.put("/api/commands/{command_id}")
def update_command(command_id: int, payload: CommandRequest):
    if not db.update_command(command_id, payload.name, payload.description, payload.action_kind, payload.payload, payload.status):
        raise HTTPException(status_code=404, detail="command not found")
    return {"commands": db.list_commands()}


@app.delete("/api/commands/{command_id}")
def delete_command(command_id: int):
    if not db.delete_command(command_id):
        raise HTTPException(status_code=404, detail="command not found")
    return {"ok": True}


@app.get("/api/recurring-tasks")
def list_recurring_tasks():
    return {"recurring_tasks": db.list_recurring_tasks()}


@app.post("/api/recurring-tasks")
def create_recurring_task(payload: RecurringTaskRequest):
    return {
        "recurring_task": db.create_recurring_task(
            payload.name,
            payload.prompt,
            payload.schedule,
            payload.action_kind,
            payload.payload,
            payload.status,
            payload.next_run_at,
        )
    }


@app.put("/api/recurring-tasks/{task_id}")
def update_recurring_task(task_id: int, payload: RecurringTaskRequest):
    if not db.update_recurring_task(
        task_id,
        payload.name,
        payload.prompt,
        payload.schedule,
        payload.action_kind,
        payload.payload,
        payload.status,
        payload.next_run_at,
    ):
        raise HTTPException(status_code=404, detail="recurring task not found")
    return {"recurring_tasks": db.list_recurring_tasks()}


@app.delete("/api/recurring-tasks/{task_id}")
def delete_recurring_task(task_id: int):
    if not db.delete_recurring_task(task_id):
        raise HTTPException(status_code=404, detail="recurring task not found")
    return {"ok": True}


@app.post("/api/recurring-tasks/run-due")
def run_due_recurring_tasks():
    return {"queued": queue_due_recurring_tasks()}


@app.get("/api/projects")
def list_projects():
    return {"projects": db.list_projects()}


@app.post("/api/projects")
def create_project(payload: ProjectRequest):
    return {"project": db.create_project(payload.name, payload.root_path, payload.summary, payload.memory, payload.status)}


@app.put("/api/projects/{project_id}")
def update_project(project_id: int, payload: ProjectRequest):
    if not db.update_project(project_id, payload.name, payload.root_path, payload.summary, payload.memory, payload.status):
        raise HTTPException(status_code=404, detail="project not found")
    return {"projects": db.list_projects()}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int):
    if not db.delete_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}


@app.get("/api/role-plugins")
def list_role_plugins():
    return {"role_plugins": db.list_role_plugins()}


@app.post("/api/role-plugins")
def create_role_plugin(payload: RolePluginRequest):
    return {
        "role_plugin": db.create_role_plugin(
            payload.name,
            payload.role,
            payload.description,
            payload.instructions,
            payload.tool_hints,
            payload.status,
        )
    }


@app.put("/api/role-plugins/{plugin_id}")
def update_role_plugin(plugin_id: int, payload: RolePluginRequest):
    if not db.update_role_plugin(
        plugin_id,
        payload.name,
        payload.role,
        payload.description,
        payload.instructions,
        payload.tool_hints,
        payload.status,
    ):
        raise HTTPException(status_code=404, detail="role plugin not found")
    return {"role_plugins": db.list_role_plugins()}


@app.delete("/api/role-plugins/{plugin_id}")
def delete_role_plugin(plugin_id: int):
    if not db.delete_role_plugin(plugin_id):
        raise HTTPException(status_code=404, detail="role plugin not found")
    return {"ok": True}


@app.get("/api/tools/health")
def tools_health():
    checks: dict[str, Any] = {
        "executor": {
            "configured": bool(os.getenv("CLAUDE_EXECUTOR_COMMAND") or os.getenv("CODING_AGENT_COMMAND")),
            "timeout": int(os.getenv("CLAUDE_EXECUTOR_TIMEOUT", "900")),
            "path": "Claude executor -> free-claude-code -> CLIProxy",
        },
        "executors": {
            "selected": "claude",
            "claude": {
                "configured": bool(os.getenv("CLAUDE_EXECUTOR_COMMAND") or os.getenv("CODING_AGENT_COMMAND")),
                "timeout": int(os.getenv("CLAUDE_EXECUTOR_TIMEOUT", "900")),
                "path": "Claude executor -> free-claude-code -> CLIProxy",
            },
        },
    }
    try:
        models = http_json("GET", "http://host.docker.internal:8082/v1/models", token=os.getenv("ANTHROPIC_AUTH_TOKEN", "freecc"), timeout=10)
        checks["free_claude_code"] = {"ok": True, "model_count": len(models.get("data", [])) if isinstance(models.get("data"), list) else None}
    except Exception as exc:
        checks["free_claude_code"] = {"ok": False, "error": str(exc)}
    checks["cliproxy_base_url"] = os.getenv("CLIPROXY_BASE_URL", "http://host.docker.internal:8317").rstrip("/")
    return {"ok": True, "tools": checks}


@app.get("/api/claude/skill-index")
def claude_skill_index():
    return scan_claude_skill_index()


@app.get("/api/claude/skill-search")
def claude_skill_search(q: str = "", limit: int = 6):
    return search_claude_skill_index(q, max(1, min(limit, 20)))


@app.get("/api/tools")
def list_tools():
    return {"tools": describe_tools()}


@app.post("/api/attachments/read")
def read_attachment(payload: AttachmentReadRequest):
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="attachment base64 không hợp lệ")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail=f"File quá lớn. Giới hạn hiện tại là {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.")
    stored_path, artifact_url, workspace_path = store_attachment_file(data, payload.name, payload.job_id)
    warning = ""
    try:
        text = decode_text_attachment(data, payload.name, payload.mime_type)
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        text = ""
        warning = str(exc.detail)
    truncated = len(text) > MAX_ATTACHMENT_TEXT_CHARS
    if truncated:
        text = text[:MAX_ATTACHMENT_TEXT_CHARS]
    return {
        "name": payload.name,
        "mime_type": payload.mime_type,
        "bytes": len(data),
        "text": text,
        "truncated": truncated,
        "warning": warning,
        "stored_path": stored_path,
        "artifact_url": artifact_url,
        "workspace_path": workspace_path,
    }


@app.post("/api/attachments/delete")
def delete_attachment(payload: AttachmentDeleteRequest):
    return {"ok": delete_attachment_file(payload.stored_path)}


@app.get("/api/files/open")
def open_file(path: str):
    target = resolve_open_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    if target.is_dir():
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
            entries.append({
                "name": item.name,
                "path": str(item),
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"path": str(target), "entries": entries}
    return FileResponse(str(target), filename=target.name)


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": db.list_jobs()}


@app.get("/api/chat")
def get_main_chat():
    job = db.get_or_create_chat()
    detail = db.get_job(job["id"])
    return {"job": detail or job}


@app.post("/api/chat/messages")
def add_main_chat_message(payload: MessageRequest):
    job = db.get_or_create_chat()
    return {"job": handle_agent_message(job["id"], payload.content)}


@app.post("/api/chat/messages/stream")
def add_main_chat_message_stream(payload: MessageRequest):
    job = db.get_or_create_chat()
    detail = db.get_job(job["id"]) or job
    if is_duplicate_user_message(detail, payload.content):
        return StreamingResponse(
            iter(["event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    db.add_message(job["id"], "user", payload.content)
    return StreamingResponse(
        model_chat_stream_events(job["id"], payload.content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chats")
def create_chat_session(payload: ChatSessionRequest | None = None):
    job_id = "chat_" + secrets.token_hex(5)
    permission_mode = validate_permission_mode(payload.permission_mode if payload else "full_access")
    job = db.create_chat_session(job_id, permission_mode=permission_mode)
    refresh_session_memory(job["id"])
    return {"job": db.get_job(job["id"]) or job}


@app.post("/api/jobs/{job_id}/plan-from-chat")
def plan_from_chat(job_id: str, payload: MessageRequest):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in {"chat", "draft", "planned", "needs_input"}:
        raise HTTPException(status_code=409, detail=f"cannot plan from {job['status']}")
    db.add_message(job_id, "user", payload.content)
    updated = db.get_job(job_id) or job
    discussion = discussion_with_focus(updated)
    planning_request = f"Create an executable plan from this ongoing session.\n\nDiscussion:\n{discussion}"
    decision = planner_decision(discussion, payload.content)
    if decision["mode"] == "questions":
        db.replace_plan(job_id, [], render_questions(decision["questions"]), request=planning_request)
        db.update_job_status(job_id, "needs_input")
        refresh_session_memory(job_id)
        return {"job": db.get_job(job_id)}
    db.replace_plan(
        job_id,
        decision["plan"],
        "Mình đã có đủ thông tin để lập plan cụ thể. Bạn xem lại rồi approve nếu ổn.",
        request=planning_request,
    )
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}


@app.put("/api/jobs/{job_id}/plan")
def edit_plan(job_id: str, payload: PlanEditRequest):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in {"draft", "planned", "approved", "needs_input"}:
        raise HTTPException(status_code=409, detail=f"cannot edit plan from {job['status']}")
    clean_plan = [str(item).strip() for item in payload.plan if str(item).strip()][:20]
    if not clean_plan:
        raise HTTPException(status_code=422, detail="plan must contain at least one step")
    note = "Plan đã được chỉnh sửa thủ công. Bấm Reconfirm để mình xác nhận lại theo cách mình hiểu trước khi approve."
    db.replace_plan(job_id, clean_plan, note=note, request=job.get("request"))
    if payload.comment.strip():
        db.add_message(job_id, "user", f"[Plan edit note] {payload.comment.strip()}")
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}


@app.post("/api/jobs/{job_id}/reconfirm-plan")
def reconfirm_plan(job_id: str, payload: MessageRequest):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in {"draft", "planned", "approved", "needs_input"}:
        raise HTTPException(status_code=409, detail=f"cannot reconfirm plan from {job['status']}")

    comment = (payload.content or "").strip()
    current_plan = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(job.get("plan", [])))
    discussion = discussion_with_focus(job)
    reconfirm_brief = (
        "Reconfirm this plan after user edits.\n\n"
        f"Current plan:\n{current_plan or '(empty)'}\n\n"
        f"User note/change request:\n{comment or '(none)'}\n\n"
        "Return an executable updated plan for end-to-end implementation."
    )
    decision = planner_decision(f"{discussion}\n\n{reconfirm_brief}", comment)
    if decision["mode"] == "questions":
        db.replace_plan(job_id, job.get("plan", []), render_questions(decision["questions"]), request=job.get("request"))
        db.update_job_status(job_id, "needs_input")
        refresh_session_memory(job_id)
        return {"job": db.get_job(job_id)}

    db.replace_plan(
        job_id,
        decision["plan"],
        "Mình đã xác nhận lại plan theo bản bạn sửa. Bạn review, nếu ổn thì approve và chạy end-to-end.",
        request=job.get("request"),
    )
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}


@app.post("/api/jobs/{job_id}/code")
def code_from_chat(job_id: str, payload: MessageRequest):
    return {"job": handle_code_message(job_id, payload.content, payload.executor)}


@app.post("/api/jobs")
def create_job(payload: CreateJobRequest):
    job_id = "lg_" + secrets.token_hex(5)
    permission_mode = validate_permission_mode(payload.permission_mode)
    title = payload.title or payload.request.strip().splitlines()[0][:80]
    if is_casual_chat(payload.request):
        initial_reply = model_chat_reply([{"role": "user", "content": payload.request}], payload.request)
        job = db.create_job(job_id, title, payload.request, [], status="chat", permission_mode=permission_mode, manager_message=initial_reply)
        refresh_session_memory(job_id)
        return {"job": db.get_job(job_id) or job}
    graph = build_graph()
    state = graph.invoke({"request": payload.request})
    job = db.create_job(job_id, title, payload.request, state.get("plan", []), permission_mode=permission_mode)
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id) or job}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": job}


@app.get("/api/jobs/{job_id}/session-memory")
def get_session_memory(job_id: str):
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return {"session_memory": db.get_session_memory(job_id)}


@app.put("/api/jobs/{job_id}/session-memory")
def update_session_memory(job_id: str, payload: SessionMemoryRequest):
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    memory = db.upsert_session_memory(
        job_id,
        payload.summary,
        payload.current_goal,
        payload.open_tasks,
        payload.important_files,
        payload.decisions,
        payload.last_result,
    )
    return {"session_memory": memory, "job": db.get_job(job_id)}


@app.post("/api/jobs/{job_id}/focus-file")
def add_focus_file(job_id: str, payload: FocusFileRequest):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    content = payload.content[:MAX_FOCUS_FILE_CHARS]
    db.add_focus_file(job_id, payload.name[:240], content)
    db.add_message(job_id, "langgraph", f"Đã thêm file focus: {payload.name}. Runtime sẽ ưu tiên nội dung file này khi lập plan/chạy.")
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    if not db.delete_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    delete_job_attachments(job_id)
    return {"ok": True}


@app.patch("/api/jobs/{job_id}/title")
def rename_job(job_id: str, payload: RenameJobRequest):
    title = re.sub(r"\s+", " ", payload.title.strip())[:160]
    if not title:
        raise HTTPException(status_code=422, detail="title required")
    if not db.update_job_title(job_id, title):
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": db.get_job(job_id)}


@app.patch("/api/jobs/{job_id}/permission-mode")
def update_permission_mode(job_id: str, payload: PermissionModeRequest):
    mode = validate_permission_mode(payload.permission_mode)
    if not db.update_permission_mode(job_id, mode):
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": db.get_job(job_id)}


@app.post("/api/jobs/{job_id}/messages")
def add_message(job_id: str, payload: MessageRequest):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in {"done", "failed", "approved", "rejected"}:
        db.clear_job_run_state(job_id)
        db.update_job_status(job_id, "chat")
        job = db.get_job(job_id) or job
    if is_duplicate_user_message(job, payload.content):
        return {"job": job}
    if job["status"] == "chat":
        return {"job": handle_agent_message(job_id, payload.content)}
    if job["status"] == "needs_input":
        return plan_from_chat(job_id, payload)
    if job["status"] not in {"draft", "planned"}:
        raise HTTPException(status_code=409, detail=f"cannot discuss from {job['status']}")
    db.add_message(job_id, "user", payload.content)
    db.add_message(job_id, "langgraph", "Đã ghi nhận. Bạn có thể regenerate plan hoặc approve plan hiện tại.")
    return {"job": db.get_job(job_id)}


@app.post("/api/actions/{action_id}/approve")
def approve_action(action_id: int):
    action = db.get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="action not found")
    job = db.get_job(action["job_id"])
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not db.claim_pending_action(action_id, "running"):
        latest = db.get_pending_action(action_id) or action
        raise HTTPException(status_code=409, detail=f"action already {latest.get('status', 'updated')}")
    start_action_background(action_id)
    trace_event("action_approved", action_id=action_id, job_id=action.get("job_id"), kind=action.get("kind"))
    return {"job": db.get_job(action["job_id"])}


@app.post("/api/actions/{action_id}/reject")
def reject_action(action_id: int):
    action = db.get_pending_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"action already {action['status']}")
    db.update_pending_action_status(action_id, "rejected", "", "Rejected by user")
    db.add_message(action["job_id"], "langgraph", f"Đã reject action #{action_id}: {action['title']}")
    trace_event("action_rejected", action_id=action_id, job_id=action.get("job_id"), kind=action.get("kind"))
    refresh_session_memory(action["job_id"])
    return {"job": db.get_job(action["job_id"])}


@app.post("/api/jobs/{job_id}/messages/stream")
def add_message_stream(job_id: str, payload: MessageRequest):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in {"done", "failed", "approved", "rejected"}:
        db.clear_job_run_state(job_id)
        db.update_job_status(job_id, "chat")
        job = db.get_job(job_id) or job
    if job["status"] != "chat":
        raise HTTPException(status_code=409, detail=f"cannot stream chat from {job['status']}")
    if is_duplicate_user_message(job, payload.content):
        return StreamingResponse(
            iter(["event: done\ndata: {}\n\n"]),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    db.add_message(job_id, "user", payload.content)
    return StreamingResponse(
        model_chat_stream_events(job_id, payload.content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/jobs/{job_id}/regenerate-plan")
def regenerate_plan(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in {"draft", "planned", "needs_input"}:
        raise HTTPException(status_code=409, detail=f"cannot regenerate plan from {job['status']}")
    discussion = discussion_with_focus(job)
    planning_request = f"{job['request']}\n\nDiscussion:\n{discussion}".strip()
    recent_user = ""
    for msg in reversed(list(job.get("messages") or [])):
        if str(msg.get("role") or "") == "user":
            recent_user = str(msg.get("content") or "")
            break
    decision = planner_decision(discussion, recent_user)
    if decision["mode"] == "questions":
        db.replace_plan(job_id, [], render_questions(decision["questions"]), request=planning_request)
        db.update_job_status(job_id, "needs_input")
        refresh_session_memory(job_id)
        return {"job": db.get_job(job_id)}
    db.replace_plan(job_id, decision["plan"], "Đã regenerate plan cụ thể từ discussion mới.", request=planning_request)
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in {"planned", "draft"}:
        raise HTTPException(status_code=409, detail=f"cannot approve from {job['status']}")
    db.approve_job(job_id)
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}


@app.post("/api/jobs/{job_id}/reopen-plan")
def reopen_plan(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in {"approved", "planned", "draft", "needs_input", "chat"}:
        raise HTTPException(status_code=409, detail=f"cannot reopen from {job['status']}")
    db.close_open_pending_actions(job_id, status="rejected", note="Plan rejected by user")
    db.update_job_status(job_id, "chat", result="", error="")
    db.add_message(job_id, "langgraph", "Đã hủy plan hiện tại và quay về chat bình thường.")
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}


@app.post("/api/jobs/{job_id}/run")
def run_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "approved":
        raise HTTPException(status_code=409, detail="job must be approved before run")
    db.clear_job_run_state(job_id)
    db.update_job_status(job_id, "running")
    started = time.perf_counter()
    trace_event("job_run_start", job_id=job_id, permission_mode=job.get("permission_mode"))
    try:
        def step_callback(step: str, status: str, detail: str) -> None:
            db.update_step(job_id, step, status, detail)
            trace_event("job_step", job_id=job_id, step=step, status=status, detail=compact_text(detail, 220))

        job = db.get_job(job_id) or job
        discussion = discussion_with_focus(job)
        execution_request = (job.get("request") or "").strip() or f"Execute from this discussion:\n{discussion}"
        state = run_native_job_state(
            job_id,
            execution_request,
            discussion,
            step_callback,
            job.get("permission_mode", "auto_review"),
            [
                {"name": str(item.get("name", "")), "content": str(item.get("content", ""))}
                for item in job.get("focus_files", [])
            ],
        )
        result = state.get("result", "LangGraph native backend finished without a report.")
        chat_result = sanitize_manager_content(str(result), "Job đã chạy xong nhưng report trả về dữ liệu kỹ thuật. Chi tiết nằm trong Result/Logs.")
        verification = state.get("verification", {})
        if verification.get("ok"):
            db.update_step(job_id, "report", "done", "Result saved; verify OK")
            db.update_job_status(job_id, "done", result=result)
            db.add_message(job_id, "langgraph", chat_result)
            trace_event("job_run_finish", job_id=job_id, ok=True, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        else:
            error = "Native runtime finished but verification did not pass"
            db.update_step(job_id, "report", "failed", error)
            db.update_job_status(job_id, "failed", result=result, error=error)
            db.add_message(job_id, "langgraph", chat_result)
            trace_event(
                "job_run_finish",
                job_id=job_id,
                ok=False,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                error=error,
            )
    except Exception as exc:
        db.update_step(job_id, "run_error", "failed", str(exc))
        db.update_job_status(job_id, "failed", error=str(exc))
        db.add_message(job_id, "langgraph", sanitize_manager_content(f"Job lỗi: {exc}", "Job lỗi khi chạy. Chi tiết kỹ thuật nằm trong Logs."))
        trace_event(
            "job_run_finish",
            job_id=job_id,
            ok=False,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            error=compact_text(str(exc), 600),
        )
    refresh_session_memory(job_id)
    return {"job": db.get_job(job_id)}
