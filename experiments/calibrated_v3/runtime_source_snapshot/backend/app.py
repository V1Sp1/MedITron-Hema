"""Local API and frontend on the same origin. Bind to loopback only."""

import os
import shutil
import asyncio
import tempfile
import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from lab_parser.pipeline import validate_policy

from .features import DICTIONARY_VERSION, FEATURES, ROOT, observation_response, prepare_observation, validate_inputs
from .parser_service import run_parser
from .recommendations import phrase_catalog, phrases_for_report
from .data_sufficiency import POLICY, assess_data
from .verdicts import ModelVerdict
from .screening import screen
from .model_service import DEFAULT_MODEL_BUNDLE, ModelService, ModelUnavailable
from .store import Store, StorageFull, RETENTION_SECONDS
from .auth import AuthStore, COOKIE, SESSION_SECONDS, LoginFailure
from .privacy import ResearchSessions, IntegrationLimiter, POLICY_VERSION, USE_NOTICE, COOKIE as RESEARCH_COOKIE, minimized_observation, cleanup_abandoned

MB = 1024 * 1024
ORIGINS = [f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (8000, 5173)]


class BodyLimit:
    def __init__(self, app, max_bytes=61 * MB):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        limit = self.max_bytes if scope.get("path") == "/api/observations/pdf" else 128 * 1024
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Некорректный размер запроса."}, status_code=400)(scope, receive, send)
        if declared < 0:
            return await JSONResponse({"detail": "Некорректный размер запроса."}, status_code=400)(scope, receive, send)
        size_message = "Пакет превышает 60 МБ." if limit == self.max_bytes else "Запрос превышает 128 КБ."
        if declared > limit:
            return await JSONResponse({"detail": size_message}, status_code=413)(scope, receive, send)
        received = 0
        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise HTTPException(413, size_message)
            return message
        return await self.app(scope, limited_receive, send)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    inputs: dict[str, Any]
    observationId: str | None = None
    observationRevision: int | None = None
    reviewedObservation: bool = False


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int
    inputs: dict[str, Any]


class RecommendationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reportId: str
    audience: Literal["doctor", "patient"]


class CoverageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    inputs: dict[str, Any]
    audience: Literal["doctor", "patient"] = "patient"


class RecommendationPreviewRequest(CoverageRequest):
    verdict: ModelVerdict | None = None


class ResearchAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policyVersion: Literal[POLICY_VERSION]
    dataKind: Literal["synthetic", "anonymized"]
    researchOnly: Literal[True]


class LaboratoryRequest(ResearchAcknowledgement):
    inputs: dict[str, Any]
    audience: Literal["patient", "doctor"] = "patient"


def _id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(404, "Запись не найдена.")


def create_app(*, data_dir: Path | None = None, parser=run_parser, ocr_mode: str = "auto",
               freshness_policy: dict | None = None, phrase_file: Path | None = None,
               model_bundle: Path | None = DEFAULT_MODEL_BUNDLE, trusted_model_sha256: str | None = None) -> FastAPI:
    root = Path(data_dir or os.environ.get("HEMA_DATA_DIR", ROOT / "data/local"))
    policy = validate_policy({} if freshness_policy is None else freshness_policy)
    processing = Lock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(root)
        app.state.auth = await run_in_threadpool(AuthStore, root)
        app.state.privacy = ResearchSessions()
        app.state.integration_limiter = IntegrationLimiter()
        await run_in_threadpool(cleanup_abandoned, root)
        app.state.staging = tempfile.TemporaryDirectory(prefix="hema-live-", dir=root)
        marker = Path(app.state.staging.name) / '.hema-ephemeral-owner.json'
        marker.write_text(json.dumps({'version': 'hema-ephemeral-v1', 'pid': os.getpid()}), encoding='utf-8')
        marker.chmod(0o600)
        async def sweep():
            while True:
                await asyncio.sleep(15)
                await run_in_threadpool(app.state.store.cleanup)
                await run_in_threadpool(cleanup_abandoned, root)
                app.state.privacy.cleanup()
        cleaner = asyncio.create_task(sweep())
        try:
            app.state.model = await run_in_threadpool(ModelService, model_bundle, trusted_model_sha256) if model_bundle is not None else None
            yield
        finally:
            cleaner.cancel()
            try:
                await cleaner
            except asyncio.CancelledError:
                pass
            app.state.staging.cleanup()
            app.state.auth.close()
            app.state.store.close()

    app = FastAPI(title="Hema Local API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.add_middleware(CORSMiddleware, allow_origins=ORIGINS,
                       allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "X-Hema-Client"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        if request.url.path == "/api/auth/login":
            # Validation errors must not echo the submitted password.
            return JSONResponse({"detail": "Проверьте логин и пароль: оба поля обязательны; логин до 64, пароль до 128 символов."}, status_code=422)
        # Do not reflect request bodies, medical values or uploaded names in errors.
        return JSONResponse({"detail": "Некорректный формат запроса. Проверьте обязательные поля и допустимые значения."}, status_code=422)

    @app.exception_handler(StorageFull)
    async def storage_full(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=507)

    @app.middleware("http")
    async def local_requests(request, call_next):
        origin = request.headers.get("origin")
        same_origin = str(request.base_url).rstrip("/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in {*ORIGINS, same_origin}:
            return JSONResponse({"detail": "Запрос с этого сайта не разрешён."}, status_code=403)
        if (request.method not in {"GET", "HEAD", "OPTIONS"}
                and (request.cookies.get(COOKIE) or request.cookies.get(RESEARCH_COOKIE)
                     or request.url.path.startswith(("/api/auth/", "/api/privacy/")))
                and request.headers.get("x-hema-client") != "1"):
            return JSONResponse({"detail": "Не удалось проверить источник запроса. Обновите страницу."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
        else:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' http://localhost:8000 http://127.0.0.1:8000; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        return response

    def doctor(request):
        user = app.state.auth.current(request.cookies.get(COOKIE)) if request else None
        if user is None:
            raise HTTPException(401, "Войдите с учётной записью, выданной организацией.")
        return user

    def grant(request):
        value = app.state.privacy.current(request.cookies.get(RESEARCH_COOKIE))
        if value is None:
            raise HTTPException(428, "Сначала подтвердите исследовательский режим и вид данных. Реальные идентифицируемые анализы пока не допускаются.")
        return value

    def access(request, audience):
        user = doctor(request) if audience == "doctor" else None
        session = grant(request)
        return {"grantId": session["id"], "doctorId": user["id"] if user else None,
                "organizationId": user.get("organizationId", "local-research") if user else None,
                "dataKind": session["dataKind"], "policyVersion": session["policyVersion"],
                "expiresAt": session["expiresAt"]}

    def public(value):
        return {key: item for key, item in value.items() if not key.startswith("_")}

    def verify_live_owner(owner, request):
        session = grant(request)
        if session["id"] != owner["grantId"]:
            raise HTTPException(409, "Сеанс изменился или данные удалены. Начните новую работу.")
        if owner.get("doctorId") and doctor(request)["id"] != owner["doctorId"]:
            raise HTTPException(404, "Запись не найдена.")

    def clear_session(request):
        old = app.state.privacy.revoke(request.cookies.get(RESEARCH_COOKIE))
        if old:
            app.state.store.delete_owner(old["id"])
            app.state.privacy.event("session_delete", grant_id=old["id"])

    def get(kind, identifier, request=None):
        value = app.state.store.get(kind, _id(identifier))
        if value is None:
            raise HTTPException(404, "Запись не найдена.")
        owner = value.get("_access")
        if not owner:
            # Historical records have no trustworthy owner. Never infer one.
            raise HTTPException(404, "Запись не найдена.")
        if owner.get("doctorId"):
            user = doctor(request)
            if owner["doctorId"] != user["id"]:
                raise HTTPException(404, "Запись не найдена.")
        session = app.state.privacy.current(request.cookies.get(RESEARCH_COOKIE))
        if not session or owner["grantId"] != session["id"]:
            app.state.privacy.event("object_read", object_id=identifier, result="denied")
            raise HTTPException(404, "Запись не найдена.")
        app.state.privacy.event("object_read", grant_id=session["id"], object_id=identifier)
        return value

    def review(identifier, revision, inputs, request):
        observation = get("observation", identifier, request)
        if revision != observation["revision"]:
            raise HTTPException(409, "Наблюдение изменилось. Загрузите актуальную версию и проверьте значения.")
        try:
            inputs = validate_inputs(inputs)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        observation["review"] = {"inputs": inputs, "confirmedAt": datetime.now().astimezone().isoformat(),
                                  "source": "user_confirmed_form", "extractionRevision": revision}
        observation["review_status"] = "confirmed_inputs"
        observation["revision"] += 1
        verify_live_owner(observation["_access"], request)
        if not app.state.store.update("observation", observation, revision):
            raise HTTPException(409, "Наблюдение изменилось во время сохранения.")
        app.state.privacy.event("review_update", grant_id=observation["_access"]["grantId"], object_id=identifier)
        return observation

    @app.get("/api/privacy/status")
    def privacy_status(request: Request):
        session = app.state.privacy.current(request.cookies.get(RESEARCH_COOKIE))
        return {"mode": "research", "realPatientProcessingEnabled": False, "clinicalUseEnabled": False,
                "policyVersion": POLICY_VERSION, "notice": USE_NOTICE,
                "retentionSeconds": RETENTION_SECONDS, "storage": "volatile",
                "acknowledged": session is not None,
                "expiresAt": session["expiresAt"] if session else None,
                "dataKind": session["dataKind"] if session else None}

    @app.post("/api/privacy/acknowledge")
    def acknowledge(body: ResearchAcknowledgement, request: Request):
        clear_session(request)
        try:
            token, session = app.state.privacy.create(body.dataKind)
        except ValueError as exc:
            raise HTTPException(429, str(exc))
        app.state.privacy.event("research_acknowledgement", grant_id=session["id"])
        response = JSONResponse({"acknowledged": True, "policyVersion": POLICY_VERSION,
                                 "dataKind": session["dataKind"], "expiresAt": session["expiresAt"]})
        response.set_cookie(RESEARCH_COOKIE, token, max_age=RETENTION_SECONDS, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https", path="/")
        return response

    @app.delete("/api/privacy/session")
    def delete_session(request: Request):
        clear_session(request)
        response = JSONResponse({"deleted": True})
        response.delete_cookie(RESEARCH_COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/api/auth/session")
    def auth_session(request: Request):
        user = app.state.auth.current(request.cookies.get(COOKIE))
        return {"authenticated": user is not None, "user": user}

    @app.post("/api/auth/login")
    def auth_login(body: LoginRequest, request: Request):
        try:
            token, user = app.state.auth.login(body.username, body.password,
                request.client.host if request.client else "local", request.cookies.get(COOKIE))
        except LoginFailure as exc:
            if exc.throttled:
                raise HTTPException(429, "Слишком много попыток входа. Повторите через 15 минут или обратитесь к администратору.",
                                    headers={"Retry-After": "900"})
            raise HTTPException(401, "Неверный логин или пароль. Проверьте данные либо обратитесь к администратору организации.")
        response = JSONResponse({"authenticated": True, "user": user})
        clear_session(request)
        response.delete_cookie(RESEARCH_COOKIE, path="/", httponly=True, samesite="strict")
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            samesite="strict", secure=request.url.scheme == "https", path="/")
        return response

    @app.post("/api/auth/logout")
    def auth_logout(request: Request):
        app.state.auth.logout(request.cookies.get(COOKIE))
        clear_session(request)
        response = JSONResponse({"authenticated": False, "user": None})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        response.delete_cookie(RESEARCH_COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/api/health")
    def health():
        status = model_status()
        return {"status": "degraded" if status["state"] == "unavailable" else "ok", "serviceVersion": "0.1.0",
                "modelConnected": status["connected"], "modelVersion": status["modelVersion"], "modelState": status["state"],
                "phraseFileConfigured": phrase_file is not None, "ocrMode": ocr_mode,
                "recommendationStatus": "configured" if phrase_file else "draft",
                "recommendationVersion": phrase_catalog("patient")["version"],
                "sufficiencyPolicyVersion": POLICY["version"],
                "unitDictionaryVersion": DICTIONARY_VERSION,
                "limits": {"files": 10, "fileMB": 20, "batchMB": 60, "pagesPerFile": 100}}

    @app.get("/api/features")
    def features():
        return {"version": DICTIONARY_VERSION, "items": list(FEATURES.values())}

    @app.get("/api/models/status")
    def model_status():
        if app.state.model is not None:
            return app.state.model.status()
        return {"connected": False, "modelVersion": None, "state": "disabled",
                "supportedMethods": ["case_hemoglobin_rule"], "message": "Модель явно отключена; используется правило Hb."}

    @app.post("/api/observations/pdf", openapi_extra={"requestBody": {
        "required": True, "content": {"multipart/form-data": {"schema": {
            "type": "object", "required": ["files"], "properties": {
                "files": {"type": "array", "minItems": 1, "maxItems": 10,
                          "items": {"type": "string", "format": "binary"}}}}}}}})
    async def upload_pdf(request: Request):
        # Check the gate before parsing the multipart body or staging any PDF.
        research = grant(request)
        if not processing.acquire(blocking=False):
            raise HTTPException(429, "Сервис уже распознаёт пакет. Повторите запрос после его завершения.")
        directory = None
        try:
            async with request.form(max_files=10, max_fields=1, max_part_size=20 * MB) as form:
                files = form.getlist("files")
                if not files or len(files) > 10 or any(not isinstance(f, UploadFile) for f in files) or set(form) - {"files", "audience"}:
                    raise HTTPException(422, "Передайте от 1 до 10 PDF в поле files.")
                audience = form.get("audience", "patient")
                if not isinstance(audience, str) or audience not in {"patient", "doctor"}:
                    raise HTTPException(422, "Неизвестная роль.")
                owner = access(request, audience)
                observation_id = str(uuid4())
                directory = Path(app.state.staging.name) / observation_id
                paths, total = [], 0
                for index, upload in enumerate(files):
                    name = Path((upload.filename or "analysis.pdf").replace("\\", "/")).name
                    if not name.lower().endswith(".pdf") or len(name) > 200 or "\x00" in name:
                        raise HTTPException(422, "Поддерживаются файлы PDF.")
                    path = directory / str(index) / f"analysis-{index + 1}.pdf"
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    size, prefix = 0, b""
                    with path.open("wb") as output:
                        while chunk := await upload.read(MB):
                            size += len(chunk)
                            total += len(chunk)
                            prefix = (prefix + chunk)[:1024]
                            if size > 20 * MB or total > 60 * MB:
                                raise HTTPException(413, "До 20 МБ на файл и до 60 МБ на пакет.")
                            output.write(chunk)
                    if b"%PDF-" not in prefix:
                        raise HTTPException(422, "Один из файлов не содержит PDF.")
                    paths.append(path)
                payload = await run_in_threadpool(parser, paths, ocr_mode=ocr_mode,
                                                  assessed_on=date.today(), freshness_policy=policy)
                if (research["dataKind"] == "anonymized" and any(
                        d.get("metadata", {}).get("patient_names") or d.get("metadata", {}).get("birth_dates")
                        for d in payload["documents"])):
                    raise HTTPException(422, "В PDF обнаружены имя или дата рождения. Удалите идентификаторы из самого документа перед загрузкой.")
                observation = minimized_observation(prepare_observation(payload))
                observation["id"] = observation_id
                observation["_access"] = owner
                observation["audience"] = audience
                observation["expiresAt"] = owner["expiresAt"]
                if owner["doctorId"]:
                    observation["doctorId"] = owner["doctorId"]
                if not observation["measurements"]:
                    raise HTTPException(422, "Не удалось извлечь показатели. Проверьте читаемость PDF и доступность OCR.")
                verify_live_owner(owner, request)
                app.state.store.put("observation", observation,
                    {d["id"]: path.read_bytes() for d, path in zip(observation["documents"], paths)})
                app.state.privacy.event("pdf_upload", grant_id=owner["grantId"], object_id=observation_id)
                return observation_response(observation)
        except TimeoutError as exc:
            raise HTTPException(504, str(exc))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(422, str(exc))
        finally:
            processing.release()
            if directory:
                shutil.rmtree(directory, ignore_errors=True)

    @app.get("/api/observations/{identifier}")
    def observation(identifier: str, request: Request):
        return observation_response(get("observation", identifier, request))

    @app.get("/api/observations/{identifier}/documents/{document_id}")
    def document(identifier: str, document_id: str, request: Request):
        get("observation", identifier, request)
        content = app.state.store.get_document(identifier, document_id)
        if content is None:
            raise HTTPException(404, "Документ не найден.")
        return Response(content, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="analysis.pdf"'})

    @app.put("/api/observations/{identifier}/review")
    def confirm(identifier: str, body: ReviewRequest, request: Request):
        return observation_response(review(identifier, body.revision, body.inputs, request))

    def predict(audience, body, request):
        owner = access(request, audience)
        try:
            report = screen(body.inputs, audience)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if app.state.model is not None:
            try:
                report = app.state.model.apply(report)
            except ModelUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
        observation = None
        if body.observationId:
            if not body.reviewedObservation or body.observationRevision is None:
                raise HTTPException(409, "Перед расчётом подтвердите значения, извлечённые из PDF.")
            observation = review(body.observationId, body.observationRevision, report["inputs"], request)
            # A private doctor observation must not be copied to a public patient report.
            if observation.get("doctorId"):
                doctor(request)
                owner = observation["_access"]
            report["warnings"] += observation_response(observation)["warnings"]
        report_id = str(uuid4())
        report.update({"id": report_id, "reportId": report_id, "createdAt": datetime.now().astimezone().isoformat(),
                       "observationId": body.observationId, "observationRevision": observation["revision"] if observation else None,
                       "unitDictionaryVersion": DICTIONARY_VERSION})
        report.update(_access=owner, expiresAt=owner["expiresAt"], researchOnly=True,
                      clinicalUseEnabled=False, processingMode="research", scoreMeaning=report.get("scoreMeaning", "uncalibrated_model_score"))
        report["warnings"].append(USE_NOTICE)
        if owner["doctorId"]:
            report["doctorId"] = owner["doctorId"]
        # Freeze the default editorial response with the report; later catalog edits
        # must not silently change the wording attached to an existing result.
        try:
            report["recommendationSnapshot"] = phrases_for_report(report)
        except (ValueError, OSError) as exc:
            raise HTTPException(503, "Каталог фраз недоступен или имеет неверную структуру.") from exc
        verify_live_owner(owner, request)
        app.state.store.put("report", report)
        app.state.privacy.event("report_create", grant_id=owner["grantId"], object_id=report_id)
        return public(report)

    @app.post("/api/patient/predict")
    def patient_predict(body: PredictRequest, request: Request):
        return predict("patient", body, request)

    @app.post("/api/integration/predict")
    def integration_predict(body: LaboratoryRequest, request: Request):
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else None
        key = app.state.auth.api_key(token)
        if key is None:
            raise HTTPException(401, "Требуется действующий ключ API организации.")
        if not app.state.integration_limiter.allow(key['id']):
            raise HTTPException(429, "Лимит исследовательского API: 60 запросов в минуту на ключ.", headers={"Retry-After": "60"})
        # This route does not persist records or link to any browser observation.
        try:
            result = screen(body.inputs, body.audience)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if app.state.model is not None:
            try:
                result = app.state.model.apply(result)
            except ModelUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
        result.update(researchOnly=True, clinicalUseEnabled=False, processingMode="research",
                      scoreMeaning=result.get("scoreMeaning", "uncalibrated_model_score"), organizationId=key["organization_id"],
                      reportId=None, observationId=None, stored=False, dataKind=body.dataKind)
        result["warnings"].append(USE_NOTICE)
        result["recommendationSnapshot"] = phrases_for_report(result)
        return result

    @app.post("/api/doctor/predict")
    def doctor_predict(body: PredictRequest, request: Request):
        return predict("doctor", body, request)

    @app.get("/api/reports/{identifier}")
    def report(identifier: str, request: Request):
        return public(get("report", identifier, request))

    @app.delete("/api/reports/{identifier}")
    def delete_report(identifier: str, request: Request):
        value = get("report", identifier, request)
        app.state.store.delete("report", value["id"])
        app.state.privacy.event("report_delete", grant_id=value["_access"]["grantId"], object_id=identifier)
        return {"deleted": True}

    @app.delete("/api/observations/{identifier}")
    def delete_observation(identifier: str, request: Request):
        value = get("observation", identifier, request)
        app.state.store.delete("observation", value["id"])
        app.state.privacy.event("observation_delete", grant_id=value["_access"]["grantId"], object_id=identifier)
        return {"deleted": True}

    @app.get("/api/recommendations/catalog")
    def catalog(request: Request, audience: Literal["doctor", "patient"] = "patient"):
        if audience == "doctor":
            doctor(request)
        return phrase_catalog(audience)

    @app.post("/api/data-sufficiency")
    def data_sufficiency(body: CoverageRequest, request: Request):
        if body.audience == "doctor":
            doctor(request)
        grant(request)
        try:
            return assess_data(body.inputs, body.audience)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/recommendations/preview")
    def recommendation_preview(body: RecommendationPreviewRequest, request: Request):
        if body.audience == "doctor":
            doctor(request)
        grant(request)
        try:
            inputs = validate_inputs(body.inputs, require_core=False)
            coverage = assess_data(inputs, body.audience)
            anemia = None
            if coverage["canAssessAnemia"]:
                anemia = inputs["hemoglobin"] < (120 if inputs["sex"] == "F" else 130)
            result = phrases_for_report({"audience": body.audience, "inputs": inputs, "anemia": anemia,
                                         "modelConnected": False, "modelVersion": None,
                                         "modelVerdict": body.verdict.model_dump() if body.verdict else None}, simulation=True)
            return {**result, "audience": body.audience, "reportId": None, "anemia": anemia,
                    "source": "editorial_preview", "modelConnected": False}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/recommendations")
    def recommendations(body: RecommendationsRequest, request: Request):
        if body.audience == "doctor":
            doctor(request)
        report = get("report", body.reportId, request)
        if report["audience"] != body.audience:
            raise HTTPException(409, "Роль не совпадает с ролью сохранённого отчёта.")
        try:
            result = (phrases_for_report(report, phrase_file) if phrase_file is not None
                      else report.get("recommendationSnapshot") or phrases_for_report(report))
            return {**result, "reportId": report["reportId"], "audience": report["audience"]}
        except (ValueError, OSError):
            raise HTTPException(503, "Файл клинических фраз недоступен или имеет неподтверждённую структуру.")

    app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app
