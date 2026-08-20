# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - 异常栈多行输出（Exception Traceback Multiline Output）
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to concrete failing cases — simulate uvicorn's default LOGGING_CONFIG being applied, then log an exception with `exc_info`
  - **Test file**: `epsilon-boot/test/test_log_exception_newline.py`
  - **Test setup**:
    1. Import `uvicorn.config.LOGGING_CONFIG` and apply it via `logging.config.dictConfig(LOGGING_CONFIG)` to simulate uvicorn startup覆盖
    2. Inject the OTel record factory (`_otel_record_factory` from `main.py`) so `otelTraceID`/`otelSpanID` fields are available
    3. Create a logger and call `logger.exception()` or `logger.error(..., exc_info=True)` inside a try/except block
    4. Capture the formatted output using a `logging.handlers.MemoryHandler` or `io.StringIO` stream handler
  - **Assertions** (match Expected Behavior Properties from design):
    - The formatted output CONTAINS `"\n"` (newline characters within the traceback portion)
    - The formatted output CONTAINS `"Traceback (most recent call last):"`
    - Each stack frame appears on a separate line
  - **Property-based aspect**: Use Hypothesis to generate random exception types (`ValueError`, `TypeError`, `RuntimeError`, `KeyError`) and random message strings; for ALL generated exceptions, the above assertions must hold
  - Run test on UNFIXED code via `cd epsilon-boot && uv run pytest test/test_log_exception_newline.py::test_bug_condition_exception_traceback_multiline -x`
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists: uvicorn's default formatter compresses traceback to single line)
  - Document counterexamples found (e.g., "logger.exception() output does not contain newline in traceback portion after uvicorn LOGGING_CONFIG is applied")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - 普通日志格式不变（Normal Log Format Unchanged）
  - **IMPORTANT**: Follow observation-first methodology
  - **Test file**: `epsilon-boot/test/test_log_exception_newline.py` (same file, separate test function)
  - **Observation phase** (run on UNFIXED code):
    1. Apply uvicorn's `LOGGING_CONFIG` via `logging.config.dictConfig()` to simulate current (unfixed) environment
    2. Inject OTel record factory for `otelTraceID`/`otelSpanID` defaults
    3. Observe: `logger.info("hello")` produces single-line output matching format `"YYYY-MM-DD HH:MM:SS INFO [logger_name] [trace_id=0 span_id=0] hello"`
    4. Observe: `logger.warning("warn msg")` produces single-line output with `WARNING` level
    5. Observe: output is exactly one line (no trailing newlines beyond the single record)
  - **Property-based tests**:
    - Use Hypothesis `st.sampled_from([logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR])` for log levels and `st.text(min_size=1, max_size=200)` for messages
    - For ALL generated (level, message) pairs where `exc_info` is NOT set:
      - Output is exactly single-line (no `\n` within the formatted record, excluding trailing newline)
      - Output contains the level name (`INFO`, `WARNING`, etc.)
      - Output contains `[trace_id=0 span_id=0]` (OTel defaults)
      - Output contains the message text
  - Run tests on UNFIXED code via `cd epsilon-boot && uv run pytest test/test_log_exception_newline.py::test_preservation_normal_log_format -x`
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Fix for 日志异常栈换行修复（Log Exception Traceback Newline Fix）

  - [x] 3.1 Implement the fix in `epsilon-boot/main.py`
    - In `main()` function, before `uvicorn.run()`, define a `UVICORN_LOG_CONFIG` dictionary that:
      - Uses `version: 1` and `disable_existing_loggers: False`
      - Defines a `default` formatter with the same format string as `logging.basicConfig()`: `"%(asctime)s %(levelname)s [%(name)s] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"` and `datefmt: "%Y-%m-%d %H:%M:%S"`
      - Uses standard `logging.Formatter` class (NOT uvicorn's `DefaultFormatter`) to ensure proper multiline traceback handling
      - Defines a `default` handler (StreamHandler to stderr) using the `default` formatter
      - Configures `uvicorn`, `uvicorn.error`, and `uvicorn.access` loggers to use the `default` handler
      - The dict must be pure dict (serializable) for multi-worker compatibility
    - Pass `log_config=UVICORN_LOG_CONFIG` to `uvicorn.run()` call
    - Add detailed Chinese docstring/comments explaining why custom log_config is needed
    - _Bug_Condition: isBugCondition(input) where input.exc_info IS NOT None AND rootLoggerFormatterIsUvicornDefault()_
    - _Expected_Behavior: Traceback output contains "\n" and "Traceback (most recent call last):" with each stack frame on separate line_
    - _Preservation: Normal single-line logs, OTel trace_id/span_id injection, uvicorn access/error logs all unchanged_
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - 异常栈多行输出（Exception Traceback Multiline Output）
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run: `cd epsilon-boot && uv run pytest test/test_log_exception_newline.py::test_bug_condition_exception_traceback_multiline -x`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed — traceback now contains proper newlines)
    - _Requirements: 2.1, 2.3_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - 普通日志格式不变（Normal Log Format Unchanged）
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run: `cd epsilon-boot && uv run pytest test/test_log_exception_newline.py::test_preservation_normal_log_format -x`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — normal logs still formatted correctly)
    - Confirm all tests still pass after fix (no regressions)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd epsilon-boot && uv run pytest test/test_log_exception_newline.py -v`
  - Ensure both bug condition and preservation tests pass
  - Ensure no other existing tests are broken: `cd epsilon-boot && uv run pytest`
  - Ask the user if questions arise
