"""Scoped, tenant-isolated, local research integration. Never enable real-patient use."""
import ipaddress
import json
import shutil
import time
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .features import DICTIONARY_VERSION, FEATURES, observation_response, prepare_observation, validate_inputs
from .model_service import ModelUnavailable
from .privacy import POLICY_VERSION, USE_NOTICE, minimized_observation
from .recommendations import phrases_for_report
from .report_files import encode_reports
from .screening import screen

MB = 1024 * 1024
DRAFT_TTL = 15 * 60
PDF_UPLOAD_PATH = '/api/integration/v1/observations/pdf'
bearer = HTTPBearer(auto_error=False, scheme_name='LaboratoryKey', description='Ключ организации с необходимыми правами')


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    policyVersion: Literal[POLICY_VERSION]
    dataKind: Literal['synthetic', 'anonymized']
    researchOnly: Literal[True]


class ReportOptions(ResearchInput):
    inputs: dict[str, Any]
    audience: Literal['patient', 'doctor', 'both'] = 'both'
    format: Literal['pdf', 'json', 'zip'] = 'zip'
    pregnancyStatus: Literal['unknown', 'not_pregnant', 'pregnant'] = 'unknown'

    @model_validator(mode='after')
    def one_pdf_role(self):
        if self.format == 'pdf' and self.audience == 'both':
            raise ValueError('PDF requires one audience; select ZIP for both')
        return self


class ReviewedPDFRequest(ReportOptions):
    revision: int = Field(ge=1)
    reviewedObservation: Literal[True]


class PDFMetadata(ResearchInput):
    age_years: int | None = Field(default=None, ge=18, le=120)
    sex: Literal['F', 'M'] | None = None


def current_key(app, request, required_scope=None):
    headers = request.headers.getlist('authorization')
    scheme, _, token = headers[0].partition(' ') if len(headers) == 1 else ('', '', '')
    key = app.state.auth.api_key(token) if scheme.lower() == 'bearer' else None
    if key is None:
        raise HTTPException(401, 'Требуется действующий ключ API организации.', headers={'WWW-Authenticate':'Bearer'})
    if required_scope and required_scope not in key['scopes']:
        raise HTTPException(403, 'Ключ не имеет права на эту операцию.')
    return key


def create_laboratory_router(app, *, parser, processing, ocr_mode, freshness_policy):
    router = APIRouter(prefix='/api/integration/v1', tags=['Laboratory research API'])

    def permission(scope=None):
        def authorized(request: Request, credentials=Depends(bearer)):
            # HTTP is allowed only from loopback; do not trust caller-supplied proxy headers.
            if request.url.scheme != 'https':
                try: local = ipaddress.ip_address(request.client.host).is_loopback
                except (ValueError, AttributeError): local = False
                if not local:
                    raise HTTPException(426, 'Внешняя интеграция требует HTTPS через доверенный шлюз.')
            key = current_key(app, request, scope)
            if not app.state.integration_limiter.allow(key['id']):
                raise HTTPException(429, 'Лимит API превышен.', headers={'Retry-After':'60'})
            return key
        return authorized

    def verify_key(request, key, scope):
        live = current_key(app, request, scope)
        if (live['id'], live['organization_id']) != (key['id'], key['organization_id']):
            raise HTTPException(401, 'Ключ API изменился.')

    def record(action, key, identifier, result='allowed'):
        # Only technical identifiers: no lab values, tokens, filenames, bodies or reports.
        app.state.privacy.event(action, grant_id=key['id'], object_id=identifier, result=result)

    def get_draft(identifier, key):
        draft = app.state.store.get('integration_observation', str(identifier))
        owner = (draft or {}).get('_integration', {})
        if not draft or owner.get('keyId') != key['id'] or owner.get('organizationId') != key['organization_id']:
            record('integration_draft_access', key, str(identifier), 'denied')
            raise HTTPException(404, 'Черновик не найден или срок хранения истёк.')
        return draft

    def build_reports(body, key, draft=None):
        # Compute once, share numeric predictions, then select each role's actual phrases.
        try:
            base = screen(body.inputs, 'patient')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if app.state.model is not None:
            try: base = app.state.model.apply(base)
            except ModelUnavailable as exc: raise HTTPException(503, str(exc)) from exc
        base = app.state.ferritin.apply(base, body.pregnancyStatus)
        identifier = str(uuid4())
        base.update(requestId=identifier, createdAt=datetime.now(timezone.utc).isoformat(),
                    organizationId=key['organization_id'], unitDictionaryVersion=DICTIONARY_VERSION,
                    reportId=None, observationId=draft['id'] if draft else None,
                    stored=False, researchOnly=True, clinicalUseEnabled=False, processingMode='research',
                    dataKind=body.dataKind, policyVersion=POLICY_VERSION,
                    inputSource='reviewed_pdf' if draft else 'structured_json',
                    observationRevision=draft['revision'] if draft else None)
        if draft:
            base['observationSummary'] = {
                'documentCount':len(draft['documents']), 'measurementCount':len(draft['measurements']),
                'collectedDates':sorted({m['collected_on'] for m in draft['measurements'] if m['collected_on']}),
                'undatedMeasurements':sum(m['collected_on'] is None for m in draft['measurements']),
                'notice':'Даты относятся к извлечённому пакету. Исправленный ввод подтверждён отправителем; связь каждого исправления с конкретным исходным измерением не сохраняется.'}
        base['warnings'] = list(dict.fromkeys([*base['warnings'], USE_NOTICE,
                                               *(observation_response(draft)['warnings'] if draft else [])]))
        reports = []
        for audience in (['patient', 'doctor'] if body.audience == 'both' else [body.audience]):
            report = deepcopy(base)
            report['audience'] = audience
            try: phrases = phrases_for_report(report)
            except (ValueError, OSError) as exc:
                raise HTTPException(503, 'Каталог рекомендаций недоступен.') from exc
            old_summary = report['dataSufficiency']['summary']
            report.update(recommendationSnapshot=phrases, recommendations=phrases['items'],
                recommendationConclusions=phrases['conclusions'], dataSufficiency=phrases['dataSufficiency'],
                insufficientData=phrases['insufficientData'], recommendationVersion=phrases['phraseFileVersion'],
                recommendationStatus=phrases['status'], clinicalReviewStatus=phrases['clinicalReviewStatus'])
            report['warnings'] = list(dict.fromkeys([
                *(phrases['dataSufficiency']['summary'] if warning == old_summary else warning for warning in report['warnings']),
                *phrases['warnings']]))
            reports.append(report)
        return reports

    async def export(body, request, key, draft=None):
        reports = await run_in_threadpool(build_reports, body, key, draft)
        try: content, media, filename = await run_in_threadpool(encode_reports, reports, body.format)
        except Exception as exc:
            # Report content and parser paths must never appear in an exception response.
            raise HTTPException(503, 'Не удалось сформировать файл отчёта.') from exc
        verify_key(request, key, 'reports')
        if draft:
            # Recheck TTL/ownership after expensive inference and rendering.
            get_draft(UUID(draft['id']), key)
        identifier = reports[0]['requestId']
        record('integration_report', key, identifier)
        return Response(content, media_type=media, headers={
            'Content-Disposition':f'attachment; filename="{filename}"',
            'X-Hema-Request-Id':identifier, 'X-Hema-Research-Only':'true',
            'X-Hema-Clinical-Use-Enabled':'false', 'Cache-Control':'no-store',
            'X-Content-Type-Options':'nosniff'})

    @router.get('/capabilities')
    def capabilities(key=Depends(permission())):
        return {'apiVersion':'v1', 'policyVersion':POLICY_VERSION, 'researchOnly':True,
                'realPatientProcessingEnabled':False, 'clinicalUseEnabled':False,
                'inputFormats':['json','pdf'], 'outputFormats':['pdf','json','zip'],
                'audiences':['patient','doctor','both'], 'keyScopes':key['scopes'],
                'unitDictionaryVersion':DICTIONARY_VERSION,
                'limits':{'jsonBytes':128*1024, 'files':10, 'fileBytes':20*MB,
                          'batchBytes':60*MB, 'pagesPerFile':100, 'draftRetentionSeconds':DRAFT_TTL,
                          'requestsPerMinutePerKey':60, 'requestsPerMinutePerProcess':240},
                'notice':USE_NOTICE}

    @router.post('/reports', responses={200:{'content':{'application/pdf':{},'application/zip':{},'application/json':{}}}})
    async def report(body: ReportOptions, request: Request, key=Depends(permission('reports'))):
        return await export(body, request, key)

    @router.post('/observations/pdf', openapi_extra={'requestBody':{
        'required':True,'content':{'multipart/form-data':{'schema':{'type':'object',
        'required':['files','metadata'],'properties':{
            'files':{'type':'array','minItems':1,'maxItems':10,'items':{'type':'string','format':'binary'}},
            'metadata':{'type':'string','description':'JSON PDFMetadata: policyVersion, dataKind, researchOnly; optional age_years, sex'}}}}}}})
    async def upload(request: Request, key=Depends(permission('pdf'))):
        if not processing.acquire(blocking=False):
            raise HTTPException(429, 'Сервис уже распознаёт пакет.', headers={'Retry-After':'5'})
        directory = None
        try:
            async with request.form(max_files=10, max_fields=1, max_part_size=16*1024) as form:
                if set(form) != {'files', 'metadata'} or len(form.getlist('metadata')) != 1:
                    raise HTTPException(422, 'Передайте files и один metadata JSON.')
                raw = form['metadata']
                if not isinstance(raw, str) or len(raw.encode('utf-8')) > 16*1024:
                    raise HTTPException(422, 'Некорректный metadata JSON.')
                try: metadata = PDFMetadata.model_validate(json.loads(raw))
                except (ValueError, TypeError, ValidationError) as exc:
                    raise HTTPException(422, 'Некорректный metadata JSON; идентификаторы пациента не допускаются.') from exc
                files = form.getlist('files')
                if not 1 <= len(files) <= 10 or any(not isinstance(item, UploadFile) for item in files):
                    raise HTTPException(422, 'Передайте от 1 до 10 PDF.')
                identifier = str(uuid4())
                directory = Path(app.state.staging.name)/identifier
                total, paths = 0, []
                for index, upload in enumerate(files):
                    original = upload.filename or ''
                    name = Path(original.replace('\\','/')).name
                    if (not name.lower().endswith('.pdf') or len(original)>200 or any(c in original for c in ('\x00','\r','\n',':'))
                            or upload.content_type not in {'application/pdf','application/octet-stream'}):
                        raise HTTPException(422, 'Поддерживаются только PDF.')
                    path = directory/str(index)/f'analysis-{index+1}.pdf'
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    size, prefix = 0, b''
                    with path.open('wb') as target:
                        path.chmod(0o600)
                        while chunk := await upload.read(MB):
                            size += len(chunk); total += len(chunk); prefix = (prefix+chunk)[:1024]
                            if size > 20*MB or total > 60*MB:
                                raise HTTPException(413, 'До 20 МБ на PDF и 60 МБ на пакет.')
                            target.write(chunk)
                    if not prefix.lstrip().startswith(b'%PDF-'):
                        raise HTTPException(422, 'Некорректная сигнатура PDF.')
                    paths.append(path)
                payload = await run_in_threadpool(parser, paths, ocr_mode=ocr_mode,
                    assessed_on=date.today(), freshness_policy=freshness_policy)
                if metadata.dataKind == 'anonymized' and any(
                        doc.get('metadata',{}).get('patient_names') or doc.get('metadata',{}).get('birth_dates')
                        for doc in payload['documents']):
                    raise HTTPException(422, 'В PDF найдены идентификаторы. Обезличьте исходный документ.')
                draft = minimized_observation(prepare_observation(payload))
                if not draft['measurements']:
                    raise HTTPException(422, 'Показатели не извлечены; проверьте документ.')
                expires = min(time.time()+DRAFT_TTL, key['expires_at'])
                draft.update(id=identifier, expiresAt=expires, researchOnly=True, clinicalUseEnabled=False,
                    _access={'grantId':'integration:'+key['id'], 'expiresAt':expires},
                    _integration={'keyId':key['id'],'organizationId':key['organization_id'],
                                  'dataKind':metadata.dataKind,'policyVersion':POLICY_VERSION})
                response = observation_response(draft)
                response['inputs']['age_years'] = metadata.age_years
                response['inputs']['sex'] = metadata.sex
                draft['review'] = {'inputs':response['inputs']}  # Still explicitly unconfirmed.
                draft['review_status'] = 'requires_review'
                response = observation_response(draft)
                response.update(requiresReview=True, originalFilesRetained=False,
                                expiresAt=expires, requestId=identifier)
                verify_key(request, key, 'pdf')
                app.state.store.put('integration_observation', draft)
                record('integration_pdf_upload', key, identifier)
                return response
        except TimeoutError as exc:
            raise HTTPException(504, 'Время распознавания PDF истекло.') from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(422, 'PDF не удалось обработать. Проверьте читаемость и формат.') from exc
        finally:
            processing.release()
            if directory:shutil.rmtree(directory, ignore_errors=True)

    @router.get('/observations/{identifier}')
    def observation(identifier: UUID, key=Depends(permission('pdf'))):
        draft = get_draft(identifier, key)
        return {**observation_response(draft), 'requiresReview':True, 'originalFilesRetained':False,
                'expiresAt':draft['expiresAt']}

    @router.post('/observations/{identifier}/reports', responses={200:{'content':{'application/pdf':{},'application/zip':{},'application/json':{}}}})
    async def reviewed_report(identifier: UUID, body: ReviewedPDFRequest, request: Request,
                              key=Depends(permission('reports'))):
        if 'pdf' not in key['scopes']:
            raise HTTPException(403, 'Для отчёта по PDF нужны права reports и pdf.')
        draft = get_draft(identifier, key)
        if body.revision != draft['revision']:
            raise HTTPException(409, 'Версия черновика изменилась.')
        if body.dataKind != draft['_integration']['dataKind']:
            raise HTTPException(409, 'Вид данных должен совпадать с загруженным пакетом.')
        return await export(body, request, key, draft)

    @router.delete('/observations/{identifier}')
    def delete(identifier: UUID, key=Depends(permission('pdf'))):
        draft = get_draft(identifier, key)
        app.state.store.delete('integration_observation', draft['id'])
        record('integration_draft_delete', key, draft['id'])
        return {'deleted':True}

    return router
