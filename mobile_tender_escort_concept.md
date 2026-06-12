# Mobile Nuclear Tender: Escort Model Refactor

## 1. The Operational Concept
The current Python implementation of the "mobile tender" models a "relay" or "pony express" system where a cargo ship receives brief, discrete top-ups every 12 hours from stationary or slow-moving tenders. 

The actual intended concept is a **Dedicated Escort Model**:
1. **Departure:** A battery-electric cargo ship leaves its origin port and sails untethered through coastal/territorial waters on battery power.
2. **Rendezvous:** At a designated regulatory border, it meets an uncrewed nuclear tender. The tender has just spent a few hours idling or transiting after dropping off its previous companion.
3. **Escort & Charging:** The two vessels establish a physical cable connection (first a pilot line via drone, then the heavy power cable). They cross the open ocean together. The tender provides sufficient continuous power to drive the cargo ship's propulsion *and* fully recharge its battery over the course of the crossing.
4. **Disconnections:** In the event of severe sea states, the cable is disconnected to prevent damage, and the cargo ship rides out the storm on its own battery power.
5. **Arrival:** At the destination border, the cargo ship's battery is fully charged. It disconnects from the tender and sails untethered into the destination port. The tender waits at the border to pick up a departing ship for the return journey.

## 2. Terminology & Parameter Definitions

To accurately support this concept, the mathematical model needs to be restructured around the following generalized terms:

*   **`coastal_untethered_distance_nm`**: (Replaces `mob_rendezvous_distance_nm`). The distance from port where the ship must sail untethered on battery power before meeting the tender. While originally conceived as the 200 nm Exclusive Economic Zone (EEZ), Freedom of Navigation treaties (UNCLOS) technically allow nuclear vessels to approach up to the **12 nm** territorial sea limit. Making this generalized allows testing the cost difference between a 12 nm and 200 nm regulatory standoff.
*   **`storm_survival_duration_h`**: The maximum continuous time the ships might need to disconnect due to severe sea states during the open ocean crossing. The ship relies entirely on battery power during this time.
*   **`tender_idle_h`**: The time the tender spends waiting at the border between dropping off one ship and picking up the next or moving to intercept it.
*   **`mob_cable_v_cap_kn`**: The maximum safe speed of the cargo ship while physically tethered to the tender. (Note: The ship can sail at its unrestricted design speed during the untethered coastal transit and while disconnected for any other reason).
*   **`cable_efficiency`**: The electrical transmission efficiency of the tether. This must be estimated alongside the cable's physical dimensions to ensure the tender's reactor output - parasitic loads matches the power received at the ship's bus.

## 3. Necessary Math & Logic Refactors

### A. Battery Sizing
The cargo ship's battery must be sized for the worst-case untethered stretch (optimization regarding slow steaming or cutting power then may be added later), not a recurring 12-hour gap.
**`Battery Capacity = max(Coastal Transit Energy, Storm Survival Energy)`**
*   **Coastal Transit Energy**: The energy required to traverse `coastal_untethered_distance_nm` at untethered speeds.
*   **Storm Survival Energy**: The propulsion energy required to sail for `storm_survival_duration_h` at the tethered cruising speed.

### B. Tender Utilization & Economics
The tender's utilization is based on long-haul crossings, not hundreds of quick top-ups per year. 
*   **Tender Cycle Time**: The time required to escort a ship across the ocean `+` `tender_idle_h` at the border. 
*   **Ships per Tender**: Because a tender acts as a dedicated escort, one tender serves approximately one ship at a time. The ratio of tenders to ships will be greater than 1:1, depending on the ratio of open-ocean transit time to coastal/port time (includes fully battery powered trips between ports with swapable batteries. Economic optimization here may also be added later, for now we just look at n ships and m tenders all crossing the ocean continuously).
*   **Levelized Cost ($/kWh)**: The tender's CAPEX must be amortized over the total energy it pushes across the cable during its full ocean crossing escorts.

### C. Cable and Power Bottlenecks
The charging bottleneck (`mob_charge_power_kw` vs. net reactor power) should probably be de-duplicated. The model should verify that:
**`Tender Net Power >= (Cargo Ship Propulsion Power + Battery Recharge Rate) / cable_efficiency`**
Because the ship's speed is artificially limited by the tether (e.g., 16 knots), its propulsion load will be well below its maximum design capability (e.g., 22 knots). This guarantees there will be ample thermal and electrical headroom on the ship's bus to absorb the charging load for the batteries without requiring oversized shipboard infrastructure.
