"""RAG chat endpoints (issue #11).

A ChatSession holds a running message history for one Organization; each
`POST .../messages` runs the read-only chat agent (app/chat/agent.py) and
persists both the user's question and the agent's cited answer as
ChatMessages. Every route requires only `get_current_principal` (any role)
-- per CONTEXT.md, OrgMembership, role governs what a member can *do*, not
what they can *see*, and chat is read-only end to end so there's nothing to
gate by role the way owner/admin-only audit-log access is.
"""

import uuid

from litestar import Router, get, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException
from litestar.params import FromPath, JSONBody
from litestar.status_codes import HTTP_200_OK, HTTP_404_NOT_FOUND
from sqlalchemy.orm import Session

from app.auth import Principal
from app.chat.agent import ChatDeps, run_chat
from app.models import ChatMessage, ChatRole, ChatSession
from app.schemas import ChatMessageCreate, ChatMessageOut, ChatSessionOut
from app.scoping import org_scoped_select


def _get_session(db: Session, org_id: uuid.UUID, session_id: uuid.UUID) -> ChatSession:
    session = db.execute(
        org_scoped_select(ChatSession, org_id).where(ChatSession.id == session_id)
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Chat session not found")
    return session


@post("/sessions", sync_to_thread=True)
def create_session(
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> ChatSessionOut:
    session = ChatSession(org_id=principal.org_id, user_id=principal.user_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return ChatSessionOut.model_validate(session)


@get("/sessions", sync_to_thread=True)
def list_sessions(
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> list[ChatSessionOut]:
    stmt = org_scoped_select(ChatSession, principal.org_id).order_by(ChatSession.created_at.desc())
    return [ChatSessionOut.model_validate(chat_session) for chat_session in db.execute(stmt).scalars()]


@get("/sessions/{session_id:uuid}/messages", sync_to_thread=True)
def list_messages(
    session_id: FromPath[uuid.UUID],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
) -> list[ChatMessageOut]:
    _get_session(db, principal.org_id, session_id)
    stmt = org_scoped_select(ChatMessage, principal.org_id).where(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.created_at)
    return [ChatMessageOut.model_validate(message) for message in db.execute(stmt).scalars()]


@post("/sessions/{session_id:uuid}/messages", status_code=HTTP_200_OK, sync_to_thread=True)
def post_message(
    session_id: FromPath[uuid.UUID],
    data: JSONBody[ChatMessageCreate],
    principal: NamedDependency[Principal],
    db: NamedDependency[Session],
    chat_deps: NamedDependency[ChatDeps],
) -> list[ChatMessageOut]:
    """Ask the chat agent one question in an existing session.

    Persists the user's message, runs the read-only agent scoped to
    `principal.org_id` (app/chat/agent.py -- the same org_id an attacker
    could never substitute since it comes from the JWT, not the request
    body), and persists the assistant's answer with its citations. Returns
    both new messages so the frontend can append them without a second
    fetch (issue #11, AC6).
    """
    _get_session(db, principal.org_id, session_id)

    user_message = ChatMessage(
        org_id=principal.org_id,
        session_id=session_id,
        role=ChatRole.user,
        content=data.content,
        citations=[],
    )
    db.add(user_message)
    db.flush()

    result = run_chat(db, org_id=principal.org_id, question=data.content, deps=chat_deps)

    assistant_message = ChatMessage(
        org_id=principal.org_id,
        session_id=session_id,
        role=ChatRole.assistant,
        content=result.answer,
        citations=[citation.to_json() for citation in result.citations],
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return [ChatMessageOut.model_validate(user_message), ChatMessageOut.model_validate(assistant_message)]


router = Router(
    path="/chat",
    route_handlers=[create_session, list_sessions, list_messages, post_message],
    tags=["chat"],
)
