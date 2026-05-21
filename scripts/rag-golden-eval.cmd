@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0rag-golden-eval.ps1" %*
