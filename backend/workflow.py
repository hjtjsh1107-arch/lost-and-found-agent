import json
import logging
import operator
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from backend.categories import observation
from backend.item_schemas import classify, definitions, formatted, project_item, select_schema
from backend.model_diagnostics import collect_calls
from backend.models import ModelFailure, OllamaModels, TextRegion
from backend.photo_privacy import create_photo_checks
from backend.privacy import MASKED, POLICY, detect, public_text, publishable, redact
from backend.schemas import AttributeExtraction

LOGGER = logging.getLogger("lost_and_found.agents")
COLORS = {"검정": ("검정", "검은", "블랙"), "파랑": ("파랑", "파란", "블루"),
          "빨강": ("빨강", "빨간", "레드"), "회색": ("회색", "그레이"),
          "흰색": ("흰색", "하얀", "화이트"), "초록": ("초록", "녹색", "그린")}
MAX_CONTEXT_REGIONS = 60


class State(TypedDict, total=False):
    photos: list[bytes]
    intake: dict
    previous: dict
    correction: dict | None
    edited_fields: list[str]
    identifiers: list[str]
    attempt: int
    extraction: dict
    regions: list[TextRegion]
    evidence: dict
    item: dict
    item_schema: dict
    findings: list
    privacy: dict
    masked_photos: list[bytes]
    decision: str
    questions: list
    failure: str
    analysis_status: str
    observation: dict
    notice_privacy: dict
    ocr_failed: bool
    details_error: str
    ignored_attribute_count: int
    retry_recommended: bool
    logs: Annotated[list[dict], operator.add]


def event(agent, status, attempt, started, decision=None, **details):
    """에이전트 실행 시간과 개인정보가 없는 진단만 로그로 기록합니다."""
    result = {
        "agent": agent, "status": status, "attempt": attempt,
        "duration_ms": round((perf_counter() - started) * 1000, 1),
        "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "decision": decision,
        **details,
    }
    LOGGER.info("%s", json.dumps(result, ensure_ascii=False))
    return result


class Workflow:
    def __init__(self, settings, models):
        """등록, 근거, 병렬 검증 및 검토 질문 단계를 그래프로 연결합니다."""
        self.settings = settings
        self.models = models
        graph = StateGraph(State)
        nodes = {
            "registration": self.registration, "evidence": self.evidence,
            "verification": self.verification, "privacy_agent": self.privacy,
            "supervisor": self.supervisor, "questions": self.questions,
        }
        labels = {
            "registration": "Registration Agent", "evidence": "Evidence Tracker",
            "verification": "Verification Agent", "privacy_agent": "Privacy Agent",
            "supervisor": "Supervisor", "questions": "Adaptive Question Agent",
        }
        for name, node in nodes.items():
            graph.add_node(name, self.logged(labels[name], node))
        graph.add_edge(START, "registration")
        graph.add_conditional_edges("registration", self.after_registration,
                                    {"continue": "evidence", "error": END})
        graph.add_edge("evidence", "verification")
        graph.add_edge("evidence", "privacy_agent")
        graph.add_edge(["verification", "privacy_agent"], "supervisor")
        graph.add_edge("supervisor", "questions")
        graph.add_edge("questions", END)
        self.graph = graph.compile()

    def logged(self, label: str, node: Callable):
        """각 단계의 실패와 실행 통계를 공통 이벤트로 변환합니다."""
        def run(state):
            """한 노드의 결과와 해당 호출에서 수집한 모델 진단을 반환합니다."""
            started = perf_counter()
            error_code = None
            with collect_calls() as calls:
                try:
                    update = node(state)
                except ModelFailure as error:
                    error_code = error.code
                    if label == "Registration Agent":
                        update = {"failure": str(error), "analysis_status": error_code}
                    elif label == "Verification Agent":
                        update = {"findings": [{"field": "workflow", "status": "rejected",
                                                "reason": str(error), "error_code": error_code}]}
                    else:
                        update = {
                            "privacy": {"error": str(error), "error_code": error_code, "public": {},
                                        "verification_only": {}, "restricted": {}, "ocr": [],
                                        "photo_available": False, "policy": POLICY["version"]},
                            "masked_photos": [],
                        }
            details = {"mode": self.settings.mode, "mock_used": self.settings.mode == "mock",
                       "fallback_used": False, "model_calls": calls, "error_code": error_code}
            if "extraction" in update:
                details["extracted_fields"] = sum(bool(value) for value in update["extraction"]["item"].values())
                details["ocr_regions"] = len(update["regions"])
                if update["ocr_failed"]:
                    details["error_code"] = "ocr_failed"
                if update.get("details_error"):
                    details["error_code"] = update["details_error"]
                if update.get("ignored_attribute_count"):
                    details["ignored_attribute_count"] = update["ignored_attribute_count"]
            if mapped := update.get("observation"):
                details["category_status"] = mapped["category_status"]
            if "findings" in update:
                details["verification"] = dict(Counter(finding["status"] for finding in update["findings"]))
            if "privacy" in update:
                details["privacy_status"] = "error" if error_code else "success"
            if "analysis_status" in update:
                details["analysis_status"] = update["analysis_status"]
            update["logs"] = [event(label, "error" if error_code else "ok", state["attempt"], started,
                                    update.get("decision"), **details)]
            return update
        return run

    @staticmethod
    def after_registration(state):
        """등록 단계에서 분석 오류가 나면 후속 에이전트를 실행하지 않습니다."""
        return "error" if state.get("failure") else "continue"

    def read_regions(self, photos):
        """OCR 오류는 별도로 표시해 이미지에서 관찰한 속성을 계속 보존합니다."""
        try:
            return self.models.read_text(photos), False
        except ModelFailure as error:
            if error.code != "ocr_failed":
                raise
            return [], True

    def registration(self, state):
        """사진 유형을 인식하고 스키마로 속성을 제한한 뒤 OCR 근거를 결합합니다."""
        photos, memo = state["photos"], state["intake"]["memo"]
        if state.get("correction") is not None:
            regions, ocr_failed = self.read_regions(photos)
            return {"regions": regions, "ocr_failed": ocr_failed, "extraction": state["previous"]["extraction"]}
        previous_class = state.get("previous", {}).get("observation", {})
        selection = previous_class if previous_class.get("category_source") == "staff" else None
        details_error = None
        ignored_attributes = 0
        if hasattr(self.models, "recognize"):
            recognized = self.models.recognize(photos, memo)
            item = {"name": recognized.name, "subcategory": recognized.observed_item_type}
            original_classification = classify(item, proposed=recognized.model_dump())
            classification = classify(item, selection=selection) if selection is not None else original_classification
            schema = select_schema(item, classification)
            try:
                details = self.models.extract_attributes(photos, item, schema, [], memo)
            except ModelFailure as error:
                details, details_error = AttributeExtraction(attributes={}), error.code
            visual_keys = {
                field["field_key"] for field in schema["fields"]
                if field["store"] and field["extraction_source"] in {"vlm", "vlm_ocr"}
            }
            ignored_attributes = len(details.attributes.keys() - visual_keys)
            values = {key: value for key, value in details.attributes.items() if key in visual_keys}
            values.update(item)
            evidence, uncertain, risk = details.photo_evidence, details.uncertain_fields, recognized.photo_risk
            regions, ocr_failed = self.read_regions(photos)
        else:
            regions, ocr_failed = self.read_regions(photos)
            extraction = self.models.extract(photos, [region.text[:200] for region in regions[:60]], memo)
            values = extraction.item.model_dump() if hasattr(extraction.item, "model_dump") else dict(extraction.item)
            original_classification = classify(values)
            classification = classify(values, selection=selection) if selection is not None else original_classification
            schema = select_schema(values, classification)
            evidence, uncertain, risk = extraction.photo_evidence, extraction.uncertain_fields, extraction.photo_risk
        # 등록 데이터에 번호 원문이나 적용 불가 필드를 보관하지 않습니다.
        evidence = dict(evidence)
        ocr_fields = {}
        for field in schema["fields"]:
            field_key = field["field_key"]
            if field["extraction_source"] == "vlm_ocr" and field["store"] and not values.get(field_key):
                for index, region in enumerate(regions, 1):
                    match = re.fullmatch(rf"{re.escape(field['label'])}\s*[:：]\s*(.{{1,100}})", region.text.strip())
                    if match:
                        values[field_key] = match[1]
                        evidence[field_key] = [region.photo]
                        ocr_fields[field_key] = [index]
                        break
        item = project_item(values, schema)
        detections = self.field_detections(item, state["identifiers"], "vlm")
        item = {field: self.redact_attribute(value, state["identifiers"]) for field, value in item.items()}
        if len(photos) == 1:
            for field, value in item.items():
                if value is not None:
                    evidence.setdefault(field, [1])
        return {
            "regions": regions, "ocr_failed": ocr_failed, "details_error": details_error,
            "ignored_attribute_count": ignored_attributes,
            "extraction": {"item": item, "classification": classification, "original_classification": original_classification, "photo_risk": risk,
                           "text_detections": detections,
                           "photo_evidence": {field: refs for field, refs in evidence.items() if field in item},
                           "uncertain_fields": [field for field in uncertain if field in item], "ocr_fields": ocr_fields},
        }

    @staticmethod
    def redact_attribute(value, identifiers):
        """문자 및 복수 선택값의 개인정보를 가리고 숫자 자료형은 유지합니다."""
        if isinstance(value, str):
            return redact(value, identifiers)
        if isinstance(value, list) and all(isinstance(part, str) for part in value):
            return [redact(part, identifiers) for part in value]
        return value

    def evidence(self, state):
        """선택된 스키마에 맞춰 현재 값과 사진·OCR 근거를 조립합니다."""
        extraction = state["extraction"]
        correction = state.get("correction")
        source = "mock" if self.settings.mode == "mock" else "VLM"
        selection = correction.get("selection") if correction is not None else None
        item = dict(extraction["item"] if correction is None else correction["item"])
        prior = state.get("previous", {}).get("observation", {})
        if selection is None and prior.get("category_source") == "staff":
            selection = prior
        classification = (extraction.get("classification") if correction is None else correction.get("classification"))
        classification = classification or classify(item, selection=selection)
        schema = select_schema(item, classification)
        item = project_item(item, schema)
        edited = set(state.get("edited_fields", []))
        evidence = {}
        for field, value in item.items():
            previous = state.get("previous", {}).get("evidence", {}).get(field, {})
            is_edited = field in edited or (correction is not None and previous.get("source") == "담당자 수정")
            photos = sorted(set(extraction["photo_evidence"].get(field, [])))
            ocr_refs = extraction.get("ocr_fields", {}).get(field, [])
            evidence[field] = {
                "value": self.redact_attribute(value, state["identifiers"]),
                "source": ("코드 계산" if field == "total_amount" else "담당자 수정" if is_edited else
                           ("OCR" if ocr_refs else source) if value is not None else None),
                "photos": [] if is_edited or value is None else [n for n in photos if 1 <= n <= len(state["photos"])],
                "ocr_regions": [] if is_edited else ocr_refs or [
                    index for index, region in enumerate(state["regions"], 1)
                    if field in {"brand", "model"} and isinstance(value, str) and value and value != MASKED
                    and value.casefold() == region.text.strip().casefold()
                ],
            }
        evidence["quantity"] = {"value": state["intake"]["quantity"], "source": "담당자 입력", "photos": []}
        reviewed = {key: entry["value"] for key, entry in evidence.items() if key != "quantity"}
        original = {field: value for field, value in extraction["item"].items() if field in reviewed}
        return {"evidence": evidence, "item": reviewed, "item_schema": schema,
                "observation": observation(reviewed, observed=original, classification=classification)}

    def verification(self, state):
        """적용 가능한 필드만 사진으로 검증하고 필수 확인 누락을 표시합니다."""
        item = state["item"]
        definitions_by_key = {field["field_key"]: field for field in state["item_schema"]["fields"]}
        visual = {key: value for key, value in item.items()
                  if key in definitions_by_key and definitions_by_key[key]["extraction_source"] in {"vlm", "vlm_ocr"}}
        _, _, _, _, guarded = self.sensitivity(state)
        ocr = [] if guarded else [region.text[:200] for region in state["regions"][:60]]
        args = (state["photos"], visual, ocr)
        findings = (self.models.verify(*args, schema=state["item_schema"]) if isinstance(self.models, OllamaModels)
                    else self.models.verify(*args)).findings
        results = {}
        safe_reasons = {"verified": "사진과 일치합니다.", "uncertain": "사진에서 충분히 확인되지 않습니다.",
                        "rejected": "사진과 추출 결과가 일치하지 않습니다."}
        for finding in findings:
            if finding.field not in visual or visual[finding.field] is None:
                continue
            previous = results.get(finding.field)
            priority = {"verified": 0, "uncertain": 1, "rejected": 2}
            if previous and priority[previous["status"]] >= priority[finding.status]:
                continue
            results[finding.field] = {
                "field": finding.field, "status": finding.status,
                "reason": safe_reasons[finding.status] if guarded else redact(finding.reason, state["identifiers"]),
            }
        for field, value in visual.items():
            if value is not None and field not in results:
                results[field] = {"field": field, "status": "uncertain",
                                  "reason": "가상 분석 · 담당자 확인 필요" if self.settings.mode == "mock"
                                  else "독립 모델에서 이 필드의 확인 근거를 반환하지 않았습니다."}
        for field in (field["field_key"] for field in definitions_by_key.values()
                      if field["required"] and field["field_key"] in item):
            if not formatted(item[field]) or not formatted(item[field]).replace(MASKED, "").strip():
                results[field] = {"field": field, "status": "rejected", "reason": "유형별 필수 항목을 입력해주세요."}
        if self.settings.mode == "real":
            for field in state["extraction"].get("uncertain_fields", []):
                if field in results and results[field]["status"] == "verified" and state["evidence"][field]["source"] != "담당자 수정":
                    results[field] = {"field": field, "status": "uncertain", "reason": "초기 분석에서 추정값으로 표시했습니다."}
            if (item.get("name") or "").strip(" .") in {"습득물", "기타", "기타 물품", "물품", "미상"}:
                results["name"] = {"field": "name", "status": "rejected", "reason": "사진에 보이는 물품의 구체적인 이름을 확인해주세요."}
        memo = state["intake"]["memo"]
        for color, aliases in COLORS.items():
            if re.search(r"색상\s*[:=]\s*(?:" + "|".join(aliases) + r")(?:색)?(?:\s|$|[,.;])", memo):
                if item.get("color") and not any(alias in item["color"] for alias in aliases):
                    results["color"] = {"field": "color", "status": "rejected",
                                        "reason": "메모에 표시한 색상과 물품 색상이 다릅니다."}
                break
        return {"findings": list(results.values())}

    @staticmethod
    def sensitivity(state):
        """사진에서 관찰한 분류와 현재 분류를 함께 검사해 민감한 유형을 찾습니다."""
        taxonomy_policy = definitions()
        mapped = state["observation"]
        extraction = state["extraction"]
        original = extraction.get("original_classification") or extraction.get("classification") or classify(extraction["item"])
        majors = {mapped.get("major_category_name"), original.get("major_category_name")}
        minors = {mapped.get("minor_category_name"), original.get("minor_category_name")}
        guarded = bool(majors.intersection(taxonomy_policy["guarded_text_categories"])
                       or minors.intersection(taxonomy_policy["guarded_text_minors"]))
        return original, mapped, majors, minors, guarded

    @staticmethod
    def field_detections(values, identifiers, source):
        """필드에 발견한 개인정보의 유형과 공개 범위만 기록합니다."""
        return [{"field": field, "type": hit.kind, "visibility": hit.visibility, "source": source}
                for field, value in values.items() if isinstance(value, str) or (
                    isinstance(value, list) and all(isinstance(entry, str) for entry in value))
                for hit in detect(formatted(value), identifiers)]

    @staticmethod
    def ocr_input(regions):
        """OCR 원문을 로컬 모델 호출용 번호와 묶습니다."""
        return [{"region": index, "text": region.text[:200]}
                for index, region in enumerate(regions[:MAX_CONTEXT_REGIONS], 1)]

    @staticmethod
    def ocr_over_budget(regions):
        """모델이 모든 OCR 문맥을 검토할 수 없는 경우 사진 공개를 제한합니다."""
        return len(regions) > MAX_CONTEXT_REGIONS or any(len(region.text) > 200 for region in regions)

    def photo_review(self, photos, identifiers, manual, *, blocked, uncertain, regions=None, model=None, retained=()):
        """원본 OCR을 재확인하고 마스킹 후보의 OCR 및 맥락 재검사를 실행합니다."""
        mock = self.settings.mode == "mock"
        if regions is None:
            regions, failed = self.read_regions(photos)
            uncertain |= failed
        if model is None and regions and not mock:
            model = self.models.privacy({"ocr": self.ocr_input(regions)})
        uncertain |= self.ocr_over_budget(regions)
        uncertain |= bool(model and model.hold_photos)

        def check_context(reread):
            """마스킹 후 남은 문자의 개인정보 가능성을 재판정합니다."""
            if self.ocr_over_budget(reread):
                return True
            result = self.models.privacy({"ocr": self.ocr_input(reread)})
            return bool(result.hold_photos or result.ocr_findings)

        return create_photo_checks(photos, regions, identifiers, model.ocr_findings if model else (), manual,
                                   self.models.read_text, retained=retained, blocked=blocked, uncertain=uncertain, mock=mock,
                                   check_context=check_context)

    def privacy(self, state):
        """스키마의 공개 범위와 모델의 민감도 검사를 결합해 공개 후보를 만듭니다."""
        item, regions, intake = state["item"], state["regions"], state["intake"]
        correction = state.get("correction") or {}
        taxonomy_policy = definitions()
        original, mapped, majors, minors, guarded = self.sensitivity(state)
        fields = {field["field_key"]: field for field in state["item_schema"]["fields"] if field["field_key"] in item}
        restricted_fields = {key for key, field in fields.items() if not field["store"] or field["default_visibility"] == "restricted"}
        proposals = {
            **item,
            "category": mapped.get("major_category_name") or item["category"],
            **{field: intake[field] for field in ("region", "location", "institution")},
            "title": None if guarded else correction.get("title"),
            "description": None if guarded else correction.get("description"),
        }
        public = {field: redact(proposals.get(field), state["identifiers"]) for field in POLICY["public_fields"]}
        if guarded:
            # 민감 유형은 자유 입력 문구 및 관찰 세부 특징을 공개에 사용하지 않습니다.
            sensitive_category = next(
                (group[key] for group in (original, mapped) for key in ("minor_category_name", "major_category_name")
                 if group.get(key) in taxonomy_policy["guarded_text_categories"] or group.get(key) in taxonomy_policy["guarded_text_minors"]),
                None,
            )
            public["name"] = sensitive_category
            public["color"] = None
        if self.settings.mode == "mock":
            public["title"] = public["title"] or f"{public['name'] or '습득물'} 습득"
        if self.settings.mode == "mock" and public["description"] is None:
            public["description"] = f"{public['color']} 물품을 보관하고 있습니다." if public["color"] else "습득한 물품을 보관하고 있습니다."
        candidates = {
            field: redact(formatted(proposals.get(field)), state["identifiers"])
            for field, spec in fields.items() if not guarded and field not in POLICY["public_fields"]
            and spec["default_visibility"] == "public" and field not in restricted_fields
        }
        checked = {field: formatted(item[field]) for field in fields if field not in restricted_fields
                   and field not in candidates and field not in {"name", "color"} and item[field] is not None}
        supplied = {"memo": intake["memo"], **{key: intake[key] for key in ("region", "location", "institution")},
                    **{key: correction[key] for key in ("title", "description") if correction.get(key) is not None}}
        raw_item = (state.get("correction") or {}).get("item", {})
        identifiers = state["identifiers"]
        deterministic = (state["extraction"].get("text_detections", []) +
                         self.field_detections(supplied, identifiers, "input") +
                         self.field_detections(raw_item, identifiers, "staff") +
                         self.field_detections(item, identifiers, "attribute"))
        model = self.models.privacy({"public": {**public, **candidates}, "attributes": checked,
                                     "input": {key: redact(value, identifiers) for key, value in supplied.items()
                                               if key == "memo" or guarded and key in {"title", "description"}},
                                     "ocr": self.ocr_input(regions)})
        restricted = {
            "finder": MASKED,
            "memo": redact(intake["memo"], state["identifiers"], partial=True),
            "ocr": f"탐지된 문자 영역 {len(regions)}개",
        }
        contextual = set(model.restricted_fields) & ((set(public) | set(fields) | {"memo"}) - {"category"})
        discarded = {hit["field"] for hit in deterministic if hit["visibility"] == "discard"}
        discarded.update(key for key, field in fields.items() if not field["store"])
        denied = restricted_fields | contextual
        for field in denied:
            restricted[field] = MASKED
            if field in public:
                public[field] = None
            if field in candidates:
                candidates[field] = None
        for field in discarded & fields.keys():
            restricted.pop(field, None)
        verification_only = {field: proposals.get(field) for field in fields if field not in denied
                             and field not in discarded
                             and (field not in POLICY["public_fields"] or guarded and field == "color")}
        blocked = (
            bool(majors.intersection(taxonomy_policy["hold_photo_categories"])) or
            bool(minors.intersection(taxonomy_policy["hold_photo_minors"])) or
            item["category"] in POLICY["hold_photo_categories"]
        )
        vision_risk = state["extraction"].get("photo_risk", "uncertain")
        uncertain = state["ocr_failed"] or vision_risk != "low" or self.ocr_over_budget(regions)
        masked, checks = self.photo_review(
            state["photos"], identifiers, state["previous"].get("manual_masks", {}), blocked=blocked,
            uncertain=uncertain, regions=regions, model=model,
            retained=state["previous"].get("privacy", {}).get("photo_checks", []),
        )
        detections = [*deterministic, *({"field": field, "type": "contextual", "visibility": "restricted", "source": "llm"}
                                      for field in sorted(contextual))]
        if vision_risk == "high":
            detections.append({"field": "photo", "type": "personal_image", "visibility": "restricted", "source": "vlm"})
        return {
            "privacy": {
                "public": public, "restricted": restricted, "verification_only": verification_only,
                "discard": sorted(discarded),
                "ocr": [{"photo": region.photo, "polygon": region.polygon, "score": round(region.score, 3),
                         "text": MASKED} for region in regions],
                "photo_available": bool(checks) and all(check["privacy_check_status"] == "passed" for check in checks),
                "photo_checks": checks, "photo_blocked_by_policy": blocked, "photo_uncertain": uncertain or model.hold_photos,
                "privacy_check_status": "passed", "detections": [dict(entries) for entries in dict.fromkeys(
                    tuple(entry.items()) for entry in detections
                )],
                "text_mask_count": sum((value or "").count(MASKED) for value in public.values())
                + (restricted["memo"] or "").count(MASKED),
                "policy": POLICY["version"],
                "candidates": candidates,
                "custom_text": [] if guarded else [field for field in ("title", "description") if correction.get(field)],
            },
            "masked_photos": masked,
        }

    def supervisor(self, state):
        """정확성·개인정보·공식 분류 검사를 합쳐 공고 초안의 다음 상태를 결정합니다."""
        privacy = dict(state["privacy"])
        error = privacy.get("error_code") or next((entry.get("error_code") for entry in state["findings"]
                                                   if entry["field"] == "workflow"), None)
        if error:
            return {"decision": "error", "analysis_status": error, "notice_privacy": privacy}
        candidates = privacy.pop("candidates", {})
        findings = {entry["field"]: entry["status"] for entry in state["findings"]}
        required = {field["field_key"] for field in state["item_schema"]["fields"] if field["required"]}
        blocked = {field for field in required if findings.get(field) == "rejected"}
        if self.settings.mode == "real":
            public = dict(privacy["public"])
            verified = {
                field: value for field, value in candidates.items()
                if findings.get(field) == "verified" and value and MASKED not in value
            }
            for field in ("name", "color"):
                if findings.get(field) != "verified" or (public[field] and MASKED in public[field]):
                    public[field] = None
            expressly_private = [
                formatted(value) for field, value in state["item"].items()
                if field in privacy["restricted"] and field not in {"name", "color"} and value is not None
                and len(formatted(value) or "") >= 2
            ]
            for field, value in {"name": public["name"], "color": public["color"], **verified}.items():
                if value and any(secret.casefold() in value.casefold() for secret in expressly_private):
                    if field in public:
                        public[field] = None
                    else:
                        verified.pop(field)
            private = [
                formatted(value) for field, value in state["item"].items()
                if field not in {"category", "name", "color", *verified} and value is not None and len(formatted(value) or "") >= 2
            ]
            generated = dict(zip(("title", "description"), public_text(public, verified, state["item_schema"]), strict=True))
            for field, value in generated.items():
                custom = public[field]
                if not custom or any(secret.casefold() in custom.casefold() for secret in private):
                    public[field] = value
                    privacy["custom_text"] = [key for key in privacy["custom_text"] if key != field]
            privacy["public"] = public
            privacy["public_attributes"] = verified
            privacy["verification_only"] = {key: value for key, value in privacy["verification_only"].items() if key not in verified}
            if not public["name"]:
                blocked.add("name")
        if self.settings.mode == "mock":
            blocked.update(field for field, status in findings.items() if status == "rejected")
        needs_category = self.settings.require_official_category and state["observation"]["category_status"] != "mapped"
        review = not blocked and not needs_category and publishable({"privacy": privacy})
        if blocked or not publishable({"privacy": privacy}):
            status = "verification_failed"
        elif state["ocr_failed"] or state.get("details_error") or (self.settings.mode == "real" and any(status != "verified" for status in findings.values())):
            status = "partial"
        else:
            status = "mock" if self.settings.mode == "mock" else "success"
        return {"decision": "review" if review else "needs_review", "analysis_status": status,
                "notice_privacy": privacy,
                "retry_recommended": bool(blocked and state["attempt"] < self.settings.max_reextractions)}

    def questions(self, state):
        """필수 검증, 공식 분류와 현금 수량에 대한 검토 질문을 제한 개수로 반환합니다."""
        questions = [
            f"{finding['field']} 항목을 확인해주세요. {finding['reason']}"
            for finding in state["findings"] if finding["status"] == "rejected" and finding["field"] != "workflow"
        ]
        if state["observation"]["category_status"] == "unmapped":
            questions.append("공식 분류가 미확정입니다. 대분류와 소분류 후보를 검토해주세요.")
        if "total_amount" in state["item"] and state["item"]["total_amount"] is None:
            questions.insert(0, "권종별 수량과 통화를 확인한 후 전체 수량을 확인으로 표시해주세요.")
        if state["decision"] == "needs_review" and not publishable({"privacy": state["notice_privacy"]}):
            questions.append("공개 필수 항목을 확인하고 개인정보를 제외한 값으로 수정해주세요.")
        return {"questions": questions[:self.settings.max_questions]}

    def run(self, report=None, **inputs):
        """비공개 트레이싱 상태에서 그래프를 실행하고 필요 시 단계별 진행 상태를 전달합니다."""
        # State contains private data. Persist only the sanitized draft and events outside the graph.
        with tracing_context(enabled=False):
            if report is None:
                return self.graph.invoke(inputs)
            output = inputs
            for mode, chunk in self.graph.stream(inputs, stream_mode=["updates", "values"]):
                if mode == "values":
                    output = chunk
                else:
                    for result in chunk.values():
                        if isinstance(result, dict) and result.get("logs"):
                            report(result["logs"])
            return output
