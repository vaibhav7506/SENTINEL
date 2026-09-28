"""Console verification credentials must stay on the selected trusted HTTPS origin."""

import urllib.request

import pytest

from scripts.verify_deployment import TrustedRedirect


@pytest.mark.parametrize("destination", ["http://console.example/", "https://elsewhere.example/"])
def test_credentials_cannot_follow_unsafe_redirect(destination):
    request = urllib.request.Request(
        "https://console.example/", headers={"Authorization": "Basic isolated-test-value"}
    )
    with pytest.raises(ValueError, match="trusted HTTPS origin"):
        TrustedRedirect().redirect_request(request, None, 302, "Found", {}, destination)


def test_credentials_can_follow_same_origin_https_redirect():
    request = urllib.request.Request(
        "https://console.example/", headers={"Authorization": "Basic isolated-test-value"}
    )
    redirected = TrustedRedirect().redirect_request(
        request, None, 302, "Found", {}, "https://console.example/login"
    )
    assert redirected.full_url == "https://console.example/login"
    assert redirected.get_header("Authorization") == "Basic isolated-test-value"
