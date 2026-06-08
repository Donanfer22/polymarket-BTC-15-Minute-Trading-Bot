@echo off
REM No Windows, arquivos .ps1 normalmente abrem no Bloco de Notas ao dar duplo clique.
REM Este .bat serve apenas como um "lançador" para você dar o duplo clique e ele executar o .ps1.
powershell.exe -ExecutionPolicy Bypass -File "%~dp0iniciar_bot.ps1"
