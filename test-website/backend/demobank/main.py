from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Query, Request, Response, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .repository import PostgresRepository, Repository
from .schemas import (
    AccountResponse,
    AccountsResponse,
    Customer,
    ErrorResponse,
    HealthResponse,
    SessionResponse,
    TransactionsResponse,
    TransferRequest,
    TransferResponse,
)
from .security import hash_session_token


logger = logging.getLogger("demobank")
SESSION_COOKIE = "demobank_session"
PACIFIC = ZoneInfo("America/Los_Angeles")
session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)
UNAUTHENTICATED_RESPONSE = {401: {"model": ErrorResponse, "description": "Missing or invalid session"}}
SERVER_ERROR_RESPONSE = {500: {"model": ErrorResponse, "description": "Unexpected server error"}}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _repo(request: Request) -> Repository:
    return request.app.state.repository


def _settings(request: Request) -> Settings:
    return request.app.state.settings


async def current_customer(
    request: Request,
    token: Annotated[str | None, Security(session_cookie)],
) -> Customer:
    if not token:
        raise ApiError(401, "unauthenticated", "A valid demo session is required.")
    customer = await _repo(request).customer_for_session(
        hash_session_token(token, _settings(request).session_secret)
    )
    if customer is None:
        raise ApiError(401, "unauthenticated", "A valid demo session is required.")
    return customer


def create_app(repository: Repository | None = None, settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or Settings.from_env()
        resolved_repository = repository or PostgresRepository(resolved_settings.database_url)
        app.state.settings = resolved_settings
        app.state.repository = resolved_repository
        await resolved_repository.open()
        try:
            yield
        finally:
            await resolved_repository.close()

    app = FastAPI(
        title="DemoBank API",
        version="1.0.0",
        description="HTTP API for the DemoBank application.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        locations = {str(item) for error in errors for item in error.get("loc", ())}
        if "amount" in locations:
            return _error(422, "invalid_amount", "Amount must be a decimal string with no more than two decimal places.")
        if {"start_date", "end_date"} & locations:
            return _error(422, "invalid_date", "Dates must use YYYY-MM-DD format.")
        if {"type", "status"} & locations:
            return _error(422, "invalid_filter", "One or more transaction filters are invalid.")
        return _error(422, "invalid_request", "The request is invalid.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error(404, "not_found", "The requested resource was not found.")
        if exc.status_code == 405:
            return _error(405, "method_not_allowed", "The request method is not allowed.")
        return _error(exc.status_code, "http_error", "The request could not be completed.")

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error while serving %s %s", request.method, request.url.path, exc_info=exc)
        return _error(500, "internal_error", "DemoBank could not complete the request.")

    @app.get(
        "/health",
        response_model=HealthResponse,
        responses=SERVER_ERROR_RESPONSE,
        tags=["service"],
    )
    async def health(request: Request) -> HealthResponse:
        await _repo(request).ping()
        return HealthResponse(status="ok")

    @app.post(
        "/api/demo/session",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
        responses=SERVER_ERROR_RESPONSE,
        tags=["session"],
    )
    async def create_demo_session(request: Request, response: Response) -> SessionResponse:
        app_settings = _settings(request)
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=app_settings.session_ttl_seconds)
        customer = await _repo(request).create_session(
            hash_session_token(token, app_settings.session_secret), expires_at
        )
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=app_settings.session_ttl_seconds,
            expires=expires_at,
            path="/",
            secure=request.url.scheme == "https",
            httponly=True,
            samesite="lax",
        )
        return SessionResponse(customer=customer)

    @app.delete(
        "/api/demo/session",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
        responses=SERVER_ERROR_RESPONSE,
        tags=["session"],
    )
    async def delete_demo_session(
        request: Request,
        token: Annotated[str | None, Security(session_cookie)],
    ) -> Response:
        if token:
            await _repo(request).revoke_session(hash_session_token(token, _settings(request).session_secret))
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=request.url.scheme == "https",
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get(
        "/api/me",
        response_model=Customer,
        responses={**UNAUTHENTICATED_RESPONSE, **SERVER_ERROR_RESPONSE},
        tags=["customer"],
    )
    async def me(customer: Annotated[Customer, Depends(current_customer)]) -> Customer:
        return customer

    @app.get(
        "/api/accounts",
        response_model=AccountsResponse,
        responses={**UNAUTHENTICATED_RESPONSE, **SERVER_ERROR_RESPONSE},
        tags=["accounts"],
    )
    async def accounts(
        request: Request, customer: Annotated[Customer, Depends(current_customer)]
    ) -> AccountsResponse:
        return AccountsResponse(accounts=await _repo(request).list_accounts(customer.id))

    @app.get(
        "/api/accounts/{account_id}",
        response_model=AccountResponse,
        responses={
            **UNAUTHENTICATED_RESPONSE,
            404: {"model": ErrorResponse, "description": "Account not found"},
            422: {"model": ErrorResponse, "description": "Invalid account identifier"},
            **SERVER_ERROR_RESPONSE,
        },
        tags=["accounts"],
    )
    async def account(
        account_id: UUID,
        request: Request,
        customer: Annotated[Customer, Depends(current_customer)],
    ) -> AccountResponse:
        found = await _repo(request).get_account(customer.id, account_id)
        if found is None:
            raise ApiError(404, "account_not_found", "The account was not found.")
        return AccountResponse(account=found)

    @app.get(
        "/api/transactions",
        response_model=TransactionsResponse,
        responses={
            **UNAUTHENTICATED_RESPONSE,
            404: {"model": ErrorResponse, "description": "Account not found"},
            422: {"model": ErrorResponse, "description": "Invalid filter"},
            **SERVER_ERROR_RESPONSE,
        },
        tags=["transactions"],
    )
    async def transactions(
        request: Request,
        customer: Annotated[Customer, Depends(current_customer)],
        account_id: UUID | None = None,
        type: Annotated[Literal["deposit", "withdrawal", "transfer"] | None, Query()] = None,
        status_filter: Annotated[
            Literal["completed", "pending", "failed"] | None, Query(alias="status")
        ] = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> TransactionsResponse:
        repo = _repo(request)
        if account_id is not None and await repo.get_account(customer.id, account_id) is None:
            raise ApiError(404, "account_not_found", "The account was not found.")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ApiError(422, "invalid_date_range", "Start date must not be after end date.")
        start_at = datetime.combine(start_date, time.min, PACIFIC).astimezone(timezone.utc) if start_date else None
        end_before = datetime.combine(end_date, time.min, PACIFIC).astimezone(timezone.utc) if end_date else None
        found = await repo.list_transactions(
            customer.id, account_id, type, status_filter, start_at, end_before
        )
        return TransactionsResponse(transactions=found)

    @app.post(
        "/api/transfers",
        response_model=TransferResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            **UNAUTHENTICATED_RESPONSE,
            404: {"model": ErrorResponse, "description": "Account not found"},
            409: {"model": ErrorResponse, "description": "Insufficient funds"},
            422: {"model": ErrorResponse, "description": "Invalid transfer"},
            **SERVER_ERROR_RESPONSE,
        },
        tags=["transfers"],
    )
    async def transfer(
        payload: TransferRequest,
        request: Request,
        customer: Annotated[Customer, Depends(current_customer)],
    ) -> TransferResponse:
        if payload.source_account_id == payload.destination_account_id:
            raise ApiError(422, "same_account", "Source and destination accounts must be different.")
        amount = Decimal(payload.amount)
        completed = await _repo(request).transfer(
            customer.id,
            payload.source_account_id,
            payload.destination_account_id,
            amount,
            payload.memo,
        )
        if completed is None:
            raise ApiError(404, "account_not_found", "One or more accounts were not found.")
        transaction, updated_accounts = completed
        return TransferResponse(transaction=transaction, accounts=updated_accounts)

    return app


app = create_app()
