# Implementation Plan: ListDirTool

## Overview

实现 ListDirTool 目录内容列举工具，遵循与 ReadFileTool、WriteFileTool、EditFileTool 相同的适配器模式。核心实现包括模块级 `IGNORE_DIRS` 常量、递归模式委托 `tree()` 函数、非递归模式内联浅层列举，以及统一的错误处理。

## Tasks

- [x] 1. Implement ListDirTool class
  - [x] 1.1 Create `epsilon-boot/src/infrastructure/tools/filesystem/list_dir_tool.py` with module-level `IGNORE_DIRS` frozenset constant containing 12 noise directory names (.git, node_modules, \_\_pycache\_\_, .venv, .idea, .hypothesis, .mypy_cache, .pytest_cache, .tox, .eggs, .svn, .hg)
    - Define `ListDirTool(Tool)` class with `name` → `"list_dir"`, `description`, and `parameters` properties
    - `parameters` returns JSON Schema with `directory_path` (required, string) and `recursive` (optional, boolean, default true)
    - _Requirements: 2.1, 2.2, 2.3, 1.4_

  - [x] 1.2 Implement `execute()` method with pre-validation and recursive mode
    - Pre-validate: raise `ToolExecutionError` if path does not exist or is not a directory
    - Recursive mode (`recursive=True` or omitted): delegate to `tree(Path(directory_path), ignore=IGNORE_DIRS)`
    - Check if `tree()` result starts with `"错误："` and convert to `ToolExecutionError`
    - Catch `PermissionError` and convert to `ToolExecutionError`
    - _Requirements: 1.1, 1.2, 3.1, 3.2, 3.3, 3.4_

  - [x] 1.3 Implement non-recursive mode in `execute()`
    - When `recursive=False`: use `Path.iterdir()` to get immediate children
    - Filter out entries whose name is in `IGNORE_DIRS`
    - Sort with directories first, then files, both case-insensitive alphabetical: `sorted(entries, key=lambda p: (p.is_file(), p.name.lower()))`
    - Format output with `├──` / `└──` tree connectors
    - Catch `PermissionError` and convert to `ToolExecutionError`
    - _Requirements: 1.3, 4.1, 4.2, 4.3, 3.3_

- [x] 2. Checkpoint - Verify core implementation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Write property-based tests
  - [x] 3.1 Write property test for delegation consistency
    - **Property 1: Delegation consistency**
    - For any valid directory structure with `recursive=True`, `ListDirTool.execute()` output must equal `tree(Path(dir), ignore=IGNORE_DIRS)` directly
    - Use `tempfile.TemporaryDirectory()`, Hypothesis strategies for random dir/file names and nesting
    - `@settings(max_examples=100, deadline=2000)`, `@pytest.mark.asyncio`
    - **Validates: Requirements 1.1, 1.2, 5.2**

  - [x] 3.2 Write property test for idempotence
    - **Property 2: Idempotence**
    - For any valid directory and any `recursive` value, two consecutive calls with same params on unchanged directory produce identical results
    - **Validates: Requirements 5.1**

  - [x] 3.3 Write property test for non-recursive shallow listing
    - **Property 3: Non-recursive shallow listing**
    - For any directory with immediate children, `recursive=False` output contains exactly the non-ignored immediate children names with tree connectors, no deeper-level names
    - **Validates: Requirements 1.3, 4.1**

  - [x] 3.4 Write property test for noise filtering
    - **Property 4: Noise filtering**
    - For any directory containing noise-named subdirectories from `IGNORE_DIRS`, none of those names appear in output regardless of `recursive` value
    - **Validates: Requirements 1.4, 4.2**

  - [x] 3.5 Write property test for error propagation
    - **Property 5: Error propagation for invalid paths**
    - For any non-existent path or file path (not directory), `execute()` raises `ToolExecutionError` with the path in the message
    - **Validates: Requirements 3.1, 3.2, 3.4**

- [x] 4. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Test file location: `epsilon-boot/test/infrastructure/tools/filesystem/test_list_dir_tool_property.py`
- Follow the same adapter pattern as ReadFileTool, WriteFileTool, EditFileTool
- Property tests use Hypothesis with `@settings(max_examples=100, deadline=2000)` and `tempfile.TemporaryDirectory()`
