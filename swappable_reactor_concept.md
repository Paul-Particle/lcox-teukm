# Swappable Containerized Reactor: Lease Model Refactor

## 1. The Operational Concept
The current model for containerized nuclear-electric ships (`lcot_nuclear_elec_containerized`) assumes a standard **Ownership Model**, where a cargo ship permanently purchases its reactor modules, adds them to its balance sheet, and keeps them on board for their entire 15-year life.

Under a more permissible regulatory regime, the intended concept is a **Reactor-as-a-Service (Shared Pool) Model**:
1. **Departure:** A ship loads one or more containerized nuclear reactors at the port just before heading out to the open ocean.
2. **Voyage:** The reactor powers the ship's electric drivetrain across the ocean.
3. **Arrival & Swap:** Upon arriving at the destination port, the reactor is removed. 
4. **Waiting Period:** The reactor goes into a shared fleet pool. It may experience an idle waiting period at the port before being loaded onto the next available departing ship.

## 2. Necessary Math & Logic Refactors
To accurately reflect this, the mathematical model must be updated from a fixed CAPEX amortization to a fleet-wide lease rate:

### A. Reactor Utilization vs. Ship Utilization
Because reactors are shared, their utilization is decoupled from the ship's availability. 
*   A ship is no longer penalized for a reactor sitting idle while the ship is busy loading cargo in port. 
*   However, the reactor's levelized cost must absorb its own idle "waiting periods" at the port between ship assignments.

### B. Daily Lease Rate Calculation
Instead of adding the reactor's CAPEX directly to the ship's `annual_fixed` costs, a separate function should calculate a **Lease Rate ($/day)** for the reactor:
`Reactor Lease Rate = (Annualized Reactor CAPEX + Annual O&M) / (365 - Annual Idle Waiting Days)`
The cargo ship's LCOT calculation will then simply multiply this lease rate by the duration of its voyage.

### C. Bundled Crew & O&M
In a lease model, the specialized nuclear operators, security, and maintenance costs (`nucc_om_usd_yr` and `crew_count_nuclear`) would likely be handled by the leasing company and bundled into the lease rate, rather than permanently employed by the cargo ship owner.

## 3. Power Density Engineering Estimate Update
**Important Note:** The volumetric power density (kW per TEU slot) of modern containerized micro-reactors is potentially very high. The current engineering estimate for `nucc_overhead_slots_per_unit` (currently sitting at 45 slots for a 15 MWe module, representing reactor + shielding) should be reviewed and updated. 

We should evaluate the physical dimensions of designs like the **AMPERA** micro-reactor (which targets high thermal/electric output within a standard ISO container footprint) to ensure our assumptions about the slot penalty for containerized nuclear power aren't overly pessimistic.
