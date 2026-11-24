from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from threading import Lock
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update

from backend.categories import category_pair, observation
from backend.db import AgentLog, Draft, DraftPhoto, Notice, NoticePhoto
from backend.item_schemas import classify, project_item, select_schema
from backend.images import mask
from backend.models import ModelFailure
from backend.photo_privacy import dimensions, photo_certificates
from backend.privacy import MASKED, POLICY, publishable, redact
from backend.workflow import event

LOGGER = logging.getLogger("lost_and_found.agents")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RegistrationService:
    def __init__(self, settings, sessions, workflow):
        self.settings = settings
        self.sessions = sessions
        self.workflow = workflow
        self.active = {}
        self.intake_context = {}
        self.active_lock = Lock()

    def get(self, session, draft_id):
        draft = session.get(Draft, draft_id)
        if draft is None or draft.mode != self.settings.mode:
            raise HTTPException(404, "등록 건을 찾을 수 없습니다.")
        return draft

    @staticmethod
    def photos(session, draft_id):
        return list(session.scalars(
            select(DraftPhoto).where(DraftPhoto.draft_id == draft_id).order_by(DraftPhoto.position)
        ))

    @staticmethod
    def conflicts():
        return HTTPException(409, "등록 건이 변경되었습니다. 최신 내용을 다시 불러와주세요.")

    def update(self, session, draft, **changes):
        # Compare-and-swap protects edits, approvals and publishing even while model calls are in flight.
        count = session.execute(
            update(Draft).where(Draft.id == draft.id, Draft.revision == draft.revision)
            .values(revision=draft.revision + 1, updated_at=now(), **changes),
            execution_options={"synchronize_session": False},
        ).rowcount
        if count != 1:
            raise self.conflicts()

    @staticmethod
    def serial(draft, photo_count):
        analysis = registration_view(draft.analysis, draft.intake["quantity"])
        if privacy := analysis.get("privacy"):
            analysis = {**analysis, "privacy": {**privacy, "photo_checks": [
                {key: value for key, value in check.items() if key != "sha256"}
                for check in privacy.get("photo_checks", [])
            ], "reviewed": draft.status in {"approved", "published"}}}
        return {
            "id": draft.id, "mode": draft.mode, "status": draft.status, "revision": draft.revision,
            "intake": {key: MASKED if key == "finder" else redact(value) if isinstance(value, str)
                       and key in {"memo", "region", "location", "institution"} else value
                       for key, value in draft.intake.items()},
            "analysis": analysis, "approval": {key: value for key, value in draft.approval.items()
                                              if key != "fingerprint"} if draft.approval else None, "photo_count": photo_count,
            "reextractions": draft.reextractions,
        }

    def detail(self, draft_id):
        with self.sessions() as session:
            draft = self.get(session, draft_id)
            count = session.scalar(select(func.count()).select_from(DraftPhoto).where(DraftPhoto.draft_id == draft_id))
            return self.serial(draft, count)

    def create(self, intake, pictures):
        return self.analyze(self.save_draft(intake, pictures), 1)

    def save_draft(self, intake, pictures):
        draft_id = str(uuid4())
        metadata = intake.model_dump(mode="json")
        context = {key: metadata[key] for key in ("finder", "memo", "region", "location", "institution")}
        for key in ("memo", "region", "location", "institution"):
            metadata[key] = redact(metadata[key], [context["finder"]])
        metadata["finder"] = MASKED
        metadata["memo"] = ""
        with self.sessions.begin() as session:
            session.add(Draft(
                id=draft_id, mode=self.settings.mode, status="pending", intake=metadata,
                created_at=now(), updated_at=now(),
            ))
            session.flush()
            session.add_all([
                DraftPhoto(draft_id=draft_id, position=index, original=photo)
                for index, photo in enumerate(pictures, 1)
            ])
        with self.active_lock:
            self.intake_context[draft_id] = context
        return draft_id

    def queue(self, intake, pictures):
        draft_id = self.save_draft(intake, pictures)
        with self.active_lock:
            self.active[draft_id] = []
        return draft_id

    def progress(self, draft_id):
        with self.sessions() as session:
            draft = self.get(session, draft_id)
        with self.active_lock:
            events = self.active.get(draft_id)
            stages = list(events) if events is not None else []
        return {"status": draft.status, "running": draft.status == "pending" and events is not None, "stages": stages}

    def run_queued(self, draft_id):
        def report(entries):
            with self.active_lock:
                self.active[draft_id].extend(entries)

        try:
            self.analyze(draft_id, 1, report=report)
        except Exception as error:
            LOGGER.error("Background analysis failed: %s", type(error).__name__)
            with self.sessions.begin() as session:
                draft = self.get(session, draft_id)
                if draft.revision == 1 and draft.status == "pending":
                    self.update(session, draft, status="error", analysis={
                        "error": "분석이 중단되었습니다. 사진을 다시 분석해주세요.", "analysis_status": "processing_failed",
                    })
        finally:
            with self.active_lock:
                self.active.pop(draft_id, None)

    def analyze(self, draft_id, revision, *, correction=None, reextract=False, report=None):
        with self.active_lock:
            if report is None and draft_id in self.active:
                raise HTTPException(409, "사진을 분석 중입니다. 현재 분석이 끝날 때까지 기다려주세요.")
        with self.sessions() as session:
            draft = self.get(session, draft_id)
            if draft.revision != revision:
                raise self.conflicts()
            if draft.status == "published":
                raise HTTPException(409, "이미 공고로 등록되었습니다.")
            if reextract and draft.reextractions >= self.settings.max_reextractions:
                raise HTTPException(409, "재추출 한도에 도달했습니다. 저장된 내용을 검토하고 직접 수정해주세요.")
            previous = draft.analysis
            intake = dict(draft.intake)
            pictures = [row.original for row in self.photos(session, draft_id)]
            attempt = draft.reextractions + int(reextract)
        if not pictures or any(photo is None for photo in pictures):
            raise HTTPException(409, "재검증에 필요한 원본 사진이 없습니다.")
        with self.active_lock:
            intake.update(self.intake_context.pop(draft_id, {}))
        identifiers = [intake["finder"]] if intake["finder"] != MASKED else []
        changes = correction.model_dump(exclude={"revision"}) if correction else None
        if changes is not None:
            selection = {field: changes.pop(field) for field in ("major_category_code", "minor_category_code")}
            if not (correction.model_fields_set & selection.keys()):
                selection = None
            try:
                values = {**previous.get("item", {}), **changes["item"]}
                attributes = values.pop("attributes", {})
                if not isinstance(attributes, dict):
                    raise ValueError("속성은 항목별 값으로 입력해주세요.")
                values.update(attributes)
                saved_selection = previous.get("observation", {})
                unchanged_type = all(values.get(key) == previous.get("item", {}).get(key) for key in ("name", "subcategory"))
                effective = selection if selection is not None else (
                    saved_selection if unchanged_type and saved_selection.get("category_source") == "staff" else None
                )
                classification = (saved_selection if unchanged_type and effective is None
                                  and saved_selection.get("major_category_code") else classify(values, selection=effective))
                changes["item"] = project_item(values, select_schema(values, classification), strict=True)
                changes["selection"] = selection
                changes["classification"] = classification
            except (ValueError, TypeError):
                raise HTTPException(422, "공식 분류의 연결 관계와 물품별 입력값을 확인해주세요.") from None
            for field in ("region", "location", "institution", "memo"):
                replacement = changes.pop(field)
                if replacement is not None:
                    intake[field] = replacement
            if quantity := changes.pop("quantity"):
                intake["quantity"] = quantity
        edited = [
            field for field, value in changes["item"].items()
            if value != (previous.get("item") or {}).get(field)
        ] if changes is not None else []
        result = self.workflow.run(
            report=report,
            photos=pictures, intake=intake, identifiers=identifiers, previous=previous,
            correction=changes, edited_fields=edited, attempt=attempt, logs=[],
        )
        status = result.get("decision", "error")
        if result.get("failure"):
            analysis = {**previous, "error": result["failure"], "analysis_status": result["analysis_status"]}
            if previous.get("item"):
                analysis["previous_result"] = True
        else:
            analysis = {key: result[key] for key in (
                "item", "extraction", "evidence", "findings", "questions", "decision", "analysis_status", "observation",
                "item_schema",
            )}
            analysis["privacy"] = result["notice_privacy"]
            if previous.get("manual_masks"):
                analysis["manual_masks"] = previous["manual_masks"]
            analysis["retry_recommended"] = result.get("retry_recommended", False)
            if result["ocr_failed"]:
                analysis["warnings"] = ["OCR을 완료하지 못했습니다. 시각 분석을 유지하고 사진의 공개를 제한했습니다."]
            if result.get("details_error"):
                analysis.setdefault("warnings", []).append(
                    f"상세 특징을 추출하지 못했습니다({result['details_error']}). 확인된 물품 유형을 유지했으니 직접 입력하거나 사진 재분석을 실행해주세요."
                )
            if analysis["privacy"].get("error"):
                analysis["error"] = analysis["privacy"]["error"]
            for finding in analysis["findings"]:
                if finding["field"] in analysis["evidence"]:
                    analysis["evidence"][finding["field"]]["status"] = finding["status"]
                if finding["field"] == "workflow":
                    analysis["error"] = finding["reason"]
        privacy = analysis.get("privacy", {})
        contextual = {hit["field"] for hit in privacy.get("detections", []) if hit["source"] == "llm"}
        self.sanitize_analysis(analysis, contextual, identifiers)
        for key in ("memo", "region", "location", "institution"):
            if key in contextual or (key == "memo" and not privacy.get("privacy_check_status")):
                intake[key] = MASKED
            else:
                intake[key] = redact(intake[key], identifiers, partial=key == "memo")
        intake["finder"] = MASKED
        with self.sessions.begin() as session:
            draft = self.get(session, draft_id)
            if draft.revision != revision:
                raise self.conflicts()
            self.update(
                session, draft, status=status, analysis=analysis, intake=intake,
                approval=None, reextractions=attempt,
            )
            masked = result.get("masked_photos", [])
            for row in self.photos(session, draft_id):
                row.masked = masked[row.position - 1] if len(masked) == len(pictures) else None
            self.save_logs(session, draft_id, revision + 1, result["logs"])
        return self.detail(draft_id)

    @staticmethod
    def sanitize_analysis(analysis, contextual, identifiers):
        """분석·근거의 복제본에서도 미수집 정보와 문맥상 개인정보를 제거합니다."""
        privacy = analysis.get("privacy", {})
        private = contextual | set(privacy.get("restricted", {})) | set(privacy.get("discard", []))
        for payload in (analysis.get("item", {}), analysis.get("extraction", {}).get("item", {}),
                        analysis.get("observation", {}).get("attributes", {})):
            for key, value in payload.items():
                payload[key] = None if key in private else redact(value, identifiers) if isinstance(value, str) else value
        for key, evidence in analysis.get("evidence", {}).items():
            if key in private:
                evidence["value"] = None
        observed = analysis.get("observation", {})
        for field, observed_key in (("name", "observed_item_name"), ("subcategory", "observed_item_type")):
            if field in private:
                observed[observed_key] = None
        for finding in analysis.get("findings", []):
            reason = finding.get("reason", "")
            finding["reason"] = ("개인정보 보호를 위해 근거 원문을 가렸습니다." if contextual
                                 else redact(reason, identifiers))
        if contextual:
            analysis["questions"] = [redact(question, identifiers) for question in analysis.get("questions", [])
                                     if not any(question.startswith(field + " 항목") for field in contextual)]

    def add_manual_masks(self, draft_id, request):
        """담당자 영역을 누적하고 재검사 결과와 함께 이전 승인을 무효화합니다."""
        started = perf_counter()
        with self.sessions() as session:
            draft = self.get(session, draft_id)
            if draft.revision != request.revision:
                raise self.conflicts()
            if draft.status not in {"review", "needs_review", "approved"}:
                raise HTTPException(409, "검토 중인 초안에만 마스킹 영역을 추가할 수 있습니다.")
            analysis = dict(draft.analysis)
            rows = self.photos(session, draft_id)
            photos = [row.original for row in rows]
        if not 1 <= request.position <= len(photos) or not all(photos):
            raise HTTPException(409, "검사할 원본 사진이 없습니다.")
        privacy = dict(analysis.get("privacy", {}))
        if privacy.get("policy") != POLICY["version"]:
            raise HTTPException(409, "현재 정책으로 사진을 재분석해주세요.")
        size = dimensions(photos[request.position - 1])
        if not request.boxes or any(not (0 <= x1 < x2 < size[0] and 0 <= y1 < y2 < size[1])
                                    for x1, y1, x2, y2 in request.boxes):
            raise HTTPException(422, "이미지 안에서 유효한 마스킹 영역을 지정해주세요.")
        manual = dict(analysis.get("manual_masks", {}))
        position = str(request.position)
        manual[position] = list(dict.fromkeys(tuple(box) for box in [*manual.get(position, []), *request.boxes]))
        if len(manual[position]) > 30:
            raise HTTPException(422, "사진 한 장에 최대 30개 영역을 지정할 수 있습니다.")
        try:
            masked, checks = self.workflow.photo_review(photos, [], manual, blocked=privacy["photo_blocked_by_policy"],
                                                       uncertain=privacy["photo_uncertain"], retained=privacy["photo_checks"])
        except ModelFailure:
            masked = [mask(photo, [], cover=True) for photo in photos]
            checks = [{**check, "privacy_check_status": "blocked", "masking_status": "covered",
                       "reason": "unverified", "original_allowed": False, "sha256": sha256(photo).hexdigest()}
                      for check, photo in zip(privacy["photo_checks"], masked, strict=True)]
        privacy.update(photo_checks=checks, photo_available=bool(checks) and
                       all(check["privacy_check_status"] == "passed" for check in checks))
        analysis.update(privacy=privacy, manual_masks=manual)
        with self.sessions.begin() as session:
            draft = self.get(session, draft_id)
            if draft.revision != request.revision:
                raise self.conflicts()
            status = "review" if draft.status == "approved" else draft.status
            self.update(session, draft, status=status, analysis=analysis, approval=None)
            for row, photo in zip(self.photos(session, draft_id), masked, strict=True):
                row.masked = photo
            self.save_logs(session, draft_id, request.revision + 1, [
                event("Manual Masking", "ok", draft.reextractions, started, status,
                      photo_check="passed" if privacy["photo_available"] else "blocked"),
            ])
        return self.detail(draft_id)

    def discard_originals(self, draft_id, revision):
        """원본을 삭제하고 사진 공개와 이전 승인을 무효화합니다."""
        started = perf_counter()
        with self.sessions.begin() as session:
            draft = self.get(session, draft_id)
            if draft.revision != revision:
                raise self.conflicts()
            if draft.status not in {"error", "needs_review", "review", "approved"}:
                raise HTTPException(409, "분석 중이거나 이미 게시된 초안의 원본을 변경할 수 없습니다.")
            analysis = dict(draft.analysis)
            if privacy := analysis.get("privacy"):
                analysis["privacy"] = {**privacy, "photo_available": False, "photo_checks": []}
            for row in self.photos(session, draft_id):
                row.original = row.masked = None
            self.update(session, draft, analysis=analysis, approval=None,
                        status="review" if draft.status == "approved" else draft.status)
            self.save_logs(session, draft_id, revision + 1, [
                event("Original Photo Retention", "ok", draft.reextractions, started, "discarded"),
            ])
        return self.detail(draft_id)

    @staticmethod
    def approval_fingerprint(analysis, intake, certificates):
        """승인한 공개 텍스트와 사진 바이트의 목록을 게시 시 다시 대조합니다."""
        snapshot = {"public": analysis["privacy"]["public"], "classification": {
            key: analysis.get("observation", {}).get(key) for key in ("major_category_code", "minor_category_code")
        }, "found_at": intake["found_at"], "quantity": intake["quantity"], "photos": certificates}
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()

    @staticmethod
    def save_logs(session, draft_id, revision, events):
        for entry in sorted(events, key=lambda record: record["at"]):
            session.add(AgentLog(draft_id=draft_id, revision=revision, event=entry))

    def approve(self, draft_id, approval):
        started = perf_counter()
        with self.sessions.begin() as session:
            draft = self.get(session, draft_id)
            if draft.revision != approval.revision:
                raise self.conflicts()
            self.require_mapping(draft)
            if draft.status != "review" or not publishable(draft.analysis):
                raise HTTPException(409, "검증과 공개 정책 검사를 통과한 등록 건만 승인할 수 있습니다.")
            certificates = photo_certificates(draft.analysis["privacy"], self.photos(session, draft_id)) if approval.publish_photos else {}
            if approval.publish_photos and not certificates:
                raise HTTPException(409, "이 등록 건의 사진은 공개할 수 없습니다.")
            self.update(session, draft, status="approved", approval={
                "at": now(), "publish_photos": approval.publish_photos,
                "fingerprint": self.approval_fingerprint(draft.analysis, draft.intake, certificates),
            })
            self.save_logs(session, draft_id, approval.revision + 1, [
                event("Human Approval", "ok", draft.reextractions, started, "approved"),
            ])
        return self.detail(draft_id)

    def publish(self, draft_id, revision):
        started = perf_counter()
        with self.sessions.begin() as session:
            draft = self.get(session, draft_id)
            if draft.status == "published":
                return
            if draft.revision != revision:
                raise self.conflicts()
            if draft.status != "approved" or not draft.approval:
                raise HTTPException(409, "담당자 승인 후 공고를 등록할 수 있습니다.")
            self.require_mapping(draft)
            if not publishable(draft.analysis):
                raise HTTPException(409, "공개 정책 검사를 통과하지 못했습니다.")
            photos = self.photos(session, draft_id)
            publish_photos = draft.approval["publish_photos"]
            certificates = photo_certificates(draft.analysis["privacy"], photos) if publish_photos else {}
            if publish_photos and not certificates:
                raise HTTPException(409, "공개 가능한 마스킹 사진이 준비되지 않았습니다.")
            if draft.approval.get("fingerprint") != self.approval_fingerprint(draft.analysis, draft.intake, certificates):
                raise HTTPException(409, "승인 후 공개 정보가 변경되었습니다. 다시 검토하고 승인해주세요.")
            self.update(session, draft, status="published")
            classification = draft.analysis.get("observation", {})
            major, minor = category_pair(classification.get("major_category_code"), classification.get("minor_category_code"))
            public = {**draft.analysis["privacy"]["public"],
                      "major_category_code": major["code"] if major else None,
                      "major_category_name": major["name"] if major else None,
                      "minor_category_code": minor["code"] if minor else None,
                      "minor_category_name": minor["name"] if minor else None,
                      "legacy_category": draft.analysis.get("item", {}).get("category")}
            session.add(Notice(
                id=draft_id, mode=self.settings.mode, category=public["category"],
                public={**public, "_privacy_policy": POLICY["version"],
                        **({"_photo_certificates": certificates} if certificates else {})},
                found_at=draft.intake["found_at"], published_at=now(),
                search_text=" ".join(value for value in public.values() if value),
                photo_count=len(photos) if publish_photos else 0,
                quantity=draft.intake["quantity"],
            ))
            session.flush()
            if publish_photos:
                session.add_all([
                    NoticePhoto(notice_id=draft_id, position=row.position, content=row.masked) for row in photos
                ])
            for row in photos:
                row.original = None
            # The finder is needed for draft-time masking, and is discarded at publication.
            draft.intake = {**draft.intake, "finder": MASKED}
            self.save_logs(session, draft_id, revision + 1, [
                event("Publication Policy", "ok", draft.reextractions, started, "published"),
            ])

    def require_mapping(self, draft):
        classification = draft.analysis.get("observation", {})
        try:
            major, minor = category_pair(classification.get("major_category_code"), classification.get("minor_category_code"))
        except ValueError:
            raise HTTPException(409, "저장된 공식 분류를 확인한 후 재검증해주세요.") from None
        if self.settings.require_official_category and not (major and minor):
            raise HTTPException(409, "공고를 등록하려면 공식 대분류와 소분류가 확정되어야 합니다.")


def registration_view(snapshot, quantity):
    """기존 초안도 저장값을 바꾸지 않고 최신 유형별 서식으로 읽습니다."""
    if "item" not in snapshot:
        return snapshot
    values = snapshot["item"]
    classification = snapshot["observation"] if "item_schema" in snapshot else classify(values)
    schema = snapshot.get("item_schema") or select_schema(values, classification)
    filtered = project_item(values, schema)
    record_values = {**filtered, **{key: classification.get(key) for key in ("major_category_code", "minor_category_code")},
                     "quantity": quantity}
    privacy = snapshot.get("privacy") or {}
    allowed = {field["field_key"] for field in schema["fields"]}
    findings = [finding for finding in snapshot.get("findings", []) if finding["field"] in allowed | {"workflow"}]
    evidence = {key: value for key, value in snapshot.get("evidence", {}).items() if key in allowed}
    statuses = {entry["field"]: entry["status"] for entry in findings}
    fields = []
    for definition in schema["fields"]:
        field = definition["field_key"]
        value = record_values.get(field)
        entry = evidence.get(field, {})
        discard = not definition["store"] or field in privacy.get("discard", [])
        restricted = field in privacy.get("restricted", {})
        public = ((field in privacy.get("public_attributes", {})) or
                  (field in {"name", "color"} and value is not None and value == privacy.get("public", {}).get(field)) or
                  field in {"major_category_code", "minor_category_code", "quantity"})
        status = statuses.get(field) or ("confirmed" if field == "quantity" or
                 field == "total_amount" and value is not None or
                 field in {"major_category_code", "minor_category_code"} and value is not None else "unconfirmed")
        if field == "total_amount" and value is not None:
            source = "코드 계산"
        elif field in {"major_category_code", "minor_category_code"} and value:
            source = "담당자 선택" if classification.get("category_source") == "staff" else "공식 코드표"
        else:
            source = entry.get("source")
        fields.append({**definition, "value": None if restricted or discard else value, "source": source,
                       "evidence": {"photos": entry.get("photos", []), "ocr_regions": entry.get("ocr_regions", [])},
                       "verification_status": status,
                       "visibility": "discard" if discard else "restricted" if restricted else "public" if public else "verification_only"})
    return {**snapshot, "item": filtered, "item_schema": schema, "fields": fields, "findings": findings, "evidence": evidence,
            "observation": {**observation(filtered, classification=classification),
                            **{key: classification[key] for key in ("observed_item_name", "observed_item_type", "attributes") if key in classification}},
            **({"privacy": {**privacy, "verification_only": {
                key: value for key, value in privacy.get("verification_only", {}).items() if key in allowed
            }}} if privacy else {})}
