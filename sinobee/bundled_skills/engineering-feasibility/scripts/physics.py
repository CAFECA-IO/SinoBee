"""Engineering calculators for feasibility checks. SI units in, SI units out; every result is a dict.

Each function documents its assumptions in the returned `note`. CoolProp is used for real-gas states when it is
installed (pip install CoolProp); otherwise the ideal-gas value is returned with `real_gas: false`.
"""
from __future__ import annotations

import math

G0 = 9.80665
PA = 101325.0


def _coolprop():
    try:
        import CoolProp.CoolProp as CP  # type: ignore
        return CP
    except Exception:
        return None


def tank(p: float, V: float, T: float = 300.0, fluid: str = "Air", R: float = 287.05, p_end: float | None = None) -> dict:
    """Gas stored in a vessel: mass, isentropic blowdown to p_end (usable mass, end temperature), burst energy, fill work."""
    CP = _coolprop()
    out: dict = {"p_bar": p / 1e5, "V_m3": V, "T_K": T}
    if CP:
        rho = CP.PropsSI("D", "T", T, "P", p, fluid)
        s0 = CP.PropsSI("S", "T", T, "P", p, fluid)
        u1 = CP.PropsSI("U", "T", T, "P", p, fluid)
        u2 = CP.PropsSI("U", "S", s0, "P", PA, fluid)
        h1 = CP.PropsSI("H", "T", T, "P", p, fluid)
        h0 = CP.PropsSI("H", "T", T, "P", PA, fluid)
        sa = CP.PropsSI("S", "T", T, "P", PA, fluid)
        m = rho * V
        out.update(mass_kg=m, real_gas=True, burst_MJ=m * (u1 - u2) / 1e6, fill_min_kWh=m * ((h1 - h0) - T * (s0 - sa)) / 3.6e6)
        if p_end:
            rho_e = CP.PropsSI("D", "S", s0, "P", p_end, fluid)
            out.update(end_T_K=CP.PropsSI("T", "S", s0, "P", p_end, fluid), usable_kg=m - rho_e * V, residual_kg=rho_e * V)
    else:
        m = p * V / (R * T)
        g = 1.4
        out.update(mass_kg=m, real_gas=False, burst_MJ=p * V / (g - 1) * (1 - (PA / p) ** ((g - 1) / g)) / 1e6,
                   fill_min_kWh=m * R * T * math.log(p / PA) / 3.6e6)
        if p_end:
            ratio = (p_end / p) ** (1 / g)
            out.update(end_T_K=T * (p_end / p) ** ((g - 1) / g), usable_kg=m * (1 - ratio), residual_kg=m * ratio)
    out["burst_kg_TNT"] = out["burst_MJ"] / 4.184
    out["note"] = "isentropic (adiabatic) blowdown: the fast case; real fill work is about 2x the minimum"
    return out


def nozzle(pc: float, Tc: float, F: float | None = None, At: float | None = None, eps: float | None = None, gamma: float = 1.4, R: float = 287.05, pa: float = PA) -> dict:
    """Ideal-gas convergent-divergent nozzle. Give the thrust F (sizes the throat) or the throat area At.
    eps defaults to perfect expansion to pa. Flags separation (pe < 0.35 pa) and condensation (air dew line)."""
    if eps is None:
        Me = math.sqrt(2 / (gamma - 1) * ((pc / pa) ** ((gamma - 1) / gamma) - 1))
        eps = (1 / Me) * ((2 / (gamma + 1)) * (1 + (gamma - 1) / 2 * Me * Me)) ** ((gamma + 1) / (2 * (gamma - 1)))
    lo, hi = 1.0001, 15.0
    for _ in range(80):
        mid = (lo + hi) / 2
        f = (1 / mid) * ((2 / (gamma + 1)) * (1 + (gamma - 1) / 2 * mid * mid)) ** ((gamma + 1) / (2 * (gamma - 1))) - eps
        lo, hi = (lo, mid) if f > 0 else (mid, hi)
    Me = (lo + hi) / 2
    ratio = 1 + (gamma - 1) / 2 * Me * Me
    pe = pc * ratio ** (-gamma / (gamma - 1))
    Te = Tc / ratio
    ve = Me * math.sqrt(gamma * R * Te)
    choke = math.sqrt(gamma) * (2 / (gamma + 1)) ** ((gamma + 1) / (2 * (gamma - 1)))
    cf_guess = 0.983 * ve * choke / math.sqrt(R * Tc) + (pe - pa) * eps / pc
    if At is None:
        At = (F or 1.0) / (pc * cf_guess)
    mdot = pc * At * choke / math.sqrt(R * Tc)
    sep = pe < 0.35 * pa
    Fv = 0.983 * mdot * ve + (0 if sep else (pe - pa) * At * eps)
    dew = 81.6 + 9.9 * math.log(pe / 1e5)  # air dew line fit, 0.2-3 bar
    return {"F_N": Fv, "mdot_kg_s": mdot, "ve_m_s": ve, "isp_s": Fv / mdot / G0, "Me": Me, "eps": eps, "pe_Pa": pe, "Te_K": Te,
            "At_m2": At, "throat_d_m": 2 * math.sqrt(At / math.pi), "exit_d_m": 2 * math.sqrt(At * eps / math.pi),
            "separated": sep, "condenses": Te < dew, "note": "15 deg cone loss 0.983; Summerfield separation at pe < 0.35 pa"}


def rocket(m0: float, mp: float, ve: float) -> dict:
    """Tsiolkovsky: delta-v, total impulse and hover time for propellant mp at effective exhaust velocity ve."""
    I = mp * ve
    return {"dv_m_s": ve * math.log(m0 / (m0 - mp)), "impulse_Ns": I, "hover_s": I / (m0 * G0),
            "note": "hover_s ignores mass loss (conservative by mp/2m0)"}


def ballistic(h: float, vx: float = 0.0) -> dict:
    """Vertical speed needed to reach height h, time up/down and horizontal range at vx."""
    v = math.sqrt(2 * G0 * h)
    t = 2 * v / G0
    return {"vz_m_s": v, "airtime_s": t, "range_m": vx * t}


def landing(h: float, stroke: float, v_end: float = 0.0) -> dict:
    """Mean deceleration (g) to stop a drop from h (or from v_end) within a suspension stroke."""
    v = math.sqrt(2 * G0 * h) if h > 0 else v_end
    a = v * v / (2 * stroke)
    return {"impact_m_s": v, "mean_g": a / G0, "note": "peak is about 1.5-2x the mean for a real damper"}


def suction(mass: float, area: float, dp: float, theta_deg: float = 180.0, mu: float = 1.2, gap: float = 0.006, perimeter: float = 11.0,
            eta: float = 0.6, wheelbase: float | None = None, R: float | None = None, v: float = 0.0, clA: float = 0.0) -> dict:
    """Ground-effect suction (fan car): normal force and grip on a surface at theta (0 floor, 90 wall, 180 ceiling)."""
    t = math.radians(theta_deg)
    sag = wheelbase * wheelbase / (8 * R) if (wheelbase and R) else 0.0
    q = 0.62 * perimeter * (gap + sag) * math.sqrt(2 * dp / 1.2)
    fan = dp * area
    aero = 0.5 * 1.2 * v * v * clA
    cent = mass * v * v / R if R else 0.0
    N = fan + aero + cent + mass * G0 * math.cos(t)
    grip = mu * max(0.0, N)
    along = mass * G0 * abs(math.sin(t))
    return {"fan_g": fan / (mass * G0), "normal_g": N / (mass * G0), "spare_grip_g": max(0.0, grip - along) / (mass * G0),
            "holds": N > 0 and grip >= along, "leak_m3_s": q, "fan_kW": dp * q / eta / 1000, "sagitta_m": sag,
            "note": "pressure held only if the fan can move leak_m3_s at dp; sagitta beyond skirt travel opens the seal"}


def vessel(p: float, r: float, sigma: float, rho: float = 1550.0, sf: float = 2.25) -> dict:
    """Thin-wall cylinder: wall thickness and shell mass per metre for burst safety factor sf (sigma = fibre strength)."""
    t = sf * p * r / sigma
    return {"wall_m": t, "kg_per_m": 2 * math.pi * r * t * rho, "note": "hoop stress only; domes and liner add 20-40%"}


def energy(power_W: float = 0.0, seconds: float = 0.0, kWh: float = 0.0, specific_Wh_kg: float = 250.0) -> dict:
    """Battery energy for a power over a time, or mass of a battery holding kWh."""
    e = kWh or power_W * seconds / 3.6e6
    return {"kWh": e, "battery_kg": e * 1000 / specific_Wh_kg}


CALCS = {"tank": tank, "nozzle": nozzle, "rocket": rocket, "ballistic": ballistic, "landing": landing, "suction": suction,
         "vessel": vessel, "energy": energy}
