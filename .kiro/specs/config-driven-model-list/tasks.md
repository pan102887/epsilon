# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - HTTP 模型发现导致注册失败
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to concrete failing cases — providers that don't support `/v1/models` (e.g., Anthropic/Claude) or when the endpoint is unavailable
  - Create test file: `test/infrastructure/model_access/test_provider_registry_bug_condition.py`
  - Use Hypothesis to generate random provider names and model lists
  - Bug Condition from design: `isBugCondition(input)` where `input.uses_http_discovery == True AND (NOT provider_supports_v1_models OR NOT endpoint_available)`
  - Test asserts: calling `register_provider(provider_name, adapter, models=["model-a"])` with the NEW signature (models list, no HTTP params) should return `True` and register all models — this will FAIL on unfixed code because the current signature requires `api_base`, `api_key`, `timeout`, `max_retries` and uses HTTP discovery
  - Concrete failing case: construct a `ProviderRegistry` without `http_client`, call `register_provider` with `models=` keyword arg — expect `TypeError` on unfixed code (signature mismatch)
  - Run test on UNFIXED code with `uv run pytest test/infrastructure/model_access/test_provider_registry_bug_condition.py -x`
  - **EXPECTED OUTCOME**: Test FAILS (TypeError: unexpected keyword argument 'models') — this confirms the bug exists
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - 注册中心核心功能不变
  - **IMPORTANT**: Follow observation-first methodology
  - Create test file: `test/infrastructure/model_access/test_provider_registry_preservation.py`
  - Observe on UNFIXED code: after successful registration, `list_models()` returns sorted `ModelInfo` list with correct `owned_by` and `providers`
  - Observe on UNFIXED code: `get_adapter_for_model()` returns adapters via Round-Robin for multi-provider models
  - Observe on UNFIXED code: `get_default_model()` returns configured default or first registered model
  - Observe on UNFIXED code: `get_adapter_for_model("nonexistent")` raises `ModelAccessError`
  - **Approach**: Directly populate `ProviderRegistry` internal state (`_providers`, `_model_providers`, `_model_rr`) to test query behaviors independently of `register_provider` signature — this isolates preservation from the bug
  - Write Hypothesis property-based tests:
    - Property: for all sets of (provider_name, model_names) registrations, `list_models()` returns exactly the registered models sorted alphabetically, each with correct `providers` frozenset
    - Property: for all models with N providers, calling `get_adapter_for_model()` N times cycles through all providers (Round-Robin)
    - Property: `get_default_model()` returns configured default when set, or first registered model otherwise
    - Property: `get_adapter_for_model("unregistered_model")` always raises `ModelAccessError`
  - Run tests on UNFIXED code with `uv run pytest test/infrastructure/model_access/test_provider_registry_preservation.py -x`
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. Fix: 移除 HTTP 模型发现，改为配置驱动的模型列表注册

  - [x] 3.1 Update `ModelRegistryPort.register_provider` signature in `src/domain/model_access/ports.py`
    - Change signature: remove `api_base`, `api_key`, `timeout`, `max_retries` params; add `models: list[str]`
    - Change from `async def` to `def` (no HTTP I/O needed)
    - Update docstring: describe config-driven registration, remove HTTP discovery references
    - Update class docstring: remove `/v1/models` discovery description
    - _Bug_Condition: register_provider 依赖 HTTP 发现参数 (api_base, api_key, timeout, max_retries)_
    - _Expected_Behavior: register_provider 接受 models: list[str] 参数，直接使用配置列表注册_
    - _Preservation: ModelRegistryPort 的 list_models, get_adapter_for_model, get_default_model 签名不变_
    - _Requirements: 2.3, 2.4_

  - [x] 3.2 Refactor `ProviderRegistry` in `src/infrastructure/model_access/provider_registry.py`
    - Remove `http_client` param from `__init__`, remove `self._http_client` attribute
    - Remove `import httpx`
    - Rewrite `register_provider`: change to sync `def`, accept `models: list[str]`, directly use the list (no `_discover_models` call)
    - Delete `_discover_models` method entirely
    - Update module docstring: remove HTTP discovery description, describe config-driven approach
    - Keep all other methods (`list_models`, `get_adapter_for_model`, `get_default_model`) unchanged
    - _Bug_Condition: _discover_models() 通过 HTTP 请求 /v1/models 发现模型列表_
    - _Expected_Behavior: register_provider 直接使用传入的 models 列表完成注册_
    - _Preservation: list_models, get_adapter_for_model, get_default_model, Round-Robin 逻辑不变_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.3 Remove discovery config fields from `RouterConfig` in `src/infrastructure/model_access/router_config.py`
    - Remove `discovery_timeout` field
    - Remove `discovery_max_retries` field
    - Update class docstring: remove discovery parameter descriptions
    - _Bug_Condition: discovery_timeout 和 discovery_max_retries 仅服务于 HTTP 发现逻辑_
    - _Expected_Behavior: RouterConfig 不再包含模型发现相关配置_
    - _Requirements: 2.3_

  - [x] 3.4 Update `_init_model_client` call site in `src/application/container_config.py`
    - Change `register_provider` call: pass `models=config.get_model_list()` instead of `api_base`, `api_key`, `timeout`, `max_retries`
    - Change from `await _provider_registry.register_provider(...)` to `_provider_registry.register_provider(...)`
    - Remove `ProviderRegistry` constructor's `http_client` usage (already absent in current code — verify)
    - Update module docstring: replace "自动调用 /v1/models 发现模型列表" with config-driven description
    - Update `_init_model_client` docstring: remove HTTP discovery references
    - _Bug_Condition: 调用 register_provider 时传入 api_base, api_key, timeout, max_retries_
    - _Expected_Behavior: 调用 register_provider 时传入 models=config.get_model_list()_
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

  - [x] 3.5 Update `config.properties`
    - Remove `MODEL_ROUTER_DISCOVERY_TIMEOUT=10`
    - Remove `MODEL_ROUTER_DISCOVERY_MAX_RETRIES=3`
    - Ensure `MODEL_CLIPROXY_MODELS` config exists (add `MODEL_CLIPROXY_MODELS=glm-4.7` if missing)
    - Ensure `MODEL_CLAUDE_MODELS` config exists (add `MODEL_CLAUDE_MODELS=claude-3-5-sonnet-20241022` if missing)
    - _Requirements: 2.2, 2.5_

  - [x] 3.6 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - 配置驱动的模型列表注册
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run: `uv run pytest test/infrastructure/model_access/test_provider_registry_bug_condition.py -x`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — register_provider now accepts models list)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.7 Verify preservation tests still pass
    - **Property 2: Preservation** - 注册中心核心功能不变
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run: `uv run pytest test/infrastructure/model_access/test_provider_registry_preservation.py -x`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions in list_models, get_adapter_for_model, get_default_model, Round-Robin)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `uv run pytest test/ -x` in `epsilon-boot/`
  - Ensure all tests pass, ask the user if questions arise.
