from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.lower()


class UserCreate(Credentials):
    role: str = Field(pattern=r"^(OBSERVER|DISPATCHER|ADMIN)$")


class UserView(BaseModel):
    id: str
    username: str
    roles: list[str]


class AuthResponse(BaseModel):
    user: UserView
    csrfToken: str


class CsrfResponse(BaseModel):
    csrfToken: str


class UserList(BaseModel):
    items: list[UserView]
