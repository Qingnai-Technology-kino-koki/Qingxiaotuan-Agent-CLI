"""Behavioral tests for the Kimi OAuth Python port (qingxiaotuan.ports.oauth)."""

from __future__ import annotations

import os

import pytest

from qingxiaotuan.ports.oauth import (
    CustomRegistryApiError,
    CustomRegistryModelEntry,
    CustomRegistryProviderEntry,
    CustomRegistrySource,
    DeviceAuthorization,
    DeviceHeaders,
    DevicePollResult,
    ManagedKimiConfigShape,
    OAuthError,
    OAuthUnauthorizedError,
    RetryableRefreshError,
    TokenInfo,
    TokenState,
    apply_custom_registry_entries,
    apply_custom_registry_provider,
    assert_kimi_host_identity,
    ascii_header,
    capabilities_from_custom_entry,
    classify_poll_result,
    classify_refresh_error,
    classify_token,
    create_kimi_device_headers,
    create_kimi_device_id,
    create_kimi_user_agent,
    extract_api_error_message,
    merge_refreshed_model_alias,
    parse_device_authorization,
    parse_kimi_code_custom_headers,
    parse_token_response,
    read_kimi_device_id,
    remove_custom_registry_provider,
    replace_user_agent_product,
    required_ascii_header,
    revoked_tombstone,
    to_model_entry,
    to_provider_entry,
    token_from_wire,
    token_to_wire,
)


# ── errors ──────────────────────────────────────────────────────────────
def test_error_hierarchy():
    assert issubclass(OAuthUnauthorizedError, OAuthError)
    assert issubclass(RetryableRefreshError, OAuthError)
    err = OAuthUnauthorizedError("nope")
    assert isinstance(err, OAuthError)
    assert str(err) == "nope"


# ── api-error message extraction ────────────────────────────────────────
def test_extract_message_direct_keys():
    assert extract_api_error_message({"message": "boom"}) == "boom"
    assert extract_api_error_message({"detail": "  detailed  "}) == "detailed"
    assert extract_api_error_message({"error_description": "desc"}) == "desc"


def test_extract_message_from_error_string():
    assert extract_api_error_message({"error": "invalid_grant"}) == "invalid_grant"


def test_extract_message_nested_error_object():
    payload = {"error": {"code": "X", "message": "nested boom"}}
    assert extract_api_error_message(payload) == "nested boom"


def test_extract_message_from_errors_array():
    payload = {"errors": [{"detail": "first"}, {"message": "second"}]}
    assert extract_api_error_message(payload) == "first"


def test_extract_message_array_of_objects():
    payload = [{"message": None}, {"message": "found"}]
    assert extract_api_error_message(payload) == "found"


def test_extract_message_missing_returns_none():
    assert extract_api_error_message({"foo": 1}) is None
    assert extract_api_error_message("not a record") is None
    assert extract_api_error_message(None) is None
    assert extract_api_error_message({"message": "   "}) is None


# ── error classification from HTTP status ───────────────────────────────
def test_classify_refresh_unauthorized():
    err = classify_refresh_error(401, "invalid_grant", "bad creds")
    assert isinstance(err, OAuthUnauthorizedError)
    assert "bad creds" in str(err)
    assert isinstance(classify_refresh_error(403), OAuthUnauthorizedError)
    assert isinstance(classify_refresh_error(200, "invalid_grant"), OAuthUnauthorizedError)


def test_classify_refresh_retryable():
    for status in (429, 500, 502, 503, 504):
        err = classify_refresh_error(status)
        assert isinstance(err, RetryableRefreshError), status


def test_classify_refresh_generic():
    err = classify_refresh_error(400, detail="bad request")
    assert type(err) is OAuthError
    assert "bad request" in str(err)


# ── token response / device auth parsing ────────────────────────────────
def test_parse_token_response_valid():
    token = parse_token_response(
        {
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 3600,
            "scope": "read",
            "token_type": "Bearer",
        },
        now=1_000_000,
    )
    assert token.access_token == "a"
    assert token.refresh_token == "r"
    assert token.expires_at == 1_003_600
    assert token.expires_in == 3600
    assert token.scope == "read"
    assert token.token_type == "Bearer"


def test_parse_token_response_missing_fields():
    with pytest.raises(OAuthError):
        parse_token_response({"access_token": "a"})
    with pytest.raises(OAuthError):
        parse_token_response({"access_token": "", "refresh_token": "r", "expires_in": 10})
    with pytest.raises(OAuthError):
        parse_token_response({"access_token": "a", "refresh_token": "r", "expires_in": -1})


def test_parse_device_authorization_valid_and_defaults():
    da = parse_device_authorization(
        {
            "user_code": "ABCD",
            "device_code": "dc",
            "verification_uri_complete": "https://x/y",
        }
    )
    assert da.user_code == "ABCD"
    assert da.device_code == "dc"
    assert da.verification_uri_complete == "https://x/y"
    assert da.interval == 5
    assert da.expires_in is None


def test_parse_device_authorization_missing_fields():
    with pytest.raises(OAuthError):
        parse_device_authorization({"user_code": "x"})


# ── poll result classification ──────────────────────────────────────────
def test_classify_poll_success():
    res = classify_poll_result(
        200, {"access_token": "a", "refresh_token": "r", "expires_in": 100}
    )
    assert res.kind == "success"
    assert isinstance(res.token, TokenInfo)


def test_classify_poll_pending_expired_denied():
    pending = classify_poll_result(400, {"error": "authorization_pending"})
    assert pending.kind == "pending"
    expired = classify_poll_result(400, {"error": "expired_token"})
    assert expired.kind == "expired"
    denied = classify_poll_result(400, {"error": "access_denied", "error_description": "no"})
    assert denied.kind == "denied"
    assert denied.description == "no"


def test_classify_poll_unknown_raises():
    with pytest.raises(OAuthError):
        classify_poll_result(400, {"error": "weird"})
    with pytest.raises(OAuthError):
        classify_poll_result(503, {"error": "x"})


# ── token wire round-trip ───────────────────────────────────────────────
def test_token_wire_round_trip():
    token = TokenInfo(
        access_token="a", refresh_token="r", expires_at=123, scope="s",
        token_type="Bearer", expires_in=60,
    )
    wire = token_to_wire(token)
    assert wire == {
        "access_token": "a", "refresh_token": "r", "expires_at": 123,
        "scope": "s", "token_type": "Bearer", "expires_in": 60,
    }
    back = token_from_wire(wire)
    assert back == token


def test_token_from_wire_partial_and_garbage():
    partial = token_from_wire({"access_token": "a"})
    assert partial.access_token == "a"
    assert partial.refresh_token == ""
    assert partial.expires_at == 0
    assert token_from_wire("not a dict") == TokenInfo()


# ── token state classification ──────────────────────────────────────────
def test_classify_token_states():
    assert classify_token(None).kind == "missing"
    revoked = classify_token(TokenInfo(access_token=""))
    assert revoked.kind == "revoked"
    assert revoked.scope == ""
    valid = classify_token(TokenInfo(access_token="x", scope="s"))
    assert valid.kind == "valid"
    assert isinstance(valid.token, TokenInfo)


def test_revoked_tombstone():
    tomb = revoked_tombstone(TokenInfo(access_token="x", scope="s", token_type="Bearer"))
    assert tomb.access_token == ""
    assert tomb.refresh_token == ""
    assert tomb.expires_at == 0
    assert tomb.scope == "s"
    assert tomb.token_type == "Bearer"


# ── identity helpers ────────────────────────────────────────────────────
def test_parse_custom_headers():
    assert parse_kimi_code_custom_headers(None) == {}
    assert parse_kimi_code_custom_headers("") == {}
    raw = "X-A: 1\nno-colon-line\nY-B:  2  \n"
    assert parse_kimi_code_custom_headers(raw) == {"X-A": "1", "Y-B": "2"}


def test_user_agent_build_and_replace():
    ua = create_kimi_user_agent("kimi-code-cli", "1.2.3")
    assert ua == "kimi-code-cli/1.2.3"
    ua2 = create_kimi_user_agent("kimi-code-cli", "1.2.3", "web")
    assert ua2 == "kimi-code-cli/1.2.3 (web)"
    assert replace_user_agent_product(ua2, "acme") == "acme/1.2.3 (web)"
    assert replace_user_agent_product("bare", "acme") == "acme"


def test_ascii_header_strips_and_falls_back():
    # Non-ASCII bytes (é = U+00E9) are stripped, leaving 'hllo world'.
    assert ascii_header("héllo\x00 world") == "hllo world"
    assert ascii_header("", "fallback") == "fallback"
    with pytest.raises(ValueError):
        required_ascii_header("", "field")
    assert required_ascii_header("ok", "field") == "ok"


def test_device_id_persist_and_idempotent(tmp_path):
    home = str(tmp_path)
    first_launch_called = []
    first = create_kimi_device_id(home, on_first_launch=first_launch_called.append)
    assert read_kimi_device_id(home) == first
    second = create_kimi_device_id(home)
    assert second == first
    assert len(first_launch_called) == 1


def test_device_headers_and_assert_identity(tmp_path):
    headers = create_kimi_device_headers(str(tmp_path), "1.0", "kimi_code_cli")
    assert isinstance(headers, DeviceHeaders)
    assert headers.x_msh_platform == "kimi_code_cli"
    assert headers.x_msh_version == "1.0"
    assert headers.x_msh_device_id

    identity = assert_kimi_host_identity(
        __import__("qingxiaotuan.ports.oauth", fromlist=["KimiHostIdentity"]).KimiHostIdentity(
            product_name="p", version="1", platform="kimi_code_cli"
        )
    )
    assert identity.product_name == "p"
    with pytest.raises(ValueError):
        assert_kimi_host_identity(None)


# ── custom-registry parsing ─────────────────────────────────────────────
def test_to_model_entry_valid_and_invalid():
    entry = to_model_entry(
        {
            "id": "m1",
            "name": "Model 1",
            "limit": {"context": 200000.0, "output": 4096},
            "tool_call": True,
            "reasoning": True,
            "modalities": {"input": ["image", "text"], "output": ["image"]},
            "support_efforts": ["low", "high"],
            "default_effort": "high",
        }
    )
    assert isinstance(entry, CustomRegistryModelEntry)
    assert entry.id == "m1"
    assert entry.limit == {"context": 200000, "output": 4096}
    assert entry.modalities == {"input": ["image", "text"], "output": ["image"]}
    assert entry.support_efforts == ["low", "high"]

    assert to_model_entry({"name": "no id"}) is None
    assert to_model_entry("not a record") is None
    # non-integer limit is dropped
    partial = to_model_entry({"id": "m", "limit": {"context": 1.5}})
    assert partial.limit is None


def test_to_provider_entry_filters_unknown_types_and_models():
    raw = {
        "id": "prov1",
        "name": "Prov",
        "api": "https://api.example.com",
        "type": "anthropic",
        "env": ["KEY_A"],
        "models": {
            "good": {"id": "good"},
            "bad": {"name": "no id"},
        },
    }
    entry = to_provider_entry(raw)
    assert isinstance(entry, CustomRegistryProviderEntry)
    assert entry.type == "anthropic"
    assert entry.env == ["KEY_A"]
    assert set(entry.models) == {"good"}

    assert to_provider_entry({"id": "x", "name": "n", "api": "a", "type": "bogus", "models": {}}) is None


def test_capabilities_from_custom_entry():
    assert capabilities_from_custom_entry(
        CustomRegistryModelEntry(id="m", tool_call=True)
    ) == ["tool_use"]
    assert set(
        capabilities_from_custom_entry(
            CustomRegistryModelEntry(
                id="m",
                reasoning=True,
                modalities={"input": ["image", "video"], "output": ["image", "audio"]},
            )
        )
    ) == {"thinking", "image_in", "video_in", "image_out", "audio_out"}


# ── custom-registry config application ──────────────────────────────────
def test_apply_custom_registry_provider_merges_models():
    config = ManagedKimiConfigShape()
    source = CustomRegistrySource(url="https://r/api.json", api_key="k")
    entry = CustomRegistryProviderEntry(
        id="prov1",
        name="Prov",
        api="https://api",
        type="anthropic",
        models={
            "m1": CustomRegistryModelEntry(id="m1", tool_call=True),
        },
    )
    apply_custom_registry_provider(config, entry, source)

    assert config.providers["prov1"]["type"] == "anthropic"
    assert config.providers["prov1"]["apiKey"] == "k"
    assert config.providers["prov1"]["source"]["url"] == "https://r/api.json"

    alias = config.models["prov1/m1"]
    assert alias["provider"] == "prov1"
    assert alias["model"] == "m1"
    assert alias["capabilities"] == ["tool_use"]
    assert alias["maxContextSize"] == 131072  # default


def test_apply_entries_removes_stale_same_url_providers():
    config = ManagedKimiConfigShape()
    config.providers["old"] = {
        "source": {"kind": "apiJson", "url": "https://r/api.json", "apiKey": "x"}
    }
    config.models = {"old/m": {"provider": "old", "model": "m"}}
    config.default_model = "old/m"

    source = CustomRegistrySource(url="https://r/api.json", api_key="k")
    entry = CustomRegistryProviderEntry(
        id="new", name="New", api="https://api", type="openai",
        models={"m": CustomRegistryModelEntry(id="m")},
    )
    apply_custom_registry_entries(config, {"new": entry}, source)

    assert "old" not in config.providers
    assert "new" in config.providers
    assert "old/m" not in config.models
    assert config.default_model is None


def test_remove_custom_registry_provider_clears_defaults():
    config = ManagedKimiConfigShape()
    config.providers["p"] = {}
    config.models = {"p/m": {"provider": "p", "model": "m"}}
    config.default_model = "p/m"
    config.default_provider = "p"
    remove_custom_registry_provider(config, "p")
    assert "p" not in config.providers
    assert config.default_model is None
    assert config.default_provider is None


def test_merge_preserves_user_extras_and_overrides():
    existing = {
        "provider": "p",
        "model": "m",
        "userPinned": True,  # user extra (not remote-owned)
        "overrides": {"maxContextSize": 999},  # user overrides preserved
    }
    remote = {"provider": "p", "model": "m", "maxContextSize": 5000, "capabilities": ["tool_use"]}
    merged = merge_refreshed_model_alias(
        existing, remote, frozenset({"provider", "model", "maxContextSize", "capabilities"})
    )
    assert merged["maxContextSize"] == 5000  # remote wins
    assert merged["userPinned"] is True  # user extra survives
    assert merged["overrides"] == {"maxContextSize": 999}  # overrides preserved (cloned)


def test_custom_registry_api_error_carries_status():
    err = CustomRegistryApiError("fail", 404)
    assert err.status == 404
    assert isinstance(err, Exception)
