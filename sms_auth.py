"""Alibaba Cloud phone-number verification adapter."""

import re
from types import SimpleNamespace

from config import ALIBABA_CLOUD_ACCESS_KEY_ID, ALIBABA_CLOUD_ACCESS_KEY_SECRET


PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
_SMS_ENDPOINT = "dypnsapi.aliyuncs.com"
_TEMPLATE_PARAM = '{"code":"##code##","min":"5"}'


class SMSAuthError(RuntimeError):
    """Safe error exposed when the SMS provider is unavailable."""


def normalize_mainland_phone(value: str) -> str:
    """Return a validated mainland China mobile number."""
    phone = value.strip() if isinstance(value, str) else ""
    if not PHONE_PATTERN.fullmatch(phone):
        raise ValueError("请输入有效的中国大陆手机号")
    return phone


def mask_phone_number(phone_number: str) -> str:
    """Return a safe display form of a validated phone number."""
    phone = normalize_mainland_phone(phone_number)
    return f"{phone[:3]}****{phone[-4:]}"


class AlibabaSmsAuthService:
    """Delegate verification-code issuance and validation to Alibaba Cloud."""

    def __init__(self, sign_name, template_code, client_factory=None):
        self.sign_name = sign_name
        self.template_code = template_code
        self.client_factory = client_factory

    def _create_sdk_client(self):
        """Load the optional SDK only when SMS is actually used."""
        from alibabacloud_dypnsapi20170525.client import Client
        from alibabacloud_tea_openapi import models as open_api_models

        configuration = open_api_models.Config(
            access_key_id=ALIBABA_CLOUD_ACCESS_KEY_ID,
            access_key_secret=ALIBABA_CLOUD_ACCESS_KEY_SECRET,
            endpoint=_SMS_ENDPOINT,
        )
        return Client(configuration)

    def _call_provider(self, method_name, request_name, **request_values):
        if self.client_factory is not None:
            client = self.client_factory()
            request = SimpleNamespace(**request_values)
            runtime = SimpleNamespace()
        else:
            from alibabacloud_dypnsapi20170525 import models as dypnsapi_models
            from alibabacloud_tea_util import models as util_models

            client = self._create_sdk_client()
            request = getattr(dypnsapi_models, request_name)(**request_values)
            runtime = util_models.RuntimeOptions()
        return getattr(client, method_name)(request, runtime)

    @staticmethod
    def _successful_response_body(response):
        """Return the provider body only when the business request succeeded."""
        body = response.body
        if body.success is not True or body.code != "OK":
            raise ValueError("Provider rejected the request")
        return body

    def send_binding_code(self, phone_number: str) -> None:
        """Ask Alibaba Cloud to generate and send a binding verification code."""
        phone = normalize_mainland_phone(phone_number)
        try:
            response = self._call_provider(
                "send_sms_verify_code_with_options",
                "SendSmsVerifyCodeRequest",
                country_code="86",
                phone_number=phone,
                sign_name=self.sign_name,
                template_code=self.template_code,
                template_param=_TEMPLATE_PARAM,
            )
            self._successful_response_body(response)
        except Exception:
            raise SMSAuthError("短信服务暂时不可用，请稍后再试") from None

    def verify_binding_code(self, phone_number: str, code: str) -> bool:
        """Return whether Alibaba Cloud accepted the supplied verification code."""
        phone = normalize_mainland_phone(phone_number)
        try:
            response = self._call_provider(
                "check_sms_verify_code_with_options",
                "CheckSmsVerifyCodeRequest",
                country_code="86",
                phone_number=phone,
                verify_code=code,
            )
            body = self._successful_response_body(response)
            return body.model.verify_result == "PASS"
        except Exception:
            raise SMSAuthError("短信服务暂时不可用，请稍后再试") from None
