# Testing Guidelines

## Strategy
This project uses [Framework Name] for automated testing. We prioritize [Unit/Integration/E2E] tests for [Critical Component].

### Test Types
* **Unit Tests:** Test individual functions or modules in isolation. They are fast, deterministic, and verify core logic.
* **Integration Tests:** Verify that multiple components (e.g., database, external services) work together correctly.
* **End-to-End (E2E) Tests:** Validate the entire system pipeline from the user's perspective, typically simulating real usage flows.

## Running Tests

### Automated Suite
Run the full suite:
```bash
[command, e.g., pytest or npm test]
```
Run with coverage:
Bash

[command for coverage]

## Manual Tests

This section covers scenarios that cannot be automated: hardware-dependent behavior, visual verification, network-dependent flows.

### [Placeholder: Test Scenario Name]
* **Purpose:** [What feature or flow is being tested]
* **Usage:** [Step-by-step instructions to run the test]
* **What It Tests:** [Specific components or interactions validated]
* **Expected Output:** [What the user should see or experience on success]

### [Placeholder: Another Test Scenario Name]
* **Purpose:** [What feature or flow is being tested]
* **Usage:** [Step-by-step instructions to run the test]
* **What It Tests:** [Specific components or interactions validated]
* **Expected Output:** [What the user should see or experience on success]

## Writing New Tests

* **Pattern:** Follow the Arrange / Act / Assert pattern.
  ```[Language]
  // Arrange
  const input = setupData();
  // Act
  const result = processData(input);
  // Assert
  expect(result).toEqual(expectedOutput);
  ```
* **Isolation:** Tests must not share mutable state. Each test should set up and tear down its own environment.
* **Mocking Requirements:** No live hardware or external APIs should be used in automated runs. Depend on mocks or stubs.
* **Naming Conventions:** Name tests according to formatting standards defined in `STYLE.md`.

## Mocking Standards
External APIs should be mocked using [Library].

Database connections should use [Strategy, e.g., in-memory SQLite].

## Troubleshooting Tests

### Import Errors
**Issue:** `[Placeholder: e.g., ImportError: cannot import name 'module']`
**Fix:** `[Placeholder: e.g., Verify your PYTHONPATH is set correctly and the test environment is activated.]`

### Mocking Failures
**Issue:** `[Placeholder: e.g., MagicMock object has no attribute 'expected_call']`
**Fix:** `[Placeholder: e.g., Check the method name on the mocked object matches the actual implementation.]`

### Test Isolation Failures
**Issue:** `[Placeholder: e.g., Test suite passes individually but fails when run together]`
**Fix:** `[Placeholder: e.g., Ensure global state is reset in teardown methods and avoid shared fixtures unless explicitly read-only.]`
