"""Diagnostic et optimisation : causes de dégradation + configs SQM / cake-autorate pour OpenWrt."""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Any

# --------------------------------------------------------------------------- diagnostic

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
COMPASS = ["N", "NNE", "ENE", "E", "ESE", "SSE", "S", "SSO", "OSO", "O", "ONO", "NNO"]


@dataclass
class Finding:
    severity: str  # critical | warning | info | ok
    category: str
    title: str
    detail: str
    action: str = ""


def _worst_wedge(wedges: list[float] | None) -> tuple[int, float] | None:
    if not wedges:
        return None
    idx = max(range(len(wedges)), key=lambda i: wedges[i] or 0)
    return idx, wedges[idx] or 0.0


def diagnose(
    status: dict[str, Any] | None,
    stats_1h: dict[str, Any] | None,
    hourly: list[dict[str, Any]] | None = None,
    bufferbloat: dict[str, Any] | None = None,
    dish_error: str | None = None,
) -> list[dict[str, Any]]:
    f: list[Finding] = []
    if status is None:
        f.append(
            Finding(
                "critical",
                "connexion",
                "Parabole injoignable",
                dish_error or "Aucune donnée reçue de 192.168.100.1:9200.",
                "Vérifiez que cet hôte est sur le réseau Starlink. En mode bypass avec votre propre routeur, "
                "ajoutez une route/alias vers 192.168.100.0/24 sur l'interface WAN.",
            )
        )
        return [asdict(x) for x in f]

    state = status.get("state")
    if state and state != "CONNECTED":
        f.append(Finding("warning", "connexion", f"État : {state}", "La parabole n'est pas connectée.", ""))

    # Obstructions
    frac = status.get("fraction_obstructed") or 0.0
    wedge = _worst_wedge(status.get("wedges_fraction_obstructed"))
    where = ""
    if wedge and wedge[1] > 0:
        i = wedge[0]
        where = f" Secteur le plus touché : {COMPASS[i % 12]} (azimut ~{i * 30}°–{i * 30 + 30}°)."
    if frac >= 0.02:
        f.append(
            Finding(
                "critical",
                "obstruction",
                f"Obstructions importantes ({frac:.1%} du temps)",
                "Les obstructions provoquent des micro-coupures (visio, jeux)." + where,
                "Déplacez la parabole (plus haut, ou à l'écart de l'obstacle) en vous aidant de la carte "
                "d'obstruction, puis réinitialisez la carte dans l'app Starlink pour valider.",
            )
        )
    elif frac >= 0.005:
        f.append(
            Finding(
                "warning",
                "obstruction",
                f"Obstructions modérées ({frac:.1%} du temps)",
                "Quelques coupures brèves sont probables." + where,
                "Élaguez ou surélevez la parabole si possible.",
            )
        )
    else:
        f.append(Finding("ok", "obstruction", "Ciel dégagé", f"Obstruction : {frac:.2%}."))

    # Alertes matérielles
    alerts = set(status.get("active_alerts") or [])
    if "alert_thermal_throttle" in alerts or "alert_power_supply_thermal_throttle" in alerts:
        f.append(
            Finding(
                "critical",
                "matériel",
                "Bridage thermique",
                "La parabole ou son alimentation réduit ses performances à cause de la chaleur.",
                "Ombrez l'alimentation / le routeur, améliorez la ventilation.",
            )
        )
    if "alert_slow_ethernet_speeds" in alerts or "alert_slow_ethernet_speeds_100" in alerts:
        f.append(
            Finding(
                "critical",
                "matériel",
                "Lien Ethernet lent",
                "Le lien entre la parabole et le routeur ne négocie pas 1 Gb/s : le débit est plafonné.",
                "Vérifiez câble, connecteurs et adaptateur Ethernet Starlink.",
            )
        )
    if "alert_mast_not_near_vertical" in alerts:
        f.append(
            Finding("warning", "installation", "Mât non vertical", "Peut dégrader la visée.", "Remettez le mât d'aplomb.")
        )
    if "alert_motors_stuck" in alerts:
        f.append(Finding("critical", "matériel", "Moteurs bloqués", "", "Vérifiez qu'aucun obstacle ne bloque la parabole."))
    if "alert_is_heating" in alerts:
        f.append(Finding("info", "matériel", "Chauffage actif", "Mode fonte de neige : consommation accrue.", ""))
    if status.get("software_update_reboot_ready"):
        f.append(
            Finding("info", "firmware", "Mise à jour prête", "Un redémarrage appliquera le nouveau firmware.",
                    "Redémarrez à une heure creuse (onglet Contrôle).")
        )

    # Qualité de lien sur la dernière heure
    # Au moins ~2 min de données pour éviter les faux positifs au démarrage.
    if stats_1h and (stats_1h.get("n") or 0) >= 60:
        drop = stats_1h.get("drop_avg") or 0.0
        if drop >= 0.02:
            f.append(
                Finding(
                    "critical" if drop >= 0.05 else "warning",
                    "qualité",
                    f"Perte de paquets élevée ({drop:.1%} sur 1 h)",
                    f"{stats_1h.get('outage_samples') or 0} échantillons en coupure totale.",
                    "Croisez avec la carte d'obstruction ; si le ciel est dégagé, c'est souvent la congestion de cellule.",
                )
            )
        p95 = stats_1h.get("lat_p95")
        if p95 and p95 > 100:
            f.append(
                Finding(
                    "warning",
                    "latence",
                    f"Latence p95 élevée ({p95:.0f} ms)",
                    "Latence parabole → PoP. Des pics réguliers toutes les 15 s sont normaux (changement de satellite).",
                    "Si la latence monte surtout en charge, c'est du bufferbloat : voir l'onglet Optimisation.",
                )
            )

    # Congestion horaire
    if hourly and len(hourly) >= 6:
        dls = [h["dl_max"] for h in hourly if h.get("dl_max")]
        if dls:
            best = max(dls)
            worst = min(hourly, key=lambda h: h.get("dl_max") or best)
            if worst.get("dl_max") and worst["dl_max"] < 0.6 * best:
                f.append(
                    Finding(
                        "info",
                        "congestion",
                        f"Congestion de cellule vers {worst['h']}h",
                        f"Débit max observé {worst['dl_max'] / 1e6:.0f} Mb/s contre {best / 1e6:.0f} Mb/s au meilleur moment.",
                        "Planifiez les gros transferts hors de ces heures ; cake-autorate s'adaptera à ces variations.",
                    )
                )

    # Bufferbloat
    if bufferbloat:
        g = bufferbloat.get("grade", "?")
        d_inc = (bufferbloat.get("download") or {}).get("increase_ms")
        u_inc = (bufferbloat.get("upload") or {}).get("increase_ms")
        txt = f"Latence ajoutée : +{(d_inc or 0):.0f} ms en download, +{(u_inc or 0):.0f} ms en upload."
        if g in {"C", "D", "F"}:
            f.append(
                Finding(
                    "critical" if g in {"D", "F"} else "warning",
                    "bufferbloat",
                    f"Bufferbloat note {g}",
                    txt,
                    "Activez SQM (CAKE) + cake-autorate sur un routeur OpenWrt : configuration prête dans l'onglet Optimisation.",
                )
            )
        elif g in {"A+", "A", "B"}:
            f.append(Finding("ok", "bufferbloat", f"Bufferbloat note {g}", txt))
    else:
        f.append(
            Finding("info", "bufferbloat", "Bufferbloat non mesuré", "Aucun test de latence sous charge.",
                    "Lancez un test dans l'onglet Latence.")
        )

    f.sort(key=lambda x: SEVERITY_ORDER.get(x.severity, 9))
    return [asdict(x) for x in f]


# --------------------------------------------------------------------------- débits recommandés

# Valeurs de repli quand aucune mesure n'est disponible (Starlink résidentiel typique).
DEFAULT_DL_MBPS = [60.0, 120.0, 220.0]
DEFAULT_UL_MBPS = [8.0, 15.0, 25.0]


def _quantile(values: list[float], q: float) -> float:
    v = sorted(values)
    if len(v) == 1:
        return v[0]
    pos = q * (len(v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


@dataclass
class Rates:
    """Débits en kbit/s."""

    min_dl: int
    base_dl: int
    max_dl: int
    min_ul: int
    base_ul: int
    max_ul: int
    static_dl: int  # pour un SQM fixe sans cake-autorate
    static_ul: int
    source: str  # "mesures" | "défaut"
    samples: int


def recommend_rates(
    bufferbloat_tests: list[dict[str, Any]],
    peak_dl_bps: float | None = None,
    peak_ul_bps: float | None = None,
) -> Rates:
    dl = [t["download"]["mbps"] for t in bufferbloat_tests if (t.get("download") or {}).get("mbps")]
    ul = [t["upload"]["mbps"] for t in bufferbloat_tests if (t.get("upload") or {}).get("mbps")]
    source = "mesures" if dl and ul else "défaut"
    if not dl:
        dl = list(DEFAULT_DL_MBPS)
    if not ul:
        ul = list(DEFAULT_UL_MBPS)
    if peak_dl_bps:
        dl.append(peak_dl_bps / 1e6)
    if peak_ul_bps:
        ul.append(peak_ul_bps / 1e6)

    def triple(values: list[float], floor: float) -> tuple[int, int, int, int]:
        p10, p50, p95 = _quantile(values, 0.1), statistics.median(values), _quantile(values, 0.95)
        mx = p95 * 1.05
        base = min(p50 * 0.8, mx)
        mn = min(max(floor, p10 * 0.4), base)
        static = max(floor, _quantile(values, 0.2) * 0.85)
        return tuple(int(round(x * 1000)) for x in (mn, base, mx, static))  # type: ignore[return-value]

    mn_d, b_d, mx_d, st_d = triple(dl, 5.0)
    mn_u, b_u, mx_u, st_u = triple(ul, 2.0)
    return Rates(mn_d, b_d, mx_d, mn_u, b_u, mx_u, st_d, st_u, source, len(bufferbloat_tests))


# --------------------------------------------------------------------------- génération de configs


def render_sqm_config(rates: Rates, wan_dev: str, autorate: bool = True) -> str:
    dl = rates.base_dl if autorate else rates.static_dl
    ul = rates.base_ul if autorate else rates.static_ul
    return f"""# /etc/config/sqm — généré par StarlinkLeo
# {'Débits initiaux : cake-autorate les ajustera en continu.' if autorate else 'SQM fixe (sans cake-autorate) : débits prudents ~85 % du débit bas mesuré.'}
config queue 'starlink'
	option enabled '1'
	option interface '{wan_dev}'
	option download '{dl}'
	option upload '{ul}'
	option qdisc 'cake'
	option script 'piece_of_cake.qos'
	option linklayer 'none'
	option qdisc_advanced '1'
	option squash_dscp '1'
	option squash_ingress '1'
	option ingress_ecn 'ECN'
	option egress_ecn 'NOECN'
	option qdisc_really_really_advanced '1'
	option iqdisc_opts 'nat dual-dsthost ingress'
	option eqdisc_opts 'nat dual-srchost ack-filter'
	option debug_logging '0'
	option verbosity '5'
"""


def render_cake_autorate_config(rates: Rates, wan_dev: str) -> str:
    return f"""#!/bin/bash
# /root/cake-autorate/config.primary.sh — généré par StarlinkLeo
# Débits calculés à partir de {rates.samples} test(s) ({rates.source}).
# Seules les valeurs qui diffèrent de defaults.sh sont fixées ici.

dl_if=ifb4{wan_dev} # interface d'ingress créée par SQM (vérifier avec : tc qdisc ls)
ul_if={wan_dev}     # interface d'egress (WAN)

adjust_dl_shaper_rate=1
adjust_ul_shaper_rate=1

min_dl_shaper_rate_kbps={rates.min_dl}
base_dl_shaper_rate_kbps={rates.base_dl}
max_dl_shaper_rate_kbps={rates.max_dl}

min_ul_shaper_rate_kbps={rates.min_ul}
base_ul_shaper_rate_kbps={rates.base_ul}
max_ul_shaper_rate_kbps={rates.max_ul}

connection_active_thr_kbps=2000

# --- Réglages Starlink optionnels (point de départ, à ajuster selon les logs) ---
# Starlink change de satellite toutes les 15 s (secondes 12/27/42/57), ce qui crée des
# pics de latence qui ne sont pas du bufferbloat. Si cake-autorate réduit le débit à
# chaque changement, relevez légèrement les seuils (défauts : 30 / 10 / 60 ms) :
# dl_owd_delta_delay_thr_ms=40.0
# ul_owd_delta_delay_thr_ms=40.0
# dl_avg_owd_delta_max_adjust_down_thr_ms=80.0
# ul_avg_owd_delta_max_adjust_down_thr_ms=80.0
"""


def render_install_script(rates: Rates, wan_dev: str) -> str:
    sqm = render_sqm_config(rates, wan_dev, autorate=True)
    car = render_cake_autorate_config(rates, wan_dev)
    return f"""#!/bin/sh
# Installation SQM (CAKE) + cake-autorate sur OpenWrt — généré par StarlinkLeo
# Usage : scp install_openwrt.sh root@192.168.1.1:/tmp/ && ssh root@192.168.1.1 sh /tmp/install_openwrt.sh
set -e

echo "==> Installation des paquets (sqm-scripts, luci-app-sqm, bash, fping)"
if command -v apk >/dev/null 2>&1; then
    apk update && apk add sqm-scripts luci-app-sqm bash fping
else
    opkg update && opkg install sqm-scripts luci-app-sqm bash fping
fi

echo "==> Sauvegarde puis écriture de /etc/config/sqm"
[ -f /etc/config/sqm ] && cp /etc/config/sqm /etc/config/sqm.bak.$(date +%s)
cat > /etc/config/sqm <<'EOF_SQM'
{sqm}EOF_SQM
/etc/init.d/sqm enable
/etc/init.d/sqm restart

echo "==> Installation de cake-autorate (le script peut poser des questions)"
wget -O /tmp/cake-autorate_setup.sh https://raw.githubusercontent.com/lynxthecat/cake-autorate/master/setup.sh
sh /tmp/cake-autorate_setup.sh

echo "==> Écriture de la configuration cake-autorate"
mkdir -p /root/cake-autorate
[ -f /root/cake-autorate/config.primary.sh ] && cp /root/cake-autorate/config.primary.sh /root/cake-autorate/config.primary.sh.bak
cat > /root/cake-autorate/config.primary.sh <<'EOF_CAR'
{car}EOF_CAR

service cake-autorate enable
service cake-autorate restart

echo "==> Terminé. Vérifiez : tc qdisc ls | grep cake ; tail -f /var/log/cake-autorate.primary.log"
echo "    Puis relancez un test bufferbloat depuis StarlinkLeo."
"""


def rates_dict(r: Rates) -> dict[str, Any]:
    return asdict(r)
