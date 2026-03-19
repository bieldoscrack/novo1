@echo off
title Polymarket Hunter - Bot Completo + Dashboard
color 0A

echo.
echo  ══════════════════════════════════════════════════
echo    POLYMARKET HUNTER  ^|  BOT COMPLETO + DASHBOARD
echo  ══════════════════════════════════════════════════
echo.
echo  Iniciando launcher...
echo  O navegador vai abrir automaticamente.
echo  Para parar: feche esta janela ou pressione Ctrl+C
echo.

cd /d "%~dp0"

:: Tenta python, depois py
where python >nul 2>&1
if %errorlevel%==0 (
    python launcher.py
) else (
    where py >nul 2>&1
    if %errorlevel%==0 (
        py launcher.py
    ) else (
        echo  ERRO: Python nao encontrado!
        echo  Instale em: https://python.org
        pause
        exit /b 1
    )
)

echo.
echo  Bot encerrado.
pause
