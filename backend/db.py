from pathlib import Path

from sqlalchemy import JSON, ForeignKey, Integer, LargeBinary, String, Text, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from backend.config import ROOT, Settings


class Base(DeclarativeBase):
    pass


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(4), index=True)
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    reextractions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    intake: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    approval: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DraftPhoto(Base):
    __tablename__ = "draft_photos"

    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    original: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    masked: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id"), index=True)
    revision: Mapped[int]
    event: Mapped[dict] = mapped_column(JSON)


class Notice(Base):
    __tablename__ = "public_notices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(4), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    search_text: Mapped[str] = mapped_column(Text)
    found_at: Mapped[str] = mapped_column(String(40))
    published_at: Mapped[str] = mapped_column(String(40), index=True)
    public: Mapped[dict] = mapped_column(JSON)
    photo_count: Mapped[int]
    quantity: Mapped[int]


class NoticePhoto(Base):
    __tablename__ = "public_notice_photos"

    notice_id: Mapped[str] = mapped_column(ForeignKey("public_notices.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)


def database(settings: Settings):
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        path = ROOT / Path(url.database)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(path))
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize(engine):
    Base.metadata.create_all(engine)
