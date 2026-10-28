# Script de déploiement automatique pour Pokémon TCG Monitor

$RASPBERRY_IP = "192.168.1.28"
$RASPBERRY_USER = "lacanette"
$REMOTE_PATH = "~/pokemon-restock"
$COMMIT_MSG = "Update: automatic sync from dev PC"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "🚀 Déploiement vers GitHub & Raspberry Pi" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Push vers GitHub
Write-Host "`n1. Envoi des modifications sur GitHub..." -ForegroundColor Yellow
git add .
git commit -m "$COMMIT_MSG"
git push origin main

# 2. Synchronisation des fichiers vers le Raspberry Pi via SCP
Write-Host "`n2. Synchronisation vers le Raspberry Pi ($RASPBERRY_IP)..." -ForegroundColor Yellow
scp -r app.py monitor.py shops.json templates/ requirements.txt ${RASPBERRY_USER}@${RASPBERRY_IP}:${REMOTE_PATH}

Write-Host "`n✅ Déploiement terminé avec succès !" -ForegroundColor Green