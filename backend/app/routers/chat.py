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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.chat.agent import ChatDeps, get_chat_deps, run_chat
from app.database import get_db
from app.deps import Principal, get_current_principal
from app.models import ChatMessage, ChatRole, ChatSession
from app.schemas import ChatMessageCreate, ChatMessageOut, ChatSessionOut
from app.scoping import org_scoped_select

router = APIRouter(prefix="/chat", tags=["chat"])


def _get_session(db: Session, org_id: uuid.UUID, session_id: uuid.UUID) -> ChatSession:
    session = db.execute(
        org_scoped_select(ChatSession, org_id).where(ChatSession.id == session_id)
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat session not found")
    return session


@router.post("/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatSession:
    session = ChatSession(org_id=principal.org_id, user_id=principal.user_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[ChatSession]:
    stmt = org_scoped_select(ChatSession, principal.org_id).order_by(ChatSession.created_at.desc())
    return list(db.execute(stmt).scalars())


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def list_messages(
    session_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[ChatMessage]:
    _get_session(db, principal.org_id, session_id)
    stmt = org_scoped_select(ChatMessage, principal.org_id).where(
        ChatMessage.session_id == session_id
    ).order_by(ChatMessage.created_at)
    return list(db.execute(stmt).scalars())


@router.post("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def post_message(
    session_id: uuid.UUID,
    body: ChatMessageCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
    chat_deps: ChatDeps = Depends(get_chat_deps),
) -> list[ChatMessage]:
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
        content=body.content,
        citations=[],
    )
    db.add(user_message)
    db.flush()

    result = run_chat(db, org_id=principal.org_id, question=body.content, deps=chat_deps)

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
    return [user_message, assistant_message]
