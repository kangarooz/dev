@echo off
rem Fake 1Password CLI for the tests (Windows twin of tests/fakes/op).
rem   op --version              -> 2.30.0-fake
rem   op read op://vault/item/f -> "resolved:op://vault/item/f"
rem   FAKE_OP_FAIL=msg          -> every read fails with that message (exit 1)
if "%~1"=="--version" (
  echo 2.30.0-fake
  exit /b 0
)
if not "%~1"=="read" (
  echo [ERROR] fake op: unsupported command: %* 1>&2
  exit /b 1
)
if defined FAKE_OP_FAIL (
  echo [ERROR] fake op: %FAKE_OP_FAIL% 1>&2
  exit /b 1
)
set "ref=%~2"
if "%ref:~0,5%"=="op://" (
  echo resolved:%ref%
  exit /b 0
)
echo [ERROR] fake op: not an op:// reference: '%ref%' 1>&2
exit /b 1
