# Physics checklist by domain

Each row: the test, the formula, the calculator (`run.py calc …`), and the trap that changes the answer.

## Propulsion
| Test | Formula | Calc | Trap |
|---|---|---|---|
| Δv / total impulse | Δv = c·ln(m0/m1), I = mp·c | `rocket` | use the *usable* propellant, not the loaded mass |
| Hover time | t = I / (m g) | `rocket` | thrust must exceed weight; t ignores the mass that leaves |
| Cold-gas thruster | c ≈ √(2cpT(1−(pa/pc)^((γ−1)/γ))) | `nozzle` | blowdown cools the tank, Isp falls; exit below dew point condenses |
| Nozzle regime | pe vs pa | `nozzle` | pe < 0.35 pa separates: thrust falls, the jet flaps |
| Fans / props | T = √(2ρA P²)^(1/3)… use momentum theory | — | static thrust per kW ~ 5–10 kgf for large rotors only |
| Chemical rocket | Isp 250–450 s | — | combustion means heat, exhaust and permits |

## Energy storage
| Test | Formula | Calc | Trap |
|---|---|---|---|
| Gas mass in a vessel | m = ρ(p,T)·V | `tank` | ideal gas overstates mass by 30–60% above 300 bar |
| Usable gas | isentropic blowdown to the regulator floor | `tank p_end=` | half the gas can stay in the bottle |
| Stored (burst) energy | m(u1 − u2) | `tank` | report kg TNT; it is the safety argument |
| Refill energy | isothermal minimum × ~2 | `tank` | compare with the battery you have |
| Battery | E = P t, mass = E / (Wh/kg) | `energy` | pack level ~160–250 Wh/kg; C-rate limits peak power |
| Pressure vessel | t = SF·p·r/σ | `vessel` | domes, liner, bosses add 20–40% |

## Ground contact and aero
| Test | Formula | Calc | Trap |
|---|---|---|---|
| Suction / fan car | N = ΔP·A + ½ρv²ClA + m v²/R + m g cosθ | `suction` | the seal: leak ∝ gap; a curved surface opens the gap by L²/8R |
| Grip | μN ≥ m g sinθ + m a | `suction` | μ 1.0–1.6 only on clean, warm tyres |
| Loop / ceiling at speed | v ≥ √(gR) needs no suction | — | stationary inverted is the hard case |
| Downforce | ½ρv²ClA | — | quadratic: small at low speed |

## Flight paths and loads
| Test | Formula | Calc | Trap |
|---|---|---|---|
| Jump height / airtime | v = √(2gh), t = 2v/g | `ballistic` | clear the obstacle with the body's lowest corner at every roll angle |
| Rotation | α = τ/I, θ = αt²/4 (bang-bang) | — | body-fixed thrusters rotate with the body: inverted "lift" pushes down |
| Landing | a = v²/(2 s) | `landing` | peak 1.5–2× mean; reserve propellant for braking |
| Structure | σ = M c / I, buckling | — | inversion and hard landings load the roof and mounts |

## Thermal and other
- Joule–Thomson cooling across regulators; icing of valves with moist air.
- Heat rejection: W → °C rise = P / (ṁ cp); continuous vs pulse rating.
- Noise: supersonic jets 150+ dB near field; debris kicked up by ground jets.
- Control: actuation latency × angular rate = attitude error.
- Regulation and safety: road-legality, pressure-vessel codes, stored energy near people.

## Sanity anchors
g 9.81 m/s² · air ρ 1.2 kg/m³ · 1 bar = 1e5 Pa · 1 kg TNT = 4.184 MJ · car μ ≈ 1.2 · human tolerance ~5 g
sustained, ~20 g brief · Li-ion pack 160–250 Wh/kg · carbon fibre σ ≈ 2.5–5 GPa · steel σy 0.25–1.5 GPa.
