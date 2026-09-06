"""自研实现 (对齐上游 OAuth 的纯逻辑、轻依赖接口面)。

Mirrors the public API of the upstream TypeScript reference implementation for the parts that are
data structures and logic portable to Python with the stdlib only. Network /
device / browser interactions (``fetchCustomRegistry``, the live token manager,
the HTTP wrappers' transport) are intentionally excluded -- see ``SKIPPED.md``.
"""

from __future__ import annotations

from .api_error import extract_api_error_message
from .constants import DEFAULT_KIMI_CODE_OAUTH_HOST, KIMI_CODE_FLOW_CONFIG
from .custom_registry import (
    CUSTOM_REGISTRY_DEFAULT_CAPABILITIES,
    CUSTOM_REGISTRY_DEFAULT_MAX_CONTEXT,
    CustomRegistryApiError,
    CustomRegistryModelEntry,
    CustomRegistryProviderEntry,
    CustomRegistryProviderType,
    CustomRegistrySource,
    ManagedKimiConfigShape,
    apply_custom_registry_entries,
    apply_custom_registry_provider,
    capabilities_from_custom_entry,
    remove_custom_registry_provider,
    to_model_entry,
    to_provider_entry,
)
from .error_classification import (
    DevicePollResult,
    classify_poll_result,
    classify_refresh_error,
    parse_device_authorization,
    parse_token_response,
)
from .errors import (
    DeviceCodeExpiredError,
    DeviceCodeTimeoutError,
    OAuthAccessDeniedError,
    OAuthConnectionError,
    OAuthError,
    OAuthUnauthorizedError,
    RetryableRefreshError,
)
from .identity import (
    KIMI_CODE_CUSTOM_HEADERS_ENV,
    KIMI_CODE_PLATFORM,
    KimiHostIdentity,
    KimiIdentityOptions,
    assert_kimi_host_identity,
    ascii_header,
    create_kimi_default_headers,
    create_kimi_device_headers,
    create_kimi_device_id,
    create_kimi_user_agent,
    parse_kimi_code_custom_headers,
    read_kimi_device_id,
    replace_user_agent_product,
    required_ascii_header,
)
from .model_alias_merge import (
    CUSTOM_REGISTRY_MODEL_FIELDS,
    MANAGED_KIMI_MODEL_FIELDS,
    ManagedKimiModelAlias,
    ManagedKimiModelAliasOverrides,
    merge_refreshed_model_alias,
)
from .token_state import TokenState, classify_token, revoked_tombstone
from .types import (
    DeviceAuthorization,
    DeviceHeaders,
    OAuthFlowConfig,
    TokenInfo,
    token_from_wire,
    token_to_wire,
)
from .utils import is_record, non_empty_string

__all__ = [
    # api_error
    "extract_api_error_message",
    # constants
    "DEFAULT_KIMI_CODE_OAUTH_HOST",
    "KIMI_CODE_FLOW_CONFIG",
    # custom_registry
    "CUSTOM_REGISTRY_DEFAULT_CAPABILITIES",
    "CUSTOM_REGISTRY_DEFAULT_MAX_CONTEXT",
    "CustomRegistryApiError",
    "CustomRegistryModelEntry",
    "CustomRegistryProviderEntry",
    "CustomRegistryProviderType",
    "CustomRegistrySource",
    "ManagedKimiConfigShape",
    "apply_custom_registry_entries",
    "apply_custom_registry_provider",
    "capabilities_from_custom_entry",
    "remove_custom_registry_provider",
    "to_model_entry",
    "to_provider_entry",
    # error_classification
    "DevicePollResult",
    "classify_poll_result",
    "classify_refresh_error",
    "parse_device_authorization",
    "parse_token_response",
    # errors
    "DeviceCodeExpiredError",
    "DeviceCodeTimeoutError",
    "OAuthAccessDeniedError",
    "OAuthConnectionError",
    "OAuthError",
    "OAuthUnauthorizedError",
    "RetryableRefreshError",
    # identity
    "KIMI_CODE_CUSTOM_HEADERS_ENV",
    "KIMI_CODE_PLATFORM",
    "KimiHostIdentity",
    "KimiIdentityOptions",
    "assert_kimi_host_identity",
    "ascii_header",
    "create_kimi_default_headers",
    "create_kimi_device_headers",
    "create_kimi_device_id",
    "create_kimi_user_agent",
    "parse_kimi_code_custom_headers",
    "read_kimi_device_id",
    "replace_user_agent_product",
    "required_ascii_header",
    # model_alias_merge
    "CUSTOM_REGISTRY_MODEL_FIELDS",
    "MANAGED_KIMI_MODEL_FIELDS",
    "ManagedKimiModelAlias",
    "ManagedKimiModelAliasOverrides",
    "merge_refreshed_model_alias",
    # token_state
    "TokenState",
    "classify_token",
    "revoked_tombstone",
    # types
    "DeviceAuthorization",
    "DeviceHeaders",
    "OAuthFlowConfig",
    "TokenInfo",
    "token_from_wire",
    "token_to_wire",
    # utils
    "is_record",
    "non_empty_string",
]
