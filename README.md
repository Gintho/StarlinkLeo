# 🛰️ StarlinkLeo

Monitoring et optimisation de connexion Starlink :

- lecture de l'**API gRPC locale** de la parabole (`192.168.100.1:9200`) via le module `starlink_grpc` de
  [sparky8512/starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools) ;
- **interface web locale** : temps réel, historique 30 jours, carte d'obstruction, diagnostic ;
- **mesure du bufferbloat** (latence au repos vs sous charge) ;
- **optimisation de la latence** : génération des configs **SQM/CAKE** ([sqm-scripts](https://github.com/tohojo/sqm-scripts))
  et **[cake-autorate](https://github.com/lynxthecat/cake-autorate)** pour OpenWrt, calibrées sur vos mesures ;
- **bot Telegram** : alertes (coupures, obstructions, surchauffe…) et commandes (`/etat`, `/reboot`…) ;
- **tableau de bord distant sur Vercel**, protégé par mot de passe, avec contrôle à distance.

## Architecture

```
               réseau local Starlink                       │            Internet
                                                          │
 Parabole ──gRPC──▶ Agent StarlinkLeo (Docker, Pi/NAS/PC) ─┼─ push état / reçoit commandes ─▶ Vercel (tableau de bord)
 192.168.100.1      ├─ interface web locale :8080          │                                      ▲ mot de passe
                    ├─ SQLite (historique)                 ├─ long polling ─▶ Telegram ◀── vous   │
                    └─ génère configs SQM / cake-autorate  │                                     vous
                              │
                              ▼ (copie manuelle via SSH)
                    Routeur OpenWrt (SQM + cake-autorate)
```

L'agent n'accepte **aucune connexion entrante** depuis Internet : Telegram et Vercel fonctionnent par
requêtes sortantes. Vercel ne peut pas joindre la parabole directement ; il reçoit l'état de l'agent et lui
transmet les commandes à l'échange suivant (≤ 20 s).

## 1. Agent local

À installer sur une machine **branchée au réseau Starlink** (Raspberry Pi, NAS, PC Linux).

```bash
git clone https://github.com/Gintho/StarlinkLeo && cd StarlinkLeo
cp .env.example .env   # optionnel : Telegram / Vercel
docker compose up -d --build
```

Interface : `http://<ip-de-la-machine>:8080`. Pour essayer sans parabole : `STARLINK_MOCK=1`.

Sans Docker :

```bash
pip install . && starlinkleo
```

| Variable | Défaut | Rôle |
|---|---|---|
| `STARLINK_DISH_ADDRESS` | `192.168.100.1:9200` | adresse gRPC de la parabole |
| `STARLINK_MOCK` | `0` | `1` = simulateur |
| `STARLINK_POLL_INTERVAL` | `2` | période de collecte (s) |
| `STARLINK_RETENTION_DAYS` | `30` | rétention SQLite |
| `STARLINK_ENABLE_CONTROLS` | `1` | `0` = interdit reboot/stow (web, Telegram, Vercel) |
| `STARLINK_LATENCY_TARGET` | `1.1.1.1:443` | cible de mesure de latence (connexion TCP) |
| `OPENWRT_WAN_DEV` | `eth1` | périphérique WAN pour les configs générées |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_CHAT_IDS` | – | bot Telegram |
| `CLOUD_URL` / `CLOUD_INGEST_TOKEN` / `CLOUD_INTERVAL` | – / – / `20` | relais Vercel |
| `ALERT_OUTAGE_SECONDS` | `20` | durée avant d'alerter sur une coupure |

> **Routeur en mode bypass ?** La parabole reste joignable en `192.168.100.1` si le routeur OpenWrt a une
> adresse dans ce sous-réseau sur son WAN (ex. alias `192.168.100.2/24`).

## 2. Optimisation de la latence (bufferbloat)

1. Onglet **Latence & bufferbloat** → *Lancer le test* (à refaire à différentes heures pour bien couvrir les variations).
2. Onglet **Optimisation OpenWrt** : débits min/base/max calculés à partir des mesures et des pointes vues par la parabole.
3. Téléchargez `install_openwrt.sh`, puis sur le routeur :

```bash
scp install_openwrt.sh root@192.168.1.1:/tmp/
```

```bash
ssh root@192.168.1.1 sh /tmp/install_openwrt.sh
```

Le script installe `sqm-scripts`, `luci-app-sqm`, `bash` et `fping`, écrit `/etc/config/sqm` (CAKE sur le WAN),
installe cake-autorate (script officiel `setup.sh`) et écrit `/root/cake-autorate/config.primary.sh`.
Les fichiers existants sont sauvegardés avant d'être remplacés.
4. Relancez un test : l'objectif est une note **A ou A+**.

Les pics de latence toutes les 15 s (changement de satellite) ne sont pas du bufferbloat. Si cake-autorate réagit
trop à ces pics, des seuils à relever sont proposés, commentés, dans la config générée.

## 3. Bot Telegram

1. Dans Telegram, créez un bot avec **@BotFather** (`/newbot`) et récupérez le jeton.
2. Mettez `TELEGRAM_BOT_TOKEN` dans `.env`, démarrez l'agent, puis envoyez `/start` au bot : il répond avec votre **chat ID**.
3. Ajoutez ce chat ID dans `TELEGRAM_ALLOWED_CHAT_IDS` (plusieurs IDs séparés par des virgules), puis redémarrez l'agent.

Commandes : `/etat`, `/diag`, `/test`, `/optim`, `/reboot`, `/stow`, `/unstow`, `/aide`.
Les actions sur la parabole demandent une **confirmation par bouton**, valable 2 minutes.
Le bot ignore les chats non autorisés.

Alertes envoyées automatiquement : parabole injoignable / rétablie, coupure Internet (100 % de perte
au-delà de `ALERT_OUTAGE_SECONDS`), alertes matérielles (surchauffe, Ethernet lent, moteurs, eau…),
obstructions > 2 %, mise à jour firmware prête, résultat des tests bufferbloat.

## 4. Tableau de bord Vercel

Le dossier [`cloud/`](cloud/) est un projet Vercel autonome : pages statiques + fonctions serverless,
sans dépendance npm.

1. Sur Vercel : **Add New → Project**, importez ce repo et choisissez **Root Directory = `cloud`**.
2. **Storage → Upstash for Redis** (Marketplace, offre gratuite) et connectez-le au projet. Cela crée
   `KV_REST_API_URL` et `KV_REST_API_TOKEN`.
3. Variables d'environnement du projet :
   - `DASHBOARD_PASSWORD` : votre mot de passe d'accès (long) ;
   - `INGEST_TOKEN` : un secret aléatoire pour l'agent (ex. `openssl rand -hex 32`).
4. Redéployez, puis sur l'agent : `CLOUD_URL=https://<projet>.vercel.app` et `CLOUD_INGEST_TOKEN=<INGEST_TOKEN>`.
5. Vérification : `https://<projet>.vercel.app/api/health` doit afficher `redis`, `password` et `ingest_token` à `true`.

Sécurité : mot de passe comparé en temps constant, blocage de 15 min après 10 échecs par IP, liste blanche
d'actions, commandes expirées après 5 min si l'agent était hors ligne, et l'agent peut refuser toute action
(`STARLINK_ENABLE_CONTROLS=0`).

Dev local du tableau de bord (Redis émulé) :

```bash
cd cloud && DASHBOARD_PASSWORD=dev INGEST_TOKEN=dev npm run dev
```

## Développement

```bash
pip install -e '.[dev]' && pytest
```

```bash
cd cloud && npm test
```

## API locale

| Méthode | Route | Description |
|---|---|---|
| GET | `/api/status` | statut courant |
| GET | `/api/history?range=15m\|1h\|6h\|24h\|7d\|30d` | séries agrégées |
| GET | `/api/history/live` | historique seconde par seconde (parabole) |
| GET | `/api/obstruction-map` | carte d'obstruction |
| GET | `/api/stats`, `/api/diagnostics` | statistiques, diagnostic |
| POST/GET | `/api/bufferbloat` | lancer / suivre un test |
| GET | `/api/optimize?wan_dev=eth1` | débits + configs générées |
| POST | `/api/control/{reboot\|stow\|unstow}` | actions |
