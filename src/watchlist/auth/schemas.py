import re

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ValidationError, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,30}$")
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
EMAIL_MAX_LENGTH = 320


def field_error(message: str) -> PydanticCustomError:
    return PydanticCustomError("form_error", message)


class RegisterForm(BaseModel):
    email: str
    username: str
    password: str
    password_confirm: str

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise field_error("Вкажіть електронну пошту")
        if len(value) > EMAIL_MAX_LENGTH:
            raise field_error("Адреса пошти задовга")
        try:
            normalized = validate_email(value, check_deliverability=False).normalized
        except EmailNotValidError:
            raise field_error("Некоректна адреса пошти") from None
        return normalized.lower()

    @field_validator("username")
    @classmethod
    def check_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise field_error("Вкажіть ім'я користувача")
        if not USERNAME_PATTERN.fullmatch(value):
            raise field_error("Від 3 до 30 символів: латинські літери, цифри та _")
        return value

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        if len(value) < PASSWORD_MIN_LENGTH:
            raise field_error(f"Пароль має містити щонайменше {PASSWORD_MIN_LENGTH} символів")
        if len(value) > PASSWORD_MAX_LENGTH:
            raise field_error(f"Пароль має містити не більше {PASSWORD_MAX_LENGTH} символів")
        return value

    @field_validator("password_confirm")
    @classmethod
    def check_password_confirm(cls, value: str, info: ValidationInfo) -> str:
        password = info.data.get("password")
        if password is not None and value != password:
            raise field_error("Паролі не збігаються")
        return value


class LoginForm(BaseModel):
    login: str
    password: str

    @field_validator("login")
    @classmethod
    def check_login(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise field_error("Вкажіть пошту або ім'я користувача")
        return value

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        if not value:
            raise field_error("Вкажіть пароль")
        if len(value) > PASSWORD_MAX_LENGTH:
            raise field_error("Неправильний логін або пароль")
        return value


def form_errors(error: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for item in error.errors():
        field = str(item["loc"][0]) if item["loc"] else "__all__"
        errors.setdefault(field, item["msg"])
    return errors
