import pytest
from alibabacloud_dypnsapi20170525 import models as dypnsapi_models

from sms_auth import (
    AlibabaSmsAuthService,
    SMSAuthError,
    mask_phone_number,
    normalize_mainland_phone,
)


TEST_CODE = "1" * 6


def send_response(success=True, code="OK"):
    return dypnsapi_models.SendSmsVerifyCodeResponse(
        body=dypnsapi_models.SendSmsVerifyCodeResponseBody(
            success=success, code=code
        )
    )


def verify_response(success=True, code="OK", verify_result="PASS"):
    return dypnsapi_models.CheckSmsVerifyCodeResponse(
        body=dypnsapi_models.CheckSmsVerifyCodeResponseBody(
            success=success,
            code=code,
            model=dypnsapi_models.CheckSmsVerifyCodeResponseBodyModel(
                verify_result=verify_result
            ),
        )
    )


def test_mainland_phone_normalization_and_masking():
    assert normalize_mainland_phone(" 13800138000 ") == "13800138000"
    assert mask_phone_number("13800138000") == "138****8000"
    for value in ("", "12800138000", "1380013800", "+8613800138000"):
        with pytest.raises(ValueError):
            normalize_mainland_phone(value)


def test_service_accepts_successful_sdk_send_response_and_pass_verification():
    sent_requests = []
    verification_requests = []

    class Client:
        def send_sms_verify_code_with_options(self, request, runtime):
            sent_requests.append(request)
            return send_response()

        def check_sms_verify_code_with_options(self, request, runtime):
            verification_requests.append(request)
            return verify_response()

    service = AlibabaSmsAuthService("sign", "100005", client_factory=lambda: Client())
    service.send_binding_code("13800138000")
    assert service.verify_binding_code("13800138000", TEST_CODE) is True

    sent_request = sent_requests[0]
    assert sent_request.country_code == "86"
    assert sent_request.phone_number == "13800138000"
    assert sent_request.sign_name == "sign"
    assert sent_request.template_code == "100005"
    assert sent_request.template_param == '{"code":"##code##","min":"5"}'

    verification_request = verification_requests[0]
    assert verification_request.country_code == "86"
    assert verification_request.phone_number == "13800138000"
    assert verification_request.verify_code == TEST_CODE


@pytest.mark.parametrize(
    ("success", "code"), ((False, "OK"), (True, "BUSINESS_ERROR"))
)
def test_service_rejects_a_send_business_failure_response(success, code):
    class Client:
        def send_sms_verify_code_with_options(self, request, runtime):
            return send_response(success=success, code=code)

    service = AlibabaSmsAuthService("sign", "100005", client_factory=Client)

    with pytest.raises(SMSAuthError):
        service.send_binding_code("13800138000")


def test_service_returns_false_for_non_pass_sdk_verification_result():
    class Client:
        def check_sms_verify_code_with_options(self, request, runtime):
            return verify_response(verify_result="UNKNOWN")

    service = AlibabaSmsAuthService("sign", "100005", client_factory=Client)

    assert service.verify_binding_code("13800138000", TEST_CODE) is False


def test_service_hides_provider_failure_details():
    class FailingClient:
        def send_sms_verify_code_with_options(self, request, runtime):
            raise OSError("raw provider error")

    service = AlibabaSmsAuthService("sign", "100005", client_factory=FailingClient)

    with pytest.raises(SMSAuthError) as raised:
        service.send_binding_code("13800138000")

    assert str(raised.value) == "短信服务暂时不可用，请稍后再试"
    assert raised.value.__cause__ is None
    assert "raw provider error" not in str(raised.value)


@pytest.mark.parametrize("operation", ("send", "verify"))
def test_service_hides_malformed_provider_response_envelopes(operation):
    class MalformedResponseClient:
        def send_sms_verify_code_with_options(self, request, runtime):
            return dypnsapi_models.SendSmsVerifyCodeResponse()

        def check_sms_verify_code_with_options(self, request, runtime):
            return dypnsapi_models.CheckSmsVerifyCodeResponse(
                body=dypnsapi_models.CheckSmsVerifyCodeResponseBody(
                    success=True, code="OK"
                )
            )

    service = AlibabaSmsAuthService(
        "sign", "100005", client_factory=MalformedResponseClient
    )

    with pytest.raises(SMSAuthError) as raised:
        if operation == "send":
            service.send_binding_code("13800138000")
        else:
            service.verify_binding_code("13800138000", TEST_CODE)

    assert str(raised.value) == "短信服务暂时不可用，请稍后再试"
    assert raised.value.__cause__ is None
