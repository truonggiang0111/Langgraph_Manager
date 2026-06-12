from __future__ import annotations

import secrets
import signal
import base64
import binascii
import io
import re
import json
import os
import shlex
import shutil
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
from urllib.parse import quote, urlparse, urlunparse, parse_qsl, urlencode
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
from .tool_registry import (
    cliproxy_chat,
    cliproxy_chat_stream,
    cliproxy_vision,
    describe_tools,
    detect_tool_error_class,
    run_tool,
)


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
    permission_mode: str = "full_access"


class MessageRequest(BaseModel):
    content: str = Field(min_length=1)
    executor: str = "claude"


class ChatSessionRequest(BaseModel):
    permission_mode: str = "full_access"


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


class HostBrowserOpenRequest(BaseModel):
    url: str = Field(min_length=1)
    browser: str = "brave"


class HostBrowserOpenCurrentRequest(BaseModel):
    url: str = Field(min_length=1)
    browser: str = "brave"


class HostBrowserLaunchRequest(BaseModel):
    url: str = "about:blank"
    browser: str = "brave"
    port: int = 9222
    profile_dir: str = ""


class HostBrowserFacebookResearchRequest(BaseModel):
    query: str = Field(min_length=1)
    browser: str = "brave"
    port: int = 9222
    limit: int = 8
    scroll_rounds: int = 4
    profile_dir: str = ""


class HostBrowserRerankRequest(BaseModel):
    query: str = Field(min_length=1)
    items: list[dict[str, Any]] = Field(default_factory=list)
    top_k: int = 5


class HostBrowserSearchPlanRequest(BaseModel):
    query: str = Field(min_length=1)
    max_queries: int = 6


class HostBrowserScreenshotRequest(BaseModel):
    pid: int = 0
    full_screen: bool = False
    name: str = ""
    artifact_dir: str = ""
    port: int = 9222


class HostBrowserMouseRequest(BaseModel):
    x: int
    y: int


class HostBrowserOcrExtractRequest(BaseModel):
    image_base64: str = Field(min_length=1)
    mime_type: str = "image/png"
    query: str = ""
    url: str = ""
    title: str = ""
    excerpt: str = ""


class HostShellRunRequest(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = ""
    timeout: int = 60


class HostFileReadRequest(BaseModel):
    path: str = Field(min_length=1)
    max_chars: int = 40000


class HostFileListRequest(BaseModel):
    path: str = Field(min_length=1)
    limit: int = 200


class HostFileWriteRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str = ""
    overwrite: bool = True
    create_parents: bool = False


class HostFileMoveRequest(BaseModel):
    src_path: str = Field(min_length=1)
    dest_path: str = Field(min_length=1)
    overwrite: bool = False
    create_parents: bool = False


class HostFileDeleteRequest(BaseModel):
    path: str = Field(min_length=1)
    recursive: bool = False


class HostDockerPsRequest(BaseModel):
    all_containers: bool = False
    limit: int = 50
    timeout: int = 20


class HostServiceStatusRequest(BaseModel):
    service: str = Field(min_length=1)
    lines: int = 40
    timeout: int = 20


class HostServiceLogsRequest(BaseModel):
    service: str = Field(min_length=1)
    lines: int = 80
    since: str = ""
    timeout: int = 20


class HostDockerRestartRequest(BaseModel):
    container: str = Field(min_length=1)
    timeout: int = 30


class HostServiceRestartRequest(BaseModel):
    service: str = Field(min_length=1)
    timeout: int = 30


class HostContainerRecoveryRequest(BaseModel):
    container: str = Field(min_length=1)
    logs_lines: int = 80
    timeout: int = 45


class HostServiceRecoveryRequest(BaseModel):
    service: str = Field(min_length=1)
    status_lines: int = 30
    logs_lines: int = 80
    timeout: int = 45


class HostProcessListRequest(BaseModel):
    limit: int = 200
    query: str = ""


class HostProcessSignalRequest(BaseModel):
    pid: int
    signal_name: str = "TERM"


class HostProcessRecoveryRequest(BaseModel):
    pid: int
    signal_name: str = "TERM"
    query: str = ""


PERMISSION_MODES = {"full_access"}
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
MAX_AUTO_CONTINUATIONS = int(os.getenv("LANGGRAPH_MAX_AUTO_CONTINUATIONS", "4"))
HOST_ALLOWED_ROOTS = [
    Path("/home/giang").resolve(),
    Path(os.getenv("LANGGRAPH_STATE_DIR", str(STATE_DIR))).resolve(),
    Path("/tmp").resolve(),
]
SUPPORTED_ACTION_KINDS = {
    "workspace_inspect",
    "workspace_read_file",
    "workspace_diff",
    "workspace_command",
    "coding_agent_executor",
    "create_memory",
    "create_skill",
    "note",
    "git_checkpoint",
    "git_restore_checkpoint",
    "host_browser_list",
    "host_browser_open",
    "host_browser_open_current",
    "host_browser_launch",
    "host_browser_facebook_research",
    "host_browser_screenshot",
    "host_browser_mouse_click",
    "host_browser_mouse_move",
    "host_shell_command",
    "host_process_list",
    "host_file_read",
    "host_file_list",
    "host_file_write",
    "host_file_move",
    "host_file_delete",
    "host_docker_ps",
    "host_service_status",
    "host_service_logs",
    "host_docker_restart",
    "host_service_restart",
    "host_container_recovery",
    "host_service_recovery",
    "host_process_signal",
    "host_process_recovery",
}
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
    _ = value
    return "full_access"


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


def sanitize_facebook_search_query(content: str) -> str:
    request = str(content or "").strip()
    if not request:
        return ""
    query = request
    cleanup_patterns = [
        r"^\s*(giúp\s+mình|giup minh|giúp tôi|giup toi|cho mình|cho tôi|tim giup toi|tìm giúp tôi|tim giup minh|tìm giúp mình)\s+",
        r"^\s*(tìm\s+cho\s+tôi|tim cho toi|tìm\s+cho\s+mình|tim cho minh)\s+",
        r"^\s*(lên|len|vào|vao|mở|mo)\s+(facebook|face)\s+",
        r"^\s*(tìm|tim|search|research|kiếm|kiem)\s+",
        r"^\s*\d+\s*(bài viết|bai viet|bài|post|posts)\s+",
        r"\s*(trên|tren|ở|o)\s+(facebook|face)\s*",
        r"\s*(bài viết|bai viet|bài|post|posts)\s*",
        r"\s*\b(cho tôi|cho toi|cho mình|cho minh)\b\s*",
    ]
    for pattern in cleanup_patterns:
        query = re.sub(pattern, " ", query, flags=re.I)
    query = re.sub(r"^\s*\d+\s+", " ", query, flags=re.I)
    query = re.sub(r"\s+", " ", query).strip(" .,:;-")
    return query or request


def direct_host_browser_decision(content: str) -> dict | None:
    request = str(content or "").strip()
    normalized = re.sub(r"\s+", " ", request.lower()).strip()
    if not normalized:
        return None

    mentions_facebook = any(
        re.search(pattern, normalized)
        for pattern in [r"\bfacebook\b", r"\bfb\b", r"\bface\b", r"\bfacbook\b"]
    )
    research_markers = [
        "tìm", "tim", "search", "research", "kiếm", "kiem",
        "bài viết", "bai viet", "bài", "post", "posts",
        "tuyển", "tuyen", "hiring", "viec lam", "việc làm",
    ]
    debug_markers = [
        "sửa", "sua", "fix", "lỗi", "loi", "bug", "debug", "kiểm tra", "kiem tra",
        "không research được", "khong research duoc", "http 500", "fetch failed",
        "route", "routing", "trigger", "từ khóa", "tu khoa", "ngữ cảnh", "ngu canh",
    ]
    open_markers = [
        "mở facebook", "mo facebook", "vào facebook", "vao facebook", "lên facebook", "len facebook",
        "mở face", "mo face", "vào face", "vao face", "lên face", "len face", "trên face", "tren face",
    ]

    if mentions_facebook and any(marker in normalized for marker in debug_markers):
        return None

    if mentions_facebook and any(marker in normalized for marker in open_markers) and not any(marker in normalized for marker in research_markers):
        return {
            "mode": "pending_action",
            "reply": "Em mở Facebook bằng browser host hiện tại để dùng session local của mình.",
            "action": {
                "kind": "host_browser_open_current",
                "title": "Mở Facebook trên browser host",
                "preview": request[:4000] or "Mở Facebook bằng browser host.",
                "payload": {
                    "url": "https://www.facebook.com/",
                    "browser": "brave",
                },
            },
        }

    if not mentions_facebook:
        return None
    if not any(marker in normalized for marker in research_markers):
        return None

    query = sanitize_facebook_search_query(request)

    return {
        "mode": "pending_action",
        "reply": "Em dùng browser host với session Facebook local để tìm các bài liên quan, rồi trả nội dung chính và link.",
        "action": {
            "kind": "host_browser_facebook_research",
            "title": "Research bài Facebook bằng browser host",
            "preview": request[:4000] or "Research Facebook bằng browser host.",
            "payload": {
                "query": query,
                "browser": "brave",
                "port": 9222,
                "limit": 8,
                "scroll_rounds": 4,
            },
        },
    }


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
    "Nếu người dùng muốn mở browser thật trên máy, research web/Facebook bằng session local, chụp màn hình host, hoặc điều khiển chuột host, ưu tiên action host_browser_open_current|host_browser_open|host_browser_launch|host_browser_list|host_browser_facebook_research|host_browser_screenshot|host_browser_mouse_click|host_browser_mouse_move thay vì giả lập bằng text. "
    "Nếu câu có Facebook/face nhưng đang báo lỗi, debug, sửa route/trigger/từ khóa/ngữ cảnh, hoặc nhắc lỗi host_browser_facebook_research, không tạo host_browser_facebook_research; phải giao coding_agent_executor. "
    "Nếu yêu cầu có dấu hiệu cần sửa/debug/review/refactor code, sửa UI/frontend, đọc/phân tích repo/source/file/PDF, tìm hàm/class/route/component, tra docs/version thư viện, hoặc cần plugin/MCP như Serena/Context7, luôn trả về pending_action kind coding_agent_executor. "
    "Không dùng answer cho các việc workspace/code nặng; chỉ dùng answer cho câu hỏi khái niệm hoặc trao đổi không cần đọc/sửa file. "
    "Nếu chỉ cần trả lời/thảo luận, dùng "
    '{"mode":"answer","content":"..."}. '
    "Nếu thiếu thông tin quan trọng trước khi làm, dùng "
    '{"mode":"questions","content":"..."} với 1-4 câu hỏi cụ thể. '
    "Nếu cần đọc workspace/repo, dùng workspace_inspect trước, workspace_read_file để đọc file cụ thể, workspace_diff để xem thay đổi; không tự sửa file bằng patch. "
    "Nếu cần code/sửa repo/UI/debug/review/refactor/docs/version/file analysis, tạo action coding_agent_executor để giao việc cho Claude executor; chỉ dùng workspace_command cho lệnh đọc/kiểm tra nhẹ không cần suy luận. "
    "Nếu cần chạy lệnh trên workspace hoặc lưu memory/skill, dùng "
    '{"mode":"pending_action","reply":"...","action":{"kind":"workspace_inspect|workspace_read_file|workspace_diff|workspace_command|coding_agent_executor|create_memory|create_skill|host_browser_open_current|host_browser_open|host_browser_launch|host_browser_list|host_browser_facebook_research|host_browser_screenshot|host_browser_mouse_click|host_browser_mouse_move|note","title":"...","preview":"...","payload":{...}}}. '
    "workspace_inspect payload có thể có max_files. workspace_read_file payload cần path và có thể có max_chars. workspace_diff payload có thể có path. "
    "workspace_command payload nên có command, cwd, risk low|medium|high, timeout. "
    "coding_agent_executor payload có thể có cwd, timeout, request override; Claude executor sẽ nhận prompt qua stdin. "
    "host_browser_open_current payload nên có url và có thể có browser; nếu browser đang mở thì focus cửa sổ hiện có và mở tab mới, nếu chưa có thì tự mở browser. host_browser_open payload nên có url và có thể có browser. host_browser_launch payload có thể có url, browser, port, profile_dir để mở browser visible với remote debugging riêng. host_browser_list không cần payload. host_browser_facebook_research payload nên có query và có thể có browser, port, limit, scroll_rounds, profile_dir; nó dùng browser debug visible với profile persistent để đọc search results/bài viết. host_browser_screenshot payload có thể có pid, full_screen, name. host_browser_mouse_click|host_browser_mouse_move payload nên có x,y theo toạ độ màn hình host. "
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
    lines = [
        "Host commands có sẵn:",
        "/host help - xem các host subcommand",
        "/host ps [query] - liệt kê process trên máy",
        "/host docker [all] - liệt kê container Docker",
        "/host status <service> - xem service status",
        "/host logs <service> - xem service logs",
        "/host open <url> - mở URL bằng browser host",
        "/host facebook-search <query> - research Facebook bằng session local",
        "/host shot - chụp màn hình host",
        "/host shell <command> - chạy shell trên máy host",
        "/host ls <path> - liệt kê thư mục host",
        "/host read <path> - đọc file host",
    ]
    if not commands:
        lines.append("Chưa có command tùy biến nào. Vào Settings > Commands để tạo thêm /command dùng mãi.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Commands tùy biến đang có:")
    for item in commands:
        lines.append(f"/{item['name']} - {item.get('description') or item.get('action_kind')}")
    return "\n".join(lines)


def _host_slash_decision(command_input: str) -> dict[str, Any] | None:
    raw = str(command_input or "").strip()
    if not raw:
        return {
            "reply": slash_command_help(),
            "action": {
                "kind": "note",
                "title": "/host help",
                "preview": "Hiển thị host subcommands",
                "payload": {"kind": "note", "title": "Host command help", "content": slash_command_help()},
            },
        }
    parts = raw.split(None, 1)
    subcommand = parts[0].strip().lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if subcommand in {"help", "commands"}:
        return {
            "reply": slash_command_help(),
            "action": {
                "kind": "note",
                "title": "/host help",
                "preview": "Hiển thị host subcommands",
                "payload": {"kind": "note", "title": "Host command help", "content": slash_command_help()},
            },
        }
    if subcommand == "ps":
        return {
            "reply": "Mình đã mở action đọc process trên máy host.",
            "action": {
                "kind": "host_process_list",
                "title": "/host ps",
                "preview": rest or "Liệt kê process trên máy host",
                "payload": {"limit": 50, "query": rest},
            },
        }
    if subcommand == "docker":
        return {
            "reply": "Mình đã mở action đọc Docker trên host.",
            "action": {
                "kind": "host_docker_ps",
                "title": "/host docker",
                "preview": rest or "Liệt kê Docker containers",
                "payload": {"all_containers": "all" in rest.lower().split(), "limit": 50, "timeout": 20},
            },
        }
    if subcommand == "status":
        if not rest:
            return {
                "reply": "Thiếu tên service. Dùng `/host status nginx`.",
                "action": {"kind": "note", "title": "/host status", "preview": "Thiếu service", "payload": {"kind": "note", "title": "Thiếu service", "content": "Ví dụ: /host status nginx"}},
            }
        return {
            "reply": "Mình đã mở action xem service status trên host.",
            "action": {
                "kind": "host_service_status",
                "title": "/host status",
                "preview": rest,
                "payload": {"service": rest, "lines": 40, "timeout": 20},
            },
        }
    if subcommand == "logs":
        if not rest:
            return {
                "reply": "Thiếu tên service. Dùng `/host logs nginx`.",
                "action": {"kind": "note", "title": "/host logs", "preview": "Thiếu service", "payload": {"kind": "note", "title": "Thiếu service", "content": "Ví dụ: /host logs nginx"}},
            }
        return {
            "reply": "Mình đã mở action xem service logs trên host.",
            "action": {
                "kind": "host_service_logs",
                "title": "/host logs",
                "preview": rest,
                "payload": {"service": rest, "lines": 80, "since": "", "timeout": 20},
            },
        }
    if subcommand == "open":
        if not rest:
            return {
                "reply": "Thiếu URL. Dùng `/host open https://example.com`.",
                "action": {"kind": "note", "title": "/host open", "preview": "Thiếu URL", "payload": {"kind": "note", "title": "Thiếu URL", "content": "Ví dụ: /host open https://example.com"}},
            }
        return {
            "reply": "Mình đã mở action browser host.",
            "action": {
                "kind": "host_browser_open_current",
                "title": "/host open",
                "preview": rest,
                "payload": {"url": rest, "browser": "brave"},
            },
        }
    if subcommand in {"facebook-search", "facebook", "fb"}:
        if not rest:
            return {
                "reply": "Thiếu query. Dùng `/host facebook-search devops intern hcm`.",
                "action": {"kind": "note", "title": "/host facebook-search", "preview": "Thiếu query", "payload": {"kind": "note", "title": "Thiếu query", "content": "Ví dụ: /host facebook-search devops intern hcm"}},
            }
        return {
            "reply": "Mình đã mở action Facebook research trên host.",
            "action": {
                "kind": "host_browser_facebook_research",
                "title": "/host facebook-search",
                "preview": rest,
                "payload": {"query": rest, "browser": "brave", "port": 9222, "limit": 8, "scroll_rounds": 4},
            },
        }
    if subcommand in {"shot", "screenshot"}:
        return {
            "reply": "Mình đã mở action chụp màn hình host.",
            "action": {
                "kind": "host_browser_screenshot",
                "title": "/host shot",
                "preview": "Chụp màn hình host",
                "payload": {"name": "host_capture_{{job_id}}.png", "full_screen": False},
            },
        }
    if subcommand == "move":
        parts = rest.split()
        if len(parts) < 2:
            return {
                "reply": "Thiếu toạ độ. Dùng `/host move 800 450`.",
                "action": {"kind": "note", "title": "/host move", "preview": "Thiếu toạ độ", "payload": {"kind": "note", "title": "Thiếu toạ độ", "content": "Ví dụ: /host move 800 450"}},
            }
        return {
            "reply": "Mình đã mở action di chuyển chuột host.",
            "action": {
                "kind": "host_browser_mouse_move",
                "title": "/host move",
                "preview": rest,
                "payload": {"x": int(parts[0]), "y": int(parts[1])},
            },
        }
    if subcommand == "click":
        parts = rest.split()
        if len(parts) < 2:
            return {
                "reply": "Thiếu toạ độ. Dùng `/host click 800 450`.",
                "action": {"kind": "note", "title": "/host click", "preview": "Thiếu toạ độ", "payload": {"kind": "note", "title": "Thiếu toạ độ", "content": "Ví dụ: /host click 800 450"}},
            }
        return {
            "reply": "Mình đã mở action click chuột host.",
            "action": {
                "kind": "host_browser_mouse_click",
                "title": "/host click",
                "preview": rest,
                "payload": {"x": int(parts[0]), "y": int(parts[1])},
            },
        }
    if subcommand == "shell":
        if not rest:
            return {
                "reply": "Thiếu command. Dùng `/host shell pwd`.",
                "action": {"kind": "note", "title": "/host shell", "preview": "Thiếu command", "payload": {"kind": "note", "title": "Thiếu command", "content": "Ví dụ: /host shell pwd"}},
            }
        return {
            "reply": "Mình đã mở action shell trên host.",
            "action": {
                "kind": "host_shell_command",
                "title": "/host shell",
                "preview": rest,
                "payload": {"command": rest, "cwd": "", "timeout": 60},
            },
        }
    if subcommand == "ls":
        target = rest or "/home/giang"
        return {
            "reply": "Mình đã mở action liệt kê file trên host.",
            "action": {
                "kind": "host_file_list",
                "title": "/host ls",
                "preview": target,
                "payload": {"path": target, "limit": 200},
            },
        }
    if subcommand == "read":
        if not rest:
            return {
                "reply": "Thiếu path. Dùng `/host read /home/giang/file.txt`.",
                "action": {"kind": "note", "title": "/host read", "preview": "Thiếu path", "payload": {"kind": "note", "title": "Thiếu path", "content": "Ví dụ: /host read /home/giang/file.txt"}},
            }
        return {
            "reply": "Mình đã mở action đọc file trên host.",
            "action": {
                "kind": "host_file_read",
                "title": "/host read",
                "preview": rest,
                "payload": {"path": rest, "max_chars": 40000},
            },
        }
    return {
        "reply": f"Không hỗ trợ `/host {subcommand}`. Dùng `/host help` để xem danh sách.",
        "action": {
            "kind": "note",
            "title": "/host help",
            "preview": f"Unknown subcommand: {subcommand}",
            "payload": {"kind": "note", "title": "Unknown host subcommand", "content": slash_command_help()},
        },
    }


def handle_slash_command(job_id: str, content: str, job: dict) -> bool:
    match = re.match(r"^/([a-zA-Z0-9_-]+)(?:\s+(.*))?$", content.strip(), re.S)
    if not match:
        return False
    name = match.group(1).lower()
    command_input = (match.group(2) or "").strip()
    if name == "host":
        decision = _host_slash_decision(command_input)
        if decision:
            action = decision.get("action") if isinstance(decision.get("action"), dict) else {}
            if str(action.get("kind") or "") == "note":
                reply = str(decision.get("reply") or action.get("payload", {}).get("content") or "").strip()
                if reply:
                    db.add_message(job_id, "langgraph", reply)
                    refresh_session_memory(job_id)
                    return True
            save_pending_action_from_decision(job_id, {"mode": "pending_action", **decision}, auto_execute_safe=True)
            refresh_session_memory(job_id)
            return True
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
    browser_decision = direct_host_browser_decision(content)
    if browser_decision:
        db.update_step(job_id, "analyze", "running", "Detected host browser research intent")
        save_pending_action_from_decision(job_id, browser_decision, auto_execute_safe=True)
        db.update_step(job_id, "analyze", "done", "Routed to host browser action")
        trace_event(
            "route_decision",
            job_id=job_id,
            route="host_browser_direct",
            one_hop_executor=False,
            action_kind=((browser_decision.get("action") or {}).get("kind") if isinstance(browser_decision.get("action"), dict) else ""),
        )
        refresh_session_memory(job_id)
        return db.get_job(job_id) or job
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


def is_safe_auto_action(action: dict, permission_mode: str = "full_access") -> bool:
    kind = str(action.get("kind") or "")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    if kind != "workspace_inspect":
        return False
    blob = json.dumps(payload, ensure_ascii=False).lower()
    return not any(marker in blob for marker in SENSITIVE_BROWSER_MARKERS)


def should_auto_execute_action(action: dict, permission_mode: str = "full_access") -> bool:
    # Even in full_access, public/social publish/send style actions must be
    # explicitly approved by user to avoid accidental external side effects.
    if action_requires_publish_approval(action):
        return False
    policy = classify_action_policy(action)
    if bool(policy.get("requires_approval")):
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
        stored_output = maybe_store_action_artifact(action_id, output, is_error=not ok, kind=str(action.get("kind") or ""))
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
            error_class = detect_error_class(str(output or ""))
            agent_follow_up_after_action(action["job_id"], action, ok, output)
            db.update_pending_action_status(
                action_id,
                status,
                "",
                build_action_failure_payload(str(output or ""), error_class, action_id, kind=str(action.get("kind") or "")),
            )
            db.update_step(action["job_id"], "act", "failed", f"Action #{action_id} failed")
            trace_event(
                "action_finish",
                action_id=action_id,
                job_id=action.get("job_id"),
                ok=False,
                status=status,
                error=compact_text(str(output), 600),
                error_class=error_class,
            )
    except Exception as exc:
        error_text = compact_text(f"{type(exc).__name__}: {exc}", 1000)
        error_class = detect_error_class(error_text)
        db.update_pending_action_status(
            action_id,
            "failed",
            "",
            build_action_failure_payload(error_text, error_class, action_id, kind=str(action.get("kind") or "")),
        )
        db.update_step(action["job_id"], "verify", "failed", "Action pipeline crashed before verification completed")
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
            error_class=error_class,
        )
    finally:
        try:
            refresh_session_memory(action["job_id"])
        except Exception as exc:
            trace_event(
                "session_memory_refresh_failed",
                action_id=action_id,
                job_id=action.get("job_id"),
                error=compact_text(f"{type(exc).__name__}: {exc}", 400),
            )


def start_action_background(action_id: int) -> None:
    inline_actions = os.getenv("LANGGRAPH_INLINE_ACTIONS", "").lower() in {"1", "true", "yes"}
    disable_scheduler = os.getenv("LANGGRAPH_DISABLE_SCHEDULER", "").lower() in {"1", "true", "yes"}
    if inline_actions or disable_scheduler:
        execute_action_and_follow_up(action_id)
        return
    thread = threading.Thread(target=execute_action_and_follow_up, args=(action_id,), daemon=True)
    thread.start()


def save_pending_action_from_decision(job_id: str, decision: dict, auto_execute_safe: bool = False) -> dict:
    action = dict(decision["action"])
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    payload = dict(payload)
    if action_recovery_depth({"payload": payload}) > 0:
        payload["continuation_mode"] = "recovery"
    elif auto_execute_safe:
        payload.setdefault("continuation_mode", "auto")
    action["payload"] = payload
    saved = db.add_pending_action(job_id, action["kind"], action["title"], action["preview"], action["payload"])
    db.update_step(job_id, "act", "pending", f"Queued action #{saved['id']} ({action['kind']})")
    job = db.get_job(job_id)
    permission_mode = str((job or {}).get("permission_mode") or "full_access")
    loop_metrics = build_loop_metrics(job or {}, list((job or {}).get("pending_actions", [])))
    auto_budget_available = int(loop_metrics.get("remaining_auto_continuations") or 0) > 0
    if auto_execute_safe and should_auto_execute_action(action, permission_mode) and auto_budget_available:
        if job:
            db.update_pending_action_status(saved["id"], "running")
            start_action_background(saved["id"])
            return db.get_pending_action(saved["id"]) or saved
    approval_note = ""
    if auto_execute_safe and should_auto_execute_action(action, permission_mode) and not auto_budget_available:
        approval_note += "\n\nAuto-continuation budget đã chạm ngưỡng nên mình giữ action này ở trạng thái chờ thay vì tự chạy tiếp."
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
        for key in ("title", "url", "policy", "remote_debugging_url", "path", "profile_dir"):
            if data.get(key):
                parts.append(f"{key}: {data.get(key)}")
        if data.get("queries_used"):
            parts.append("queries_used: " + ", ".join(str(item) for item in (data.get("queries_used") or [])[:6]))
        if data.get("summary"):
            try:
                parts.append("summary:\n" + compact_text(json.dumps(data.get("summary"), ensure_ascii=False), 900))
            except Exception:
                parts.append("summary:\n" + compact_text(str(data.get("summary")), 900))
        if data.get("items"):
            try:
                parts.append("items:\n" + compact_text(json.dumps(data.get("items"), ensure_ascii=False), 1400))
            except Exception:
                parts.append("items:\n" + compact_text(str(data.get("items")), 1400))
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


def format_facebook_research_message(output: str) -> str:
    try:
        parsed = json.loads(str(output or ""))
    except Exception:
        return compact_text(str(output or ""), 1600)
    data: dict[str, Any] = {}
    if isinstance(parsed, dict):
        if isinstance(parsed.get("result"), dict):
            data = parsed.get("result") or {}
        elif isinstance(parsed.get("data"), dict):
            data = parsed.get("data") or {}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    results = data.get("results") if isinstance(data.get("results"), list) else []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    if not items and not results:
        time_label = str(summary.get("time_window_label") or "").strip()
        total_found = int(data.get("total_found") or 0)
        filtered_count = int(data.get("filtered_count") or 0)
        recent_confirmed = int(summary.get("recent_confirmed_count") or 0)
        time_unknown = int(summary.get("time_unknown_count") or 0)
        stale_confirmed = int(summary.get("stale_confirmed_count") or 0)
        detail = "Không tìm thấy bài nào phù hợp."
        if total_found or filtered_count:
            detail += f" Đã quét {total_found} candidate, còn {filtered_count} candidate sau lọc."
        if recent_confirmed or time_unknown or stale_confirmed:
            bucket_parts: list[str] = []
            if recent_confirmed:
                bucket_parts.append(f"{recent_confirmed} bài xác nhận còn mới")
            if stale_confirmed:
                bucket_parts.append(f"{stale_confirmed} bài xác nhận quá cũ")
            if bucket_parts:
                detail += " Trạng thái thời gian: " + ", ".join(bucket_parts) + "."
        if time_label:
            detail += f" Điều kiện thời gian đang bật: `{time_label}`."
        planner = str(summary.get("planner_summary") or "").strip()
        if planner:
            detail += f" {compact_text(planner, 220)}"
        return detail

    lines: list[str] = []
    recent_confirmed = int(summary.get("recent_confirmed_count") or 0)
    time_unknown = int(summary.get("time_unknown_count") or 0)
    if recent_confirmed:
        parts: list[str] = []
        if recent_confirmed:
            parts.append(f"{recent_confirmed} bài xác nhận còn mới")
        lines.append("Đã lọc xong: " + ", ".join(parts) + ".")
    source_items = results if results else items
    lines.append(f"Trả về {min(len(source_items), 10)} bài phù hợp nhất:")

    for index, item in enumerate(source_items[:10], start=1):
        title = clean_facebook_result_text(str(item.get("author") or f"Bài {index}"), 100) or f"Bài {index}"
        main_text = clean_facebook_result_text(str(item.get("summary") or item.get("excerpt") or item.get("text") or ""), 220)
        url = str(item.get("url") or "").strip()
        bucket = str(item.get("time_status") or item.get("time_bucket") or "").strip()
        if bucket == "recent_confirmed":
            time_note = "đã xác nhận còn mới"
        elif bucket == "time_unknown":
            time_note = ""
        elif bucket == "stale_confirmed":
            time_note = "đã xác nhận quá cũ"
        else:
            time_note = clean_facebook_result_text(str(item.get("time_hint") or ""), 60)
        reason = clean_facebook_result_text(str(item.get("llm_reason") or item.get("keep_reason") or ""), 180)
        line = f"{index}. {title}"
        if main_text:
            line += f"\n   - Nội dung chính: {main_text}"
        if reason:
            line += f"\n   - Lý do giữ: {reason}"
        if time_note:
            line += f"\n   - Thời gian: {time_note}"
        if url:
            line += f"\n   - Link: {url}"
        lines.append(line)
    return "\n".join(lines)


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
    if kind.startswith("host_browser_"):
        return "Kiểm tra bridge host còn sống, đổi browser/url, hoặc chụp screenshot để xác nhận trạng thái browser hiện tại."
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
    return detect_tool_error_class(raw)


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


def _maybe_store_large_text_artifact(prefix: str, output: str, is_error: bool = False) -> str:
    text = str(output or "")
    if len(text) <= ACTION_RESULT_ARTIFACT_THRESHOLD:
        return text
    artifact_dir = Path(os.getenv("LANGGRAPH_STATE_DIR", str(STATE_DIR))).resolve() / "action_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    suffix = "error" if is_error else "result"
    path = artifact_dir / f"{prefix}_{suffix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
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


def maybe_store_action_artifact(action_id: int, output: str, is_error: bool = False, kind: str = "") -> str:
    text = str(output or "")
    if str(kind or "") == "host_browser_facebook_research":
        return text
    return _maybe_store_large_text_artifact(f"action_{action_id}", text, is_error=is_error)


def maybe_store_job_result_artifact(job_id: str, output: str, is_error: bool = False) -> str:
    return _maybe_store_large_text_artifact(f"job_{job_id}", output, is_error=is_error)


def build_action_failure_payload(output: str, error_class: str, action_id: int, kind: str = "") -> str:
    stored = maybe_store_action_artifact(action_id, output, is_error=True, kind=kind)
    parsed: dict[str, Any] = {}
    try:
        candidate = json.loads(str(stored or ""))
        if isinstance(candidate, dict):
            parsed = candidate
    except Exception:
        parsed = {}
    return json.dumps(
        {
            "summary": compact_action_output_for_model(str(output or ""), 800),
            "error_class": str(error_class or detect_error_class(str(output or "")) or "runtime_error"),
            "artifact": bool(parsed.get("artifact")),
            "artifact_path": str(parsed.get("artifact_path") or ""),
            "artifact_url": str(parsed.get("artifact_url") or ""),
            "bytes": int(parsed.get("bytes") or len(str(output or "").encode("utf-8"))),
            "truncated": bool(parsed.get("truncated")),
        },
        ensure_ascii=False,
        indent=2,
    )


def _hydrate_artifact_backed_text(raw_value: str) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw
    if not (isinstance(parsed, dict) and parsed.get("artifact") and parsed.get("artifact_path")):
        return raw
    artifact_path = Path(str(parsed.get("artifact_path") or "")).expanduser()
    if not artifact_path.exists():
        return raw
    try:
        return artifact_path.read_text(encoding="utf-8")
    except Exception:
        return raw


def hydrate_action_result_for_ui(action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(action, dict):
        return action
    hydrated = dict(action)
    raw_result = str(action.get("result") or "").strip()
    if raw_result:
        hydrated["result"] = _hydrate_artifact_backed_text(raw_result)
    raw_error = str(action.get("error") or "").strip()
    if raw_error:
        hydrated["error"] = _hydrate_artifact_backed_text(raw_error)
    hydrated["policy"] = classify_action_policy(hydrated)
    return hydrated


def classify_action_policy(action: dict[str, Any]) -> dict[str, Any]:
    kind = str(action.get("kind") or "")
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    preview = " ".join(
        [
            kind,
            str(action.get("title") or ""),
            str(action.get("preview") or ""),
            json.dumps(payload, ensure_ascii=False),
        ]
    ).lower()
    capability_tags: list[str] = []
    approval_class = "read_only"
    reason = "Safe read-oriented action."
    if kind.startswith("host_browser_"):
        capability_tags.extend(["host", "browser"])
        approval_class = "network"
        reason = "Host/browser action reaches external pages or local browser state."
    elif kind == "host_shell_command":
        capability_tags.extend(["host", "shell"])
        approval_class = "write"
        reason = "Host shell action executes directly on Linux machine."
    elif kind == "host_docker_ps":
        capability_tags.extend(["host", "docker", "read"])
        approval_class = "read_only"
        reason = "Host Docker inventory is a read-only Linux operator action."
    elif kind == "host_process_list":
        capability_tags.extend(["host", "process", "read"])
        approval_class = "read_only"
        reason = "Host process inventory is a read-only machine inspection action."
    elif kind == "host_process_signal":
        capability_tags.extend(["host", "process", "write"])
        approval_class = "operator_write"
        reason = "Host process signal mutates running Linux processes and requires approval."
    elif kind == "host_process_recovery":
        capability_tags.extend(["host", "process", "recovery"])
        approval_class = "operator_write"
        reason = "Host process recovery may signal Linux processes and requires approval."
    elif kind == "host_service_status":
        capability_tags.extend(["host", "service", "read"])
        approval_class = "read_only"
        reason = "Host service status inspects Linux unit health without mutation."
    elif kind == "host_service_logs":
        capability_tags.extend(["host", "service", "logs"])
        approval_class = "read_only"
        reason = "Host service logs tail inspects recent Linux journal output."
    elif kind in {"host_docker_restart", "host_service_restart"}:
        capability_tags.extend(["host", "operator", "write"])
        if "docker" in kind:
            capability_tags.append("docker")
        if "service" in kind:
            capability_tags.append("service")
        approval_class = "operator_write"
        reason = "Host operator write action mutates Linux runtime state and requires approval."
    elif kind in {"host_container_recovery", "host_service_recovery"}:
        capability_tags.extend(["host", "operator", "recovery"])
        if "container" in kind or "docker" in kind:
            capability_tags.append("docker")
        if "service" in kind:
            capability_tags.append("service")
        approval_class = "operator_write"
        reason = "Host recovery workflow may restart Linux runtime targets and requires approval."
    elif kind in {"host_file_read", "host_file_list"}:
        capability_tags.extend(["host", "file"])
        approval_class = "read_only"
        reason = "Host file action reads Linux filesystem state."
    elif kind in {"host_file_write", "host_file_move", "host_file_delete"}:
        capability_tags.extend(["host", "file", "write"])
        approval_class = "operator_write"
        reason = "Host file mutation changes Linux filesystem state and requires approval."
    elif kind == "coding_agent_executor":
        capability_tags.extend(["executor", "code"])
        approval_class = "write"
        reason = "Executor may edit code, files, or run multi-step commands."
    elif kind == "workspace_command":
        capability_tags.extend(["workspace", "shell"])
        approval_class = "write"
        reason = "Shell command may mutate workspace or environment."
    elif kind in {"workspace_diff", "workspace_read_file", "workspace_inspect"}:
        capability_tags.extend(["workspace", "read"])
    elif kind in {"git_checkpoint", "git_restore_checkpoint"}:
        capability_tags.extend(["git", "checkpoint"])
        approval_class = "write"
        reason = "Git checkpoint actions mutate repository state."
    elif kind == "note":
        capability_tags.append("note")
    if kind.startswith("host_browser_") and any(marker in preview for marker in SENSITIVE_BROWSER_MARKERS):
        approval_class = "credentialed"
        capability_tags.append("credentialed")
        reason = "Action touches login, token, password, or other credentialed flow."
    if action_requires_publish_approval(action):
        approval_class = "publish"
        capability_tags.append("publish")
        reason = "Action may publish, send, or create external side effects."
    if approval_class in {"network", "credentialed", "publish"} and "network" not in capability_tags:
        capability_tags.append("network")
    status = str(action.get("status") or "")
    requires_approval = status == "pending" and approval_class in {"credentialed", "publish", "network", "operator_write"}
    return {
        "approval_class": approval_class,
        "requires_approval": requires_approval,
        "capability_tags": capability_tags,
        "reason": reason,
    }


def build_job_notifications(job: dict[str, Any], verification: dict[str, Any], pending_actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notifications: list[dict[str, Any]] = []
    status = str(job.get("status") or "")
    if verification and not bool(verification.get("ok", True)):
        missing = verification.get("missing_requirements") if isinstance(verification.get("missing_requirements"), list) else []
        notifications.append(
            {
                "level": "warning",
                "code": "verification_failed",
                "message": compact_text(", ".join(str(item) for item in missing[:4]) or "Verification failed. Review evidence before retry.", 220),
            }
        )
    for action in pending_actions[:5]:
        policy = action.get("policy") if isinstance(action.get("policy"), dict) else classify_action_policy(action)
        if str(action.get("status") or "") != "pending":
            continue
        if policy.get("requires_approval"):
            notifications.append(
                {
                    "level": "info",
                    "code": "approval_required",
                    "message": compact_text(f"Approval needed for {action.get('title') or action.get('kind')} ({policy.get('approval_class')}).", 220),
                }
            )
            continue
        if str(action.get("title") or "").startswith("Recurring:"):
            notifications.append(
                {
                    "level": "info",
                    "code": "recurring_due",
                    "message": compact_text(f"Recurring task ready: {action.get('title')}.", 220),
                }
            )
    if status == "done":
        notifications.append({"level": "success", "code": "job_done", "message": "Job completed with structured evidence."})
    elif status == "failed" and not notifications:
        notifications.append({"level": "warning", "code": "job_failed", "message": "Job failed. Review artifacts, logs, and next action hints."})
    return notifications[:6]


def build_job_session_summary(job: dict[str, Any], verification: dict[str, Any], pending_actions: list[dict[str, Any]]) -> str:
    plan_count = len(job.get("plan") or []) if isinstance(job.get("plan"), list) else 0
    pending_count = sum(1 for action in pending_actions if str(action.get("status") or "") == "pending")
    done_count = sum(1 for action in pending_actions if str(action.get("status") or "") == "done")
    parts = [
        f"status={str(job.get('status') or 'unknown')}",
        f"plan_steps={plan_count}",
        f"pending_actions={pending_count}",
        f"completed_actions={done_count}",
    ]
    if verification:
        parts.append(f"verification={'ok' if verification.get('ok') else 'failed'}")
    title = str(job.get("title") or "").strip()
    if title:
        parts.insert(0, compact_text(title, 60))
    return " | ".join(parts)


def action_recovery_depth(action: dict[str, Any]) -> int:
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    try:
        return max(0, int(payload.get("recovery_depth") or 0))
    except (TypeError, ValueError):
        return 0


def action_continuation_mode(action: dict[str, Any]) -> str:
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    mode = str(payload.get("continuation_mode") or "").strip().lower()
    if mode:
        return mode
    if action_recovery_depth(action) > 0:
        return "recovery"
    return ""


def build_loop_metrics(job: dict[str, Any], pending_actions: list[dict[str, Any]]) -> dict[str, Any]:
    permission_mode = str(job.get("permission_mode") or "full_access")
    auto_candidates = 0
    auto_pending_candidates = 0
    used_auto = 0
    max_recovery = 0
    for action in pending_actions:
        policy = action.get("policy") if isinstance(action.get("policy"), dict) else classify_action_policy(action)
        continuation_mode = action_continuation_mode(action)
        status = str(action.get("status") or "")
        max_recovery = max(max_recovery, action_recovery_depth(action))
        if continuation_mode in {"auto", "recovery"}:
            used_auto += 1
        if status != "pending":
            continue
        if policy.get("requires_approval"):
            continue
        if should_auto_execute_action(action, permission_mode):
            auto_candidates += 1
            if continuation_mode in {"auto", "recovery"}:
                auto_pending_candidates += 1
    remaining_auto = max(0, MAX_AUTO_CONTINUATIONS - used_auto)
    return {
        "max_auto_continuations": MAX_AUTO_CONTINUATIONS,
        "used_auto_continuations": used_auto,
        "remaining_auto_continuations": remaining_auto,
        "max_recovery_depth": max_recovery,
        "pending_auto_candidates": auto_pending_candidates,
        "auto_runnable_next_action_count": min(auto_candidates, remaining_auto) if remaining_auto > 0 else 0,
        "can_auto_continue": auto_candidates > 0 and remaining_auto > 0,
    }


def build_autonomy_status(job: dict[str, Any], pending_actions: list[dict[str, Any]], verification: dict[str, Any]) -> dict[str, Any]:
    used_actions = len(pending_actions)
    max_actions = 12 if str(job.get("permission_mode") or "") == "full_access" else 6
    loop_metrics = build_loop_metrics(job, pending_actions)
    blocked_actions = [
        action for action in pending_actions
        if isinstance(action.get("policy"), dict)
        and bool(action["policy"].get("requires_approval"))
        and str(action.get("status") or "") == "pending"
    ]
    failed_actions = [action for action in pending_actions if str(action.get("status") or "") == "failed"]
    stop_reason = ""
    if blocked_actions:
        stop_reason = "approval_required"
    elif used_actions >= max_actions:
        stop_reason = "action_budget_exhausted"
    elif verification and not bool(verification.get("ok", True)):
        stop_reason = "verification_failed"
    elif len(failed_actions) >= MAX_ACTION_RECOVERY_DEPTH:
        stop_reason = "recovery_budget_exhausted"
    elif loop_metrics["remaining_auto_continuations"] <= 0 and loop_metrics["pending_auto_candidates"] > 0:
        stop_reason = "continuation_budget_exhausted"
    escalation_level = "none"
    if stop_reason == "approval_required":
        escalation_level = "approval"
    elif stop_reason in {"verification_failed", "recovery_budget_exhausted"}:
        escalation_level = "review"
    elif stop_reason in {"action_budget_exhausted", "continuation_budget_exhausted"}:
        escalation_level = "budget"
    managed_loop_state = "idle"
    if stop_reason:
        managed_loop_state = "blocked"
    elif any(str(action.get("status") or "") == "running" for action in pending_actions):
        managed_loop_state = "running"
    elif loop_metrics["can_auto_continue"]:
        managed_loop_state = "ready_to_continue"
    elif any(str(action.get("status") or "") == "pending" for action in pending_actions):
        managed_loop_state = "waiting"
    return {
        "profile": "full_access",
        "budget": {
            "max_actions": max_actions,
            "used_actions": used_actions,
            "remaining_actions": max(0, max_actions - used_actions),
        },
        "blocked": bool(stop_reason),
        "stop_reason": stop_reason,
        "approval_required_count": len(blocked_actions),
        "can_continue_without_user": bool(loop_metrics["can_auto_continue"]) and not bool(stop_reason),
        "escalation_level": escalation_level,
        "managed_loop_state": managed_loop_state,
        "loop": loop_metrics,
    }


def build_attention_score(job: dict[str, Any]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0
    status = str(job.get("status") or "")
    autonomy = job.get("autonomy") if isinstance(job.get("autonomy"), dict) else {}
    notifications = job.get("notifications") if isinstance(job.get("notifications"), list) else []
    next_actions = job.get("next_actions") if isinstance(job.get("next_actions"), list) else []
    if any(str(item.get("code") or "") == "verification_failed" for item in notifications if isinstance(item, dict)):
        score += 50
        reasons.append("verification_failed")
    if int(autonomy.get("approval_required_count") or 0) > 0:
        score += 40
        reasons.append("approval_required")
    if any(str(item.get("code") or "") == "recurring_due" for item in notifications if isinstance(item, dict)):
        score += 25
        reasons.append("recurring_due")
    if status == "running":
        score += 20
        reasons.append("running")
    if status == "failed":
        score += 15
        reasons.append("failed")
    if next_actions:
        score += 10
        reasons.append("next_action_ready")
    return score, reasons


def _normalized_text_tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_.@/-]+", str(text or "").lower()) if len(token) >= 3]


def rank_projects_for_job(
    job: dict[str, Any],
    projects: list[dict[str, Any]] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    active_projects = projects if projects is not None else db.list_projects(include_inactive=False)
    title = str(job.get("title") or "")
    request = str(job.get("request") or "")
    session_summary = str(job.get("session_summary") or "")
    message_text = ""
    messages = job.get("messages") if isinstance(job.get("messages"), list) else []
    if messages:
        message_text = "\n".join(str(item.get("content") or "") for item in messages[-6:] if isinstance(item, dict))
    haystack = "\n".join(part for part in [title, request, session_summary, message_text] if part).lower()
    haystack_tokens = set(_normalized_text_tokens(haystack))
    ranked: list[dict[str, Any]] = []
    for project in active_projects:
        score = 0
        reasons: list[str] = []
        name = str(project.get("name") or "").strip()
        root_path = str(project.get("root_path") or "").strip()
        summary = str(project.get("summary") or "").strip()
        memory = str(project.get("memory") or "").strip()
        if root_path and root_path.lower() in haystack:
            score += 60
            reasons.append("root_path_match")
        name_tokens = set(_normalized_text_tokens(name))
        name_hits = sorted(name_tokens & haystack_tokens)
        if name_hits:
            score += min(36, 18 * len(name_hits))
            reasons.append(f"name:{','.join(name_hits[:3])}")
        context_tokens = set(_normalized_text_tokens(f"{summary}\n{memory}"))
        context_hits = sorted(context_tokens & haystack_tokens)
        if context_hits:
            score += min(28, 4 * len(context_hits))
            reasons.append(f"context:{','.join(context_hits[:4])}")
        if score <= 0:
            continue
        ranked.append(
            {
                "id": project.get("id"),
                "name": name,
                "root_path": root_path,
                "status": project.get("status"),
                "score": score,
                "reasons": reasons,
            }
        )
    ranked.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            str(item.get("name") or "").lower(),
        ),
        reverse=True,
    )
    return ranked[: max(1, int(limit))]


def build_project_assistant_entry(project: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    related_jobs: list[dict[str, Any]] = []
    project_id = int(project.get("id") or 0)
    for job in jobs:
        primary = job.get("primary_project") if isinstance(job.get("primary_project"), dict) else {}
        if int(primary.get("id") or 0) != project_id:
            continue
        entry = build_inbox_entry(job)
        related_jobs.append(entry)
    related_jobs.sort(
        key=lambda item: (
            int(item.get("attention_score") or 0),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )
    status_counts: dict[str, int] = {}
    for item in related_jobs:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "project": project,
        "open_jobs_count": sum(1 for item in related_jobs if str(item.get("status") or "") not in {"done"}),
        "urgent_jobs_count": sum(1 for item in related_jobs if int(item.get("attention_score") or 0) >= 40),
        "top_attention_score": max((int(item.get("attention_score") or 0) for item in related_jobs), default=0),
        "last_activity_at": max((str(item.get("updated_at") or "") for item in related_jobs), default=""),
        "status_counts": status_counts,
        "jobs": related_jobs[:8],
    }


def build_inbox_entry(job: dict[str, Any]) -> dict[str, Any]:
    hydrated = hydrate_job_for_ui(job)
    score, reasons = build_attention_score(hydrated)
    return {
        "job_id": hydrated.get("id"),
        "title": hydrated.get("title"),
        "status": hydrated.get("status"),
        "updated_at": hydrated.get("updated_at"),
        "attention_score": score,
        "attention_reasons": reasons,
        "session_summary": hydrated.get("session_summary", ""),
        "notifications": hydrated.get("notifications", []),
        "next_actions": hydrated.get("next_actions", []),
        "autonomy": hydrated.get("autonomy", {}),
        "primary_project": hydrated.get("primary_project"),
        "related_projects": hydrated.get("related_projects", []),
    }


def build_next_action_entry(action: dict[str, Any], permission_mode: str, autonomy: dict[str, Any]) -> dict[str, Any]:
    policy = action.get("policy") if isinstance(action.get("policy"), dict) else classify_action_policy(action)
    loop = autonomy.get("loop") if isinstance(autonomy.get("loop"), dict) else {}
    auto_capable = bool(should_auto_execute_action(action, permission_mode)) and not bool(policy.get("requires_approval"))
    budget_blocked = auto_capable and int(loop.get("remaining_auto_continuations") or 0) <= 0
    blocked_reason = ""
    if bool(policy.get("requires_approval")):
        blocked_reason = "approval_required"
    elif budget_blocked:
        blocked_reason = "continuation_budget_exhausted"
    return {
        "type": "pending_action",
        "action_id": action.get("id"),
        "kind": action.get("kind"),
        "title": action.get("title"),
        "preview": compact_text(str(action.get("preview") or ""), 240),
        "policy": policy,
        "requires_approval": bool(policy.get("requires_approval")),
        "auto_runnable": auto_capable and not budget_blocked,
        "blocked_reason": blocked_reason,
        "continuation_mode": action_continuation_mode(action),
        "recovery_depth": action_recovery_depth(action),
    }


def hydrate_job_for_ui(job: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(job, dict):
        return job
    hydrated = dict(job)
    if str(job.get("result") or "").strip():
        hydrated["result"] = _hydrate_artifact_backed_text(str(job.get("result") or ""))
    pending_actions = job.get("pending_actions") if isinstance(job.get("pending_actions"), list) else []
    hydrated["pending_actions"] = [hydrate_action_result_for_ui(action) for action in pending_actions]
    verification = job.get("verification") if isinstance(job.get("verification"), dict) else {}
    hydrated["verification"] = verification
    permission_mode = str(hydrated.get("permission_mode") or "full_access")
    autonomy = build_autonomy_status(hydrated, hydrated["pending_actions"], verification)
    next_actions: list[dict[str, Any]] = []
    for action in hydrated["pending_actions"][:5]:
        if str(action.get("status") or "") != "pending":
            continue
        next_actions.append(build_next_action_entry(action, permission_mode, autonomy))
    if not next_actions and str(job.get("status") or "") == "failed" and verification:
        missing = verification.get("missing_requirements") if isinstance(verification.get("missing_requirements"), list) else []
        next_actions.append(
            {
                "type": "review_verification",
                "title": "Review failed verification evidence",
                "preview": compact_text(", ".join(str(item) for item in missing[:6]) or "Inspect verification details and retry safely.", 240),
            }
        )
    hydrated["next_actions"] = next_actions
    hydrated["notifications"] = build_job_notifications(hydrated, verification, hydrated["pending_actions"])
    hydrated["session_summary"] = build_job_session_summary(hydrated, verification, hydrated["pending_actions"])
    hydrated["autonomy"] = autonomy
    related_projects = rank_projects_for_job(hydrated)
    hydrated["related_projects"] = related_projects
    hydrated["primary_project"] = related_projects[0] if related_projects else None
    return hydrated


def job_response(job_or_id: dict[str, Any] | str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    job_id = str(job_or_id.get("id") or "") if isinstance(job_or_id, dict) else str(job_or_id or "")
    job = db.get_job(job_id) if job_id else None
    if not job and isinstance(job_or_id, dict):
        job = job_or_id
    if not job and fallback:
        job = fallback
    return {"job": hydrate_job_for_ui(job or {})}


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
    if ok and action.get("kind") == "host_browser_facebook_research":
        refresh_session_memory(job_id)
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
    allow_retry_kinds = {"host_browser_facebook_research"}
    if kind not in {"coding_agent_executor", *allow_retry_kinds} and lesson and lesson.get("status") == "active" and int(lesson.get("fail_count") or 0) >= 2:
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
                "permission_mode": job.get("permission_mode", "full_access"),
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
                "permission_mode": job.get("permission_mode", "full_access"),
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
                "permission_mode": job.get("permission_mode", "full_access"),
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
                "permission_mode": job.get("permission_mode", "full_access"),
            },
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_list":
        try:
            result = host_browser_call("/browsers", method="GET")
        except Exception as exc:
            return False, f"Không gọi được host browser bridge: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_open":
        url = str(payload.get("url") or "").strip()
        if not url:
            return False, "host_browser_open cần payload.url"
        req = {"url": url, "browser": str(payload.get("browser") or "brave")}
        try:
            result = host_browser_call("/open-url", req)
        except Exception as exc:
            return False, f"Không mở được browser host: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_open_current":
        url = str(payload.get("url") or "").strip()
        if not url:
            return False, "host_browser_open_current cần payload.url"
        req = {"url": url, "browser": str(payload.get("browser") or "brave")}
        try:
            result = host_browser_call("/open-current", req)
        except Exception as exc:
            return False, f"Không mở/focus được browser host hiện tại: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_launch":
        req = {
            "url": str(payload.get("url") or "about:blank"),
            "browser": str(payload.get("browser") or "brave"),
            "port": int(payload.get("port") or 9222),
            "profile_dir": str(payload.get("profile_dir") or ""),
        }
        try:
            result = host_browser_call("/launch-debug", req)
        except Exception as exc:
            return False, f"Không launch được debug browser host: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_facebook_research":
        query = str(payload.get("query") or "").strip()
        if not query:
            return False, "host_browser_facebook_research cần payload.query"
        try:
            result = build_host_browser_facebook_research_result(
                HostBrowserFacebookResearchRequest(
                    query=query,
                    browser=str(payload.get("browser") or "brave"),
                    port=int(payload.get("port") or 9222),
                    limit=int(payload.get("limit") or 8),
                    scroll_rounds=int(payload.get("scroll_rounds") or 4),
                    profile_dir=str(payload.get("profile_dir") or ""),
                )
            )
        except Exception as exc:
            return False, f"Không research được Facebook host: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_screenshot":
        req = {
            "pid": int(payload.get("pid") or 0),
            "full_screen": bool(payload.get("full_screen") or False),
            "name": str(payload.get("name") or ""),
            "artifact_dir": str(payload.get("artifact_dir") or ""),
        }
        try:
            result = host_browser_call("/screenshot", req)
        except Exception as exc:
            return False, f"Không chụp được màn hình host: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_mouse_move":
        req = {
            "x": int(payload.get("x") or 0),
            "y": int(payload.get("y") or 0),
        }
        try:
            result = host_browser_call("/mouse-move", req)
        except Exception as exc:
            return False, f"Không di chuyển được chuột host: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_browser_mouse_click":
        req = {
            "x": int(payload.get("x") or 0),
            "y": int(payload.get("y") or 0),
        }
        try:
            result = host_browser_call("/mouse-click", req)
        except Exception as exc:
            return False, f"Không click được chuột host: {exc}"
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_shell_command":
        result = run_host_shell_action(
            str(payload.get("command") or ""),
            cwd=str(payload.get("cwd") or ""),
            timeout=int(payload.get("timeout") or 60),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_file_read":
        result = read_host_file(
            str(payload.get("path") or ""),
            max_chars=int(payload.get("max_chars") or 40000),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_file_list":
        result = list_host_files(
            str(payload.get("path") or ""),
            limit=int(payload.get("limit") or 200),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_file_write":
        result = write_host_file(
            str(payload.get("path") or ""),
            content=str(payload.get("content") or ""),
            overwrite=bool(payload.get("overwrite", True)),
            create_parents=bool(payload.get("create_parents") or False),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_file_move":
        result = move_host_file(
            str(payload.get("src_path") or ""),
            str(payload.get("dest_path") or ""),
            overwrite=bool(payload.get("overwrite") or False),
            create_parents=bool(payload.get("create_parents") or False),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_file_delete":
        result = delete_host_path(
            str(payload.get("path") or ""),
            recursive=bool(payload.get("recursive") or False),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_process_list":
        result = run_host_process_list(
            limit=int(payload.get("limit") or 200),
            query=str(payload.get("query") or ""),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_process_signal":
        result = run_host_process_signal(
            int(payload.get("pid") or 0),
            signal_name=str(payload.get("signal_name") or "TERM"),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_process_recovery":
        result = run_host_process_recovery(
            int(payload.get("pid") or 0),
            signal_name=str(payload.get("signal_name") or "TERM"),
            query=str(payload.get("query") or ""),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_docker_ps":
        result = run_host_docker_ps(
            all_containers=bool(payload.get("all_containers") or False),
            limit=int(payload.get("limit") or 50),
            timeout=int(payload.get("timeout") or 20),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_service_status":
        result = run_host_service_status(
            str(payload.get("service") or ""),
            lines=int(payload.get("lines") or 40),
            timeout=int(payload.get("timeout") or 20),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_service_logs":
        result = run_host_service_logs(
            str(payload.get("service") or ""),
            lines=int(payload.get("lines") or 80),
            since=str(payload.get("since") or ""),
            timeout=int(payload.get("timeout") or 20),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_docker_restart":
        result = run_host_docker_restart(
            str(payload.get("container") or ""),
            timeout=int(payload.get("timeout") or 30),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_service_restart":
        result = run_host_service_restart(
            str(payload.get("service") or ""),
            timeout=int(payload.get("timeout") or 30),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_container_recovery":
        result = run_host_container_recovery(
            str(payload.get("container") or ""),
            logs_lines=int(payload.get("logs_lines") or 80),
            timeout=int(payload.get("timeout") or 45),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return bool(result.get("ok")), text
    if kind == "host_service_recovery":
        result = run_host_service_recovery(
            str(payload.get("service") or ""),
            status_lines=int(payload.get("status_lines") or 30),
            logs_lines=int(payload.get("logs_lines") or 80),
            timeout=int(payload.get("timeout") or 45),
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
                "permission_mode": job.get("permission_mode", "full_access"),
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
    if looks_like_onboarding_agent_request(onboarding_scope):
        return {
            "mode": "plan",
            "plan": ONBOARDING_AGENT_PLAN,
            "summary": "deterministic_onboarding_fallback",
        }
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


def clean_facebook_result_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\U0001D400-\U0001D7FF]", " ", text)
    text = re.sub(r"[\uFFFD\u200B-\u200D\u2060]", " ", text)
    text = re.sub(r"[`*_#~<>\[\](){}|\\]", " ", text)
    text = re.sub(r"\bFacebook(?:\s+Facebook)+\b", "Facebook", text, flags=re.I)
    text = re.sub(r"\b(?:Like|Comment|Share|Join|Follow|Thích|Bình luận|Chia sẻ)\b", " ", text, flags=re.I)
    text = re.sub(r"\bSee more\b", " ", text, flags=re.I)
    text = re.sub(r"\b\S+\.comGiang\*?", " ", text, flags=re.I)
    text = re.sub(r"https?://\S+", " ", text, flags=re.I)
    text = re.sub(r"\b[a-z0-9]{18,}\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    return compact_text(text, limit)


def parse_json_payload(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    candidates.extend(fenced)
    brace_match = re.search(r"(\{.*\})", raw, flags=re.S)
    if brace_match:
        candidates.append(brace_match.group(1))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def fallback_host_browser_search_plan(query: str, max_queries: int = 6) -> dict[str, Any]:
    raw_query = compact_text(sanitize_facebook_search_query(query), 300)
    normalized = str(raw_query).lower()
    variants: list[str] = [raw_query]
    criteria: list[str] = []
    intent = "facebook research"
    profile = {
        "original_query": raw_query,
        "normalized_query": normalized,
        "roleTerms": [],
        "seniorityTerms": [],
        "locationTerms": [],
        "anchorTerms": [],
        "locationAliases": [],
        "mustIncludeGroups": [],
    }

    district_aliases: dict[str, list[str]] = {
        "go vap": ["go vap", "gò vấp", "gv", "binh thanh", "phu nhuan", "tan binh", "quan 12"],
        "quan 7": ["quan 7", "q7", "phu my hung", "nha be"],
        "thu duc": ["thu duc", "q2", "quan 2", "q9", "quan 9", "tp thu duc"],
        "tan binh": ["tan binh", "san bay", "phu nhuan", "go vap"],
    }

    def add_variant(*values: str) -> None:
        for value in values:
            clean = compact_text(value, 120).strip()
            if clean and clean.lower() not in {item.lower() for item in variants}:
                variants.append(clean)

    has_job = any(token in normalized for token in ["tuyển", "tuyen", "việc", "viec", "job", "hiring", "recruit"])
    has_rental = any(token in normalized for token in ["trọ", "tro", "phòng", "phong", "thuê", "thue", "ở ghép", "o ghep"])
    has_sale = any(token in normalized for token in ["mua", "bán", "ban", "thanh lý", "thanh ly", "pass"])
    has_review = any(token in normalized for token in ["review", "đánh giá", "danh gia", "trải nghiệm", "trai nghiem"])

    role_aliases: list[str] = []
    if any(token in normalized for token in ["devops", "sre", "cloud", "platform", "infra", "infrastructure", "sysadmin"]):
        role_aliases = ["devops", "sre", "cloud", "platform engineer", "infrastructure", "sysadmin"]
        profile["roleTerms"] = ["devops", "sre", "cloud", "platform", "sysadmin", "infra"]
        profile["anchorTerms"].extend(["devops", "sre", "cloud", "platform", "kubernetes", "docker", "terraform", "cicd"])
        profile["mustIncludeGroups"].append(["devops", "sre", "cloud", "platform", "infra"])
    seniority_aliases: list[str] = []
    if any(token in normalized for token in ["intern", "fresher", "thực tập", "thuc tap", "new grad", "junior", "0-1 năm", "0-1 nam"]):
        seniority_aliases = ["intern", "thực tập sinh", "fresher", "junior", "new grad", "0-1 năm"]
        profile["seniorityTerms"] = ["intern", "thực tập", "thực tập sinh", "fresher", "junior", "new grad"]
        profile["anchorTerms"].extend(["intern", "thực tập", "thực tập sinh", "fresher", "junior", "không cần kinh nghiệm", "entry level"])
        profile["mustIncludeGroups"].append(["intern", "thực tập", "fresher", "junior", "new grad"])
    location_aliases: list[str] = []
    if any(token in normalized for token in ["hcm", "tphcm", "tp hcm", "ho chi minh", "hcmc", "sài gòn", "sai gon"]):
        location_aliases = ["hcm", "tphcm", "ho chi minh", "hcmc", "sài gòn", "sai gon"]
        profile["locationTerms"] = ["hcm", "tphcm", "hồ chí minh", "sài gòn", "hcmc"]
        profile["locationAliases"].extend(["hcm", "tphcm", "tp hcm", "ho chi minh", "sai gon", "hcmc"])
        profile["mustIncludeGroups"].append(["hcm", "tphcm", "ho chi minh", "sai gon", "hcmc"])
    for district, aliases in district_aliases.items():
        if district in normalized or any(alias in normalized for alias in aliases):
            profile["locationTerms"].extend(aliases[:3])
            profile["locationAliases"].extend(aliases)
            profile["mustIncludeGroups"].append(aliases[:4])
            break

    if has_job:
        intent = "tìm bài tuyển dụng"
        criteria = ["đúng bài tuyển thật", "đúng role tương đương", "đúng seniority/location", "ưu tiên bài mới"]
        if role_aliases and seniority_aliases and location_aliases:
            add_variant(
                "devops intern hcm",
                "tuyển devops intern tphcm",
                "thực tập sinh devops ho chi minh",
                "cloud intern hcm",
                "platform engineer intern hcm",
                "sre fresher sai gon",
            )
        elif role_aliases and seniority_aliases:
            add_variant("devops intern", "thực tập sinh devops", "cloud intern", "platform fresher")
        elif role_aliases:
            add_variant("tuyển devops", "hiring sre", "cloud engineer hcm")
    elif has_rental:
        intent = "tìm bài cho thuê/trọ"
        criteria = ["đúng bài cho thuê", "giá/khu vực rõ", "ưu tiên bài mới"]
        profile["anchorTerms"].extend(["nhà trọ", "phòng trọ", "cho thuê", "ở ghép", "studio", "còn phòng"])
        add_variant(
            raw_query.replace("tìm", "cho thuê").replace("tim", "cho thue"),
            raw_query.replace("thuê trọ", "phòng trọ").replace("thue tro", "phong tro"),
            raw_query.replace("quận 7", "q7").replace("quan 7", "q7"),
        )
    elif has_sale:
        intent = "tìm bài mua bán"
        criteria = ["đúng món cần tìm", "giá/tình trạng rõ", "ưu tiên bài mới"]
        profile["anchorTerms"].extend(["mua", "bán", "pass", "thanh lý", "giá", "tình trạng"])
    elif has_review:
        intent = "tìm bài review/trải nghiệm"
        criteria = ["đúng chủ đề review", "nhiều chi tiết", "ưu tiên bài có trải nghiệm thật"]
        profile["anchorTerms"].extend(["review", "đánh giá", "trải nghiệm", "ưu nhược điểm"])

    return {
        "ok": True,
        "intent": intent,
        "criteria": criteria,
        "query_variants": variants[: max(1, min(int(max_queries or 6), 10))],
        "profile": profile,
        "negative_signals": ["bài chỉ xin thông tin", "bài lệch chủ đề", "group/page không có nội dung bài cụ thể"],
        "summary": "Fallback planner generated query variants.",
        "raw": "",
    }


def host_browser_search_plan(query: str, max_queries: int = 6) -> dict[str, Any]:
    cleaned_query = sanitize_facebook_search_query(query)
    fallback = fallback_host_browser_search_plan(cleaned_query, max_queries)
    planner_system_prompt = (
        "Bạn là search query planner cho Facebook search. "
        "Nhiệm vụ duy nhất: từ câu người dùng, sinh ra nhiều query search khác nhau để tăng recall. "
        "Ưu tiên synonym, alias địa điểm, cách diễn đạt khác, biến thể ngắn/gọn và các cách viết tự nhiên dễ match trên Facebook. "
        "Không cần giải thích. Không cần criteria. Không cần profile. "
        "Không chỉ đảo vị trí từ. Mỗi query nên đại diện cho một cách tìm khác nhau nếu có thể. "
        "Trả JSON thuần."
    )
    planner_user_prompt = (
        "Sinh query search cho Facebook.\n"
        f"User query: {compact_text(cleaned_query, 400)}\n"
        f"Max queries: {max(1, min(int(max_queries or 6), 10))}\n\n"
        'Trả JSON đúng schema: {"query_variants":["..."]}'
    )
    started = time.perf_counter()
    result = cliproxy_chat(
        [{"role": "user", "content": planner_user_prompt}],
        compose_system_prompt(planner_system_prompt),
    )
    trace_event(
        "model_call",
        route="host_browser_search_plan",
        ok=bool(result.get("ok")),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    if not result.get("ok"):
        fallback["summary"] = compact_text(str(result.get("summary") or fallback.get("summary") or ""), 400)
        return fallback
    raw = str((result.get("data") or {}).get("content") or "").strip()
    parsed = parse_json_payload(raw)
    variants = parsed.get("query_variants") if isinstance(parsed.get("query_variants"), list) else []
    cleaned_variants: list[str] = []
    seen: set[str] = set()
    for value in [cleaned_query, *variants]:
        clean = compact_text(str(value or "").strip(), 120)
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            cleaned_variants.append(clean)
    if not cleaned_variants:
        fallback["raw"] = raw[:1800]
        return fallback
    profile = fallback["profile"]
    return {
        "ok": True,
        "intent": compact_text(str(fallback.get("intent") or query), 240),
        "criteria": list(fallback.get("criteria") or []),
        "query_variants": cleaned_variants[: max(1, min(int(max_queries or 6), 10))],
        "negative_signals": list(fallback.get("negative_signals") or []),
        "profile": profile,
        "summary": "AI generated search query variants.",
        "raw": raw[:1800],
    }


def fallback_host_browser_rerank(query: str, items: list[dict[str, Any]], top_k: int = 5) -> dict[str, Any]:
    normalized_query = str(query or "").lower()
    base_terms = _keyword_set(normalized_query)
    rental_intent = any(term in normalized_query for term in ["trọ", "tro", "phòng", "phong", "cho thuê", "thue", "ở ghép", "o ghep", "căn hộ", "can ho"])
    job_intent = any(term in normalized_query for term in ["tuyển", "tuyen", "việc", "viec", "job", "intern", "fresher", "devops"])
    sale_intent = any(term in normalized_query for term in ["mua", "bán", "ban", "thanh lý", "thanh ly", "pass lại", "pass lai"])
    review_intent = any(term in normalized_query for term in ["review", "đánh giá", "danh gia", "trải nghiệm", "trai nghiem"])
    criteria: list[str] = ["đúng ý định chính", "nhiều chi tiết hơn", "ưu tiên bài mới nếu có thời gian"]
    if rental_intent:
        criteria = ["đúng bài cho thuê", "giá/khu vực rõ", "ưu tiên bài mới"]
    elif job_intent:
        criteria = ["đúng bài tuyển", "đúng role/seniority/location", "ưu tiên bài mới"]
    elif sale_intent:
        criteria = ["đúng món cần tìm", "giá/tình trạng rõ", "ưu tiên bài mới"]
    elif review_intent:
        criteria = ["trải nghiệm thực tế", "nhiều chi tiết", "ít xin ý kiến chung chung"]

    role_terms: list[str] = []
    if job_intent:
        if any(term in normalized_query for term in ["devops", "cloud", "platform", "sre", "infra", "sysadmin"]):
            role_terms = ["devops", "cloud", "platform", "sre", "infra", "sysadmin", "kubernetes", "docker", "terraform"]
    seniority_terms = ["intern", "thực tập", "thuc tap", "fresher", "junior", "entry level", "không cần kinh nghiệm", "khong can kinh nghiem"] if job_intent else []
    wants_hcm = any(term in normalized_query for term in ["hcm", "tphcm", "tp hcm", "hồ chí minh", "ho chi minh", "sài gòn", "sai gon", "hcmc"])
    wants_hn = any(term in normalized_query for term in ["hn", "hà nội", "ha noi", "hanoi"])
    hcm_terms = ["hcm", "tphcm", "tp hcm", "ho chi minh", "hồ chí minh", "sai gon", "sài gòn", "hcmc", "quận", "q."]
    hn_terms = ["hn", "ha noi", "hà nội", "hanoi", "gia lâm", "gia lam", "cầu giấy", "cau giay"]

    ranked: list[dict[str, Any]] = []
    for index, item in enumerate(items[:20]):
        if not isinstance(item, dict):
            continue
        blob = "\n".join(
            [
                str(item.get("author") or ""),
                str(item.get("text") or ""),
                str(item.get("excerpt") or ""),
                str(item.get("time_hint") or ""),
                str(item.get("query_used") or ""),
            ]
        )
        hay = blob.lower()
        score = _score_relevance(base_terms, blob) * 2
        reasons: list[str] = []
        role_hit = any(term in hay for term in role_terms) if role_terms else False
        seniority_hit = any(term in hay for term in seniority_terms) if seniority_terms else False
        hcm_hit = any(term in hay for term in hcm_terms)
        hn_hit = any(term in hay for term in hn_terms)
        is_seeker_post = any(marker in hay for marker in [
            "mình đang tìm", "em đang tìm", "anh đang tìm", "cần tìm", "can tim",
            "tìm gấp", "tim gap", "tim phong", "tìm phòng", "tim tro", "tìm trọ",
            "looking for", "need to find", "xin review", "ai review",
        ])
        is_aggregate_post = any(marker in hay for marker in [
            "góc nghề nghiệp", "goc nghe nghiep", "tổng hợp thông tin tuyển dụng", "tong hop thong tin tuyen dung",
            "forum trường", "forum truong", "box việc làm", "box viec lam", "săn job", "san job"
        ])
        if any(marker in hay for marker in [" giờ", "gio", "hôm nay", "hom nay", "phút", "phut", "mới", "moi", "today", "hour", "hours"]):
            score += 1
            reasons.append("có tín hiệu thời gian mới")
        if rental_intent:
            if any(marker in hay for marker in ["cho thuê", "cho thue", "còn phòng", "con phong", "trống phòng", "trong phong", "pass phòng", "pass phong", "ở ghép", "o ghep", "studio"]) and not is_seeker_post:
                score += 4
                reasons.append("đúng bài cho thuê")
            if re.search(r"(\d+[.,]?\d*)\s*(triệu|trieu|k|000)", hay):
                score += 2
                reasons.append("có giá cụ thể")
            if is_seeker_post:
                score -= 5
                reasons.append("bài đi tìm thay vì bài đăng cho thuê")
        elif job_intent:
            if any(marker in hay for marker in ["tuyển", "tuyen", "hiring", "recruit", "apply", "job description", "jd"]) and not is_seeker_post:
                score += 4
                reasons.append("đúng bài tuyển")
            if is_seeker_post or any(marker in hay for marker in ["mình đang tìm việc", "em đang tìm việc", "looking for job", "tìm cơ hội", "tim viec"]):
                score -= 6
                reasons.append("bài ứng viên đi tìm việc")
            if role_terms:
                if role_hit:
                    score += 5
                    reasons.append("đúng role chính")
                else:
                    score -= 4
                    reasons.append("không đúng role chính")
            if seniority_terms:
                if seniority_hit:
                    score += 3
                    reasons.append("đúng seniority")
                else:
                    score -= 2
                    reasons.append("thiếu tín hiệu intern/fresher")
            if wants_hcm:
                if hcm_hit:
                    score += 2
                    reasons.append("khớp khu vực HCM")
                elif hn_hit:
                    score -= 200
                    reasons.append("sai khu vực, nghiêng Hà Nội")
            elif wants_hn:
                if hn_hit:
                    score += 2
                    reasons.append("khớp khu vực Hà Nội")
                elif hcm_hit:
                    score -= 30
                    reasons.append("sai khu vực, nghiêng HCM")
            if is_aggregate_post:
                score -= 30
                reasons.append("bài tổng hợp/forum")
        elif sale_intent:
            if any(marker in hay for marker in ["bán", "ban ", "pass", "thanh lý", "thanh ly", "new", "99%"]) and not is_seeker_post:
                score += 4
                reasons.append("đúng bài bán/pass")
            if is_seeker_post or any(marker in hay for marker in ["cần mua", "tim mua", "tìm mua", "xin pass"]):
                score -= 4
                reasons.append("bài người đi tìm mua")
        elif review_intent:
            if any(marker in hay for marker in ["review", "trải nghiệm", "trai nghiem", "đánh giá", "danh gia"]) and not is_seeker_post:
                score += 4
                reasons.append("có nội dung review")
            if is_seeker_post or any(marker in hay for marker in ["ai review", "xin review", "cho em xin review"]):
                score -= 3
                reasons.append("bài xin review hơn là review thật")
        verdict = "strong" if score >= 7 else ("medium" if score >= 3 else "weak")
        reason = "; ".join(reasons[:3]) or "khớp một phần theo từ khóa và ngữ cảnh"
        ranked.append({"index": index, "score": min(10.0, max(0.0, float(score))), "verdict": verdict, "reason": reason})
    ranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return {
        "ok": bool(ranked),
        "intent": compact_text(query, 240),
        "criteria": criteria,
        "summary": "Fallback rerank dùng overlap + intent heuristics.",
        "ranked_items": ranked[: max(1, min(int(top_k or 5), 10))],
        "raw": "",
    }


def host_browser_rerank(query: str, items: list[dict[str, Any]], top_k: int = 5) -> dict[str, Any]:
    rerank_plan = fallback_host_browser_search_plan(query, 4)
    rerank_profile = rerank_plan.get("profile") if isinstance(rerank_plan, dict) else {}
    role_terms = rerank_profile.get("roleTerms") if isinstance(rerank_profile, dict) and isinstance(rerank_profile.get("roleTerms"), list) else []
    seniority_terms = rerank_profile.get("seniorityTerms") if isinstance(rerank_profile, dict) and isinstance(rerank_profile.get("seniorityTerms"), list) else []
    location_terms = rerank_profile.get("locationTerms") if isinstance(rerank_profile, dict) and isinstance(rerank_profile.get("locationTerms"), list) else []
    location_aliases = rerank_profile.get("locationAliases") if isinstance(rerank_profile, dict) and isinstance(rerank_profile.get("locationAliases"), list) else []
    must_include_groups = rerank_profile.get("mustIncludeGroups") if isinstance(rerank_profile, dict) and isinstance(rerank_profile.get("mustIncludeGroups"), list) else []
    lowered_query = str(query or "").lower()
    wants_hcm = any(term in lowered_query for term in ["hcm", "tphcm", "tp hcm", "hồ chí minh", "ho chi minh", "sài gòn", "sai gon", "hcmc"])
    wants_hn = any(term in lowered_query for term in ["hn", "hà nội", "ha noi", "hanoi"])
    hcm_terms = ["hcm", "tphcm", "tp hcm", "ho chi minh", "hồ chí minh", "sai gon", "sài gòn", "hcmc", "quận", "q."]
    hn_terms = ["hn", "ha noi", "hà nội", "hanoi", "gia lâm", "gia lam", "cầu giấy", "cau giay"]

    trimmed_items: list[dict[str, Any]] = []
    for index, item in enumerate(items[:20]):
        if not isinstance(item, dict):
            continue
        blob = "\n".join(
            [
                str(item.get("author") or ""),
                str(item.get("text") or ""),
                str(item.get("excerpt") or ""),
                str(item.get("time_hint") or ""),
                str(item.get("query_used") or ""),
            ]
        )
        hay = blob.lower()
        role_hit = any(term.lower() in hay for term in role_terms) if role_terms else False
        seniority_hit = any(term.lower() in hay for term in seniority_terms) if seniority_terms else False
        location_hit = any(term.lower() in hay for term in (location_terms or location_aliases)) if (location_terms or location_aliases) else False
        location_mismatch = (wants_hcm and any(term in hay for term in hn_terms) and not any(term in hay for term in hcm_terms)) or (
            wants_hn and any(term in hay for term in hcm_terms) and not any(term in hay for term in hn_terms)
        )
        aggregate_post = any(marker in hay for marker in [
            "góc nghề nghiệp", "goc nghe nghiep", "tổng hợp", "tong hop", "forum", "box việc làm", "box viec lam", "săn job", "san job"
        ])
        seeker_post = any(marker in hay for marker in [
            "mình đang tìm", "em đang tìm", "anh đang tìm", "looking for job", "mình đang tìm việc", "em đang tìm việc", "cần tìm", "can tim"
        ])
        semantic_group_hits: list[str] = []
        if role_hit:
            semantic_group_hits.append("role")
        if seniority_hit:
            semantic_group_hits.append("seniority")
        if location_hit:
            semantic_group_hits.append("location")
        for group in must_include_groups[:4]:
            if isinstance(group, list) and any(str(term).lower() in hay for term in group):
                semantic_group_hits.append("group:" + "/".join(str(term) for term in group[:2]))
        trimmed_items.append(
            {
                "index": index,
                "url": compact_text(str(item.get("url") or ""), 400),
                "author": compact_text(str(item.get("author") or ""), 160),
                "text": compact_text(str(item.get("text") or item.get("excerpt") or ""), 1600),
                "excerpt": compact_text(str(item.get("excerpt") or item.get("text") or ""), 320),
                "time_hint": compact_text(str(item.get("time_hint") or ""), 160),
                "query_used": compact_text(str(item.get("query_used") or ""), 200),
                "score": item.get("score"),
                "reasons": item.get("reasons") if isinstance(item.get("reasons"), list) else [],
                "role_hit": role_hit,
                "seniority_hit": seniority_hit,
                "location_hit": location_hit,
                "location_mismatch": location_mismatch,
                "aggregate_post": aggregate_post,
                "seeker_post": seeker_post,
                "semantic_group_hits": semantic_group_hits[:6],
            }
        )
    if not trimmed_items:
        return {"ok": False, "intent": query, "criteria": [], "summary": "", "ranked_items": []}

    rerank_system_prompt = (
        "Bạn là bộ xếp hạng candidate bài Facebook theo kiểu trợ lý thông minh. "
        "Nhiệm vụ: suy ra ý định thật sự từ query, tự quyết tiêu chí phù hợp với ý định đó, rồi xếp hạng candidate nào đáng đưa lên đầu. "
        "Không được mặc định đây là tìm việc; có thể là tìm trọ, mua bán, review, drama, thông báo, dịch vụ hoặc chủ đề khác. "
        "Phải hiểu match theo cụm nghĩa, không chỉ exact token. Ví dụ intern ~ thực tập ~ fresher ~ junior; "
        "devops ~ cloud ~ platform ~ sre ~ infra; hcm ~ tphcm ~ hồ chí minh ~ sài gòn. "
        "Ưu tiên bài đáp ứng đúng mục tiêu, thông tin cụ thể, độ mới nếu đọc được ngày/giờ, và loại bỏ bài chỉ liên quan lỏng. "
        "Nếu query có role/location cụ thể thì bài sai role hoặc sai location phải bị hạ rất mạnh. "
        "Không được xếp bài sai location rõ ràng hoặc sai role rõ ràng vào top 2 nếu vẫn còn bài khớp tốt hơn. "
        "Bài tổng hợp/forum/repost yếu hơn bài gốc trực tiếp. "
        "Trả JSON thuần, không markdown."
    )
    rerank_user_prompt = (
        "Xếp hạng các candidate Facebook sau theo đúng ý định của người dùng.\n"
        f"User query: {compact_text(query, 500)}\n\n"
        f"Semantic profile: {json.dumps({'role_terms': role_terms, 'seniority_terms': seniority_terms, 'location_terms': location_terms, 'location_aliases': location_aliases, 'must_include_groups': must_include_groups}, ensure_ascii=False)}\n\n"
        "Yêu cầu:\n"
        "- Tự suy ra intent.\n"
        "- Tự nêu 3-6 tiêu chí xếp hạng ngắn gọn.\n"
        "- Chỉ dùng index có trong danh sách.\n"
        "- Ưu tiên bài phù hợp nhất, bài mới hơn nếu có tín hiệu thời gian, và bài có chi tiết thực chất.\n"
        "- Match theo meaning/synonym, không chỉ exact text.\n"
        "- Nếu một bài là sai vai trò/ngược nhu cầu (ví dụ người đi tìm việc thay vì bài tuyển), hạ hạng mạnh.\n"
        "- Nếu query có location cụ thể và candidate có `location_mismatch=true`, hạ rất mạnh; gần như không cho vào top 2.\n"
        "- Nếu query có role cụ thể và candidate thiếu role chính, không cho vào top 2 nếu còn candidate khác khớp role.\n"
        "- Candidate có `aggregate_post=true` hoặc `seeker_post=true` phải đứng dưới bài trực tiếp/gốc.\n\n"
        "- Với mỗi candidate được xếp hạng, hãy viết lại tiêu đề hiển thị ngắn gọn, sạch, tối đa 90 ký tự vào `display_title`.\n"
        "- Với mỗi candidate được xếp hạng, hãy viết lại tóm tắt hiển thị ngắn gọn, sạch, tối đa 180 ký tự vào `display_summary`.\n"
        "- Không copy nguyên chuỗi rác, id dài, markdown thô, ký tự loạn, hoặc text bẩn từ DOM vào display_title/display_summary.\n\n"
        'Trả JSON đúng schema: {"intent":"...","criteria":["..."],"summary":"...","ranked_items":[{"index":0,"score":0-10,"verdict":"strong|medium|weak","reason":"...","display_title":"...","display_summary":"..."}]}\n\n'
        f"Candidates:\n{json.dumps(trimmed_items, ensure_ascii=False)}"
    )
    started = time.perf_counter()
    result = cliproxy_chat(
        [{"role": "user", "content": rerank_user_prompt}],
        compose_system_prompt(rerank_system_prompt),
    )
    trace_event(
        "model_call",
        route="host_browser_rerank",
        ok=bool(result.get("ok")),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        items=len(trimmed_items),
    )
    if not result.get("ok"):
        fallback = fallback_host_browser_rerank(query, trimmed_items, top_k)
        fallback["summary"] = compact_text(str(result.get("summary") or fallback.get("summary") or ""), 600)
        return fallback

    raw = str((result.get("data") or {}).get("content") or "").strip()
    parsed = parse_json_payload(raw)
    ranked_entries = parsed.get("ranked_items") if isinstance(parsed.get("ranked_items"), list) else []
    normalized_ranked: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()
    for entry in ranked_entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(trimmed_items) or index in seen_indexes:
            continue
        seen_indexes.add(index)
        verdict = str(entry.get("verdict") or "medium").strip().lower()
        if verdict not in {"strong", "medium", "weak"}:
            verdict = "medium"
        try:
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            score = 0.0
        normalized_ranked.append(
            {
                "index": index,
                "score": score,
                "verdict": verdict,
                "reason": compact_text(str(entry.get("reason") or ""), 320),
                "display_title": compact_text(str(entry.get("display_title") or ""), 120),
                "display_summary": compact_text(str(entry.get("display_summary") or ""), 240),
            }
        )
    if not normalized_ranked:
        fallback = fallback_host_browser_rerank(query, trimmed_items, top_k)
        fallback["summary"] = compact_text(str(parsed.get("summary") or fallback.get("summary") or ""), 600)
        fallback["raw"] = raw[:2400]
        return fallback
    return {
        "ok": bool(normalized_ranked),
        "intent": compact_text(str(parsed.get("intent") or query), 240),
        "criteria": [compact_text(str(item), 120) for item in (parsed.get("criteria") or [])[:6]],
        "summary": compact_text(str(parsed.get("summary") or ""), 600),
        "ranked_items": normalized_ranked[: max(1, min(int(top_k or 5), 10))],
        "raw": raw[:2400],
    }


def canonicalize_facebook_result_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or "facebook.com" not in raw.lower():
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    path = parsed.path or ""
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_map = {k: v for k, v in query_pairs}
    lowered = raw.lower()
    if "/commerce/listing/" in lowered:
        cleaned_pairs = [(k, v) for k, v in query_pairs if k in {"media_id", "ref"} and v]
        query = urlencode(cleaned_pairs)
        return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", query, ""))
    if "/permalink/" in lowered or "/posts/" in lowered or "story_fbid=" in lowered or "permalink.php" in lowered:
        keep_keys = ("story_fbid", "id", "post_id")
        cleaned_pairs = [(k, query_map[k]) for k in keep_keys if k in query_map and query_map[k]]
        query = urlencode(cleaned_pairs)
        return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", query, ""))
    if "/photo/?" in lowered or "fbid=" in lowered:
        keep_keys = ("fbid", "set", "idorvanity")
        cleaned_pairs = [(k, query_map[k]) for k in keep_keys if k in query_map and query_map[k]]
        if not cleaned_pairs:
            return ""
        query = urlencode(cleaned_pairs)
        return urlunparse((parsed.scheme or "https", parsed.netloc, path or "/photo/", "", query, ""))
    return ""


def build_facebook_research_candidates(query: str, bridge_result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = bridge_result.get("items") if isinstance(bridge_result.get("items"), list) else []
    if not raw_items:
        raw_items = bridge_result.get("top_items") if isinstance(bridge_result.get("top_items"), list) else []
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        raw_url = str(
            item.get("url")
            or item.get("link")
            or item.get("photoOnlyUrl")
            or item.get("photo_only_url")
            or ""
        )
        canonical_url = canonicalize_facebook_result_url(raw_url)
        text = compact_text(str(item.get("text") or ""), 1600)
        image_text = compact_text(str(item.get("image_text") or item.get("imageText") or ""), 1600)
        merged_text = text or image_text
        if not canonical_url or canonical_url in seen_urls or not merged_text:
            continue
        seen_urls.add(canonical_url)
        excerpt = compact_text(merged_text, 320)
        candidates.append(
            {
                "index": index,
                "url": canonical_url,
                "raw_url": raw_url,
                "author": "",
                "text": merged_text,
                "excerpt": excerpt,
                "time_hint": "",
                "query_used": query,
                "score": item.get("score"),
                "reasons": [],
                "source_text": text,
                "source_image_text": image_text,
            }
        )
    return candidates


def build_host_browser_facebook_research_result(payload: HostBrowserFacebookResearchRequest) -> dict[str, Any]:
    data = host_browser_call("/facebook-research", payload.model_dump(), timeout=240)
    bridge_result = data.get("result") if isinstance(data.get("result"), dict) else {}
    candidates = build_facebook_research_candidates(payload.query, bridge_result)
    rerank_error = ""
    rerank: dict[str, Any] = {"ok": False, "summary": "", "criteria": [], "ranked_items": []}
    if candidates:
        try:
            rerank = host_browser_rerank(payload.query, candidates, payload.limit)
        except Exception as exc:
            rerank_error = compact_text(str(exc), 320)
            rerank = {"ok": False, "summary": "", "criteria": [], "ranked_items": []}
    ranked_items = rerank.get("ranked_items") if isinstance(rerank.get("ranked_items"), list) else []
    results: list[dict[str, Any]] = []
    for rank_index, entry in enumerate(ranked_items, start=1):
        if not isinstance(entry, dict):
            continue
        try:
            source_index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if source_index < 0 or source_index >= len(candidates):
            continue
        source = candidates[source_index]
        display_title = compact_text(str(entry.get("display_title") or ""), 120) or compact_text(str(source.get("excerpt") or "").split(".")[0], 120) or f"Bài {rank_index}"
        display_summary = compact_text(str(entry.get("display_summary") or ""), 240) or compact_text(str(source.get("excerpt") or ""), 240)
        results.append(
            {
                "rank": rank_index,
                "author": display_title,
                "summary": display_summary,
                "keep_reason": compact_text(str(entry.get("reason") or ""), 320),
                "time_status": "",
                "url": str(source.get("url") or ""),
                "raw_url": str(source.get("raw_url") or ""),
                "text": str(source.get("text") or ""),
            }
        )
    if not results and candidates:
        for rank_index, source in enumerate(candidates[: max(1, min(int(payload.limit or 5), 10))], start=1):
            results.append(
                {
                    "rank": rank_index,
                    "author": compact_text(str(source.get("excerpt") or "").split(".")[0], 120) or f"Bài {rank_index}",
                    "summary": compact_text(str(source.get("excerpt") or source.get("text") or ""), 240),
                    "keep_reason": "Fallback ordering from bridge results",
                    "time_status": "",
                    "url": str(source.get("url") or ""),
                    "raw_url": str(source.get("raw_url") or ""),
                    "text": str(source.get("text") or ""),
                }
            )
    result_payload = {
        **bridge_result,
        "items": candidates,
        "results": results,
        "summary": {
            "planner_summary": "",
            "rerank_summary": str(rerank.get("summary") or rerank_error or ""),
            "criteria": rerank.get("criteria") if isinstance(rerank.get("criteria"), list) else [],
            "recent_confirmed_count": 0,
            "time_unknown_count": 0,
            "stale_confirmed_count": 0,
        },
    }
    return {"ok": True, "bridge_url": host_browser_bridge_url(), "result": result_payload}


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


def _compact_lines(lines: list[str], limit: int, max_chars: int) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        text = compact_text(str(raw or "").strip(), max_chars)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return "\n".join(out)


def refresh_session_memory(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        return db.default_session_memory(job_id)
    db.upsert_user_preference("last_active_job_id", str(job_id), source="system")
    db.upsert_user_preference("last_permission_mode", str(job.get("permission_mode") or "full_access"), source="system")
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
    open_tasks = _compact_lines([f"- #{a.get('id')} {a.get('title')} ({a.get('kind')})" for a in pending[:8]], 8, 160)
    important_files = _compact_lines([f"- {f.get('name')} ({len(str(f.get('content', '')))} chars)" for f in job.get("focus_files", [])[:12]], 12, 160)
    decisions = _compact_lines(
        [
        f"- {compact_text(m.get('content', ''), 240)}"
        for m in manager_messages[-5:]
        if any(marker in str(m.get("content", "")).lower() for marker in ["đã", "cần", "sẽ", "approve", "action", "plan"])
        ],
        5,
        240,
    )
    raw_last_result = job.get("result") or job.get("error") or (completed[-1].get("result") or completed[-1].get("error") if completed else "") or (manager_messages[-1]["content"] if manager_messages else "")
    if completed and (completed[-1].get("result") or completed[-1].get("error")) and not (job.get("result") or job.get("error")):
        last_result = compact_action_output_for_model(raw_last_result, 1200)
    else:
        last_result = compact_text(raw_last_result, 1200)
    summary = (
        f"Mục tiêu: {current_goal or 'Chưa rõ'}\n"
        f"Trạng thái: {job.get('status')}\n"
        f"Gần đây:\n" + _compact_lines([f"- {line}" for line in recent], 8, 180)
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


def queue_verification_follow_up(job_id: str, verification: dict[str, Any]) -> None:
    job = db.get_job(job_id)
    if not job:
        return
    existing = [a for a in job.get("pending_actions", []) if str(a.get("status") or "") == "pending" and str(a.get("kind") or "") == "note"]
    if any("Review failed verification" in str(a.get("title") or "") for a in existing):
        return
    missing = verification.get("missing_requirements") if isinstance(verification.get("missing_requirements"), list) else []
    tool_details = verification.get("tool_failure_details") if isinstance(verification.get("tool_failure_details"), list) else []
    preview = compact_text(
        "Missing: "
        + (", ".join(str(item) for item in missing[:6]) or "unknown")
        + " | Tool failures: "
        + (", ".join(str(item) for item in tool_details[:6]) or "none"),
        400,
    )
    db.add_pending_action(
        job_id,
        "note",
        "Review failed verification evidence",
        preview,
        {
            "note": preview,
            "kind": "verification_follow_up",
            "missing_requirements": missing,
            "tool_failure_details": tool_details,
        },
    )
    db.add_message(
        job_id,
        "langgraph",
        "Mình đã xếp sẵn một follow-up action để review verification lỗi trước khi retry.",
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
    yield sse_event("done", {"job": hydrate_job_for_ui(db.get_job(job_id) or {})})


def job_event_stream(job_id: str):
    last_seen = ""
    last_ping = time.monotonic()
    yield sse_event("connected", {"job_id": job_id, "ts": time.time()})
    while True:
        job = db.get_job(job_id)
        if not job:
            yield sse_event("error", {"error": "job not found"})
            return
        fingerprint = json.dumps(
            {
                "updated_at": job.get("updated_at", ""),
                "status": job.get("status", ""),
                "message_count": len(job.get("messages", []) or []),
                "pending_actions": [
                    (
                        item.get("id"),
                        item.get("status"),
                        item.get("updated_at"),
                        item.get("result"),
                        item.get("error"),
                    )
                    for item in (job.get("pending_actions") or [])
                ],
                "steps": [
                    (
                        item.get("name"),
                        item.get("status"),
                        item.get("detail"),
                        item.get("finished_at"),
                    )
                    for item in (job.get("steps") or [])
                ],
                "result": job.get("result", ""),
                "error": job.get("error", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if fingerprint != last_seen:
            last_seen = fingerprint
            yield sse_event("job", {"job": hydrate_job_for_ui(job)})
            last_ping = time.monotonic()
        elif time.monotonic() - last_ping >= 15:
            yield sse_event("ping", {"job_id": job_id, "ts": time.time()})
            last_ping = time.monotonic()
        time.sleep(0.25)


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


def host_browser_bridge_url() -> str:
    return os.getenv("HOST_BROWSER_BRIDGE_URL", "http://host.docker.internal:3342").strip().rstrip("/")


HOST_BROWSER_AUTOSTART_LOCK = threading.Lock()
HOST_BROWSER_AUTOSTART_PROCESS: subprocess.Popen[str] | None = None


def _host_browser_default_command(base_url: str) -> list[str] | None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port or 3342
    bind_host = "127.0.0.1"
    tools_dir = APP_DIR.parent.parent / "tools"
    mjs_path = tools_dir / "host_browser_bridge.mjs"
    ps1_path = tools_dir / "host_browser_bridge.ps1"
    node_bin = shutil.which("node")
    pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")

    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return None
    if mjs_path.exists() and node_bin:
        return [node_bin, str(mjs_path)]
    if ps1_path.exists() and pwsh_bin:
        return [pwsh_bin, "-File", str(ps1_path), "-Port", str(port), "-Bind", f"http://{bind_host}"]
    return None


def _host_browser_autostart_command(base_url: str) -> list[str] | None:
    raw = str(os.getenv("HOST_BROWSER_BRIDGE_COMMAND") or "").strip()
    if raw:
        return shlex.split(raw)
    return _host_browser_default_command(base_url)


def _host_browser_runtime_guidance(base_url: str) -> str:
    base = base_url or "http://host.docker.internal:3342"
    command = "node tools/host_browser_bridge.mjs"
    if Path("/.dockerenv").exists():
        return (
            f"Host browser bridge chưa reachable tại {base}. "
            f"Stack hiện chạy trong Docker nên manager không thể tự mở browser bridge trên desktop host. "
            f"Hãy chạy `{command}` trên máy host, hoặc trỏ `HOST_BROWSER_BRIDGE_URL` sang bridge đang chạy sẵn."
        )
    return (
        f"Host browser bridge chưa reachable tại {base}. "
        f"Hãy chạy `{command}` trong repo này, hoặc cấu hình `HOST_BROWSER_BRIDGE_COMMAND` nếu muốn app tự spawn bridge."
    )


def ensure_host_browser_bridge_running(timeout: float = 4.0) -> None:
    base = host_browser_bridge_url()
    if not base:
        raise RuntimeError("HOST_BROWSER_BRIDGE_URL is empty")
    try:
        http_json("GET", f"{base}/health", timeout=max(1, int(timeout)))
        return
    except Exception:
        pass

    command = _host_browser_autostart_command(base)
    if not command:
        raise RuntimeError(_host_browser_runtime_guidance(base))

    global HOST_BROWSER_AUTOSTART_PROCESS
    with HOST_BROWSER_AUTOSTART_LOCK:
        process = HOST_BROWSER_AUTOSTART_PROCESS
        if process is None or process.poll() is not None:
            try:
                HOST_BROWSER_AUTOSTART_PROCESS = subprocess.Popen(
                    command,
                    cwd=str(APP_DIR.parent.parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"{_host_browser_runtime_guidance(base)} Autostart command failed: {exc}"
                ) from exc

    deadline = time.monotonic() + max(1.0, timeout)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            http_json("GET", f"{base}/health", timeout=2)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    detail = f" Last error: {last_error}" if last_error else ""
    raise RuntimeError(f"{_host_browser_runtime_guidance(base)}{detail}")


def host_browser_call(path: str, payload: dict[str, Any] | None = None, method: str = "POST", timeout: int = 30) -> dict[str, Any]:
    base = host_browser_bridge_url()
    if not base:
        raise RuntimeError("HOST_BROWSER_BRIDGE_URL is empty")
    ensure_host_browser_bridge_running(timeout=min(float(timeout), 5.0))
    verb = method.upper()
    use_payload = payload if verb != "GET" else None
    return http_json(verb, f"{base}{path}", payload=use_payload, timeout=timeout)


def _mark_host_worker_transport(result: dict[str, Any], transport: str) -> dict[str, Any]:
    data = result.get("data")
    if isinstance(data, dict) and not str(data.get("transport") or "").strip():
        data["transport"] = transport
    return result


def _try_host_worker_call(path: str, payload: dict[str, Any] | None = None, method: str = "POST", timeout: int = 30) -> tuple[bool, dict[str, Any] | str]:
    try:
        result = host_browser_call(path, payload=payload, method=method, timeout=timeout)
    except Exception as exc:
        return False, str(exc)
    if isinstance(result, dict):
        return True, _mark_host_worker_transport(result, "host_worker")
    return True, {"ok": False, "summary": f"Host worker returned invalid payload for {path}", "data": {"transport": "host_worker"}}


def _resolve_host_path(raw_path: str) -> Path:
    candidate = Path(str(raw_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = (Path("/home/giang") / candidate).resolve()
    else:
        candidate = candidate.resolve()
    for root in HOST_ALLOWED_ROOTS:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    raise ValueError(f"host path is outside allowed roots: {candidate}")


def _check_host_command_policy(command: str) -> tuple[bool, str]:
    raw = str(command or "").strip().lower()
    if not raw:
        return False, "empty host command"
    blocked_patterns = [
        r"\brm\s+-rf\s+/(?:\s|$)",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\buserdel\b",
        r"\bmount\b",
        r"\bumount\b",
    ]
    if any(re.search(pattern, raw) for pattern in blocked_patterns):
        return False, "host policy blocked destructive machine command"
    return True, "host policy allowed command"


def _run_host_operator_command(argv: list[str], timeout: int = 20, cwd: str = "/home/giang") -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "summary": f"Host operator command not available: {argv[0]}",
            "data": {"argv": argv, "cwd": cwd},
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "summary": f"Host operator command timed out: {' '.join(argv)}",
            "data": {"argv": argv, "cwd": cwd},
        }
    return {
        "ok": completed.returncode == 0,
        "summary": f"Host operator command {'completed' if completed.returncode == 0 else 'failed'}: {' '.join(argv)}",
        "data": {
            "argv": argv,
            "cwd": cwd,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    }


def run_host_shell_action(command: str, cwd: str = "", timeout: int = 60) -> dict[str, Any]:
    allowed, reason = _check_host_command_policy(command)
    if not allowed:
        return {"ok": False, "summary": reason, "data": {"command": command, "cwd": cwd}}
    remote_ok, remote_result = _try_host_worker_call(
        "/shell/run",
        {"command": command, "cwd": cwd, "timeout": timeout},
        timeout=timeout,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    target_cwd = str(_resolve_host_path(cwd or "/home/giang"))
    try:
        completed = subprocess.run(
            ["sh", "-lc", command],
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "summary": "Host shell command timed out", "data": {"command": command, "cwd": target_cwd}}
    payload = {
        "command": command,
        "cwd": target_cwd,
        "policy": reason,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "transport": "manager_local_fallback",
        "worker_error": str(remote_result),
    }
    return {
        "ok": completed.returncode == 0,
        "summary": f"Host shell command {'completed' if completed.returncode == 0 else 'failed'}: {command}",
        "data": payload,
    }


def read_host_file(path: str, max_chars: int = 40000) -> dict[str, Any]:
    remote_ok, remote_result = _try_host_worker_call(
        "/files/read",
        {"path": path, "max_chars": max_chars},
        timeout=20,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    target = _resolve_host_path(path)
    if not target.exists():
        return {"ok": False, "summary": f"host file not found: {target}", "data": {"path": str(target)}}
    if not target.is_file():
        return {"ok": False, "summary": f"host path is not a file: {target}", "data": {"path": str(target)}}
    text = target.read_text(encoding="utf-8", errors="replace")
    return {
        "ok": True,
        "summary": f"Read host file: {target}",
        "data": {
            "path": str(target),
            "content": text[: max(1, int(max_chars))],
            "bytes": target.stat().st_size,
            "transport": "manager_local_fallback",
            "worker_error": str(remote_result),
        },
    }


def list_host_files(path: str, limit: int = 200) -> dict[str, Any]:
    remote_ok, remote_result = _try_host_worker_call(
        "/files/list",
        {"path": path, "limit": limit},
        timeout=20,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    target = _resolve_host_path(path)
    if not target.exists():
        return {"ok": False, "summary": f"host path not found: {target}", "data": {"path": str(target)}}
    if not target.is_dir():
        return {"ok": False, "summary": f"host path is not a directory: {target}", "data": {"path": str(target)}}
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[: max(1, int(limit))]:
        entries.append(
            {
                "name": item.name,
                "path": str(item),
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            }
        )
    return {
        "ok": True,
        "summary": f"Listed host directory: {target}",
        "data": {"path": str(target), "entries": entries, "transport": "manager_local_fallback", "worker_error": str(remote_result)},
    }


def write_host_file(path: str, content: str = "", overwrite: bool = True, create_parents: bool = False) -> dict[str, Any]:
    remote_ok, remote_result = _try_host_worker_call(
        "/files/write",
        {"path": path, "content": content, "overwrite": overwrite, "create_parents": create_parents},
        timeout=30,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    target = _resolve_host_path(path)
    if target.exists() and not overwrite:
        return {"ok": False, "summary": f"host file already exists: {target}", "data": {"path": str(target)}}
    parent = target.parent
    if not parent.exists():
        if not create_parents:
            return {"ok": False, "summary": f"host parent directory not found: {parent}", "data": {"path": str(target)}}
        parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content or ""), encoding="utf-8")
    return {
        "ok": True,
        "summary": f"Wrote host file: {target}",
        "data": {
            "path": str(target),
            "bytes": len(str(content or "").encode("utf-8")),
            "transport": "manager_local_fallback",
            "worker_error": str(remote_result),
        },
    }


def move_host_file(src_path: str, dest_path: str, overwrite: bool = False, create_parents: bool = False) -> dict[str, Any]:
    remote_ok, remote_result = _try_host_worker_call(
        "/files/move",
        {
            "src_path": src_path,
            "dest_path": dest_path,
            "overwrite": overwrite,
            "create_parents": create_parents,
        },
        timeout=30,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    src = _resolve_host_path(src_path)
    dest = _resolve_host_path(dest_path)
    if not src.exists():
        return {"ok": False, "summary": f"host source not found: {src}", "data": {"src_path": str(src), "dest_path": str(dest)}}
    if dest.exists() and not overwrite:
        return {"ok": False, "summary": f"host destination already exists: {dest}", "data": {"src_path": str(src), "dest_path": str(dest)}}
    parent = dest.parent
    if not parent.exists():
        if not create_parents:
            return {"ok": False, "summary": f"host destination parent not found: {parent}", "data": {"src_path": str(src), "dest_path": str(dest)}}
        parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and overwrite:
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    shutil.move(str(src), str(dest))
    return {
        "ok": True,
        "summary": f"Moved host path: {src} -> {dest}",
        "data": {
            "src_path": str(src),
            "dest_path": str(dest),
            "transport": "manager_local_fallback",
            "worker_error": str(remote_result),
        },
    }


def delete_host_path(path: str, recursive: bool = False) -> dict[str, Any]:
    remote_ok, remote_result = _try_host_worker_call(
        "/files/delete",
        {"path": path, "recursive": recursive},
        timeout=30,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    target = _resolve_host_path(path)
    if not target.exists():
        return {"ok": False, "summary": f"host path not found: {target}", "data": {"path": str(target)}}
    if target.is_dir():
        if not recursive:
            return {"ok": False, "summary": f"host directory requires recursive delete: {target}", "data": {"path": str(target)}}
        shutil.rmtree(target)
    else:
        target.unlink()
    return {
        "ok": True,
        "summary": f"Deleted host path: {target}",
        "data": {
            "path": str(target),
            "recursive": bool(recursive),
            "transport": "manager_local_fallback",
            "worker_error": str(remote_result),
        },
    }


def _validate_service_name(service: str) -> str:
    name = str(service or "").strip()
    if not name:
        raise ValueError("service name is required")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", name):
        raise ValueError(f"invalid service name: {name}")
    return name


def _validate_container_name(container: str) -> str:
    name = str(container or "").strip()
    if not name:
        raise ValueError("container name is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError(f"invalid container name: {name}")
    return name


def run_host_docker_ps(all_containers: bool = False, limit: int = 50, timeout: int = 20) -> dict[str, Any]:
    remote_ok, remote_result = _try_host_worker_call(
        "/ops/docker-ps",
        {"all_containers": all_containers, "limit": limit, "timeout": timeout},
        timeout=timeout,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    argv = ["docker", "ps"]
    if all_containers:
        argv.append("-a")
    argv.extend(["--format", "{{json .}}"])
    result = _run_host_operator_command(argv, timeout=timeout)
    if not result.get("ok"):
        return result
    stdout = str(((result.get("data") or {}).get("stdout")) or "")
    containers: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        item = line.strip()
        if not item:
            continue
        try:
            containers.append(json.loads(item))
        except json.JSONDecodeError:
            containers.append({"raw": item})
    use_limit = max(1, int(limit))
    result["summary"] = f"Listed Docker containers on host ({min(len(containers), use_limit)} items)"
    result["data"]["containers"] = containers[:use_limit]
    result["data"]["all_containers"] = bool(all_containers)
    result["data"]["limit"] = use_limit
    result["data"]["transport"] = "manager_local_fallback"
    result["data"]["worker_error"] = str(remote_result)
    return result


def run_host_service_status(service: str, lines: int = 40, timeout: int = 20) -> dict[str, Any]:
    try:
        safe_service = _validate_service_name(service)
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {"service": service}}
    remote_ok, remote_result = _try_host_worker_call(
        "/ops/service-status",
        {"service": safe_service, "lines": lines, "timeout": timeout},
        timeout=timeout,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    use_lines = max(1, int(lines))
    argv = ["systemctl", "status", safe_service, "--no-pager", f"--lines={use_lines}"]
    result = _run_host_operator_command(argv, timeout=timeout)
    if result.get("ok"):
        result["summary"] = f"Read host service status: {safe_service}"
        result["data"]["service"] = safe_service
        result["data"]["lines"] = use_lines
        result["data"]["transport"] = "manager_local_fallback"
        result["data"]["worker_error"] = str(remote_result)
    return result


def run_host_service_logs(service: str, lines: int = 80, since: str = "", timeout: int = 20) -> dict[str, Any]:
    try:
        safe_service = _validate_service_name(service)
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {"service": service}}
    remote_ok, remote_result = _try_host_worker_call(
        "/ops/service-logs",
        {"service": safe_service, "lines": lines, "since": since, "timeout": timeout},
        timeout=timeout,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    use_lines = max(1, int(lines))
    argv = ["journalctl", "-u", safe_service, "--no-pager", "-n", str(use_lines)]
    if str(since or "").strip():
        argv.extend(["--since", str(since).strip()])
    result = _run_host_operator_command(argv, timeout=timeout)
    if result.get("ok"):
        result["summary"] = f"Read host service logs: {safe_service}"
        result["data"]["service"] = safe_service
        result["data"]["lines"] = use_lines
        result["data"]["since"] = str(since or "").strip()
        result["data"]["transport"] = "manager_local_fallback"
        result["data"]["worker_error"] = str(remote_result)
    return result


def run_host_docker_logs(container: str, lines: int = 80, timeout: int = 20) -> dict[str, Any]:
    try:
        safe_container = _validate_container_name(container)
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {"container": container}}
    remote_ok, remote_result = _try_host_worker_call(
        "/ops/docker-logs",
        {"container": safe_container, "lines": lines, "timeout": timeout},
        timeout=timeout,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    use_lines = max(1, int(lines))
    argv = ["docker", "logs", "--tail", str(use_lines), safe_container]
    result = _run_host_operator_command(argv, timeout=timeout)
    if result.get("ok"):
        result["summary"] = f"Read host container logs: {safe_container}"
        result["data"]["container"] = safe_container
        result["data"]["lines"] = use_lines
        result["data"]["transport"] = "manager_local_fallback"
        result["data"]["worker_error"] = str(remote_result)
    return result


def run_host_docker_restart(container: str, timeout: int = 30) -> dict[str, Any]:
    try:
        safe_container = _validate_container_name(container)
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {"container": container}}
    remote_ok, remote_result = _try_host_worker_call(
        "/ops/docker-restart",
        {"container": safe_container, "timeout": timeout},
        timeout=timeout,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    argv = ["docker", "restart", safe_container]
    result = _run_host_operator_command(argv, timeout=timeout)
    if result.get("ok"):
        result["summary"] = f"Restarted Docker container on host: {safe_container}"
        result["data"]["container"] = safe_container
        result["data"]["transport"] = "manager_local_fallback"
        result["data"]["worker_error"] = str(remote_result)
    return result


def run_host_service_restart(service: str, timeout: int = 30) -> dict[str, Any]:
    try:
        safe_service = _validate_service_name(service)
    except ValueError as exc:
        return {"ok": False, "summary": str(exc), "data": {"service": service}}
    remote_ok, remote_result = _try_host_worker_call(
        "/ops/service-restart",
        {"service": safe_service, "timeout": timeout},
        timeout=timeout,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    argv = ["systemctl", "restart", safe_service]
    result = _run_host_operator_command(argv, timeout=timeout)
    if result.get("ok"):
        result["summary"] = f"Restarted host service: {safe_service}"
        result["data"]["service"] = safe_service
        result["data"]["transport"] = "manager_local_fallback"
        result["data"]["worker_error"] = str(remote_result)
    return result


def run_host_container_recovery(container: str, logs_lines: int = 80, timeout: int = 45) -> dict[str, Any]:
    before = run_host_docker_ps(all_containers=True, limit=200, timeout=timeout)
    restart = run_host_docker_restart(container, timeout=timeout)
    after = run_host_docker_ps(all_containers=True, limit=200, timeout=timeout)
    logs = run_host_docker_logs(container, lines=logs_lines, timeout=timeout)
    ok = bool(restart.get("ok")) and bool(after.get("ok"))
    return {
        "ok": ok,
        "summary": f"Host container recovery {'completed' if ok else 'failed'}: {container}",
        "data": {
            "container": str(container or "").strip(),
            "before": (before.get("data") or {}),
            "restart": (restart.get("data") or {}),
            "after": (after.get("data") or {}),
            "logs": (logs.get("data") or {}),
        },
    }


def run_host_service_recovery(service: str, status_lines: int = 30, logs_lines: int = 80, timeout: int = 45) -> dict[str, Any]:
    before = run_host_service_status(service, lines=status_lines, timeout=timeout)
    restart = run_host_service_restart(service, timeout=timeout)
    after = run_host_service_status(service, lines=status_lines, timeout=timeout)
    logs = run_host_service_logs(service, lines=logs_lines, timeout=timeout)
    ok = bool(restart.get("ok")) and bool(after.get("ok"))
    return {
        "ok": ok,
        "summary": f"Host service recovery {'completed' if ok else 'failed'}: {service}",
        "data": {
            "service": str(service or "").strip(),
            "before": (before.get("data") or {}),
            "restart": (restart.get("data") or {}),
            "after": (after.get("data") or {}),
            "logs": (logs.get("data") or {}),
        },
    }


def run_host_process_list(limit: int = 200, query: str = "") -> dict[str, Any]:
    encoded_query = quote(str(query or ""))
    remote_ok, remote_result = _try_host_worker_call(
        f"/processes?limit={max(1, int(limit))}&query={encoded_query}",
        method="GET",
        timeout=20,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    return {
        "ok": False,
        "summary": "Host process listing requires host worker",
        "data": {"limit": max(1, int(limit)), "query": query, "transport": "manager_local_unavailable", "worker_error": str(remote_result)},
    }


def run_host_process_signal(pid: int, signal_name: str = "TERM") -> dict[str, Any]:
    safe_signal = str(signal_name or "TERM").strip().upper()
    remote_ok, remote_result = _try_host_worker_call(
        "/processes/signal",
        {"pid": int(pid), "signal_name": safe_signal},
        timeout=20,
    )
    if remote_ok:
        return remote_result if isinstance(remote_result, dict) else {"ok": False, "summary": str(remote_result), "data": {"transport": "host_worker"}}
    if int(pid) <= 0:
        return {"ok": False, "summary": f"invalid pid: {pid}", "data": {"pid": int(pid), "signal_name": safe_signal}}
    signal_value = getattr(signal, f"SIG{safe_signal}", None)
    if signal_value is None:
        return {"ok": False, "summary": f"unsupported signal: {safe_signal}", "data": {"pid": int(pid), "signal_name": safe_signal}}
    try:
        os.kill(int(pid), signal_value)
    except ProcessLookupError:
        return {"ok": False, "summary": f"host process not found: pid={pid}", "data": {"pid": int(pid), "signal_name": safe_signal}}
    except PermissionError:
        return {"ok": False, "summary": f"permission denied signaling pid={pid}", "data": {"pid": int(pid), "signal_name": safe_signal}}
    return {
        "ok": True,
        "summary": f"Signaled host process pid={pid} with {safe_signal}",
        "data": {
            "pid": int(pid),
            "signal_name": safe_signal,
            "transport": "manager_local_fallback",
            "worker_error": str(remote_result),
        },
    }


def run_host_process_recovery(pid: int, signal_name: str = "TERM", query: str = "") -> dict[str, Any]:
    safe_query = str(query or "").strip() or str(int(pid))
    before = run_host_process_list(limit=200, query=safe_query)
    signal_result = run_host_process_signal(pid=int(pid), signal_name=signal_name)
    after = run_host_process_list(limit=200, query=safe_query)
    ok = bool(signal_result.get("ok"))
    return {
        "ok": ok,
        "summary": f"Host process recovery {'completed' if ok else 'failed'}: pid={int(pid)}",
        "data": {
            "pid": int(pid),
            "signal_name": str(signal_name or "TERM").strip().upper(),
            "query": safe_query,
            "before": (before.get("data") or {}),
            "signal": (signal_result.get("data") or {}),
            "after": (after.get("data") or {}),
        },
    }


def queue_due_recurring_tasks() -> list[dict]:
    now = datetime.now(timezone.utc)
    due = db.list_due_recurring_tasks(now.isoformat())
    queued: list[dict] = []
    for task in due:
        job_id = "auto_" + secrets.token_hex(5)
        job = db.create_chat_session(job_id, title=f"Recurring: {task['name']}", permission_mode="full_access")
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
        (
            "browser",
            "Focus browser hiện có nếu đang mở rồi tạo tab mới; nếu chưa có thì tự mở browser host",
            "host_browser_open_current",
            {"url": "{{input}}", "browser": "brave"},
        ),
        (
            "facebook",
            "Focus Brave hiện có rồi mở Facebook bằng session local; nếu chưa có thì tự mở",
            "host_browser_open_current",
            {"url": "https://www.facebook.com/", "browser": "brave"},
        ),
        (
            "facebook-research",
            "Research bài viết Facebook bằng browser debug visible với profile persistent",
            "host_browser_facebook_research",
            {"query": "{{input}}", "browser": "brave", "port": 9222, "limit": 8, "scroll_rounds": 4},
        ),
        (
            "hostshot",
            "Chụp màn hình host để xác nhận browser/UI đang hiển thị gì",
            "host_browser_screenshot",
            {"name": "host_capture_{{job_id}}.png", "full_screen": False},
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


@app.get("/api/host-browser/health")
def host_browser_health():
    bridge = host_browser_bridge_url()
    try:
        data = host_browser_call("/health", method="GET")
        return {"ok": True, "bridge_url": bridge, "bridge": data}
    except Exception as exc:
        return {"ok": False, "bridge_url": bridge, "error": str(exc)}


@app.get("/api/host-worker/health")
def host_worker_health():
    bridge = host_browser_bridge_url()
    try:
        data = host_browser_call("/health", method="GET")
        return {"ok": True, "worker_url": bridge, "worker": data}
    except Exception as exc:
        return {"ok": False, "worker_url": bridge, "error": str(exc)}


@app.post("/api/host-shell/run")
def host_shell_run(payload: HostShellRunRequest):
    result = run_host_shell_action(payload.command, cwd=payload.cwd, timeout=payload.timeout)
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-files/read")
def host_files_read(payload: HostFileReadRequest):
    result = read_host_file(payload.path, max_chars=payload.max_chars)
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-files/list")
def host_files_list(payload: HostFileListRequest):
    result = list_host_files(payload.path, limit=payload.limit)
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-files/write")
def host_files_write(payload: HostFileWriteRequest):
    result = write_host_file(
        payload.path,
        content=payload.content,
        overwrite=payload.overwrite,
        create_parents=payload.create_parents,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-files/move")
def host_files_move(payload: HostFileMoveRequest):
    result = move_host_file(
        payload.src_path,
        payload.dest_path,
        overwrite=payload.overwrite,
        create_parents=payload.create_parents,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-files/delete")
def host_files_delete(payload: HostFileDeleteRequest):
    result = delete_host_path(
        payload.path,
        recursive=payload.recursive,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-worker/processes")
def host_worker_processes(payload: HostProcessListRequest):
    result = run_host_process_list(limit=payload.limit, query=payload.query)
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-worker/process-signal")
def host_worker_process_signal(payload: HostProcessSignalRequest):
    result = run_host_process_signal(pid=payload.pid, signal_name=payload.signal_name)
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-worker/process-recovery")
def host_worker_process_recovery(payload: HostProcessRecoveryRequest):
    result = run_host_process_recovery(pid=payload.pid, signal_name=payload.signal_name, query=payload.query)
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-ops/docker-ps")
def host_ops_docker_ps(payload: HostDockerPsRequest):
    result = run_host_docker_ps(
        all_containers=payload.all_containers,
        limit=payload.limit,
        timeout=payload.timeout,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-ops/service-status")
def host_ops_service_status(payload: HostServiceStatusRequest):
    result = run_host_service_status(
        payload.service,
        lines=payload.lines,
        timeout=payload.timeout,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-ops/service-logs")
def host_ops_service_logs(payload: HostServiceLogsRequest):
    result = run_host_service_logs(
        payload.service,
        lines=payload.lines,
        since=payload.since,
        timeout=payload.timeout,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-ops/docker-restart")
def host_ops_docker_restart(payload: HostDockerRestartRequest):
    result = run_host_docker_restart(
        payload.container,
        timeout=payload.timeout,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-ops/service-restart")
def host_ops_service_restart(payload: HostServiceRestartRequest):
    result = run_host_service_restart(
        payload.service,
        timeout=payload.timeout,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-ops/container-recovery")
def host_ops_container_recovery(payload: HostContainerRecoveryRequest):
    result = run_host_container_recovery(
        payload.container,
        logs_lines=payload.logs_lines,
        timeout=payload.timeout,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/host-ops/service-recovery")
def host_ops_service_recovery(payload: HostServiceRecoveryRequest):
    result = run_host_service_recovery(
        payload.service,
        status_lines=payload.status_lines,
        logs_lines=payload.logs_lines,
        timeout=payload.timeout,
    )
    return {"ok": bool(result.get("ok")), **result}


@app.get("/api/host-browser/browsers")
def host_browser_browsers():
    try:
        data = host_browser_call("/browsers", method="GET")
        return {"ok": True, "bridge_url": host_browser_bridge_url(), **data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/open-url")
def host_browser_open(payload: HostBrowserOpenRequest):
    try:
        data = host_browser_call("/open-url", payload.model_dump())
        return {"ok": True, "bridge_url": host_browser_bridge_url(), **data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/open-current")
def host_browser_open_current(payload: HostBrowserOpenCurrentRequest):
    try:
        data = host_browser_call("/open-current", payload.model_dump())
        return {"ok": True, "bridge_url": host_browser_bridge_url(), **data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/launch-debug")
def host_browser_launch(payload: HostBrowserLaunchRequest):
    try:
        data = host_browser_call("/launch-debug", payload.model_dump())
        return {"ok": True, "bridge_url": host_browser_bridge_url(), **data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/facebook-research")
def host_browser_facebook_research(payload: HostBrowserFacebookResearchRequest):
    try:
        return build_host_browser_facebook_research_result(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/facebook-search-plan")
def host_browser_facebook_search_plan(payload: HostBrowserSearchPlanRequest):
    data = host_browser_search_plan(payload.query, payload.max_queries)
    return {
        "ok": bool(data.get("ok")),
        "summary": data.get("summary") or "Facebook search plan ready",
        "data": data,
    }


@app.post("/api/host-browser/facebook-rerank")
def host_browser_facebook_rerank(payload: HostBrowserRerankRequest):
    data = host_browser_rerank(payload.query, payload.items, payload.top_k)
    return {
        "ok": bool(data.get("ok")),
        "summary": data.get("summary") or ("Facebook candidates reranked" if data.get("ok") else "Facebook rerank unavailable"),
        "data": data,
    }


@app.post("/api/host-browser/screenshot")
def host_browser_screenshot(payload: HostBrowserScreenshotRequest):
    try:
        data = host_browser_call("/screenshot", payload.model_dump())
        return {"ok": True, "bridge_url": host_browser_bridge_url(), **data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/mouse-move")
def host_browser_mouse_move(payload: HostBrowserMouseRequest):
    try:
        data = host_browser_call("/mouse-move", payload.model_dump())
        return {"ok": True, "bridge_url": host_browser_bridge_url(), **data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/mouse-click")
def host_browser_mouse_click(payload: HostBrowserMouseRequest):
    try:
        data = host_browser_call("/mouse-click", payload.model_dump())
        return {"ok": True, "bridge_url": host_browser_bridge_url(), **data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/host-browser/ocr-extract")
def host_browser_ocr_extract(payload: HostBrowserOcrExtractRequest):
    system_prompt = (
        "Bạn là OCR/vision extractor cho Facebook research. "
        "Chỉ đọc phần bài viết hoặc poster tuyển dụng đang hiển thị trong ảnh. "
        "Ưu tiên headline, role, location, nội dung chính trong poster hoặc phần thân bài. "
        "Bỏ phần UI nhiễu nếu có thể. "
        'Trả JSON thuần {"text":"...","confidence":"high|medium|low","reason":"...","needs_more_context":false}.'
    )
    user_prompt = (
        "Trích xuất nội dung chữ nhìn thấy từ ảnh chụp bài Facebook này.\n"
        f"- user query: {compact_text(payload.query, 240)}\n"
        f"- page title: {compact_text(payload.title, 200)}\n"
        f"- page url: {compact_text(payload.url, 300)}\n"
        f"- DOM excerpt hiện có (có thể thiếu): {compact_text(payload.excerpt, 500)}\n"
        "Nếu đây là poster tuyển dụng, hãy ưu tiên đọc chính xác các cụm chữ lớn như job title, role, location, công nghệ.\n"
        "Nếu ảnh không đủ rõ, vẫn trả phần đọc được tốt nhất, không suy diễn."
    )
    result = cliproxy_vision(
        prompt=user_prompt,
        image_base64=payload.image_base64,
        mime_type=payload.mime_type or "image/png",
        system_prompt=compose_system_prompt(system_prompt),
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "summary": result.get("summary") or "OCR extract failed",
            "data": {"text": "", "confidence": "low", "reason": "vision-call-failed"},
        }
    content = str((result.get("data") or {}).get("content") or "").strip()
    parsed = parse_json_payload(content)
    text = compact_text(str(parsed.get("text") or content), 8000).strip()
    confidence = str(parsed.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    return {
        "ok": bool(text),
        "summary": "OCR extracted text" if text else "OCR returned empty text",
        "data": {
            "text": text,
            "confidence": confidence,
            "reason": str(parsed.get("reason") or "").strip(),
            "needs_more_context": bool(parsed.get("needs_more_context")),
            "raw": content[:2000],
        },
    }


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


@app.get("/api/projects/assistant-view")
def list_project_assistant_view():
    jobs: list[dict[str, Any]] = []
    for item in db.list_jobs():
        detail = db.get_job(str(item.get("id") or "")) or item
        jobs.append(hydrate_job_for_ui(detail))
    entries = [build_project_assistant_entry(project, jobs) for project in db.list_projects(include_inactive=False)]
    entries.sort(
        key=lambda item: (
            int(item.get("urgent_jobs_count") or 0),
            int(item.get("top_attention_score") or 0),
            str(((item.get("project") or {}).get("updated_at")) or ""),
        ),
        reverse=True,
    )
    return {"projects": entries}


@app.get("/api/projects/{project_id}/assistant-view")
def get_project_assistant_view(project_id: int):
    project = next((item for item in db.list_projects() if int(item.get("id") or 0) == project_id), None)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    jobs: list[dict[str, Any]] = []
    for item in db.list_jobs():
        detail = db.get_job(str(item.get("id") or "")) or item
        jobs.append(hydrate_job_for_ui(detail))
    return build_project_assistant_entry(project, jobs)


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


@app.get("/api/cliproxy/models")
def cliproxy_models():
    base_url = os.getenv("CLIPROXY_BASE_URL", "http://host.docker.internal:8317").rstrip("/")
    api_key = os.getenv("CLIPROXY_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="CLIPROXY_API_KEY is not configured")
    try:
        data = http_json("GET", f"{base_url}/v1/models", token=api_key, timeout=20)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"CLIProxy models lookup failed: {exc}") from exc
    return data


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


@app.get("/api/inbox")
def get_inbox():
    entries: list[dict[str, Any]] = []
    for job in db.list_jobs():
        detail = db.get_job(str(job.get("id") or "")) or job
        entries.append(build_inbox_entry(detail))
    entries.sort(
        key=lambda item: (
            int(item.get("attention_score") or 0),
            str(item.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return {"inbox": entries[:50]}


@app.get("/api/chat")
def get_main_chat():
    job = db.get_or_create_chat()
    return job_response(job, fallback=job)


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
    return job_response(job, fallback=job)


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
        return job_response(job_id)
    db.replace_plan(
        job_id,
        decision["plan"],
        "Mình đã có đủ thông tin để lập plan cụ thể. Bạn xem lại rồi approve nếu ổn.",
        request=planning_request,
    )
    refresh_session_memory(job_id)
    return job_response(job_id)


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
    return job_response(job_id)


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
        return job_response(job_id)

    db.replace_plan(
        job_id,
        decision["plan"],
        "Mình đã xác nhận lại plan theo bản bạn sửa. Bạn review, nếu ổn thì approve và chạy end-to-end.",
        request=job.get("request"),
    )
    refresh_session_memory(job_id)
    return job_response(job_id)


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
        return job_response(job, fallback=job)
    graph = build_graph()
    state = graph.invoke({"request": payload.request})
    job = db.create_job(job_id, title, payload.request, state.get("plan", []), permission_mode=permission_mode)
    refresh_session_memory(job_id)
    return job_response(job, fallback=job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": hydrate_job_for_ui(job)}


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
    return {"session_memory": memory, **job_response(job_id)}


@app.post("/api/jobs/{job_id}/focus-file")
def add_focus_file(job_id: str, payload: FocusFileRequest):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    content = payload.content[:MAX_FOCUS_FILE_CHARS]
    db.add_focus_file(job_id, payload.name[:240], content)
    db.add_message(job_id, "langgraph", f"Đã thêm file focus: {payload.name}. Runtime sẽ ưu tiên nội dung file này khi lập plan/chạy.")
    refresh_session_memory(job_id)
    return job_response(job_id)


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
    return job_response(job_id)


@app.patch("/api/jobs/{job_id}/permission-mode")
def update_permission_mode(job_id: str, payload: PermissionModeRequest):
    mode = validate_permission_mode(payload.permission_mode)
    if not db.update_permission_mode(job_id, mode):
        raise HTTPException(status_code=404, detail="job not found")
    return job_response(job_id)


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
        return job_response(job, fallback=job)
    if job["status"] == "chat":
        return {"job": handle_agent_message(job_id, payload.content)}
    if job["status"] == "needs_input":
        return plan_from_chat(job_id, payload)
    if job["status"] not in {"draft", "planned"}:
        raise HTTPException(status_code=409, detail=f"cannot discuss from {job['status']}")
    db.add_message(job_id, "user", payload.content)
    db.add_message(job_id, "langgraph", "Đã ghi nhận. Bạn có thể regenerate plan hoặc approve plan hiện tại.")
    return job_response(job_id)


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
    return job_response(action["job_id"], fallback=job)


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
    return job_response(action["job_id"])


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


@app.get("/api/jobs/{job_id}/events")
def stream_job_events(job_id: str):
    if not db.get_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return StreamingResponse(
        job_event_stream(job_id),
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
        return job_response(job_id)
    db.replace_plan(job_id, decision["plan"], "Đã regenerate plan cụ thể từ discussion mới.", request=planning_request)
    refresh_session_memory(job_id)
    return job_response(job_id)


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] not in {"planned", "draft"}:
        raise HTTPException(status_code=409, detail=f"cannot approve from {job['status']}")
    db.approve_job(job_id)
    refresh_session_memory(job_id)
    return job_response(job_id)


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
    return job_response(job_id)


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
            job.get("permission_mode", "full_access"),
            [
                {"name": str(item.get("name", "")), "content": str(item.get("content", ""))}
                for item in job.get("focus_files", [])
            ],
        )
        result = state.get("result", "LangGraph native backend finished without a report.")
        chat_result = sanitize_manager_content(str(result), "Job đã chạy xong nhưng report trả về dữ liệu kỹ thuật. Chi tiết nằm trong Result/Logs.")
        verification = state.get("verification", {})
        db.upsert_job_verification(job_id, verification)
        verify_ok = bool(verification.get("ok"))
        stored_result = maybe_store_job_result_artifact(job_id, str(result), is_error=not verify_ok)
        if verification.get("ok"):
            db.update_step(job_id, "report", "done", "Result saved; verify OK")
            db.update_job_status(job_id, "done", result=stored_result)
            db.add_message(job_id, "langgraph", chat_result)
            trace_event(
                "job_run_finish",
                job_id=job_id,
                ok=True,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                artifact=stored_result.lstrip().startswith("{") and '"artifact": true' in stored_result.lower(),
            )
        else:
            tool_failure_details = verification.get("tool_failure_details") if isinstance(verification.get("tool_failure_details"), list) else []
            detail = ", ".join(str(item) for item in tool_failure_details[:6])
            error = "Native runtime finished but verification did not pass"
            if detail:
                error += f" [{detail}]"
            db.update_step(job_id, "report", "failed", error)
            db.update_job_status(job_id, "failed", result=stored_result, error=error)
            db.add_message(job_id, "langgraph", chat_result)
            queue_verification_follow_up(job_id, verification)
            trace_event(
                "job_run_finish",
                job_id=job_id,
                ok=False,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                error=error,
                error_class="verification_failed",
                artifact=stored_result.lstrip().startswith("{") and '"artifact": true' in stored_result.lower(),
            )
    except Exception as exc:
        error_text = str(exc)
        db.update_step(job_id, "run_error", "failed", str(exc))
        db.update_job_status(job_id, "failed", error=error_text)
        db.upsert_job_verification(job_id, {"ok": False, "error_class": detect_error_class(error_text), "exception": compact_text(error_text, 600)})
        db.add_message(job_id, "langgraph", sanitize_manager_content(f"Job lỗi: {exc}", "Job lỗi khi chạy. Chi tiết kỹ thuật nằm trong Logs."))
        trace_event(
            "job_run_finish",
            job_id=job_id,
            ok=False,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            error=compact_text(error_text, 600),
            error_class=detect_error_class(error_text),
        )
    refresh_session_memory(job_id)
    return {"job": hydrate_job_for_ui(db.get_job(job_id) or {})}
