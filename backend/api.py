from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from backend.config import MAX_IMAGE_BYTES, Settings
from backend.categories import category_pair, taxonomy
from backend.db import AgentLog, Draft, DraftPhoto, Notice, NoticePhoto, database, initialize
from backend.images import normalize
from backend.item_schemas import classify, select_schema
from backend.models import MockModels, OllamaModels
from backend.privacy import POLICY, redact
from backend.schemas import Approval, Correction, Input, ManualMask, Revision, SchemaSelection
from backend.service import RegistrationService
from backend.staff_session import COOKIE, StaffSession
from backend.workflow import Workflow


def notice_response(notice):
    public_keys = {*POLICY["public_fields"], "major_category_code", "major_category_name",
                   "minor_category_code", "minor_category_name"}
    return {
        "id": notice.id, "mode": notice.mode, "found_at": notice.found_at,
        "published_at": notice.published_at, "photo_count": len(notice.public.get("_photo_certificates", {})),
        **{key: redact(value) if key in POLICY["public_fields"] else value
           for key, value in notice.public.items() if key in public_keys},
        "quantity": notice.quantity,
    }


class StaffLogin(BaseModel):
    key: str = Field(min_length=1, max_length=512)


def create_app(settings=None, models=None):
    settings = settings or Settings.from_env()
    engine, sessions = database(settings)
    if models is None:
        models = MockModels() if settings.mode == "mock" else OllamaModels(settings)
    service = RegistrationService(settings, sessions, Workflow(settings, models))
    access = StaffSession(settings.staff_token)

    @asynccontextmanager
    async def lifespan(_):
        initialize(engine)
        try:
            yield
        finally:
            engine.dispose()

    api = FastAPI(title="습득물 공고 PoC", version="0.1.0", lifespan=lifespan)

    def staff(request: Request, authorization: Annotated[str, Header()] = ""):
        if authorization.startswith("Bearer ") and access.check_key(authorization[7:]):
            return
        if access.active(request):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                access.check_origin(request)
            return
        raise HTTPException(401, "담당자 접속이 필요합니다.", headers={"WWW-Authenticate": "Bearer"})

    staff_only = [Depends(staff)]

    @api.middleware("http")
    async def protect_staff_response(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/staff/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @api.exception_handler(RequestValidationError)
    async def bad_request(_, error):
        fields = ", ".join(".".join(map(str, entry["loc"])) for entry in error.errors())
        return JSONResponse({"detail": f"입력 형식 또는 필수 항목을 확인해주세요. ({fields})"}, status_code=422)

    @api.get("/health")
    def health():
        with sessions() as session:
            session.scalar(select(1))
        return {"status": "ok", "mode": settings.mode}

    @api.get("/catalog")
    def catalog():
        return taxonomy()

    @api.get("/staff/session")
    def session_state(request: Request):
        return {"authenticated": access.active(request)}

    @api.post("/staff/session")
    def login(body: StaffLogin, request: Request, response: Response):
        access.check_origin(request)
        if not access.check_key(body.key):
            raise HTTPException(401, "담당자 접근 키를 확인해주세요.")
        access.issue(request, response)
        return {"authenticated": True}

    @api.delete("/staff/session", status_code=204)
    def logout(request: Request):
        access.check_origin(request)
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, httponly=True, samesite="strict", secure=request.url.scheme == "https")
        return response

    @api.get("/staff/drafts", dependencies=staff_only)
    def drafts():
        with sessions() as session:
            records = session.scalars(
                select(Draft).where(Draft.mode == settings.mode).order_by(Draft.updated_at.desc()).limit(50)
            )
            return {
                "max_reextractions": settings.max_reextractions,
                "items": [{"id": row.id, "status": row.status, "updated_at": row.updated_at} for row in records],
            }

    @api.post("/staff/drafts", status_code=201, dependencies=staff_only)
    async def create(metadata: Annotated[str, Form(max_length=5000)],
                     photos: Annotated[list[UploadFile], File()], background_tasks: BackgroundTasks,
                     background: bool = False):
        try:
            intake = Input.model_validate_json(metadata)
        except ValidationError as error:
            fields = ", ".join(".".join(map(str, entry["loc"])) for entry in error.errors())
            raise HTTPException(422, f"습득 정보의 형식과 필수 항목을 확인해주세요. ({fields})") from None
        if not 1 <= len(photos) <= 3:
            raise HTTPException(422, "사진은 1~3장 첨부해주세요.")
        pictures = []
        for file in photos:
            raw = await file.read(MAX_IMAGE_BYTES + 1)
            try:
                pictures.append(await run_in_threadpool(normalize, raw))
            except ValueError as error:
                return JSONResponse({"detail": str(error), "error_code": "image_input_failed"}, status_code=422)
        if background:
            draft_id = await run_in_threadpool(service.queue, intake, pictures)
            background_tasks.add_task(service.run_queued, draft_id)
            return JSONResponse({"id": draft_id, "status": "pending"}, status_code=202)
        return await run_in_threadpool(service.create, intake, pictures)

    @api.get("/staff/drafts/{draft_id}", dependencies=staff_only)
    def detail(draft_id: UUID):
        return service.detail(str(draft_id))

    @api.get("/staff/drafts/{draft_id}/progress", dependencies=staff_only)
    def progress(draft_id: UUID):
        return service.progress(str(draft_id))

    @api.post("/staff/drafts/{draft_id}/reextract", dependencies=staff_only)
    def reextract(draft_id: UUID, body: Revision):
        return service.analyze(str(draft_id), body.revision, reextract=True)

    @api.post("/staff/drafts/{draft_id}/validate", dependencies=staff_only)
    def validate(draft_id: UUID, body: Correction):
        draft_id = str(draft_id)
        if "extraction" not in service.detail(draft_id)["analysis"]:
            raise HTTPException(409, "분석 결과가 아직 없습니다. 먼저 사진을 재분석해주세요.")
        return service.analyze(draft_id, body.revision, correction=body)

    @api.post("/staff/drafts/{draft_id}/schema", dependencies=staff_only)
    def draft_schema(draft_id: UUID, body: SchemaSelection):
        draft = service.detail(str(draft_id))
        item = {**(draft["analysis"].get("item") or {}), "name": body.name, "subcategory": body.subcategory}
        codes = {"major_category_code", "minor_category_code"}
        try:
            selection = body.model_dump(include=codes) if body.model_fields_set & codes else None
            classification = classify(item, selection=selection)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"classification": classification, "item_schema": select_schema(item, classification)}

    @api.post("/staff/drafts/{draft_id}/approve", dependencies=staff_only)
    def approve(draft_id: UUID, body: Approval):
        return service.approve(str(draft_id), body)

    @api.post("/staff/drafts/{draft_id}/masking", dependencies=staff_only)
    def manual_masking(draft_id: UUID, body: ManualMask):
        return service.add_manual_masks(str(draft_id), body)

    @api.post("/staff/drafts/{draft_id}/discard-originals", dependencies=staff_only)
    def discard_originals(draft_id: UUID, body: Revision):
        return service.discard_originals(str(draft_id), body.revision)

    @api.post("/staff/drafts/{draft_id}/publish", dependencies=staff_only)
    def publish(draft_id: UUID, body: Revision):
        service.publish(str(draft_id), body.revision)
        return public_detail(draft_id)

    @api.get("/staff/drafts/{draft_id}/logs", dependencies=staff_only)
    def logs(draft_id: UUID):
        with sessions() as session:
            service.get(session, str(draft_id))
            rows = session.scalars(
                select(AgentLog).where(AgentLog.draft_id == str(draft_id)).order_by(AgentLog.id)
            )
            return [{"sequence": index, "revision": row.revision, **row.event} for index, row in enumerate(rows, 1)]

    @api.get("/staff/drafts/{draft_id}/photos/{position}", dependencies=staff_only)
    def draft_photo(draft_id: UUID, position: int, kind: Annotated[str, Query(pattern="^(masked|original)$")] = "masked"):
        with sessions() as session:
            draft = service.get(session, str(draft_id))
            row = session.get(DraftPhoto, (str(draft_id), position))
            privacy = draft.analysis.get("privacy", {})
            check = next((entry for entry in privacy.get("photo_checks", []) if entry["position"] == position), None)
            if (not row or not check or draft.analysis.get("previous_result") or getattr(row, kind) is None
                    or (kind == "original" and not check["original_allowed"])
                    or (kind == "masked" and sha256(row.masked).hexdigest() != check["sha256"])):
                raise HTTPException(404, "미리보기 사진이 없습니다.")
            return Response(getattr(row, kind), media_type="image/png")

    @api.get("/notices")
    def public_list(q: Annotated[str, Query(max_length=120)] = "",
                    category: Annotated[str, Query(max_length=40)] | None = None,
                    minor_category: Annotated[str, Query(max_length=16)] | None = None,
                    limit: Annotated[int, Query(ge=1, le=50)] = 12, offset: Annotated[int, Query(ge=0)] = 0):
        filters = [Notice.mode == settings.mode]
        if q.strip():
            filters.append(((Notice.public["_privacy_policy"].as_string() == POLICY["version"]) &
                            Notice.search_text.icontains(q.strip(), autoescape=True))
                           | Notice.id.icontains(q.strip(), autoescape=True))
        if category:
            official = next((entry for entry in taxonomy()["majors"] if category in {entry["code"], entry["name"]}), None)
            if minor_category:
                try:
                    category_pair(official["code"] if official else None, minor_category)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
                filters.append(Notice.public["minor_category_code"].as_string() == minor_category)
            if official:
                filters.append((Notice.public["major_category_code"].as_string() == official["code"]) |
                               (Notice.category == official["name"]))
            else:
                filters.append((Notice.category == category) | (Notice.public["legacy_category"].as_string() == category))
        elif minor_category:
            raise HTTPException(422, "소분류를 검색하려면 대분류를 선택해주세요.")
        with sessions() as session:
            total = session.scalar(select(func.count()).select_from(Notice).where(*filters))
            records = session.scalars(
                select(Notice).where(*filters).order_by(Notice.published_at.desc(), Notice.id)
                .limit(limit).offset(offset)
            )
            return {"total": total, "items": [notice_response(row) for row in records]}

    @api.get("/notices/{notice_id}")
    def public_detail(notice_id: UUID):
        with sessions() as session:
            notice = session.get(Notice, str(notice_id))
            if not notice or notice.mode != settings.mode:
                raise HTTPException(404, "등록된 공고를 찾을 수 없습니다.")
            return notice_response(notice)

    @api.get("/notices/{notice_id}/photos/{position}")
    def public_photo(notice_id: UUID, position: int):
        with sessions() as session:
            record = session.execute(
                select(NoticePhoto, Notice.public).join(Notice).where(
                    Notice.id == str(notice_id), Notice.mode == settings.mode, NoticePhoto.position == position,
                )
            ).one_or_none()
            if not record:
                raise HTTPException(404, "공개 사진이 없습니다.")
            photo, public = record
            if sha256(photo.content).hexdigest() != public.get("_photo_certificates", {}).get(str(position)):
                raise HTTPException(404, "공개 사진이 없습니다.")
            return Response(photo.content, media_type="image/png")

    return api
